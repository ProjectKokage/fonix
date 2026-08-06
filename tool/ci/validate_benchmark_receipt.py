#!/usr/bin/env python3
"""Validate and aggregate one exact, externally produced benchmark receipt.

The tool is intentionally offline. It rehashes the model, fixtures, native
build manifest, runtime library, final application artifact, and normalized
provider-assignment evidence named on the command line. It then validates the
closed receipt, recomputes every percentile and throughput value from bounded
integer samples, and emits a path-free validation record.

Passing this validator proves consistency only for the submitted evidence
tuple. It does not create a hardware support claim, a performance baseline, or
a regression threshold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable


sys.dont_write_bytecode = True

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_INPUT_BYTES = 16 * 1024 * 1024 * 1024
MAX_SAMPLES = 100_000
MAX_THROUGHPUT_WINDOWS = 4_096
MAX_DURATION_MICROSECONDS = 10**15
MAX_COMPLETED_RUNS = 10**12
MAX_RATE_MILLI = 10**15
MAX_BYTE_COUNT = (1 << 63) - 1
MAX_NODES = 1_000_000
MAX_OPTIONS = 64
MAX_PROVIDERS = 16
MAX_THREADS = 4_096
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+() ,:=+-]{0,255}$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
PLATFORMS: dict[str, frozenset[str]] = {
    "ios": frozenset({"arm64"}),
    "macos": frozenset({"arm64", "x86_64"}),
    "android": frozenset({"arm64-v8a", "x86_64", "armeabi-v7a"}),
    "linux": frozenset({"x86_64", "arm64"}),
    "windows": frozenset({"x64", "arm64"}),
}
RUNTIME_SOURCES = frozenset({"linked", "bundled", "process", "file"})
RUNTIME_PROFILES = frozenset({"linked", "bundled", "external"})
RUNTIME_OWNERS = frozenset({"wrapper", "sherpa", "application", "system"})
ARTIFACT_TYPES = frozenset(
    {
        "android-apk",
        "android-aab",
        "ios-application-archive",
        "macos-application-archive",
        "linux-application-archive",
        "windows-application-archive",
        "standalone-executable",
    }
)
PROVIDER_REQUIREMENTS = frozenset({"active", "full"})
FALLBACK_POLICIES = frozenset({"allow", "report", "reject-cpu", "reject-any"})
CACHE_STATES = frozenset({"not-applicable", "disabled", "cold", "hit"})
THERMAL_STATES = frozenset(
    {"nominal", "fair", "serious", "critical", "platform-not-exposed"}
)
NOT_RECORDED_SENTINELS = frozenset(
    {"unknown", "unrecorded", "not-recorded", "n/a", "na", "none", "null"}
)
SENSITIVE_OPTION_NAME = re.compile(
    r"(?:^|[_-])(?:path|file|dir|directory|root|location|secret|token|password|"
    r"credential|authorization|api[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
SENSITIVE_OPTION_FRAGMENTS = (
    "cachepath",
    "cachedirectory",
    "cachedir",
    "filepath",
    "rootpath",
    "secret",
    "password",
    "credential",
    "authorization",
    "apikey",
    "apitoken",
    "accesstoken",
    "authtoken",
)
ARTIFACT_TYPES_BY_PLATFORM: dict[str, frozenset[str]] = {
    "ios": frozenset({"ios-application-archive"}),
    "macos": frozenset(
        {"macos-application-archive", "standalone-executable"}
    ),
    "android": frozenset({"android-apk", "android-aab"}),
    "linux": frozenset(
        {"linux-application-archive", "standalone-executable"}
    ),
    "windows": frozenset(
        {"windows-application-archive", "standalone-executable"}
    ),
}


class BenchmarkReceiptError(RuntimeError):
    """The submitted evidence cannot support a validation record."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkReceiptError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise BenchmarkReceiptError(f"non-finite JSON number {value!r} is forbidden")


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise BenchmarkReceiptError(f"{label} has an unexpected field set")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkReceiptError(f"{label} must be an object")
    return value


def _array(value: Any, label: str, *, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise BenchmarkReceiptError(f"{label} count is outside its bound")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise BenchmarkReceiptError(f"{label} is outside its integer bound")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkReceiptError(f"{label} must be a boolean")
    return value


def _token(value: Any, label: str) -> str:
    if not isinstance(value, str) or TOKEN.fullmatch(value) is None:
        raise BenchmarkReceiptError(f"{label} must be a bounded path-free token")
    return value


def _recorded_token(value: Any, label: str) -> str:
    token = _token(value, label)
    if token.lower() in NOT_RECORDED_SENTINELS:
        raise BenchmarkReceiptError(f"{label} must record an explicit value")
    return token


def _label(value: Any, label: str) -> str:
    if not isinstance(value, str) or LABEL.fullmatch(value) is None:
        raise BenchmarkReceiptError(f"{label} must be bounded and path-free")
    if value != value.strip() or "://" in value or "\\" in value or "/" in value:
        raise BenchmarkReceiptError(f"{label} contains a path or URI")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise BenchmarkReceiptError(f"{label} must be a lowercase SHA-256")
    return value


def _nullable_digest(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _digest(value, label)


def _nullable_token(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _token(value, label)


def _regular_file(path: Path, label: str, maximum: int) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise BenchmarkReceiptError(f"missing {label}") from error
    if not stat.S_ISREG(status.st_mode):
        raise BenchmarkReceiptError(f"{label} must be a regular non-link file")
    if status.st_size <= 0 or status.st_size > maximum:
        raise BenchmarkReceiptError(f"{label} size is outside its bound")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, int | str]:
    return {"sizeBytes": path.stat().st_size, "sha256": _sha256(path)}


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    _regular_file(path, label, MAX_JSON_BYTES)
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkReceiptError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise BenchmarkReceiptError(f"{label} must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_file_hash(
    path: Path, expected: str, label: str, maximum: int = MAX_INPUT_BYTES
) -> dict[str, int | str]:
    _regular_file(path, label, maximum)
    identity = _identity(path)
    if identity["sha256"] != expected:
        raise BenchmarkReceiptError(f"{label} hash does not match the receipt")
    return identity


def _nearest_rank(sorted_samples: list[int], percentile: int) -> int:
    index = ((percentile * len(sorted_samples) + 99) // 100) - 1
    return sorted_samples[index]


def _statistics(samples: list[int]) -> dict[str, int]:
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50": _nearest_rank(ordered, 50),
        "p95": _nearest_rank(ordered, 95),
        "p99": _nearest_rank(ordered, 99),
        "max": ordered[-1],
    }


def _validate_statistics(value: Any, expected: dict[str, int], label: str) -> None:
    statistics = _object(value, label)
    _exact_keys(statistics, {"count", "p50", "p95", "p99", "max"}, label)
    for key, expected_value in expected.items():
        if statistics[key] != expected_value or isinstance(statistics[key], bool):
            raise BenchmarkReceiptError(f"{label}.{key} does not match raw samples")


def _validate_series(
    value: Any,
    label: str,
    *,
    maximum_value: int,
) -> tuple[list[int], dict[str, int]]:
    series = _object(value, label)
    _exact_keys(series, {"samples", "statistics"}, label)
    raw_samples = _array(
        series["samples"], f"{label}.samples", minimum=1, maximum=MAX_SAMPLES
    )
    samples = [
        _integer(sample, f"{label}.samples[{index}]", minimum=1, maximum=maximum_value)
        for index, sample in enumerate(raw_samples)
    ]
    expected = _statistics(samples)
    _validate_statistics(series["statistics"], expected, f"{label}.statistics")
    return samples, expected


def _rate_milli(completed_runs: int, duration_microseconds: int) -> int:
    numerator = completed_runs * 1_000_000_000
    rate = (numerator + duration_microseconds // 2) // duration_microseconds
    if rate <= 0 or rate > MAX_RATE_MILLI:
        raise BenchmarkReceiptError("derived throughput rate is outside its bound")
    return rate


def _validate_throughput(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    throughput = _object(value, "measurements.throughput")
    _exact_keys(
        throughput,
        {
            "windows",
            "statisticsRunsPerSecondMilli",
            "totalCompletedRuns",
            "totalDurationMicroseconds",
            "aggregateRunsPerSecondMilli",
        },
        "measurements.throughput",
    )
    windows = _array(
        throughput["windows"],
        "measurements.throughput.windows",
        minimum=1,
        maximum=MAX_THROUGHPUT_WINDOWS,
    )
    normalized: list[dict[str, int]] = []
    rates: list[int] = []
    total_runs = 0
    total_duration = 0
    for index, raw_window in enumerate(windows):
        window = _object(raw_window, f"throughput window {index}")
        _exact_keys(
            window,
            {"completedRuns", "durationMicroseconds"},
            f"throughput window {index}",
        )
        completed = _integer(
            window["completedRuns"],
            f"throughput window {index}.completedRuns",
            minimum=1,
            maximum=MAX_COMPLETED_RUNS,
        )
        duration = _integer(
            window["durationMicroseconds"],
            f"throughput window {index}.durationMicroseconds",
            minimum=1,
            maximum=MAX_DURATION_MICROSECONDS,
        )
        total_runs += completed
        total_duration += duration
        if total_runs > MAX_COMPLETED_RUNS or total_duration > MAX_DURATION_MICROSECONDS:
            raise BenchmarkReceiptError("throughput totals exceed their bounds")
        normalized.append(
            {"completedRuns": completed, "durationMicroseconds": duration}
        )
        rates.append(_rate_milli(completed, duration))

    rate_statistics = _statistics(rates)
    _validate_statistics(
        throughput["statisticsRunsPerSecondMilli"],
        rate_statistics,
        "measurements.throughput.statisticsRunsPerSecondMilli",
    )
    aggregate = _rate_milli(total_runs, total_duration)
    if throughput["totalCompletedRuns"] != total_runs:
        raise BenchmarkReceiptError("throughput totalCompletedRuns is inconsistent")
    if throughput["totalDurationMicroseconds"] != total_duration:
        raise BenchmarkReceiptError("throughput totalDurationMicroseconds is inconsistent")
    if throughput["aggregateRunsPerSecondMilli"] != aggregate:
        raise BenchmarkReceiptError("throughput aggregate rate is inconsistent")
    return (
        {
            "windows": normalized,
            "statisticsRunsPerSecondMilli": rate_statistics,
            "totalCompletedRuns": total_runs,
            "totalDurationMicroseconds": total_duration,
            "aggregateRunsPerSecondMilli": aggregate,
        },
        {
            "statisticsRunsPerSecondMilli": rate_statistics,
            "totalCompletedRuns": total_runs,
            "totalDurationMicroseconds": total_duration,
            "aggregateRunsPerSecondMilli": aggregate,
        },
    )


def _validate_provider_inventory(
    value: Any, label: str, *, nullable: bool
) -> list[dict[str, str | None]] | None:
    if value is None:
        if nullable:
            return None
        raise BenchmarkReceiptError(f"{label} must be a bounded non-empty array")
    raw_providers = _array(value, label, minimum=1, maximum=64)
    providers: list[dict[str, str | None]] = []
    provider_ids: set[str] = set()
    for index, raw_provider in enumerate(raw_providers):
        provider = _object(raw_provider, f"{label}[{index}]")
        _exact_keys(
            provider,
            {"wrapperId", "reportedName"},
            f"{label}[{index}]",
        )
        wrapper_id = _token(provider["wrapperId"], f"{label}[{index}].wrapperId")
        if wrapper_id in provider_ids:
            raise BenchmarkReceiptError(f"{label} provider IDs must be unique")
        provider_ids.add(wrapper_id)
        reported_name = provider["reportedName"]
        if reported_name is not None:
            reported_name = _token(
                reported_name,
                f"{label}[{index}].reportedName",
            )
        providers.append({"wrapperId": wrapper_id, "reportedName": reported_name})
    return providers


def _validate_build_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        manifest,
        {
            "schemaVersion",
            "nativeIdentity",
            "shimAbiVersion",
            "requiredOrtApiVersion",
            "runtimeProfile",
            "androidRuntimeOwner",
            "allowedRuntimeSources",
            "buildId",
            "artifact",
        },
        "native build manifest",
    )
    if manifest["schemaVersion"] != 3 or manifest["nativeIdentity"] != "fonix_shim":
        raise BenchmarkReceiptError("native build manifest is not schema 3 Fonix data")
    _integer(
        manifest["shimAbiVersion"], "build manifest shim ABI", minimum=1, maximum=1000
    )
    _integer(
        manifest["requiredOrtApiVersion"],
        "build manifest required ORT API",
        minimum=1,
        maximum=1000,
    )
    profile = manifest["runtimeProfile"]
    if profile not in RUNTIME_PROFILES:
        raise BenchmarkReceiptError("native build manifest runtime profile is invalid")
    _token(manifest["buildId"], "native build manifest buildId")

    owner = manifest["androidRuntimeOwner"]
    if owner is not None and owner not in {"sherpa", "application"}:
        raise BenchmarkReceiptError("native build manifest Android owner is invalid")
    sources = _array(
        manifest["allowedRuntimeSources"],
        "native build manifest allowedRuntimeSources",
        minimum=1,
        maximum=4,
    )
    if any(source not in RUNTIME_SOURCES for source in sources) or len(set(sources)) != len(
        sources
    ):
        raise BenchmarkReceiptError("native build manifest runtime sources are invalid")
    expected_sources = {
        "sherpa": ["process"],
        "application": ["bundled"],
    }.get(
        owner,
        {
            "external": ["process", "file"],
            "bundled": ["bundled"],
            "linked": ["linked"],
        }[profile],
    )
    if sources != expected_sources:
        raise BenchmarkReceiptError("native build manifest source policy is contradictory")

    raw_artifact = manifest["artifact"]
    artifact: dict[str, Any] | None
    if raw_artifact is None:
        artifact = None
    else:
        artifact = _object(raw_artifact, "native build manifest artifact")
        _exact_keys(
            artifact,
            {
                "id",
                "lockSha256",
                "sourceSha256",
                "targetOs",
                "targetArchitecture",
                "targetVariant",
                "minimumOs",
                "flavor",
                "runtimeMode",
                "thirdPartyNoticesSha256",
                "providers",
            },
            "native build manifest artifact",
        )
        for key in ("id", "targetOs", "targetArchitecture", "targetVariant", "flavor"):
            _token(artifact[key], f"native build manifest artifact.{key}")
        for key in ("lockSha256", "sourceSha256", "thirdPartyNoticesSha256"):
            _digest(artifact[key], f"native build manifest artifact.{key}")
        _label(artifact["minimumOs"], "native build manifest artifact.minimumOs")
        if artifact["runtimeMode"] not in {"bundled", "linked"}:
            raise BenchmarkReceiptError("native build artifact runtimeMode is invalid")
        if artifact["runtimeMode"] != profile:
            raise BenchmarkReceiptError("native build artifact profile is contradictory")
        artifact["providers"] = _validate_provider_inventory(
            artifact["providers"],
            "native build manifest artifact.providers",
            nullable=False,
        )

    if (profile == "external") != (artifact is None):
        raise BenchmarkReceiptError("native build artifact presence is contradictory")
    if owner == "sherpa" and (profile != "external" or artifact is not None):
        raise BenchmarkReceiptError("sherpa-owned manifest must be external")
    if owner == "application" and (
        profile != "bundled" or artifact is None or artifact["targetOs"] != "android"
    ):
        raise BenchmarkReceiptError("application-owned Android manifest is contradictory")
    if owner is None and artifact is not None and artifact["targetOs"] == "android":
        raise BenchmarkReceiptError("Android artifact has no explicit owner")
    return manifest


def _validate_model(value: Any) -> dict[str, Any]:
    model = _object(value, "model")
    _exact_keys(
        model,
        {
            "id",
            "onnxSha256",
            "inputFixtureSha256",
            "referenceOutputSha256",
            "opset",
        },
        "model",
    )
    _token(model["id"], "model.id")
    for key in ("onnxSha256", "inputFixtureSha256", "referenceOutputSha256"):
        _digest(model[key], f"model.{key}")
    _integer(model["opset"], "model.opset", minimum=1, maximum=1000)
    return model


def _validate_build(value: Any) -> dict[str, Any]:
    build = _object(value, "build")
    _exact_keys(
        build,
        {
            "packageVersion",
            "nativeBuildManifestSha256",
            "finalArtifactType",
            "finalArtifactSha256",
        },
        "build",
    )
    if not isinstance(build["packageVersion"], str) or SEMVER.fullmatch(
        build["packageVersion"]
    ) is None:
        raise BenchmarkReceiptError("build.packageVersion must be strict semver")
    _digest(build["nativeBuildManifestSha256"], "build.nativeBuildManifestSha256")
    _digest(build["finalArtifactSha256"], "build.finalArtifactSha256")
    if build["finalArtifactType"] not in ARTIFACT_TYPES:
        raise BenchmarkReceiptError("build.finalArtifactType is outside the closed set")
    return build


def _validate_runtime(value: Any) -> dict[str, Any]:
    runtime = _object(value, "runtime")
    _exact_keys(
        runtime,
        {
            "ortVersion",
            "requiredOrtApi",
            "negotiatedOrtApi",
            "shimAbi",
            "shimBuildId",
            "runtimeOwner",
            "runtimeSource",
            "artifactFlavor",
            "artifactId",
            "artifactSourceSha256",
            "compiledProviders",
            "librarySha256",
        },
        "runtime",
    )
    _label(runtime["ortVersion"], "runtime.ortVersion")
    for key in ("requiredOrtApi", "negotiatedOrtApi", "shimAbi"):
        _integer(runtime[key], f"runtime.{key}", minimum=1, maximum=1000)
    _token(runtime["shimBuildId"], "runtime.shimBuildId")
    if runtime["runtimeOwner"] not in RUNTIME_OWNERS:
        raise BenchmarkReceiptError("runtime.runtimeOwner is outside the closed set")
    if runtime["runtimeSource"] not in RUNTIME_SOURCES:
        raise BenchmarkReceiptError("runtime.runtimeSource is outside the closed set")
    _token(runtime["artifactFlavor"], "runtime.artifactFlavor")
    _nullable_token(runtime["artifactId"], "runtime.artifactId")
    _nullable_digest(runtime["artifactSourceSha256"], "runtime.artifactSourceSha256")
    runtime["compiledProviders"] = _validate_provider_inventory(
        runtime["compiledProviders"], "runtime.compiledProviders", nullable=True
    )
    _digest(runtime["librarySha256"], "runtime.librarySha256")
    if runtime["requiredOrtApi"] != runtime["negotiatedOrtApi"]:
        raise BenchmarkReceiptError("runtime ORT API negotiation is inconsistent")
    return runtime


def _validate_options(value: Any, expected_hash: Any) -> list[dict[str, str]]:
    raw_options = _array(value, "provider.options", minimum=0, maximum=MAX_OPTIONS)
    options: list[dict[str, str]] = []
    observed: set[str] = set()
    for index, raw_option in enumerate(raw_options):
        option = _object(raw_option, f"provider option {index}")
        _exact_keys(option, {"name", "encoding", "value"}, f"provider option {index}")
        name = _token(option["name"], f"provider option {index}.name")
        if name in observed:
            raise BenchmarkReceiptError("provider option names must be unique")
        observed.add(name)
        encoding = option["encoding"]
        if encoding not in {"plain", "sha256"}:
            raise BenchmarkReceiptError("provider option encoding is invalid")
        if encoding == "sha256":
            value_text = _digest(option["value"], f"provider option {name}.value")
        else:
            value_text = _label(option["value"], f"provider option {name}.value")
            normalized_name = re.sub(r"[^a-z0-9]", "", name.lower())
            if SENSITIVE_OPTION_NAME.search(name) is not None or any(
                fragment in normalized_name for fragment in SENSITIVE_OPTION_FRAGMENTS
            ):
                raise BenchmarkReceiptError(
                    "path- or secret-bearing provider options must use SHA-256 encoding"
                )
            lowered = value_text.lower()
            if (
                lowered.startswith("bearer ")
                or lowered.startswith("sk-")
                or "password=" in lowered
                or "token=" in lowered
            ):
                raise BenchmarkReceiptError("provider option value resembles a secret")
        options.append({"name": name, "encoding": encoding, "value": value_text})
    if [option["name"] for option in options] != sorted(observed):
        raise BenchmarkReceiptError("provider options must be sorted by name")
    expected = _digest(expected_hash, "provider.optionsSha256")
    if _canonical_hash(options) != expected:
        raise BenchmarkReceiptError("provider options canonical hash mismatch")
    return options


def _validate_assignment_counts(value: Any, label: str) -> dict[str, int]:
    counts = _object(value, label)
    if not 1 <= len(counts) <= MAX_PROVIDERS:
        raise BenchmarkReceiptError(f"{label} provider count is outside its bound")
    normalized: dict[str, int] = {}
    total = 0
    for provider_id, raw_count in counts.items():
        _token(provider_id, f"{label} provider ID")
        count = _integer(
            raw_count,
            f"{label}.{provider_id}",
            minimum=1,
            maximum=MAX_NODES,
        )
        total += count
        if total > MAX_NODES:
            raise BenchmarkReceiptError(f"{label} total exceeds its bound")
        normalized[provider_id] = count
    return normalized


def _validate_provider(value: Any) -> dict[str, Any]:
    provider = _object(value, "provider")
    _exact_keys(
        provider,
        {
            "id",
            "reportedName",
            "discoverable",
            "registered",
            "requirement",
            "fallbackPolicy",
            "options",
            "optionsSha256",
            "assignmentEvidenceSha256",
            "nodeExecutionCount",
            "nodeExecutionsByProvider",
            "fallbackObserved",
        },
        "provider",
    )
    provider_id = _token(provider["id"], "provider.id")
    _token(provider["reportedName"], "provider.reportedName")
    if _boolean(provider["discoverable"], "provider.discoverable") is not True:
        raise BenchmarkReceiptError("benchmark provider must be discoverable")
    if _boolean(provider["registered"], "provider.registered") is not True:
        raise BenchmarkReceiptError("benchmark provider must be registered")
    if provider["requirement"] not in PROVIDER_REQUIREMENTS:
        raise BenchmarkReceiptError("provider.requirement is outside the closed set")
    if provider["fallbackPolicy"] not in FALLBACK_POLICIES:
        raise BenchmarkReceiptError("provider.fallbackPolicy is outside the closed set")
    options = _validate_options(provider["options"], provider["optionsSha256"])
    _digest(provider["assignmentEvidenceSha256"], "provider.assignmentEvidenceSha256")
    node_count = _integer(
        provider["nodeExecutionCount"],
        "provider.nodeExecutionCount",
        minimum=1,
        maximum=MAX_NODES,
    )
    counts = _validate_assignment_counts(
        provider["nodeExecutionsByProvider"], "provider.nodeExecutionsByProvider"
    )
    if sum(counts.values()) != node_count:
        raise BenchmarkReceiptError("provider assignment counts do not sum to the total")
    if counts.get(provider_id, 0) <= 0:
        raise BenchmarkReceiptError("target provider executed no recorded node")
    fallback_observed = any(key != provider_id for key in counts)
    if _boolean(provider["fallbackObserved"], "provider.fallbackObserved") != fallback_observed:
        raise BenchmarkReceiptError("provider fallback flag contradicts assignment counts")
    if provider["requirement"] == "full" and fallback_observed:
        raise BenchmarkReceiptError("full provider requirement contradicts fallback evidence")
    if (
        provider["fallbackPolicy"] == "reject-cpu"
        and provider_id != "cpu"
        and counts.get("cpu", 0) > 0
    ):
        raise BenchmarkReceiptError("reject-cpu policy contradicts CPU assignment")
    if provider["fallbackPolicy"] == "reject-any" and fallback_observed:
        raise BenchmarkReceiptError("reject-any policy contradicts fallback evidence")
    return {
        **provider,
        "options": options,
        "nodeExecutionsByProvider": counts,
    }


def _validate_environment(value: Any) -> dict[str, Any]:
    environment = _object(value, "environment")
    _exact_keys(
        environment,
        {
            "platform",
            "architecture",
            "deviceIdentitySha256",
            "osVersion",
            "osBuild",
            "driverIdentity",
            "firmwareIdentity",
            "powerMode",
            "thermalState",
        },
        "environment",
    )
    platform = environment["platform"]
    if platform not in PLATFORMS:
        raise BenchmarkReceiptError("environment.platform is outside the closed set")
    if environment["architecture"] not in PLATFORMS[platform]:
        raise BenchmarkReceiptError("environment architecture does not match platform")
    _digest(
        environment["deviceIdentitySha256"],
        "environment.deviceIdentitySha256",
    )
    _label(environment["osVersion"], "environment.osVersion")
    _recorded_token(environment["osBuild"], "environment.osBuild")
    _recorded_token(environment["driverIdentity"], "environment.driverIdentity")
    _recorded_token(environment["firmwareIdentity"], "environment.firmwareIdentity")
    _recorded_token(environment["powerMode"], "environment.powerMode")
    if environment["thermalState"] not in THERMAL_STATES:
        raise BenchmarkReceiptError("environment.thermalState is outside the closed set")
    return environment


def _validate_workload(value: Any) -> dict[str, Any]:
    workload = _object(value, "workload")
    _exact_keys(
        workload,
        {
            "precision",
            "batchSize",
            "concurrency",
            "intraOpThreads",
            "interOpThreads",
            "warmupRuns",
            "measuredWarmRuns",
            "throughputWindowCount",
            "cacheState",
            "inputShapeSignatureSha256",
        },
        "workload",
    )
    _recorded_token(workload["precision"], "workload.precision")
    _integer(workload["batchSize"], "workload.batchSize", minimum=1, maximum=1_000_000)
    _integer(workload["concurrency"], "workload.concurrency", minimum=1, maximum=1024)
    for key in ("intraOpThreads", "interOpThreads"):
        _integer(workload[key], f"workload.{key}", minimum=0, maximum=MAX_THREADS)
    _integer(workload["warmupRuns"], "workload.warmupRuns", minimum=0, maximum=MAX_SAMPLES)
    _integer(
        workload["measuredWarmRuns"],
        "workload.measuredWarmRuns",
        minimum=1,
        maximum=MAX_SAMPLES,
    )
    _integer(
        workload["throughputWindowCount"],
        "workload.throughputWindowCount",
        minimum=1,
        maximum=MAX_THROUGHPUT_WINDOWS,
    )
    if workload["cacheState"] not in CACHE_STATES:
        raise BenchmarkReceiptError("workload.cacheState is outside the closed set")
    _digest(workload["inputShapeSignatureSha256"], "workload.inputShapeSignatureSha256")
    return workload


def _validate_measurements(
    value: Any, workload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    measurements = _object(value, "measurements")
    _exact_keys(
        measurements,
        {
            "coldRuntimeLoadMicroseconds",
            "sessionCreateMicroseconds",
            "firstRunMicroseconds",
            "warmRunMicroseconds",
            "throughput",
        },
        "measurements",
    )
    normalized: dict[str, Any] = {}
    aggregate: dict[str, Any] = {}
    for key in (
        "coldRuntimeLoadMicroseconds",
        "sessionCreateMicroseconds",
        "firstRunMicroseconds",
        "warmRunMicroseconds",
    ):
        samples, statistics = _validate_series(
            measurements[key], f"measurements.{key}", maximum_value=MAX_DURATION_MICROSECONDS
        )
        normalized[key] = {"samples": samples, "statistics": statistics}
        aggregate[key] = statistics
    if len(normalized["warmRunMicroseconds"]["samples"]) != workload["measuredWarmRuns"]:
        raise BenchmarkReceiptError("warm-run sample count does not match workload")
    throughput, throughput_aggregate = _validate_throughput(measurements["throughput"])
    if len(throughput["windows"]) != workload["throughputWindowCount"]:
        raise BenchmarkReceiptError("throughput window count does not match workload")
    normalized["throughput"] = throughput
    aggregate["throughput"] = throughput_aggregate
    return normalized, aggregate


def _validate_resources(
    value: Any,
    workload: dict[str, Any],
    *,
    final_artifact_size: int,
    runtime_library_size: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resources = _object(value, "resources")
    _exact_keys(resources, {"rssBytes", "binaryBytes", "cacheBytes"}, "resources")

    rss = _object(resources["rssBytes"], "resources.rssBytes")
    rss_aggregate: dict[str, Any]
    if rss.get("status") == "measured":
        _exact_keys(rss, {"status", "samples", "statistics"}, "resources.rssBytes")
        samples, statistics = _validate_series(
            {"samples": rss["samples"], "statistics": rss["statistics"]},
            "resources.rssBytes",
            maximum_value=MAX_BYTE_COUNT,
        )
        normalized_rss: dict[str, Any] = {
            "status": "measured",
            "samples": samples,
            "statistics": statistics,
        }
        rss_aggregate = {"status": "measured", "statistics": statistics}
    elif rss.get("status") == "not-applicable":
        _exact_keys(rss, {"status", "reason"}, "resources.rssBytes")
        if rss["reason"] != "target-does-not-expose-rss":
            raise BenchmarkReceiptError("RSS omission reason is outside the closed set")
        normalized_rss = dict(rss)
        rss_aggregate = dict(rss)
    else:
        raise BenchmarkReceiptError("RSS must be measured or explicitly not applicable")

    binary = _object(resources["binaryBytes"], "resources.binaryBytes")
    _exact_keys(
        binary,
        {
            "finalArtifact",
            "runtimeLibrary",
            "shimLibrary",
            "providerDependencies",
        },
        "resources.binaryBytes",
    )
    for key in binary:
        minimum = 1 if key in {"finalArtifact", "runtimeLibrary", "shimLibrary"} else 0
        _integer(
            binary[key],
            f"resources.binaryBytes.{key}",
            minimum=minimum,
            maximum=MAX_BYTE_COUNT,
        )
    if binary["finalArtifact"] != final_artifact_size:
        raise BenchmarkReceiptError("final artifact size does not match the receipt")
    if binary["runtimeLibrary"] != runtime_library_size:
        raise BenchmarkReceiptError("runtime library size does not match the receipt")

    cache = _object(resources["cacheBytes"], "resources.cacheBytes")
    cache_state = workload["cacheState"]
    if cache_state in {"cold", "hit"}:
        _exact_keys(
            cache,
            {"status", "state", "before", "after"},
            "resources.cacheBytes",
        )
        if cache["status"] != "measured" or cache["state"] != cache_state:
            raise BenchmarkReceiptError("cache measurement state contradicts workload")
        _integer(cache["before"], "resources.cacheBytes.before", minimum=0, maximum=MAX_BYTE_COUNT)
        _integer(cache["after"], "resources.cacheBytes.after", minimum=0, maximum=MAX_BYTE_COUNT)
    else:
        _exact_keys(cache, {"status", "reason"}, "resources.cacheBytes")
        expected_reason = {
            "not-applicable": "provider-has-no-cache",
            "disabled": "cache-disabled",
        }[cache_state]
        if cache["status"] != "not-applicable" or cache["reason"] != expected_reason:
            raise BenchmarkReceiptError("cache omission does not match workload state")

    normalized = {
        "rssBytes": normalized_rss,
        "binaryBytes": dict(binary),
        "cacheBytes": dict(cache),
    }
    aggregate = {
        "rssBytes": rss_aggregate,
        "binaryBytes": dict(binary),
        "cacheBytes": dict(cache),
    }
    return normalized, aggregate


def _validate_assignment_evidence(
    evidence: dict[str, Any], provider: dict[str, Any]
) -> dict[str, Any]:
    _exact_keys(
        evidence,
        {"schemaVersion", "nodeExecutionCount", "nodeExecutionsByProvider"},
        "provider assignment evidence",
    )
    if evidence["schemaVersion"] != 1:
        raise BenchmarkReceiptError("provider assignment evidence schema is unsupported")
    node_count = _integer(
        evidence["nodeExecutionCount"],
        "assignment evidence nodeExecutionCount",
        minimum=1,
        maximum=MAX_NODES,
    )
    counts = _validate_assignment_counts(
        evidence["nodeExecutionsByProvider"],
        "assignment evidence nodeExecutionsByProvider",
    )
    if sum(counts.values()) != node_count:
        raise BenchmarkReceiptError("assignment evidence counts do not sum to the total")
    if node_count != provider["nodeExecutionCount"] or counts != provider[
        "nodeExecutionsByProvider"
    ]:
        raise BenchmarkReceiptError("provider summary contradicts assignment evidence")
    return {
        "schemaVersion": 1,
        "nodeExecutionCount": node_count,
        "nodeExecutionsByProvider": counts,
    }


def _cross_check_build_manifest(
    receipt: dict[str, Any], manifest: dict[str, Any]
) -> None:
    build = receipt["build"]
    runtime = receipt["runtime"]
    environment = receipt["environment"]
    if runtime["shimAbi"] != manifest["shimAbiVersion"]:
        raise BenchmarkReceiptError("runtime shim ABI contradicts the build manifest")
    if runtime["shimBuildId"] != manifest["buildId"]:
        raise BenchmarkReceiptError("runtime build ID contradicts the build manifest")
    if runtime["requiredOrtApi"] != manifest["requiredOrtApiVersion"]:
        raise BenchmarkReceiptError("runtime API floor contradicts the build manifest")
    if runtime["runtimeSource"] not in manifest["allowedRuntimeSources"]:
        raise BenchmarkReceiptError("runtime source is forbidden by the build manifest")

    android_owner = manifest["androidRuntimeOwner"]
    expected_owner = {
        "sherpa": "sherpa",
        "application": "application",
    }.get(
        android_owner,
        {
            "linked": "wrapper",
            "bundled": "wrapper",
            "file": "application",
            "process": "system",
        }[runtime["runtimeSource"]],
    )
    if runtime["runtimeOwner"] != expected_owner:
        raise BenchmarkReceiptError("runtime owner contradicts the build/source policy")

    artifact = manifest["artifact"]
    if artifact is None:
        if runtime["artifactId"] is not None or runtime["artifactSourceSha256"] is not None:
            raise BenchmarkReceiptError("external runtime invents a build artifact identity")
        if runtime["compiledProviders"] is not None:
            raise BenchmarkReceiptError(
                "external runtime invents a compiled provider inventory"
            )
    else:
        if runtime["artifactId"] != artifact["id"]:
            raise BenchmarkReceiptError("runtime artifact ID contradicts the build manifest")
        if runtime["artifactSourceSha256"] != artifact["sourceSha256"]:
            raise BenchmarkReceiptError("runtime source digest contradicts the build manifest")
        if runtime["artifactFlavor"] != artifact["flavor"]:
            raise BenchmarkReceiptError("runtime flavor contradicts the build manifest")
        if environment["platform"] != artifact["targetOs"]:
            raise BenchmarkReceiptError("benchmark platform contradicts the build artifact")
        if environment["architecture"] != artifact["targetArchitecture"]:
            raise BenchmarkReceiptError("benchmark architecture contradicts the build artifact")
        if runtime["compiledProviders"] != artifact["providers"]:
            raise BenchmarkReceiptError(
                "runtime compiled provider inventory contradicts the build manifest"
            )
        artifact_providers = {
            entry["wrapperId"]: entry["reportedName"]
            for entry in artifact["providers"]
        }
        provider_id = receipt["provider"]["id"]
        if provider_id not in artifact_providers:
            raise BenchmarkReceiptError(
                "target provider is absent from the compiled artifact inventory"
            )
        if artifact_providers[provider_id] != receipt["provider"]["reportedName"]:
            raise BenchmarkReceiptError(
                "target provider reported name contradicts the artifact inventory"
            )
    if build["finalArtifactType"] not in ARTIFACT_TYPES_BY_PLATFORM[
        environment["platform"]
    ]:
        raise BenchmarkReceiptError("final artifact type contradicts the platform")


def _validate_receipt_structure(receipt: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        receipt,
        {
            "schemaVersion",
            "result",
            "purpose",
            "receiptId",
            "model",
            "build",
            "runtime",
            "provider",
            "environment",
            "workload",
            "measurements",
            "resources",
        },
        "benchmark receipt",
    )
    if receipt["schemaVersion"] != 1 or receipt["result"] != "measured":
        raise BenchmarkReceiptError("receipt must be a measured schemaVersion 1 record")
    if receipt["purpose"] != "benchmark-evidence-only":
        raise BenchmarkReceiptError("receipt purpose cannot make a performance claim")
    _token(receipt["receiptId"], "receipt.receiptId")
    model = _validate_model(receipt["model"])
    build = _validate_build(receipt["build"])
    runtime = _validate_runtime(receipt["runtime"])
    provider = _validate_provider(receipt["provider"])
    environment = _validate_environment(receipt["environment"])
    workload = _validate_workload(receipt["workload"])
    return {
        **receipt,
        "model": model,
        "build": build,
        "runtime": runtime,
        "provider": provider,
        "environment": environment,
        "workload": workload,
    }


def validate(arguments: argparse.Namespace) -> dict[str, Any]:
    receipt, receipt_hash = _read_json(arguments.receipt, "benchmark receipt")
    receipt = _validate_receipt_structure(receipt)

    build_manifest, build_manifest_hash = _read_json(
        arguments.build_manifest, "native build manifest"
    )
    build_manifest = _validate_build_manifest(build_manifest)
    if build_manifest_hash != receipt["build"]["nativeBuildManifestSha256"]:
        raise BenchmarkReceiptError("native build manifest hash does not match the receipt")

    assignment, assignment_hash = _read_json(
        arguments.assignment_evidence, "provider assignment evidence"
    )
    if assignment_hash != receipt["provider"]["assignmentEvidenceSha256"]:
        raise BenchmarkReceiptError("assignment evidence hash does not match the receipt")
    _validate_assignment_evidence(assignment, receipt["provider"])

    bindings: dict[str, dict[str, int | str]] = {
        "model": _validate_file_hash(
            arguments.model, receipt["model"]["onnxSha256"], "ONNX model"
        ),
        "inputFixture": _validate_file_hash(
            arguments.input_fixture,
            receipt["model"]["inputFixtureSha256"],
            "input fixture",
        ),
        "referenceOutput": _validate_file_hash(
            arguments.reference_output,
            receipt["model"]["referenceOutputSha256"],
            "reference output",
        ),
        "nativeBuildManifest": _identity(arguments.build_manifest),
        "runtimeLibrary": _validate_file_hash(
            arguments.runtime_artifact,
            receipt["runtime"]["librarySha256"],
            "runtime library",
        ),
        "finalArtifact": _validate_file_hash(
            arguments.final_artifact,
            receipt["build"]["finalArtifactSha256"],
            "final application artifact",
        ),
        "assignmentEvidence": _identity(arguments.assignment_evidence),
    }
    _cross_check_build_manifest(receipt, build_manifest)

    measurements, aggregate_measurements = _validate_measurements(
        receipt["measurements"], receipt["workload"]
    )
    resources, aggregate_resources = _validate_resources(
        receipt["resources"],
        receipt["workload"],
        final_artifact_size=int(bindings["finalArtifact"]["sizeBytes"]),
        runtime_library_size=int(bindings["runtimeLibrary"]["sizeBytes"]),
    )
    receipt["measurements"] = measurements
    receipt["resources"] = resources

    return {
        "schemaVersion": 1,
        "result": "validated",
        "claimStatus": "measurement-only",
        "validatorSha256": _sha256(Path(__file__)),
        "receiptSha256": receipt_hash,
        "receiptId": receipt["receiptId"],
        "bindings": bindings,
        "model": receipt["model"],
        "build": receipt["build"],
        "runtime": receipt["runtime"],
        "provider": receipt["provider"],
        "environment": receipt["environment"],
        "workload": receipt["workload"],
        "statistics": aggregate_measurements,
        "resources": aggregate_resources,
        "claimBoundary": (
            "This record proves bounded receipt consistency only for the exact "
            "model, fixtures, build manifest, runtime library, final artifact, "
            "provider options, assignment evidence, target environment, workload, "
            "and raw samples above. It is not a support claim, performance baseline, "
            "regression threshold, or evidence transferable to another tuple."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input-fixture", type=Path, required=True)
    parser.add_argument("--reference-output", type=Path, required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--runtime-artifact", type=Path, required=True)
    parser.add_argument("--final-artifact", type=Path, required=True)
    parser.add_argument("--assignment-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise BenchmarkReceiptError("--output must be absolute")
    if path.exists() or path.is_symlink():
        raise BenchmarkReceiptError("--output must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise BenchmarkReceiptError("temporary output already exists")
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        record = validate(arguments)
        _write_new(arguments.output, record)
    except (BenchmarkReceiptError, FileNotFoundError, OSError, UnicodeError) as error:
        print(f"validate_benchmark_receipt: {error}", file=sys.stderr)
        return 1
    print("Wrote validated benchmark evidence record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
