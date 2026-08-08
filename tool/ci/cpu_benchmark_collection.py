#!/usr/bin/env python3
"""Strict, path-free derivation helpers for CPU benchmark collections.

This module deliberately does not launch an application.  It validates one
already captured target-fragment protocol, binds packaged native inputs to the
current lock and resolver manifest, and derives cross-launch aggregates from
the unfiltered raw evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence


MAXIMUM_JSON_BYTES = 8 * 1024 * 1024
MAXIMUM_JSON_DEPTH = 32
MAXIMUM_JSON_NODES = 262_144
MAXIMUM_FRAGMENT_BYTES = 128 * 1024
MAXIMUM_HOST_OBSERVATION_BYTES = 1024 * 1024
MAXIMUM_FILE_BYTES = 16 * 1024 * 1024 * 1024
MAXIMUM_SHIM_BYTES = 256 * 1024 * 1024
MAXIMUM_TREE_ENTRIES = 16_384
MAXIMUM_TREE_DEPTH = 64
MAXIMUM_TREE_PATH_BYTES = 4096
MAXIMUM_TREE_BYTES = 16 * 1024 * 1024 * 1024
MAXIMUM_LAUNCHES = 16
MAXIMUM_DURATION = 1_000_000_000_000_000
MAXIMUM_DERIVATION_THROUGHPUT_DURATION = 3 * MAXIMUM_LAUNCHES * MAXIMUM_DURATION
MAXIMUM_RSS_BYTES = 0x7FFF_FFFF_FFFF_FFFF

PROTOCOL_ID = "fonix-cpu-benchmark-target-v3"
PROTOCOL_VERSION = 3
PROTOCOL_DESCRIPTOR_SHA256 = (
    "1bd8d293f4cb205991f5a0da1d9f9bc98710bc0ba5054c7d1f9d5150ba2dd7fa"
)
PROTOCOL_DESCRIPTOR_V2_SHA256 = (
    "93a33f420c1b38d1061eb00c713fb5d13135dfed7c283673c26c185fbd3727ac"
)
TARGET_FRAGMENT_SCHEMA_SHA256 = (
    "b38d7a8015ccc8068fb4f9854ccc692bddd14f7d678be9a9720d38cbac31359b"
)
TARGET_FRAGMENT_SCHEMA_V2_SHA256 = (
    "58c02fb47c71f95030792476dc96ea0614879b9cd88680d2f13443656051060d"
)
SOURCE_MANIFEST_VALIDATOR_SIZE_BYTES = 17_636
SOURCE_MANIFEST_VALIDATOR_SHA256 = (
    "9ef720e3bae376a01b4b61c2b2a4214c31760075c23bd64dca4fb887641dd5b6"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+() ,:=+-]{0,255}$")
_MACOS_POWER_MODE = re.compile(
    r"^macos-(?:ac-power|battery-power|ups-power)-low-power-(?:on|off)-"
    r"profile-sha256-[0-9a-f]{64}$"
)
_LINUX_POWER_MODE = re.compile(
    r"^cpu-governor-[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)
_HOST_API_UNAVAILABLE = "not-exposed-by-host-api"
_THERMAL_STATES = frozenset(
    {
        _HOST_API_UNAVAILABLE,
        "nominal",
        "fair",
        "serious",
        "critical",
    }
)
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

_FRAGMENT_KEYS = {
    "schemaVersion",
    "result",
    "purpose",
    "protocol",
    "launchChallenge",
    "processId",
    "freshProcessRequired",
    "executionSurface",
    "model",
    "runtime",
    "session",
    "timing",
    "stabilization",
    "measurements",
    "providerAssignment",
    "resources",
    "lifecycle",
    "poolEvidence",
    "claimBoundary",
}

_MODEL = {
    "id": (
        "cpu-benchmark-matmul-sha256-"
        "19bc0466ef8627df9764b40d947ff2c7cfa978c7daa6952ca9553c700a6dbcf0"
    ),
    "onnxSha256": (
        "19bc0466ef8627df9764b40d947ff2c7cfa978c7daa6952ca9553c700a6dbcf0"
    ),
    "onnxSizeBytes": 4_194_629,
    "inputFixtureSha256": (
        "2025466d19e8aa6a9820266d0622d7b154059b61a1051b9edf1d020bf127c36a"
    ),
    "inputFixtureSizeBytes": 8_388_608,
    "referenceOutputSha256": (
        "c79ff7588eadd3d82ba4a5028955ed02b98a72b11da828131b33075c781a40fb"
    ),
    "referenceOutputSizeBytes": 8_388_608,
    "metadataSha256": (
        "7c089a5a6c6cd444eb802bb0066a2fae054e54a1b924cffac7c53b80bd9c7c8a"
    ),
    "metadataSizeBytes": 3_481,
    "generatorId": "cpu-benchmark-matmul-v1",
    "generatorSha256": (
        "5730c2193acc8bd9fb3fd52fb9c973d46a512e2bd56c4d815caca49fe4e16ec2"
    ),
    "generatorSizeBytes": 20_969,
    "opset": 17,
    "precision": "float32",
    "inputName": "input",
    "inputShape": [2048, 1024],
    "outputName": "output",
    "outputShape": [2048, 1024],
    "referencePolicy": {
        "comparison": "exact-ieee754-binary32-bits",
        "absoluteTolerance": 0,
        "relativeTolerance": 0,
        "nanPolicy": "forbid",
        "infinityPolicy": "forbid",
    },
}

_SESSION = {
    "poolSize": 1,
    "concurrency": 1,
    "graphOptimization": "all",
    "executionMode": "sequential",
    "intraOpThreads": 1,
    "interOpThreads": 1,
    "cpuMemoryArena": True,
    "memoryPattern": True,
    "deterministicCompute": True,
    "timedProviderPolicy": "cpu-required-report-fallback",
    "throughputCycle": "inference-output-copy-bit-validation-result-disposal",
    "copyBoundaries": [
        "model-bytes-to-session",
        "input-fixture-to-dart-float32",
        "dart-float32-to-native-tensor",
        "native-output-to-dart-float32",
    ],
}

_TIMING = {
    "durationUnit": "microseconds",
    "clockScope": "process-local-monotonic-stopwatch",
    "runtimeLoadScope": "ort-runtime-open",
    "sessionCreateScope": "verified-model-bytes-to-session",
    "dataPreparationScope": "dart-float32-to-native-tensor-copy",
    "inferenceScope": "synchronous-session-run-only",
    "outputMaterializationScope": "native-output-to-dart-float32-copy-only",
    "throughputWindowTargetMicroseconds": 1_000_000,
    "throughputWindowStopRule": (
        "complete-runs-until-monotonic-elapsed-gte-target"
    ),
    "assignmentScope": "separate-profiled-session-after-all-timing",
}

_RSS_PHASES = [
    "after-asset-preparation",
    "after-session-create",
    "after-first-run",
    "after-stabilization",
    "after-warm-runs",
    "after-throughput",
    "after-assignment-evidence",
    "after-dispose",
]

_LIFECYCLE = {
    "doubleDispose": "passed",
    "zeroPendingWork": "passed",
    "temporaryAssignmentArtifacts": "deleted",
}

_POOL_CONFIGURATION = {
    "poolSize": 2,
    "concurrency": 2,
    "workerProtocolVersion": 4,
    "maxPendingRunsPerWorker": 1,
    "maxMessageBytes": 33_554_432,
    "maxOutstandingInputBytesPerWorker": 16_777_216,
    "inputReservationBytesPerRun": 8_388_629,
    "graphOptimization": "all",
    "executionMode": "sequential",
    "intraOpThreads": 1,
    "interOpThreads": 1,
    "cpuMemoryArena": True,
    "memoryPattern": True,
    "deterministicCompute": True,
    "timedProviderPolicy": "cpu-required-report-fallback",
    "throughputCycle": (
        "controller-input-copy-worker-decode-native-tensor-inference-worker-"
        "output-copy-transfer-controller-decode-dart-output-copy-bit-validation"
    ),
    "copyBoundaries": [
        "input-fixture-to-immutable-isolate-tensor",
        "isolate-tensor-to-transferable-input",
        "worker-transfer-to-native-tensor",
        "native-output-to-worker-transfer",
        "worker-transfer-to-isolate-output",
        "isolate-output-to-dart-float32",
    ],
}

_POOL_TIMING = {
    "durationUnit": "microseconds",
    "clockScope": "process-local-monotonic-stopwatch",
    "inputPreparationScope": "input-fixture-to-immutable-isolate-tensor-copy",
    "poolStartupScope": "two-worker-runtime-session-ready",
    "firstConcurrentRoundScope": (
        "two-runs-admitted-before-await-through-output-bit-validation"
    ),
    "stabilizationRoundScope": (
        "two-concurrent-runs-through-output-bit-validation"
    ),
    "throughputWindowTargetMicroseconds": 1_000_000,
    "throughputWindowStopRule": (
        "two-lanes-stop-admission-at-target-then-drain"
    ),
    "assignmentScope": "separate-strict-two-worker-pool-after-all-pool-timing",
}

_POOL_RSS_PHASES = [
    "after-input-preparation",
    "after-pool-startup",
    "after-first-concurrent-round",
    "after-stabilization",
    "after-throughput",
    "after-pool-close",
    "after-assignment-evidence",
]

_POOL_LIFECYCLE = {
    "initialConcurrentOccupancy": "passed",
    "zeroOutstandingBeforeClose": "passed",
    "idempotentClose": "passed",
    "zeroOutstandingAfterClose": "passed",
    "strictAssignmentConcurrentOccupancy": "passed",
    "strictAssignmentPoolClosed": "passed",
    "temporaryAssignmentArtifacts": "deleted",
}

_FRAGMENT_CLAIM = (
    "One fresh-process target fragment containing serial and bounded "
    "session-pool measurements only; not a performance baseline, regression "
    "threshold, provider qualification, platform support claim, release "
    "approval, or cross-target evidence."
)
_COLLECTION_CLAIM = (
    "A strict raw-preserving aggregation of fresh-process CPU target fragments "
    "for one challenge/PID-bound target tuple and one canonical application "
    "tree observed unchanged around the launches. The target-reported runtime "
    "basename is bound to one unique packaged member and the shim reports its "
    "embedded build contract; supplied native members remain packaged inputs, "
    "not independent proof that their exact bytes were loaded. The separately "
    "recorded source tree is not a compiled-source provenance proof, and no "
    "distribution archive is claimed. Measurement-only and not a baseline, "
    "threshold, provider qualification, platform support claim, release "
    "approval, or cross-target evidence."
)
_HOST_OBSERVATION_CLAIM = (
    "Raw challenge-bound host observations captured around five fresh target "
    "processes. Collector-observed measurement input only; not a baseline, "
    "threshold, support claim, release approval, or independent hardware "
    "attestation."
)
_RESOLVER_CLAIM = (
    "Successful staging proves exact archive and member bytes only. It does "
    "not prove loading, linking, provider registration, inference, packaging, "
    "signing, or target-device support."
)


class CpuBenchmarkCollectionError(RuntimeError):
    """Captured CPU benchmark evidence violates its closed contract."""


def _json_depth(text: str, *, label: str, maximum_depth: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > maximum_depth:
                raise CpuBenchmarkCollectionError(
                    f"{label} exceeds the JSON nesting bound"
                )
        elif character in "]}":
            depth -= 1
            if depth < 0:
                break


def strict_json_loads(
    data: bytes | str,
    *,
    label: str,
    maximum_bytes: int = MAXIMUM_JSON_BYTES,
    maximum_depth: int = MAXIMUM_JSON_DEPTH,
    maximum_nodes: int = MAXIMUM_JSON_NODES,
) -> Any:
    """Decode bounded strict UTF-8 JSON with duplicate-key/depth rejection."""

    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 1
        or type(maximum_depth) is not int
        or maximum_depth < 1
        or type(maximum_nodes) is not int
        or maximum_nodes < 1
    ):
        raise CpuBenchmarkCollectionError("JSON bounds are invalid")
    try:
        if isinstance(data, bytes):
            encoded = data
            text = data.decode("utf-8", errors="strict")
        elif isinstance(data, str):
            text = data
            encoded = data.encode("utf-8", errors="strict")
        else:
            raise CpuBenchmarkCollectionError(f"{label} must be UTF-8 JSON")
    except (UnicodeDecodeError, UnicodeEncodeError) as error:
        raise CpuBenchmarkCollectionError(
            f"{label} is not strict UTF-8 JSON"
        ) from error
    if not encoded or len(encoded) > maximum_bytes:
        raise CpuBenchmarkCollectionError(f"{label} is empty or oversized")
    _json_depth(text, label=label, maximum_depth=maximum_depth)

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CpuBenchmarkCollectionError(
                    f"{label} contains a duplicate JSON key"
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        raise CpuBenchmarkCollectionError(
            f"{label} contains a non-finite JSON number"
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except CpuBenchmarkCollectionError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError, OverflowError) as error:
        raise CpuBenchmarkCollectionError(f"{label} is not strict JSON") from error

    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > maximum_nodes:
            raise CpuBenchmarkCollectionError(f"{label} exceeds the JSON node bound")
        if depth > maximum_depth:
            raise CpuBenchmarkCollectionError(
                f"{label} exceeds the JSON nesting bound"
            )
        if type(current) is dict:
            stack.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CpuBenchmarkCollectionError(
            "value is not canonical JSON data"
        ) from error


def _plain_json_copy(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("ascii"))


def _object(value: Any, label: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CpuBenchmarkCollectionError(f"{label} field set changed")
    return value


def _array(value: Any, label: str, *, minimum: int, maximum: int) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise CpuBenchmarkCollectionError(f"{label} length is outside its bound")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CpuBenchmarkCollectionError(f"{label} is outside its integer bound")
    return value


def _digest(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CpuBenchmarkCollectionError(f"{label} is not a lowercase SHA-256")
    return value


def _token(value: Any, label: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise CpuBenchmarkCollectionError(f"{label} is not a bounded token")
    return value


def _label(value: Any, label: str) -> str:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise CpuBenchmarkCollectionError(f"{label} is not a path-free label")
    return value


def _exact(value: Any, expected: Any, label: str) -> None:
    if type(value) is not type(expected):
        raise CpuBenchmarkCollectionError(f"{label} changed")
    if type(expected) is dict:
        if set(value) != set(expected):
            raise CpuBenchmarkCollectionError(f"{label} field set changed")
        for key, expected_value in expected.items():
            _exact(value[key], expected_value, f"{label}.{key}")
    elif type(expected) is list:
        if len(value) != len(expected):
            raise CpuBenchmarkCollectionError(f"{label} length changed")
        for index, (item, expected_item) in enumerate(zip(value, expected)):
            _exact(item, expected_item, f"{label}[{index}]")
    elif value != expected:
        raise CpuBenchmarkCollectionError(f"{label} changed")


def canonical_statistics(
    samples: Sequence[int], *, label: str = "samples"
) -> dict[str, int]:
    """Return nearest-rank p50/p95/p99 and max over positive integers."""

    if isinstance(samples, (str, bytes)) or not 1 <= len(samples) <= 1_000_000:
        raise CpuBenchmarkCollectionError(f"{label} length is outside its bound")
    values = [
        _integer(value, f"{label}[{index}]", minimum=1, maximum=MAXIMUM_RSS_BYTES)
        for index, value in enumerate(samples)
    ]
    values.sort()

    def nearest_rank(percentile: int) -> int:
        return values[((percentile * len(values) + 99) // 100) - 1]

    return {
        "count": len(values),
        "p50": nearest_rank(50),
        "p95": nearest_rank(95),
        "p99": nearest_rank(99),
        "max": values[-1],
    }


def _throughput_rate_milli(
    completed_runs: int,
    duration_microseconds: int,
    *,
    maximum_duration: int,
) -> int:
    completed = _integer(
        completed_runs, "completed runs", minimum=1, maximum=1_000_000_000
    )
    duration = _integer(
        duration_microseconds,
        "throughput duration",
        minimum=1,
        maximum=maximum_duration,
    )
    numerator = completed * 1_000_000_000
    return (numerator + duration // 2) // duration


def throughput_rate_milli(completed_runs: int, duration_microseconds: int) -> int:
    """Return one window's integer milli-runs/second, rounded half up."""

    return _throughput_rate_milli(
        completed_runs,
        duration_microseconds,
        maximum_duration=MAXIMUM_DURATION,
    )


def _aggregate_throughput_rate_milli(
    completed_runs: int, duration_microseconds: int
) -> int:
    """Return a bounded cross-launch, three-window aggregate rate."""

    return _throughput_rate_milli(
        completed_runs,
        duration_microseconds,
        maximum_duration=MAXIMUM_DERIVATION_THROUGHPUT_DURATION,
    )


def _require_positive_throughput_rate(
    completed_runs: int, duration_microseconds: int, *, label: str
) -> None:
    if throughput_rate_milli(completed_runs, duration_microseconds) < 1:
        raise CpuBenchmarkCollectionError(
            f"{label} rounds to zero milli-runs per second"
        )


def _duration_array(
    value: Any, label: str, *, count: int
) -> list[int]:
    values = _array(value, label, minimum=count, maximum=count)
    return [
        _integer(item, f"{label}[{index}]", minimum=1, maximum=MAXIMUM_DURATION)
        for index, item in enumerate(values)
    ]


def _validate_runtime(value: Any) -> dict[str, Any]:
    runtime = _object(
        value,
        "fragment.runtime",
        {
            "packageVersion",
            "runtimeVersion",
            "runtimeLibraryIdentity",
            "runtimeSource",
            "runtimeOwner",
            "artifactFlavor",
            "artifactId",
            "artifactSourceSha256",
            "platform",
            "architecture",
            "shimNativeIdentity",
            "shimAbi",
            "shimBuildId",
            "requiredOrtApi",
            "negotiatedOrtApi",
            "compiledProviders",
        },
    )
    package_version = runtime["packageVersion"]
    if type(package_version) is not str or _SEMVER.fullmatch(package_version) is None:
        raise CpuBenchmarkCollectionError("fragment.runtime.packageVersion changed")
    _label(runtime["runtimeVersion"], "fragment.runtime.runtimeVersion")
    _token(
        runtime["runtimeLibraryIdentity"],
        "fragment.runtime.runtimeLibraryIdentity",
    )
    if type(runtime["runtimeSource"]) is not str or runtime[
        "runtimeSource"
    ] not in {"linked", "bundled", "process", "file"}:
        raise CpuBenchmarkCollectionError("fragment.runtime.runtimeSource changed")
    if type(runtime["runtimeOwner"]) is not str or runtime["runtimeOwner"] not in {
        "wrapper",
        "sherpa",
        "application",
        "system",
    }:
        raise CpuBenchmarkCollectionError("fragment.runtime.runtimeOwner changed")
    _token(runtime["artifactFlavor"], "fragment.runtime.artifactFlavor")
    _token(runtime["artifactId"], "fragment.runtime.artifactId")
    _digest(
        runtime["artifactSourceSha256"],
        "fragment.runtime.artifactSourceSha256",
    )
    if type(runtime["platform"]) is not str or runtime["platform"] not in {
        "macos",
        "linux",
    }:
        raise CpuBenchmarkCollectionError("fragment.runtime.platform changed")
    if type(runtime["architecture"]) is not str or runtime[
        "architecture"
    ] not in {"arm64", "x86_64"}:
        raise CpuBenchmarkCollectionError("fragment.runtime.architecture changed")
    _token(
        runtime["shimNativeIdentity"],
        "fragment.runtime.shimNativeIdentity",
    )
    _integer(runtime["shimAbi"], "fragment.runtime.shimAbi", minimum=1, maximum=1000)
    _token(runtime["shimBuildId"], "fragment.runtime.shimBuildId")
    required_api = _integer(
        runtime["requiredOrtApi"],
        "fragment.runtime.requiredOrtApi",
        minimum=1,
        maximum=1000,
    )
    negotiated_api = _integer(
        runtime["negotiatedOrtApi"],
        "fragment.runtime.negotiatedOrtApi",
        minimum=1,
        maximum=1000,
    )
    if negotiated_api < required_api:
        raise CpuBenchmarkCollectionError(
            "fragment.runtime negotiated API is below its required API"
        )
    providers = _array(
        runtime["compiledProviders"],
        "fragment.runtime.compiledProviders",
        minimum=1,
        maximum=64,
    )
    seen: set[str] = set()
    for index, entry_value in enumerate(providers):
        entry = _object(
            entry_value,
            f"fragment.runtime.compiledProviders[{index}]",
            {"wrapperId", "reportedName"},
        )
        wrapper_id = _token(
            entry["wrapperId"],
            f"fragment.runtime.compiledProviders[{index}].wrapperId",
        )
        if wrapper_id in seen:
            raise CpuBenchmarkCollectionError(
                "fragment.runtime.compiledProviders contains a duplicate provider"
            )
        seen.add(wrapper_id)
        reported = entry["reportedName"]
        if reported is not None:
            _token(
                reported,
                f"fragment.runtime.compiledProviders[{index}].reportedName",
            )
    if "cpu" not in seen:
        raise CpuBenchmarkCollectionError(
            "fragment.runtime.compiledProviders omits CPU"
        )
    return runtime


def _validate_stabilization(value: Any) -> dict[str, Any]:
    stabilization = _object(
        value,
        "fragment.stabilization",
        {
            "method",
            "batchSize",
            "thresholdBasisPoints",
            "requiredConsecutiveTransitions",
            "maximumRuns",
            "actualRuns",
            "inferenceMicroseconds",
            "batchMedianMicroseconds",
            "result",
        },
    )
    _exact(
        {key: stabilization[key] for key in (
            "method",
            "batchSize",
            "thresholdBasisPoints",
            "requiredConsecutiveTransitions",
            "maximumRuns",
            "result",
        )},
        {
            "method": "bounded-batch-median-relative-change",
            "batchSize": 5,
            "thresholdBasisPoints": 1000,
            "requiredConsecutiveTransitions": 3,
            "maximumRuns": 100,
            "result": "stabilized",
        },
        "fragment.stabilization policy",
    )
    actual_runs = _integer(
        stabilization["actualRuns"],
        "fragment.stabilization.actualRuns",
        minimum=20,
        maximum=100,
    )
    if actual_runs % 5 != 0:
        raise CpuBenchmarkCollectionError(
            "fragment.stabilization.actualRuns is not a complete batch count"
        )
    samples = _duration_array(
        stabilization["inferenceMicroseconds"],
        "fragment.stabilization.inferenceMicroseconds",
        count=actual_runs,
    )
    declared_medians = _duration_array(
        stabilization["batchMedianMicroseconds"],
        "fragment.stabilization.batchMedianMicroseconds",
        count=actual_runs // 5,
    )
    medians: list[int] = []
    for offset in range(0, actual_runs, 5):
        medians.append(sorted(samples[offset : offset + 5])[2])
    if declared_medians != medians:
        raise CpuBenchmarkCollectionError(
            "fragment.stabilization batch medians do not match raw samples"
        )
    stable_transitions = 0
    first_stable_batch: int | None = None
    for index in range(1, len(medians)):
        if abs(medians[index] - medians[index - 1]) * 10_000 <= (
            medians[index - 1] * 1000
        ):
            stable_transitions += 1
        else:
            stable_transitions = 0
        if stable_transitions >= 3:
            first_stable_batch = index
            break
    if first_stable_batch is None:
        raise CpuBenchmarkCollectionError(
            "fragment.stabilization did not satisfy its declared stop rule"
        )
    if first_stable_batch != len(medians) - 1:
        raise CpuBenchmarkCollectionError(
            "fragment.stabilization contains samples after its first stop point"
        )
    return stabilization


def _validate_measurements(value: Any) -> dict[str, Any]:
    measurements = _object(
        value,
        "fragment.measurements",
        {
            "coldRuntimeLoadMicroseconds",
            "sessionCreateMicroseconds",
            "dataPreparationMicroseconds",
            "firstRunMicroseconds",
            "firstOutputMaterializationMicroseconds",
            "warmRunMicroseconds",
            "warmOutputMaterializationMicroseconds",
            "throughput",
        },
    )
    for name in (
        "coldRuntimeLoadMicroseconds",
        "sessionCreateMicroseconds",
        "dataPreparationMicroseconds",
        "firstRunMicroseconds",
        "firstOutputMaterializationMicroseconds",
    ):
        _duration_array(measurements[name], f"fragment.measurements.{name}", count=1)
    for name in ("warmRunMicroseconds", "warmOutputMaterializationMicroseconds"):
        _duration_array(
            measurements[name], f"fragment.measurements.{name}", count=100
        )
    windows = _array(
        measurements["throughput"],
        "fragment.measurements.throughput",
        minimum=3,
        maximum=3,
    )
    for index, window_value in enumerate(windows):
        window = _object(
            window_value,
            f"fragment.measurements.throughput[{index}]",
            {"completedRuns", "durationMicroseconds"},
        )
        completed_runs = _integer(
            window["completedRuns"],
            f"fragment.measurements.throughput[{index}].completedRuns",
            minimum=1,
            maximum=1_000_000,
        )
        duration_microseconds = _integer(
            window["durationMicroseconds"],
            f"fragment.measurements.throughput[{index}].durationMicroseconds",
            minimum=1_000_000,
            maximum=MAXIMUM_DURATION,
        )
        _require_positive_throughput_rate(
            completed_runs,
            duration_microseconds,
            label=f"fragment.measurements.throughput[{index}]",
        )
    return measurements


def _validate_provider_assignment(
    value: Any, *, label: str = "fragment.providerAssignment"
) -> dict[str, Any]:
    provider = _object(
        value,
        label,
        {
            "providerId",
            "reportedName",
            "discoverable",
            "registered",
            "requirement",
            "fallbackPolicy",
            "nodeExecutionCount",
            "nodeExecutionsByProvider",
            "fallbackObserved",
        },
    )
    _exact(
        {key: provider[key] for key in (
            "providerId",
            "discoverable",
            "registered",
            "requirement",
            "fallbackPolicy",
            "fallbackObserved",
        )},
        {
            "providerId": "cpu",
            "discoverable": True,
            "registered": True,
            "requirement": "full",
            "fallbackPolicy": "reject-any",
            "fallbackObserved": False,
        },
        f"{label} policy",
    )
    _token(provider["reportedName"], f"{label}.reportedName")
    count = _integer(
        provider["nodeExecutionCount"],
        f"{label}.nodeExecutionCount",
        minimum=1,
        maximum=1_000_000,
    )
    by_provider = _object(
        provider["nodeExecutionsByProvider"],
        f"{label}.nodeExecutionsByProvider",
        {"cpu"},
    )
    cpu_count = _integer(
        by_provider["cpu"],
        f"{label}.nodeExecutionsByProvider.cpu",
        minimum=1,
        maximum=1_000_000,
    )
    if cpu_count != count:
        raise CpuBenchmarkCollectionError(
            f"{label} counts do not match"
        )
    return provider


def _validate_pool_stabilization(value: Any) -> dict[str, Any]:
    label = "fragment.poolEvidence.stabilization"
    stabilization = _object(
        value,
        label,
        {
            "method",
            "batchSize",
            "thresholdBasisPoints",
            "requiredConsecutiveTransitions",
            "maximumRounds",
            "actualRounds",
            "roundDurationMicroseconds",
            "batchMedianMicroseconds",
            "result",
        },
    )
    _exact(
        {
            key: stabilization[key]
            for key in (
                "method",
                "batchSize",
                "thresholdBasisPoints",
                "requiredConsecutiveTransitions",
                "maximumRounds",
                "result",
            )
        },
        {
            "method": "bounded-batch-median-relative-change",
            "batchSize": 5,
            "thresholdBasisPoints": 1000,
            "requiredConsecutiveTransitions": 3,
            "maximumRounds": 100,
            "result": "stabilized",
        },
        f"{label} policy",
    )
    actual_rounds = _integer(
        stabilization["actualRounds"],
        f"{label}.actualRounds",
        minimum=20,
        maximum=100,
    )
    if actual_rounds % 5 != 0:
        raise CpuBenchmarkCollectionError(
            f"{label}.actualRounds is not a complete batch count"
        )
    samples = _duration_array(
        stabilization["roundDurationMicroseconds"],
        f"{label}.roundDurationMicroseconds",
        count=actual_rounds,
    )
    declared_medians = _duration_array(
        stabilization["batchMedianMicroseconds"],
        f"{label}.batchMedianMicroseconds",
        count=actual_rounds // 5,
    )
    medians = [
        sorted(samples[offset : offset + 5])[2]
        for offset in range(0, actual_rounds, 5)
    ]
    if declared_medians != medians:
        raise CpuBenchmarkCollectionError(
            f"{label} batch medians do not match raw samples"
        )
    stable_transitions = 0
    first_stable_batch: int | None = None
    for index in range(1, len(medians)):
        if abs(medians[index] - medians[index - 1]) * 10_000 <= (
            medians[index - 1] * 1000
        ):
            stable_transitions += 1
        else:
            stable_transitions = 0
        if stable_transitions >= 3:
            first_stable_batch = index
            break
    if first_stable_batch is None:
        raise CpuBenchmarkCollectionError(
            f"{label} did not satisfy its declared stop rule"
        )
    if first_stable_batch != len(medians) - 1:
        raise CpuBenchmarkCollectionError(
            f"{label} contains samples after its first stop point"
        )
    return stabilization


def _validate_pool_round(
    value: Any,
    *,
    label: str,
    minimum_duration: int,
    exactly_one_per_lane: bool = False,
    require_positive_throughput_rate: bool = False,
) -> dict[str, Any]:
    round_record = _object(
        value,
        label,
        {
            "completedRunsByLane",
            "totalCompletedRuns",
            "durationMicroseconds",
            "maximumObservedInFlightRuns",
        },
    )
    raw_lane_counts = _array(
        round_record["completedRunsByLane"],
        f"{label}.completedRunsByLane",
        minimum=2,
        maximum=2,
    )
    lane_counts = [
        _integer(
            count,
            f"{label}.completedRunsByLane[{index}]",
            minimum=1,
            maximum=1_000_000,
        )
        for index, count in enumerate(raw_lane_counts)
    ]
    if exactly_one_per_lane and lane_counts != [1, 1]:
        raise CpuBenchmarkCollectionError(
            f"{label} must complete exactly one run on each lane"
        )
    total = _integer(
        round_record["totalCompletedRuns"],
        f"{label}.totalCompletedRuns",
        minimum=2,
        maximum=2_000_000,
    )
    if total != sum(lane_counts):
        raise CpuBenchmarkCollectionError(
            f"{label} total does not match its two lane counts"
        )
    duration_microseconds = _integer(
        round_record["durationMicroseconds"],
        f"{label}.durationMicroseconds",
        minimum=minimum_duration,
        maximum=MAXIMUM_DURATION,
    )
    if require_positive_throughput_rate:
        _require_positive_throughput_rate(
            total,
            duration_microseconds,
            label=label,
        )
    _exact(
        round_record["maximumObservedInFlightRuns"],
        2,
        f"{label}.maximumObservedInFlightRuns",
    )
    return round_record


def _validate_pool_resources(
    value: Any, *, serial_final_peak_bytes: int
) -> dict[str, Any]:
    label = "fragment.poolEvidence.resources"
    resources = _object(
        value, label, {"rssScope", "rssSamples", "nativeRss", "cpuUtilization"}
    )
    _exact(resources["rssScope"], "total-process", f"{label}.rssScope")
    samples = _array(
        resources["rssSamples"],
        f"{label}.rssSamples",
        minimum=len(_POOL_RSS_PHASES),
        maximum=len(_POOL_RSS_PHASES),
    )
    previous_peak = serial_final_peak_bytes
    for index, expected_phase in enumerate(_POOL_RSS_PHASES):
        sample = _object(
            samples[index],
            f"{label}.rssSamples[{index}]",
            {"phase", "currentBytes", "peakBytes"},
        )
        _exact(
            sample["phase"], expected_phase, f"{label}.rssSamples[{index}].phase"
        )
        current = _integer(
            sample["currentBytes"],
            f"{label}.rssSamples[{index}].currentBytes",
            minimum=1,
            maximum=MAXIMUM_RSS_BYTES,
        )
        peak = _integer(
            sample["peakBytes"],
            f"{label}.rssSamples[{index}].peakBytes",
            minimum=1,
            maximum=MAXIMUM_RSS_BYTES,
        )
        if peak < current:
            raise CpuBenchmarkCollectionError(
                f"{label} RSS peak is below current RSS"
            )
        if index == 0 and peak < serial_final_peak_bytes:
            raise CpuBenchmarkCollectionError(
                f"{label} initial RSS peak is below the serial after-dispose "
                "lifetime peak"
            )
        if peak < previous_peak:
            raise CpuBenchmarkCollectionError(
                f"{label} RSS peak decreased across phases"
            )
        previous_peak = peak
    _exact(
        resources["nativeRss"],
        {"status": "not-exposed-by-target-api"},
        f"{label}.nativeRss",
    )
    _exact(
        resources["cpuUtilization"],
        {"status": "not-exposed-by-target-api"},
        f"{label}.cpuUtilization",
    )
    return resources


def _validate_pool_evidence(
    value: Any,
    *,
    provider_assignment: dict[str, Any],
    serial_final_peak_bytes: int,
) -> dict[str, Any]:
    label = "fragment.poolEvidence"
    pool = _object(
        value,
        label,
        {
            "executionSurface",
            "configuration",
            "timing",
            "stabilization",
            "measurements",
            "providerAssignments",
            "resources",
            "lifecycle",
        },
    )
    _exact(
        pool["executionSurface"],
        "public-ort-session-pool",
        f"{label}.executionSurface",
    )
    _exact(pool["configuration"], _POOL_CONFIGURATION, f"{label}.configuration")
    _exact(pool["timing"], _POOL_TIMING, f"{label}.timing")
    _validate_pool_stabilization(pool["stabilization"])
    measurements = _object(
        pool["measurements"],
        f"{label}.measurements",
        {
            "inputPreparationMicroseconds",
            "poolStartupMicroseconds",
            "firstConcurrentRound",
            "throughput",
        },
    )
    for name in ("inputPreparationMicroseconds", "poolStartupMicroseconds"):
        _duration_array(
            measurements[name], f"{label}.measurements.{name}", count=1
        )
    _validate_pool_round(
        measurements["firstConcurrentRound"],
        label=f"{label}.measurements.firstConcurrentRound",
        minimum_duration=1,
        exactly_one_per_lane=True,
    )
    windows = _array(
        measurements["throughput"],
        f"{label}.measurements.throughput",
        minimum=3,
        maximum=3,
    )
    for index, window in enumerate(windows):
        _validate_pool_round(
            window,
            label=f"{label}.measurements.throughput[{index}]",
            minimum_duration=1_000_000,
            require_positive_throughput_rate=True,
        )
    assignments = _array(
        pool["providerAssignments"],
        f"{label}.providerAssignments",
        minimum=2,
        maximum=2,
    )
    for index, assignment_value in enumerate(assignments):
        assignment = _validate_provider_assignment(
            assignment_value,
            label=f"{label}.providerAssignments[{index}]",
        )
        _exact(
            assignment,
            provider_assignment,
            f"{label}.providerAssignments[{index}] serial assignment parity",
        )
    _validate_pool_resources(
        pool["resources"], serial_final_peak_bytes=serial_final_peak_bytes
    )
    _exact(pool["lifecycle"], _POOL_LIFECYCLE, f"{label}.lifecycle")
    return pool


def _validate_resources(value: Any) -> dict[str, Any]:
    resources = _object(
        value,
        "fragment.resources",
        {
            "rssScope",
            "rssSamples",
            "nativeRss",
            "cpuUtilization",
            "thermalStart",
            "thermalEnd",
            "powerMode",
        },
    )
    _exact(resources["rssScope"], "total-process", "fragment.resources.rssScope")
    samples = _array(
        resources["rssSamples"],
        "fragment.resources.rssSamples",
        minimum=8,
        maximum=8,
    )
    previous_peak = 0
    for index, expected_phase in enumerate(_RSS_PHASES):
        sample = _object(
            samples[index],
            f"fragment.resources.rssSamples[{index}]",
            {"phase", "currentBytes", "peakBytes"},
        )
        _exact(
            sample["phase"],
            expected_phase,
            f"fragment.resources.rssSamples[{index}].phase",
        )
        current = _integer(
            sample["currentBytes"],
            f"fragment.resources.rssSamples[{index}].currentBytes",
            minimum=1,
            maximum=MAXIMUM_RSS_BYTES,
        )
        peak = _integer(
            sample["peakBytes"],
            f"fragment.resources.rssSamples[{index}].peakBytes",
            minimum=1,
            maximum=MAXIMUM_RSS_BYTES,
        )
        if peak < current:
            raise CpuBenchmarkCollectionError(
                "fragment.resources RSS peak is below current RSS"
            )
        if peak < previous_peak:
            raise CpuBenchmarkCollectionError(
                "fragment.resources RSS peak decreased across phases"
            )
        previous_peak = peak
    _exact(
        resources["nativeRss"],
        {"status": "not-exposed-by-target-api"},
        "fragment.resources.nativeRss",
    )
    _exact(
        resources["cpuUtilization"],
        {"status": "not-exposed-by-target-api"},
        "fragment.resources.cpuUtilization",
    )
    _exact(
        resources["thermalStart"],
        "host-evidence-required",
        "fragment.resources.thermalStart",
    )
    _exact(
        resources["thermalEnd"],
        "host-evidence-required",
        "fragment.resources.thermalEnd",
    )
    _exact(
        resources["powerMode"],
        "host-evidence-required",
        "fragment.resources.powerMode",
    )
    return resources


def validate_fragment(
    value: Any,
    *,
    expected_challenge: str | None = None,
    expected_process_id: int | None = None,
    raw_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize one exact CPU target-fragment v3 object."""

    fragment = _object(value, "fragment", _FRAGMENT_KEYS)
    _exact(fragment["schemaVersion"], 3, "fragment.schemaVersion")
    _exact(fragment["result"], "measured", "fragment.result")
    _exact(
        fragment["purpose"],
        "measurement-only-target-fragment",
        "fragment.purpose",
    )
    _exact(
        fragment["protocol"],
        {
            "id": PROTOCOL_ID,
            "version": PROTOCOL_VERSION,
            "descriptorSha256": PROTOCOL_DESCRIPTOR_SHA256,
            "targetFragmentSchemaSha256": TARGET_FRAGMENT_SCHEMA_SHA256,
        },
        "fragment.protocol",
    )
    challenge = _digest(fragment["launchChallenge"], "fragment.launchChallenge")
    process_id = _integer(
        fragment["processId"], "fragment.processId", minimum=1, maximum=0x7FFF_FFFF
    )
    if expected_challenge is not None:
        _digest(expected_challenge, "expected launch challenge")
        if challenge != expected_challenge:
            raise CpuBenchmarkCollectionError(
                "fragment launch challenge does not match the collector challenge"
            )
    if expected_process_id is not None:
        expected_pid = _integer(
            expected_process_id,
            "expected process id",
            minimum=1,
            maximum=0x7FFF_FFFF,
        )
        if process_id != expected_pid:
            raise CpuBenchmarkCollectionError(
                "fragment process id does not match the launched process"
            )
    if raw_sha256 is not None:
        _digest(raw_sha256, "raw fragment SHA-256")
    _exact(
        fragment["freshProcessRequired"], True, "fragment.freshProcessRequired"
    )
    _exact(
        fragment["executionSurface"],
        "synchronous-and-isolate-pool-public-api",
        "fragment.executionSurface",
    )
    _exact(fragment["model"], _MODEL, "fragment.model")
    runtime = _validate_runtime(fragment["runtime"])
    _exact(fragment["session"], _SESSION, "fragment.session")
    _exact(fragment["timing"], _TIMING, "fragment.timing")
    _validate_stabilization(fragment["stabilization"])
    _validate_measurements(fragment["measurements"])
    provider = _validate_provider_assignment(fragment["providerAssignment"])
    resources = _validate_resources(fragment["resources"])
    _exact(fragment["lifecycle"], _LIFECYCLE, "fragment.lifecycle")
    _validate_pool_evidence(
        fragment["poolEvidence"],
        provider_assignment=provider,
        serial_final_peak_bytes=resources["rssSamples"][-1]["peakBytes"],
    )
    _exact(fragment["claimBoundary"], _FRAGMENT_CLAIM, "fragment.claimBoundary")
    cpu_inventory = [
        entry
        for entry in runtime["compiledProviders"]
        if entry["wrapperId"] == "cpu"
    ]
    if (
        len(cpu_inventory) != 1
        or cpu_inventory[0]["reportedName"] != provider["reportedName"]
    ):
        raise CpuBenchmarkCollectionError(
            "fragment CPU assignment does not match compiled provider identity"
        )
    return _plain_json_copy(fragment)


def _same_file_metadata(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _read_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[bytes, dict[str, Any]]:
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise CpuBenchmarkCollectionError("regular-file size bound is invalid")
    try:
        before = path.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise CpuBenchmarkCollectionError(
            f"{label} must be a regular file, not a link"
        )
    if not 1 <= before.st_size <= maximum_bytes:
        raise CpuBenchmarkCollectionError(f"{label} size is outside its bound")
    if not hasattr(os, "O_NOFOLLOW"):
        raise CpuBenchmarkCollectionError(
            "this host cannot enforce no-follow file identity"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_metadata(
            before, opened
        ):
            raise CpuBenchmarkCollectionError(f"{label} changed before it was read")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        consumed = 0
        while True:
            requested = min(1024 * 1024, maximum_bytes + 1 - consumed)
            chunk = os.read(descriptor, requested)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise CpuBenchmarkCollectionError(f"{label} exceeds its size bound")
            chunks.append(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if consumed != before.st_size or not _same_file_metadata(before, after):
            raise CpuBenchmarkCollectionError(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} disappeared after reading") from error
    if not _same_file_metadata(before, final):
        raise CpuBenchmarkCollectionError(f"{label} changed after it was read")
    return b"".join(chunks), {"sizeBytes": consumed, "sha256": digest.hexdigest()}


def regular_file_identity(
    path: Path,
    *,
    label: str,
    maximum_bytes: int = MAXIMUM_FILE_BYTES,
) -> dict[str, Any]:
    """Hash one stable non-empty regular file without following its leaf."""

    _, identity = _read_regular_file(
        Path(path), label=label, maximum_bytes=maximum_bytes
    )
    return identity


def _safe_tree_path(value: str, *, label: str) -> str:
    if type(value) is not str:
        raise CpuBenchmarkCollectionError(f"{label} is not a safe tree path")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise CpuBenchmarkCollectionError(f"{label} is not UTF-8") from error
    if (
        not value
        or len(encoded) > MAXIMUM_TREE_PATH_BYTES
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise CpuBenchmarkCollectionError(f"{label} is not a safe tree path")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != value
    ):
        raise CpuBenchmarkCollectionError(f"{label} is not a canonical tree path")
    return value


def _safe_link_target(value: str, *, label: str) -> str:
    target = _safe_tree_path(value, label=label)
    if PurePosixPath(target).is_absolute():
        raise CpuBenchmarkCollectionError(f"{label} must be relative")
    return target


def _directory_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise CpuBenchmarkCollectionError(
            "this host cannot enforce no-follow directory traversal"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _hash_file_at(
    directory_descriptor: int,
    name: str,
    before: os.stat_result,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    if not 0 <= before.st_size <= maximum_bytes:
        raise CpuBenchmarkCollectionError(f"{label} size is outside its bound")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_metadata(
            before, opened
        ):
            raise CpuBenchmarkCollectionError(f"{label} changed before hashing")
        digest = hashlib.sha256()
        consumed = 0
        while True:
            requested = min(1024 * 1024, maximum_bytes + 1 - consumed)
            chunk = os.read(descriptor, requested)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise CpuBenchmarkCollectionError(f"{label} exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if consumed != before.st_size or not _same_file_metadata(before, after):
            raise CpuBenchmarkCollectionError(f"{label} changed while hashing")
    finally:
        os.close(descriptor)
    try:
        final = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} disappeared after hashing") from error
    if not _same_file_metadata(before, final):
        raise CpuBenchmarkCollectionError(f"{label} changed after hashing")
    return {"sizeBytes": consumed, "sha256": digest.hexdigest()}


def _read_link_at(
    directory_descriptor: int,
    name: str,
    before: os.stat_result,
    *,
    label: str,
) -> str:
    try:
        target = os.readlink(name, dir_fd=directory_descriptor)
        after = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} cannot be read safely") from error
    if not stat.S_ISLNK(after.st_mode) or not _same_file_metadata(before, after):
        raise CpuBenchmarkCollectionError(f"{label} changed while being read")
    return _safe_link_target(target, label=f"{label} target")


def _resolve_tree_symlinks(entries: Mapping[str, dict[str, Any]]) -> None:
    for link_path, link in entries.items():
        if link["type"] != "symlink":
            continue
        parent = list(PurePosixPath(link_path).parent.parts)
        if parent == ["."]:
            parent = []
        pending = parent + list(PurePosixPath(link["target"]).parts)
        resolved: list[str] = []
        followed: set[str] = set()
        steps = 0
        while pending:
            steps += 1
            if steps > MAXIMUM_TREE_ENTRIES:
                raise CpuBenchmarkCollectionError(
                    "application tree symbolic-link resolution exceeded its bound"
                )
            component = pending.pop(0)
            candidate = "/".join((*resolved, component))
            entry = entries.get(candidate)
            if entry is None:
                raise CpuBenchmarkCollectionError(
                    "application tree contains a dangling symbolic link"
                )
            if entry["type"] == "symlink":
                if candidate in followed:
                    raise CpuBenchmarkCollectionError(
                        "application tree contains a symbolic-link cycle"
                    )
                followed.add(candidate)
                pending = list(PurePosixPath(entry["target"]).parts) + pending
                continue
            if pending and entry["type"] != "directory":
                raise CpuBenchmarkCollectionError(
                    "application tree symbolic link crosses a non-directory"
                )
            resolved.append(component)


def canonical_application_tree_identity(
    root: Path,
    *,
    label: str = "application",
    maximum_entries: int = MAXIMUM_TREE_ENTRIES,
    maximum_bytes: int = MAXIMUM_TREE_BYTES,
    include_entries: bool = False,
) -> dict[str, Any]:
    """Hash a stable app tree without following links or exposing its paths.

    Relative non-escaping links are recorded by path, type, mode and exact
    target.  Their targets must resolve within the inventoried tree without a
    cycle.  Regular files are opened relative to stable directory descriptors.
    """

    if (
        type(maximum_entries) is not int
        or not 1 <= maximum_entries <= 1_000_000
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
        or type(include_entries) is not bool
    ):
        raise CpuBenchmarkCollectionError("application-tree bounds are invalid")
    root = Path(root)
    try:
        root_before = root.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} root cannot be inspected") from error
    if stat.S_ISLNK(root_before.st_mode) or not stat.S_ISDIR(root_before.st_mode):
        raise CpuBenchmarkCollectionError(
            f"{label} root must be a directory, not a link"
        )
    try:
        root_descriptor = os.open(os.fspath(root), _directory_flags())
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} root cannot be opened safely") from error
    entries: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = [
        {
            "path": ".",
            "type": "directory",
            "mode": stat.S_IMODE(root_before.st_mode),
        }
    ]
    file_count = 0
    directory_count = 1
    symbolic_link_count = 0
    byte_count = 0
    entry_count = 1

    def visit(
        directory_descriptor: int,
        relative_parts: tuple[str, ...],
        directory_before: os.stat_result,
        depth: int,
    ) -> None:
        nonlocal file_count
        nonlocal directory_count
        nonlocal symbolic_link_count
        nonlocal byte_count
        nonlocal entry_count
        if depth > MAXIMUM_TREE_DEPTH:
            raise CpuBenchmarkCollectionError(
                f"{label} tree exceeds its directory-depth bound"
            )
        opened = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_file_metadata(
            directory_before, opened
        ):
            raise CpuBenchmarkCollectionError(f"{label} directory changed before scan")
        try:
            with os.scandir(directory_descriptor) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError as error:
            raise CpuBenchmarkCollectionError(f"{label} directory cannot be scanned") from error
        if len(names) != len(set(names)):
            raise CpuBenchmarkCollectionError(f"{label} directory has duplicate names")
        for name in names:
            relative = "/".join((*relative_parts, name))
            _safe_tree_path(relative, label=f"{label} entry")
            entry_count += 1
            if entry_count > maximum_entries:
                raise CpuBenchmarkCollectionError(
                    f"{label} tree exceeds its entry bound"
                )
            try:
                before = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise CpuBenchmarkCollectionError(
                    f"{label} entry cannot be inspected"
                ) from error
            mode = stat.S_IMODE(before.st_mode)
            if stat.S_ISREG(before.st_mode):
                remaining = maximum_bytes - byte_count
                identity = _hash_file_at(
                    directory_descriptor,
                    name,
                    before,
                    label=f"{label} regular file",
                    maximum_bytes=max(0, remaining),
                )
                byte_count += identity["sizeBytes"]
                if byte_count > maximum_bytes:
                    raise CpuBenchmarkCollectionError(
                        f"{label} tree exceeds its byte bound"
                    )
                record = {
                    "path": relative,
                    "type": "file",
                    "mode": mode,
                    **identity,
                }
                entries[relative] = record
                records.append(record)
                file_count += 1
            elif stat.S_ISDIR(before.st_mode):
                try:
                    child_descriptor = os.open(
                        name, _directory_flags(), dir_fd=directory_descriptor
                    )
                except OSError as error:
                    raise CpuBenchmarkCollectionError(
                        f"{label} directory cannot be opened safely"
                    ) from error
                try:
                    child_opened = os.fstat(child_descriptor)
                    if not _same_file_metadata(before, child_opened):
                        raise CpuBenchmarkCollectionError(
                            f"{label} directory changed before traversal"
                        )
                    record = {
                        "path": relative,
                        "type": "directory",
                        "mode": mode,
                    }
                    entries[relative] = record
                    records.append(record)
                    directory_count += 1
                    visit(
                        child_descriptor,
                        (*relative_parts, name),
                        before,
                        depth + 1,
                    )
                    child_after = os.fstat(child_descriptor)
                    if not _same_file_metadata(before, child_after):
                        raise CpuBenchmarkCollectionError(
                            f"{label} directory changed during traversal"
                        )
                finally:
                    os.close(child_descriptor)
                try:
                    final = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise CpuBenchmarkCollectionError(
                        f"{label} directory disappeared after traversal"
                    ) from error
                if not _same_file_metadata(before, final):
                    raise CpuBenchmarkCollectionError(
                        f"{label} directory changed after traversal"
                    )
            elif stat.S_ISLNK(before.st_mode):
                target = _read_link_at(
                    directory_descriptor,
                    name,
                    before,
                    label=f"{label} symbolic link",
                )
                record = {
                    "path": relative,
                    "type": "symlink",
                    "mode": mode,
                    "target": target,
                }
                entries[relative] = record
                records.append(record)
                symbolic_link_count += 1
            else:
                raise CpuBenchmarkCollectionError(
                    f"{label} tree contains a special filesystem entry"
                )
        directory_after = os.fstat(directory_descriptor)
        if not _same_file_metadata(directory_before, directory_after):
            raise CpuBenchmarkCollectionError(
                f"{label} directory changed while being scanned"
            )

    try:
        root_opened = os.fstat(root_descriptor)
        if not _same_file_metadata(root_before, root_opened):
            raise CpuBenchmarkCollectionError(f"{label} root changed before traversal")
        visit(root_descriptor, (), root_before, 1)
        root_after = os.fstat(root_descriptor)
        if not _same_file_metadata(root_before, root_after):
            raise CpuBenchmarkCollectionError(f"{label} root changed during traversal")
    finally:
        os.close(root_descriptor)
    try:
        root_final = root.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectionError(f"{label} root disappeared") from error
    if not _same_file_metadata(root_before, root_final):
        raise CpuBenchmarkCollectionError(f"{label} root changed after traversal")
    _resolve_tree_symlinks(entries)
    if file_count < 1 or byte_count < 1:
        raise CpuBenchmarkCollectionError(
            f"{label} tree must contain application bytes"
        )
    records.sort(key=lambda entry: entry["path"])
    result: dict[str, Any] = {
        "format": "canonical-application-tree-v1",
        "fileCount": file_count,
        "directoryCount": directory_count,
        "symbolicLinkCount": symbolic_link_count,
        "byteCount": byte_count,
        "sha256": hashlib.sha256(_canonical_bytes(records)).hexdigest(),
    }
    if include_entries:
        # Private collector/validator state used to prove that every separately
        # supplied executable/native input is one of the exact entries covered
        # by the public path-free tree digest.  Callers must not serialize it.
        result["_entries"] = _plain_json_copy(records)
    return result


_REPOSITORY_EVIDENCE_FILES = {
    "sourceManifestValidator": (
        "tool/ci/source_checksum_manifest.py",
        SOURCE_MANIFEST_VALIDATOR_SIZE_BYTES,
        SOURCE_MANIFEST_VALIDATOR_SHA256,
    ),
    "protocolDescriptor": (
        "templates/ci/cpu_benchmark_protocol_v3.json",
        8_817,
        PROTOCOL_DESCRIPTOR_SHA256,
    ),
    "protocolDescriptorV2": (
        "templates/ci/cpu_benchmark_protocol_v2.json",
        4_304,
        PROTOCOL_DESCRIPTOR_V2_SHA256,
    ),
    "targetFragmentSchema": (
        "templates/ci/cpu_benchmark_target_fragment_v3.schema.json",
        12_447,
        TARGET_FRAGMENT_SCHEMA_SHA256,
    ),
    "targetFragmentSchemaV2": (
        "templates/ci/cpu_benchmark_target_fragment_v2.schema.json",
        16_843,
        TARGET_FRAGMENT_SCHEMA_V2_SHA256,
    ),
    "model": (
        "example/assets/models/cpu_benchmark_matmul.onnx",
        _MODEL["onnxSizeBytes"],
        _MODEL["onnxSha256"],
    ),
    "inputFixture": (
        "example/assets/models/cpu_benchmark_matmul.input.f32le",
        _MODEL["inputFixtureSizeBytes"],
        _MODEL["inputFixtureSha256"],
    ),
    "referenceOutput": (
        "example/assets/models/cpu_benchmark_matmul.output.f32le",
        _MODEL["referenceOutputSizeBytes"],
        _MODEL["referenceOutputSha256"],
    ),
    "metadata": (
        "example/assets/models/cpu_benchmark_matmul.json",
        _MODEL["metadataSizeBytes"],
        _MODEL["metadataSha256"],
    ),
    "generator": (
        "example/assets/models/generate_cpu_benchmark_matmul.py",
        _MODEL["generatorSizeBytes"],
        _MODEL["generatorSha256"],
    ),
}


def _load_trusted_source_manifest_helper() -> Any:
    """Load the hash-pinned helper adjacent to this trusted collector core.

    The repository supplied for measurement is data, never executable code.
    A validator from that candidate tree therefore cannot authorize its own
    source manifest.
    """

    helper_path = Path(__file__).resolve(strict=True).parent / (
        "source_checksum_manifest.py"
    )
    identity = regular_file_identity(
        helper_path,
        label="trusted source-checksum helper",
        maximum_bytes=4 * 1024 * 1024,
    )
    _exact(
        identity,
        {
            "sizeBytes": SOURCE_MANIFEST_VALIDATOR_SIZE_BYTES,
            "sha256": SOURCE_MANIFEST_VALIDATOR_SHA256,
        },
        "trusted source-checksum helper identity",
    )
    spec = importlib.util.spec_from_file_location(
        "_fonix_cpu_collection_source_manifest", helper_path
    )
    if spec is None or spec.loader is None:
        raise CpuBenchmarkCollectionError(
            "source-checksum helper cannot be loaded"
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise CpuBenchmarkCollectionError(
            "source-checksum helper could not initialize"
        ) from error
    if not callable(getattr(module, "check_manifest", None)):
        raise CpuBenchmarkCollectionError(
            "source-checksum helper contract changed"
        )
    return module


def repository_evidence_identity(repository: Path) -> dict[str, Any]:
    """Verify the closed source manifest and benchmark protocol/model bytes."""

    repository = Path(repository)
    try:
        root_status = repository.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectionError("repository root cannot be inspected") from error
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise CpuBenchmarkCollectionError(
            "repository root must be a directory, not a link"
        )
    manifest_path = repository / "MANIFEST.sha256"
    manifest_identity = regular_file_identity(
        manifest_path,
        label="source checksum manifest",
        maximum_bytes=4 * 1024 * 1024,
    )
    helper = _load_trusted_source_manifest_helper()
    try:
        checked_digest = helper.check_manifest(repository, manifest_path)
    except Exception as error:
        raise CpuBenchmarkCollectionError(
            "source checksum manifest does not match the closed repository"
        ) from error
    if checked_digest != manifest_identity["sha256"]:
        raise CpuBenchmarkCollectionError(
            "source checksum manifest identity changed during validation"
        )
    result: dict[str, Any] = {"sourceManifest": manifest_identity}
    for name, (relative, expected_size, expected_sha256) in (
        _REPOSITORY_EVIDENCE_FILES.items()
    ):
        identity = regular_file_identity(
            repository.joinpath(*relative.split("/")),
            label=f"repository benchmark {name}",
            maximum_bytes=16 * 1024 * 1024,
        )
        expected = {"sizeBytes": expected_size, "sha256": expected_sha256}
        _exact(identity, expected, f"repository benchmark {name} identity")
        result[name] = identity
    return result


def _lock_artifact(repository_lock: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    _object(
        repository_lock,
        "native lock",
        {
            "schema",
            "snapshot_date",
            "release_state",
            "shim",
            "onnxruntime",
            "release_targets",
            "artifacts",
        },
    )
    _exact(repository_lock["schema"], 2, "native lock schema")
    _label(repository_lock["snapshot_date"], "native lock snapshot date")
    _token(repository_lock["release_state"], "native lock release state")
    shim = _object(
        repository_lock["shim"],
        "native lock shim",
        {"abi", "required_ort_api", "source_revision"},
    )
    _integer(shim["abi"], "native lock shim ABI", minimum=1, maximum=1000)
    _integer(
        shim["required_ort_api"],
        "native lock required ORT API",
        minimum=1,
        maximum=1000,
    )
    if shim["source_revision"] is not None:
        _label(shim["source_revision"], "native lock shim source revision")
    artifacts = _array(
        repository_lock["artifacts"],
        "native lock artifacts",
        minimum=1,
        maximum=256,
    )
    selected = [
        entry
        for entry in artifacts
        if type(entry) is dict and entry.get("id") == artifact_id
    ]
    if len(selected) != 1:
        raise CpuBenchmarkCollectionError(
            "resolver artifact is not unique in the current native lock"
        )
    artifact = _object(
        selected[0],
        "selected native-lock artifact",
        {
            "id",
            "target",
            "flavor",
            "runtime_mode",
            "source",
            "containers",
            "expected_files",
            "expected_symlinks",
            "notices",
            "providers",
            "build",
            "licenses",
        },
    )
    _token(artifact["id"], "selected native-lock artifact id")
    return artifact


def _locked_runtime_version(lock: dict[str, Any]) -> str:
    onnxruntime = _object(
        lock["onnxruntime"],
        "native lock onnxruntime",
        {"compatibility_floor"},
    )
    floor = _object(
        onnxruntime["compatibility_floor"],
        "native lock ONNX Runtime compatibility floor",
        {"c_api", "header", "ep_header", "license"},
    )
    required_api = _integer(
        lock["shim"]["required_ort_api"],
        "native lock shim required ORT API",
        minimum=1,
        maximum=1000,
    )
    _exact(
        floor["c_api"],
        required_api,
        "native lock compatibility-floor C API",
    )
    versions: list[str] = []
    for name in ("header", "ep_header", "license"):
        source = _object(
            floor[name],
            f"native lock compatibility-floor {name}",
            {
                "version",
                "source_repository",
                "source_ref",
                "path",
                "sha256",
                "size_bytes",
            },
        )
        version = source["version"]
        if type(version) is not str or _SEMVER.fullmatch(version) is None:
            raise CpuBenchmarkCollectionError(
                f"native lock compatibility-floor {name} version changed"
            )
        if source["source_repository"] != "https://github.com/microsoft/onnxruntime":
            raise CpuBenchmarkCollectionError(
                f"native lock compatibility-floor {name} repository changed"
            )
        _exact(
            source["source_ref"],
            f"v{version}",
            f"native lock compatibility-floor {name} source ref",
        )
        _safe_tree_path(
            source["path"],
            label=f"native lock compatibility-floor {name} path",
        )
        _digest(
            source["sha256"],
            f"native lock compatibility-floor {name} SHA-256",
        )
        _integer(
            source["size_bytes"],
            f"native lock compatibility-floor {name} size",
            minimum=1,
            maximum=MAXIMUM_FILE_BYTES,
        )
        versions.append(version)
    if len(set(versions)) != 1:
        raise CpuBenchmarkCollectionError(
            "native lock compatibility-floor component versions disagree"
        )
    return versions[0]


def _repository_package_version(repository: Path) -> str:
    raw, _identity = _read_regular_file(
        repository / "pubspec.yaml",
        label="current package manifest",
        maximum_bytes=1024 * 1024,
    )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CpuBenchmarkCollectionError(
            "current package manifest is not strict UTF-8"
        ) from error
    matches = re.findall(r"^version: ([^\s#]+)\s*$", text, flags=re.MULTILINE)
    if len(matches) != 1 or _SEMVER.fullmatch(matches[0]) is None:
        raise CpuBenchmarkCollectionError(
            "current package manifest must declare one strict semantic version"
        )
    return matches[0]


def _selected_artifact_resolver_identity(
    lock: dict[str, Any], artifact: dict[str, Any], lock_sha256: str
) -> dict[str, Any]:
    target = _object(
        artifact["target"],
        "selected native-lock target",
        {"os", "architecture", "variant", "min_os"},
    )
    for key in ("os", "architecture", "variant"):
        _token(target[key], f"selected native-lock target.{key}")
    _label(target["min_os"], "selected native-lock target.min_os")
    _token(artifact["flavor"], "selected native-lock flavor")
    if type(artifact["runtime_mode"]) is not str or artifact[
        "runtime_mode"
    ] not in {"linked", "bundled", "external"}:
        raise CpuBenchmarkCollectionError("selected native-lock runtime mode changed")
    source = _object(
        artifact["source"],
        "selected native-lock source",
        {"url", "source_revision", "sha256", "size_bytes", "archive"},
    )
    if type(source["url"]) is not str or not source["url"].startswith("https://"):
        raise CpuBenchmarkCollectionError("selected native-lock source URL changed")
    _label(source["source_revision"], "selected native-lock source revision")
    _digest(source["sha256"], "selected native-lock source digest")
    _integer(
        source["size_bytes"],
        "selected native-lock source size",
        minimum=1,
        maximum=MAXIMUM_FILE_BYTES,
    )
    _token(source["archive"], "selected native-lock source archive")

    containers = _array(
        artifact["containers"],
        "selected native-lock containers",
        minimum=0,
        maximum=8,
    )
    resolver_containers: list[dict[str, Any]] = []
    for index, container_value in enumerate(containers):
        container = _object(
            container_value,
            f"selected native-lock containers[{index}]",
            {"path", "sha256", "size_bytes", "archive"},
        )
        _safe_tree_path(
            container["path"], label=f"selected native-lock containers[{index}].path"
        )
        _digest(
            container["sha256"],
            f"selected native-lock containers[{index}].sha256",
        )
        _integer(
            container["size_bytes"],
            f"selected native-lock containers[{index}].size_bytes",
            minimum=1,
            maximum=MAXIMUM_FILE_BYTES,
        )
        _token(
            container["archive"],
            f"selected native-lock containers[{index}].archive",
        )
        resolver_containers.append(
            {
                "depth": index + 1,
                "path": container["path"],
                "archive": container["archive"],
                "sha256": container["sha256"],
                "sizeBytes": container["size_bytes"],
            }
        )

    expected_files = _array(
        artifact["expected_files"],
        "selected native-lock files",
        minimum=1,
        maximum=256,
    )
    payloads: list[dict[str, Any]] = []
    for index, file_value in enumerate(expected_files):
        file_entry = _object(
            file_value,
            f"selected native-lock files[{index}]",
            {"path", "staged_path", "sha256", "size_bytes"},
        )
        _safe_tree_path(
            file_entry["path"], label=f"selected native-lock files[{index}].path"
        )
        _safe_tree_path(
            file_entry["staged_path"],
            label=f"selected native-lock files[{index}].staged_path",
        )
        _digest(
            file_entry["sha256"],
            f"selected native-lock files[{index}].sha256",
        )
        _integer(
            file_entry["size_bytes"],
            f"selected native-lock files[{index}].size_bytes",
            minimum=1,
            maximum=MAXIMUM_FILE_BYTES,
        )
        payloads.append(
            {
                "archivePath": file_entry["path"],
                "stagedPath": file_entry["staged_path"],
                "sha256": file_entry["sha256"],
                "sizeBytes": file_entry["size_bytes"],
            }
        )
    payloads.sort(key=lambda entry: entry["stagedPath"])

    expected_symlinks = _array(
        artifact["expected_symlinks"],
        "selected native-lock symlinks",
        minimum=0,
        maximum=256,
    )
    symlinks: list[dict[str, Any]] = []
    for index, link_value in enumerate(expected_symlinks):
        link = _object(
            link_value,
            f"selected native-lock symlinks[{index}]",
            {"path", "target"},
        )
        _safe_tree_path(
            link["path"], label=f"selected native-lock symlinks[{index}].path"
        )
        _safe_link_target(
            link["target"], label=f"selected native-lock symlinks[{index}].target"
        )
        symlinks.append({"path": link["path"], "target": link["target"]})
    symlinks.sort(key=lambda entry: entry["path"])

    lock_notices = _array(
        artifact["notices"],
        "selected native-lock notices",
        minimum=1,
        maximum=64,
    )
    notices: list[dict[str, Any]] = []
    for index, notice_value in enumerate(lock_notices):
        notice = _object(
            notice_value,
            f"selected native-lock notices[{index}]",
            {
                "id",
                "container_depth",
                "path",
                "staged_path",
                "sha256",
                "size_bytes",
            },
        )
        _token(notice["id"], f"selected native-lock notices[{index}].id")
        _integer(
            notice["container_depth"],
            f"selected native-lock notices[{index}].container_depth",
            minimum=0,
            maximum=len(containers),
        )
        _safe_tree_path(
            notice["path"], label=f"selected native-lock notices[{index}].path"
        )
        _safe_tree_path(
            notice["staged_path"],
            label=f"selected native-lock notices[{index}].staged_path",
        )
        _digest(
            notice["sha256"],
            f"selected native-lock notices[{index}].sha256",
        )
        _integer(
            notice["size_bytes"],
            f"selected native-lock notices[{index}].size_bytes",
            minimum=1,
            maximum=MAXIMUM_FILE_BYTES,
        )
        notices.append(
            {
                "id": notice["id"],
                "containerDepth": notice["container_depth"],
                "archivePath": notice["path"],
                "stagedPath": notice["staged_path"],
                "sha256": notice["sha256"],
                "sizeBytes": notice["size_bytes"],
            }
        )
    notices.sort(key=lambda entry: entry["stagedPath"])

    return {
        "schema": 2,
        "artifactId": artifact["id"],
        "lock": {
            "path": "native/versions.lock.yaml",
            "sha256": lock_sha256,
            "snapshotDate": lock["snapshot_date"],
            "releaseState": lock["release_state"],
        },
        "target": {
            "os": target["os"],
            "architecture": target["architecture"],
            "variant": target["variant"],
            "minimumOs": target["min_os"],
            "flavor": artifact["flavor"],
            "runtimeMode": artifact["runtime_mode"],
        },
        "source": {
            "url": source["url"],
            "sourceRevision": source["source_revision"],
            "archive": source["archive"],
            "sha256": source["sha256"],
            "sizeBytes": source["size_bytes"],
        },
        "containers": resolver_containers,
        "payloadFiles": payloads,
        "verifiedSymlinks": symlinks,
        "notices": notices,
        "claimBoundary": _RESOLVER_CLAIM,
    }


def _validate_archive_inspections(
    value: Any, expected_formats: Sequence[str]
) -> None:
    inspections = _array(
        value,
        "resolver archive inspections",
        minimum=len(expected_formats),
        maximum=len(expected_formats),
    )
    for index, inspection_value in enumerate(inspections):
        inspection = _object(
            inspection_value,
            f"resolver archiveInspections[{index}]",
            {
                "depth",
                "format",
                "memberCount",
                "regularFileCount",
                "directoryCount",
                "symbolicLinkCount",
                "compressedBytes",
                "uncompressedBytes",
            },
        )
        _exact(
            inspection["depth"], index, f"resolver archiveInspections[{index}].depth"
        )
        _exact(
            inspection["format"],
            expected_formats[index],
            f"resolver archiveInspections[{index}].format",
        )
        member_count = _integer(
            inspection["memberCount"],
            f"resolver archiveInspections[{index}].memberCount",
            minimum=1,
            maximum=1_000_000,
        )
        counted = 0
        for key in ("regularFileCount", "directoryCount", "symbolicLinkCount"):
            counted += _integer(
                inspection[key],
                f"resolver archiveInspections[{index}].{key}",
                minimum=0,
                maximum=member_count,
            )
        if counted != member_count:
            raise CpuBenchmarkCollectionError(
                "resolver archive inspection member counts do not add up"
            )
        for key in ("compressedBytes", "uncompressedBytes"):
            _integer(
                inspection[key],
                f"resolver archiveInspections[{index}].{key}",
                minimum=1,
                maximum=MAXIMUM_FILE_BYTES,
            )


def _embedded_manifest_expected(
    lock: dict[str, Any], artifact: dict[str, Any], lock_sha256: str
) -> dict[str, Any]:
    target = artifact["target"]
    providers_value = _array(
        artifact["providers"],
        "selected native-lock providers",
        minimum=1,
        maximum=64,
    )
    providers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, provider_value in enumerate(providers_value):
        provider = _object(
            provider_value,
            f"selected native-lock providers[{index}]",
            {"wrapper_id", "reported_name"},
        )
        wrapper_id = _token(
            provider["wrapper_id"],
            f"selected native-lock providers[{index}].wrapper_id",
        )
        if wrapper_id in seen:
            raise CpuBenchmarkCollectionError(
                "selected native-lock providers contain a duplicate"
            )
        seen.add(wrapper_id)
        reported = provider["reported_name"]
        if reported is not None:
            _token(
                reported,
                f"selected native-lock providers[{index}].reported_name",
            )
        providers.append({"wrapperId": wrapper_id, "reportedName": reported})
    notices = [
        notice
        for notice in artifact["notices"]
        if type(notice) is dict and notice.get("id") == "ThirdPartyNotices"
    ]
    if len(notices) != 1:
        raise CpuBenchmarkCollectionError(
            "selected native-lock artifact must have one ThirdPartyNotices entry"
        )
    notice_sha256 = _digest(
        notices[0].get("sha256"), "selected native-lock ThirdPartyNotices digest"
    )
    if type(target["os"]) is not str or target["os"] not in {"macos", "linux"}:
        raise CpuBenchmarkCollectionError(
            "CPU benchmark native binding is limited to macOS and Linux"
        )
    if artifact["runtime_mode"] != "bundled":
        raise CpuBenchmarkCollectionError(
            "desktop CPU benchmark requires a bundled native artifact"
        )
    return {
        "schemaVersion": 3,
        "nativeIdentity": "fonix_shim",
        "shimAbiVersion": lock["shim"]["abi"],
        "requiredOrtApiVersion": lock["shim"]["required_ort_api"],
        "runtimeProfile": "bundled",
        "androidRuntimeOwner": None,
        "allowedRuntimeSources": ["bundled"],
        "buildId": artifact["id"],
        "artifact": {
            "id": artifact["id"],
            "lockSha256": lock_sha256,
            "sourceSha256": artifact["source"]["sha256"],
            "targetOs": target["os"],
            "targetArchitecture": target["architecture"],
            "targetVariant": target["variant"],
            "minimumOs": target["min_os"],
            "flavor": artifact["flavor"],
            "runtimeMode": artifact["runtime_mode"],
            "thirdPartyNoticesSha256": notice_sha256,
            "providers": providers,
        },
    }


def _extract_embedded_build_manifest(shim_bytes: bytes) -> tuple[dict[str, Any], str]:
    prefix = b'{"schemaVersion":3,"nativeIdentity":"fonix_shim"'
    offsets: list[int] = []
    offset = 0
    while True:
        found = shim_bytes.find(prefix, offset)
        if found < 0:
            break
        offsets.append(found)
        offset = found + 1
    if len(offsets) != 1:
        raise CpuBenchmarkCollectionError(
            "shim must embed exactly one schema-3 build manifest"
        )
    start = offsets[0]
    end = shim_bytes.find(b"\x00", start, min(len(shim_bytes), start + 64 * 1024 + 1))
    if end < 0:
        raise CpuBenchmarkCollectionError("embedded build manifest is not bounded")
    raw = shim_bytes[start:end]
    value = strict_json_loads(
        raw,
        label="embedded build manifest",
        maximum_bytes=64 * 1024,
        maximum_depth=16,
    )
    if type(value) is not dict:
        raise CpuBenchmarkCollectionError("embedded build manifest is not an object")
    return value, hashlib.sha256(raw).hexdigest()


def validate_native_build_binding(
    *,
    repository: Path,
    shim_binary: Path,
    resolver_manifest: Path,
) -> dict[str, Any]:
    """Bind an embedded schema-3 shim identity to current lock/resolver bytes."""

    repository = Path(repository)
    try:
        repository_status = repository.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectionError("repository root cannot be inspected") from error
    if stat.S_ISLNK(repository_status.st_mode) or not stat.S_ISDIR(
        repository_status.st_mode
    ):
        raise CpuBenchmarkCollectionError(
            "repository root must be a directory, not a link"
        )
    lock_bytes, lock_identity = _read_regular_file(
        repository / "native/versions.lock.yaml",
        label="current native lock",
        maximum_bytes=4 * 1024 * 1024,
    )
    resolver_bytes, resolver_identity = _read_regular_file(
        Path(resolver_manifest),
        label="resolver manifest",
        maximum_bytes=4 * 1024 * 1024,
    )
    shim_bytes, shim_identity = _read_regular_file(
        Path(shim_binary),
        label="shim library",
        maximum_bytes=MAXIMUM_SHIM_BYTES,
    )
    lock = strict_json_loads(
        lock_bytes, label="current native lock", maximum_bytes=4 * 1024 * 1024
    )
    resolver = strict_json_loads(
        resolver_bytes, label="resolver manifest", maximum_bytes=4 * 1024 * 1024
    )
    if type(lock) is not dict or type(resolver) is not dict:
        raise CpuBenchmarkCollectionError("native lock and resolver must be objects")
    resolver_object = _object(
        resolver,
        "resolver manifest",
        {
            "schema",
            "artifactId",
            "lock",
            "target",
            "source",
            "containers",
            "payloadFiles",
            "verifiedSymlinks",
            "notices",
            "archiveInspections",
            "claimBoundary",
        },
    )
    artifact_id = _token(resolver_object["artifactId"], "resolver artifact id")
    artifact = _lock_artifact(lock, artifact_id)
    runtime_version = _locked_runtime_version(lock)
    package_version = _repository_package_version(repository)
    if not artifact_id.startswith(f"onnxruntime-{runtime_version}-"):
        raise CpuBenchmarkCollectionError(
            "selected native artifact ID contradicts the locked runtime version"
        )
    expected_resolver = _selected_artifact_resolver_identity(
        lock, artifact, lock_identity["sha256"]
    )
    for key, expected_value in expected_resolver.items():
        _exact(resolver_object[key], expected_value, f"resolver manifest.{key}")
    formats = [artifact["source"]["archive"]] + [
        entry["archive"] for entry in artifact["containers"]
    ]
    formats = ["tar" if value in {"tgz", "tar.gz"} else value for value in formats]
    _validate_archive_inspections(resolver_object["archiveInspections"], formats)
    embedded, embedded_sha256 = _extract_embedded_build_manifest(shim_bytes)
    expected_embedded = _embedded_manifest_expected(
        lock, artifact, lock_identity["sha256"]
    )
    _exact(embedded, expected_embedded, "embedded build manifest")
    payload_files: list[dict[str, Any]] = []
    seen_payload_ids: set[str] = set()
    expected_resolver_payloads = expected_resolver["payloadFiles"]
    for index, payload in enumerate(expected_resolver_payloads):
        payload_id = PurePosixPath(payload["stagedPath"]).name
        _label(payload_id, f"native payload {index} id")
        if payload_id in seen_payload_ids:
            raise CpuBenchmarkCollectionError(
                "native payload basenames are not unique"
            )
        seen_payload_ids.add(payload_id)
        payload_files.append(
            {
                "id": payload_id,
                "sizeBytes": payload["sizeBytes"],
                "sha256": payload["sha256"],
            }
        )
    payload_files.sort(key=lambda entry: entry["id"])
    return {
        "nativeLock": lock_identity,
        "packageVersion": package_version,
        "runtimeVersion": runtime_version,
        "resolverManifest": resolver_identity,
        "shimLibrary": shim_identity,
        "embeddedBuildManifestSha256": embedded_sha256,
        "embeddedBuildManifestCanonicalSha256": hashlib.sha256(
            _canonical_bytes(embedded)
        ).hexdigest(),
        "buildManifest": _plain_json_copy(embedded),
        "nativePayloadFiles": payload_files,
    }


def _file_evidence(value: Any, label: str, *, with_id: bool) -> dict[str, Any]:
    keys = {"sizeBytes", "sha256"} | ({"id"} if with_id else set())
    evidence = _object(value, label, keys)
    if with_id:
        _label(evidence["id"], f"{label}.id")
    _integer(
        evidence["sizeBytes"],
        f"{label}.sizeBytes",
        minimum=1,
        maximum=MAXIMUM_FILE_BYTES,
    )
    _digest(evidence["sha256"], f"{label}.sha256")
    return evidence


def _validate_artifacts(value: Any) -> dict[str, Any]:
    artifacts = _object(
        value,
        "collection artifacts",
        {
            "applicationTree",
            "executable",
            "shimLibrary",
            "runtimeLibrary",
            "providerDependencies",
            "nativeLock",
            "packageVersion",
            "runtimeVersion",
            "resolverManifest",
            "embeddedBuildManifestSha256",
            "embeddedBuildManifestCanonicalSha256",
            "embeddedBuildManifest",
            "nativePayloadFiles",
            "nativePayloadRelationship",
            "repositoryEvidence",
        },
    )
    tree = _object(
        artifacts["applicationTree"],
        "collection artifacts.applicationTree",
        {
            "format",
            "fileCount",
            "directoryCount",
            "symbolicLinkCount",
            "byteCount",
            "sha256",
        },
    )
    _exact(
        tree["format"],
        "canonical-application-tree-v1",
        "collection artifacts.applicationTree.format",
    )
    file_count = _integer(
        tree["fileCount"],
        "collection artifacts.applicationTree.fileCount",
        minimum=1,
        maximum=MAXIMUM_TREE_ENTRIES,
    )
    directory_count = _integer(
        tree["directoryCount"],
        "collection artifacts.applicationTree.directoryCount",
        minimum=1,
        maximum=MAXIMUM_TREE_ENTRIES,
    )
    symbolic_link_count = _integer(
        tree["symbolicLinkCount"],
        "collection artifacts.applicationTree.symbolicLinkCount",
        minimum=0,
        maximum=MAXIMUM_TREE_ENTRIES,
    )
    _integer(
        tree["byteCount"],
        "collection artifacts.applicationTree.byteCount",
        minimum=1,
        maximum=MAXIMUM_TREE_BYTES,
    )
    _digest(tree["sha256"], "collection artifacts.applicationTree.sha256")
    if file_count + directory_count + symbolic_link_count > MAXIMUM_TREE_ENTRIES:
        raise CpuBenchmarkCollectionError(
            "collection application-tree counts exceed the entry bound"
        )
    _file_evidence(
        artifacts["executable"],
        "collection artifacts.executable",
        with_id=True,
    )
    _file_evidence(
        artifacts["shimLibrary"],
        "collection artifacts.shimLibrary",
        with_id=True,
    )
    _file_evidence(
        artifacts["runtimeLibrary"],
        "collection artifacts.runtimeLibrary",
        with_id=True,
    )
    dependencies = _array(
        artifacts["providerDependencies"],
        "collection artifacts.providerDependencies",
        minimum=0,
        maximum=64,
    )
    dependency_ids: list[str] = []
    for index, dependency in enumerate(dependencies):
        checked = _file_evidence(
            dependency,
            f"collection artifacts.providerDependencies[{index}]",
            with_id=True,
        )
        dependency_ids.append(checked["id"])
    if dependency_ids != sorted(set(dependency_ids)):
        raise CpuBenchmarkCollectionError(
            "collection provider dependencies are not sorted and unique"
        )
    _file_evidence(
        artifacts["nativeLock"],
        "collection artifacts.nativeLock",
        with_id=False,
    )
    for key in ("packageVersion", "runtimeVersion"):
        value = artifacts[key]
        if type(value) is not str or _SEMVER.fullmatch(value) is None:
            raise CpuBenchmarkCollectionError(
                f"collection artifacts.{key} changed"
            )
    _file_evidence(
        artifacts["resolverManifest"],
        "collection artifacts.resolverManifest",
        with_id=True,
    )
    _digest(
        artifacts["embeddedBuildManifestSha256"],
        "collection artifacts.embeddedBuildManifestSha256",
    )
    canonical_manifest_sha256 = _digest(
        artifacts["embeddedBuildManifestCanonicalSha256"],
        "collection artifacts.embeddedBuildManifestCanonicalSha256",
    )
    embedded_manifest = artifacts["embeddedBuildManifest"]
    if type(embedded_manifest) is not dict:
        raise CpuBenchmarkCollectionError(
            "collection artifacts.embeddedBuildManifest is not an object"
        )
    if hashlib.sha256(_canonical_bytes(embedded_manifest)).hexdigest() != (
        canonical_manifest_sha256
    ):
        raise CpuBenchmarkCollectionError(
            "collection embedded build manifest canonical identity changed"
        )
    payloads = _array(
        artifacts["nativePayloadFiles"],
        "collection artifacts.nativePayloadFiles",
        minimum=1,
        maximum=256,
    )
    payload_ids: list[str] = []
    for index, payload in enumerate(payloads):
        checked = _file_evidence(
            payload,
            f"collection artifacts.nativePayloadFiles[{index}]",
            with_id=True,
        )
        payload_ids.append(checked["id"])
    if payload_ids != sorted(set(payload_ids)):
        raise CpuBenchmarkCollectionError(
            "collection native payload identities are not sorted and unique"
        )
    _exact(
        artifacts["nativePayloadRelationship"],
        "lock-resolver-source-bytes;packaged-equivalence-requires-platform-audit",
        "collection artifacts.nativePayloadRelationship",
    )
    repository_evidence = _object(
        artifacts["repositoryEvidence"],
        "collection artifacts.repositoryEvidence",
        {"sourceManifest", *_REPOSITORY_EVIDENCE_FILES.keys()},
    )
    _file_evidence(
        repository_evidence["sourceManifest"],
        "collection artifacts.repositoryEvidence.sourceManifest",
        with_id=False,
    )
    for name, (_relative, expected_size, expected_sha256) in (
        _REPOSITORY_EVIDENCE_FILES.items()
    ):
        evidence = _file_evidence(
            repository_evidence[name],
            f"collection artifacts.repositoryEvidence.{name}",
            with_id=False,
        )
        _exact(
            evidence,
            {"sizeBytes": expected_size, "sha256": expected_sha256},
            f"collection artifacts.repositoryEvidence.{name}",
        )
    return artifacts


def _validate_native_fragment_binding(
    artifacts: dict[str, Any], runtime: dict[str, Any]
) -> None:
    manifest = _object(
        artifacts["embeddedBuildManifest"],
        "collection embedded build manifest",
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
    )
    _exact(manifest["schemaVersion"], 3, "embedded build manifest schema")
    _exact(
        manifest["nativeIdentity"],
        "fonix_shim",
        "embedded build manifest native identity",
    )
    _integer(
        manifest["shimAbiVersion"],
        "embedded build manifest shim ABI",
        minimum=1,
        maximum=1000,
    )
    _integer(
        manifest["requiredOrtApiVersion"],
        "embedded build manifest required ORT API",
        minimum=1,
        maximum=1000,
    )
    _exact(
        manifest["runtimeProfile"],
        "bundled",
        "embedded build manifest runtime profile",
    )
    _exact(
        manifest["androidRuntimeOwner"],
        None,
        "embedded build manifest Android owner",
    )
    _exact(
        manifest["allowedRuntimeSources"],
        ["bundled"],
        "embedded build manifest runtime sources",
    )
    _token(manifest["buildId"], "embedded build manifest build id")
    artifact = _object(
        manifest["artifact"],
        "collection embedded build manifest artifact",
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
    )
    for key in ("id", "targetOs", "targetArchitecture", "targetVariant", "flavor"):
        _token(artifact[key], f"embedded build manifest artifact.{key}")
    _label(artifact["minimumOs"], "embedded build manifest artifact.minimumOs")
    _digest(artifact["lockSha256"], "embedded build manifest artifact.lockSha256")
    _digest(
        artifact["sourceSha256"],
        "embedded build manifest artifact.sourceSha256",
    )
    _digest(
        artifact["thirdPartyNoticesSha256"],
        "embedded build manifest artifact.thirdPartyNoticesSha256",
    )
    _exact(
        artifact["runtimeMode"],
        "bundled",
        "embedded build manifest artifact.runtimeMode",
    )
    providers = _array(
        artifact["providers"],
        "embedded build manifest artifact.providers",
        minimum=1,
        maximum=64,
    )
    seen: set[str] = set()
    for index, provider_value in enumerate(providers):
        provider = _object(
            provider_value,
            f"embedded build manifest artifact.providers[{index}]",
            {"wrapperId", "reportedName"},
        )
        wrapper_id = _token(
            provider["wrapperId"],
            f"embedded build manifest artifact.providers[{index}].wrapperId",
        )
        if wrapper_id in seen:
            raise CpuBenchmarkCollectionError(
                "embedded build manifest providers contain a duplicate"
            )
        seen.add(wrapper_id)
        if provider["reportedName"] is not None:
            _token(
                provider["reportedName"],
                f"embedded build manifest artifact.providers[{index}].reportedName",
            )
    expected_runtime = {
        "packageVersion": artifacts["packageVersion"],
        "runtimeVersion": artifacts["runtimeVersion"],
        "runtimeLibraryIdentity": artifacts["runtimeLibrary"]["id"],
        "runtimeSource": artifact["runtimeMode"],
        "runtimeOwner": "wrapper",
        "artifactFlavor": artifact["flavor"],
        "artifactId": artifact["id"],
        "artifactSourceSha256": artifact["sourceSha256"],
        "platform": artifact["targetOs"],
        "architecture": artifact["targetArchitecture"],
        "shimNativeIdentity": manifest["nativeIdentity"],
        "shimAbi": manifest["shimAbiVersion"],
        "shimBuildId": manifest["buildId"],
        "requiredOrtApi": manifest["requiredOrtApiVersion"],
        "negotiatedOrtApi": manifest["requiredOrtApiVersion"],
        "compiledProviders": providers,
    }
    actual_runtime = {key: runtime[key] for key in expected_runtime}
    _exact(
        actual_runtime,
        expected_runtime,
        "fragment runtime/native build binding",
    )
    if manifest["buildId"] != artifact["id"]:
        raise CpuBenchmarkCollectionError(
            "embedded build id does not match its artifact id"
        )
    if artifact["lockSha256"] != artifacts["nativeLock"]["sha256"]:
        raise CpuBenchmarkCollectionError(
            "embedded build manifest does not match current native-lock bytes"
        )


def _validate_environment(
    value: Any, expected_launches: Sequence[tuple[str, int]]
) -> dict[str, Any]:
    environment = _object(
        value,
        "collection environment",
        {
            "platform",
            "architecture",
            "deviceIdentitySha256",
            "osVersion",
            "osBuild",
            "driverIdentity",
            "firmwareIdentity",
            "cpuUtilization",
            "launchObservations",
            "comparability",
        },
    )
    if type(environment["platform"]) is not str or environment[
        "platform"
    ] not in {"macos", "linux"}:
        raise CpuBenchmarkCollectionError("collection environment platform changed")
    if type(environment["architecture"]) is not str or environment[
        "architecture"
    ] not in {"arm64", "x86_64"}:
        raise CpuBenchmarkCollectionError("collection environment architecture changed")
    platform_tuple = (environment["platform"], environment["architecture"])
    if platform_tuple not in {("macos", "arm64"), ("linux", "x86_64")}:
        raise CpuBenchmarkCollectionError(
            "collection environment platform/architecture tuple changed"
        )
    _digest(
        environment["deviceIdentitySha256"],
        "collection environment.deviceIdentitySha256",
    )
    for key in (
        "osVersion",
        "osBuild",
        "driverIdentity",
        "firmwareIdentity",
    ):
        _label(environment[key], f"collection environment.{key}")
    observations = _array(
        environment["launchObservations"],
        "collection environment.launchObservations",
        minimum=len(expected_launches),
        maximum=len(expected_launches),
    )
    for index, observation_value in enumerate(observations):
        observation = _object(
            observation_value,
            f"collection environment.launchObservations[{index}]",
            {
                "index",
                "launchChallenge",
                "processId",
                "powerModeStart",
                "powerModeEnd",
                "thermalStateStart",
                "thermalStateEnd",
            },
        )
        _exact(
            observation["index"],
            index,
            f"collection environment.launchObservations[{index}].index",
        )
        _exact(
            observation["launchChallenge"],
            expected_launches[index][0],
            f"collection environment.launchObservations[{index}].launchChallenge",
        )
        _exact(
            observation["processId"],
            expected_launches[index][1],
            f"collection environment.launchObservations[{index}].processId",
        )
        for key in ("powerModeStart", "powerModeEnd"):
            label = (
                f"collection environment.launchObservations[{index}].{key}"
            )
            power_mode = _label(observation[key], label)
            if environment["platform"] == "macos":
                valid_power_mode = (
                    power_mode == _HOST_API_UNAVAILABLE
                    or _MACOS_POWER_MODE.fullmatch(power_mode) is not None
                )
            else:
                valid_power_mode = (
                    power_mode == _HOST_API_UNAVAILABLE
                    or _LINUX_POWER_MODE.fullmatch(power_mode) is not None
                )
            if not valid_power_mode:
                raise CpuBenchmarkCollectionError(
                    f"{label} is outside the closed host power-mode grammar"
                )
        for key in ("thermalStateStart", "thermalStateEnd"):
            label = (
                f"collection environment.launchObservations[{index}].{key}"
            )
            thermal_state = _label(observation[key], label)
            valid_thermal_state = (
                thermal_state in _THERMAL_STATES
                if environment["platform"] == "macos"
                else thermal_state == _HOST_API_UNAVAILABLE
            )
            if not valid_thermal_state:
                raise CpuBenchmarkCollectionError(
                    f"{label} is outside the closed host thermal-state grammar"
                )

    def summarize(values: Sequence[str], *, thermal: bool) -> dict[str, Any]:
        unavailable = "not-exposed-by-host-api"
        known = [item for item in values if item != unavailable]
        availability = (
            "unavailable"
            if not known
            else "available"
            if len(known) == len(values)
            else "partial"
        )
        changed = len(set(known)) > 1
        state_key = "drift" if thermal else "stability"
        if changed:
            state = "observed" if thermal else "changed"
        elif availability == "available":
            state = "none-observed" if thermal else "stable"
        else:
            state = "indeterminate"
        return {
            "availability": availability,
            state_key: state,
            "stableValue": known[0]
            if availability == "available" and not changed
            else None,
        }

    power_values = [
        item
        for observation in observations
        for item in (
            observation["powerModeStart"],
            observation["powerModeEnd"],
        )
    ]
    thermal_values = [
        item
        for observation in observations
        for item in (
            observation["thermalStateStart"],
            observation["thermalStateEnd"],
        )
    ]
    power = summarize(power_values, thermal=False)
    thermal = summarize(thermal_values, thermal=True)
    reasons: list[str] = []
    if power["availability"] == "unavailable":
        reasons.append("power-mode-unavailable")
    elif power["availability"] == "partial":
        reasons.append("power-mode-partially-unavailable")
    if power["stability"] == "changed":
        reasons.append("power-mode-changed")
    if thermal["availability"] == "unavailable":
        reasons.append("thermal-state-unavailable")
    elif thermal["availability"] == "partial":
        reasons.append("thermal-state-partially-unavailable")
    if thermal["drift"] == "observed":
        reasons.append("thermal-drift-observed")
    changed = power["stability"] == "changed" or thermal["drift"] == "observed"
    incomplete = (
        power["availability"] != "available"
        or thermal["availability"] != "available"
    )
    expected_comparability = {
        "status": "non-comparable"
        if changed
        else "incomplete"
        if incomplete
        else "baseline-comparable",
        "powerMode": power,
        "thermalState": thermal,
        "reasons": reasons,
    }
    _exact(
        environment["comparability"],
        expected_comparability,
        "collection environment.comparability",
    )
    cpu = environment["cpuUtilization"]
    if type(cpu) is not dict or cpu.get("status") not in {
        "not-exposed",
        "measured",
    }:
        raise CpuBenchmarkCollectionError(
            "collection environment.cpuUtilization changed"
        )
    if cpu["status"] == "not-exposed":
        _exact(
            cpu,
            {
                "status": "not-exposed",
                "reason": "target-api-not-integrated",
            },
            "collection environment.cpuUtilization",
        )
    else:
        measured = _object(
            cpu,
            "collection environment.cpuUtilization",
            {"status", "launches"},
        )
        launches = _array(
            measured["launches"],
            "collection environment.cpuUtilization.launches",
            minimum=len(expected_launches),
            maximum=len(expected_launches),
        )
        for index, launch_value in enumerate(launches):
            launch = _object(
                launch_value,
                f"collection environment.cpuUtilization.launches[{index}]",
                {
                    "index",
                    "launchChallenge",
                    "processId",
                    "userMicroseconds",
                    "systemMicroseconds",
                    "wallMicroseconds",
                },
            )
            _exact(
                launch["index"],
                index,
                f"collection environment.cpuUtilization.launches[{index}].index",
            )
            _exact(
                launch["launchChallenge"],
                expected_launches[index][0],
                f"collection environment.cpuUtilization.launches[{index}].launchChallenge",
            )
            _exact(
                launch["processId"],
                expected_launches[index][1],
                f"collection environment.cpuUtilization.launches[{index}].processId",
            )
            user = _integer(
                launch["userMicroseconds"],
                f"collection environment.cpuUtilization.launches[{index}].userMicroseconds",
                minimum=0,
                maximum=MAXIMUM_DURATION,
            )
            system = _integer(
                launch["systemMicroseconds"],
                f"collection environment.cpuUtilization.launches[{index}].systemMicroseconds",
                minimum=0,
                maximum=MAXIMUM_DURATION,
            )
            wall = _integer(
                launch["wallMicroseconds"],
                f"collection environment.cpuUtilization.launches[{index}].wallMicroseconds",
                minimum=1,
                maximum=MAXIMUM_DURATION,
            )
            if user + system > wall * 4096:
                raise CpuBenchmarkCollectionError(
                    "collection CPU time is outside its bounded parallelism contract"
                )
    return environment


def _series(samples: Sequence[int], *, label: str) -> dict[str, Any]:
    copied = list(samples)
    return {
        "samples": copied,
        "statistics": canonical_statistics(copied, label=label),
    }


def _validate_host_observation_payload(
    payload: bytes,
    expected_launches: Sequence[tuple[str, int]],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if (
        type(payload) is not bytes
        or not payload
        or len(payload) > MAXIMUM_HOST_OBSERVATION_BYTES
    ):
        raise CpuBenchmarkCollectionError(
            "raw host observation is not bounded immutable bytes"
        )
    digest = hashlib.sha256(payload).hexdigest()
    value = strict_json_loads(
        payload,
        label="raw host observation",
        maximum_bytes=MAXIMUM_HOST_OBSERVATION_BYTES,
    )
    record = _object(
        value,
        "raw host observation",
        {
            "schemaVersion",
            "result",
            "claimStatus",
            "purpose",
            "launchCount",
            "environment",
            "claimBoundary",
        },
    )
    _exact(record["schemaVersion"], 1, "raw host observation schema")
    _exact(record["result"], "measured", "raw host observation result")
    _exact(
        record["claimStatus"],
        "measurement-only",
        "raw host observation claim status",
    )
    _exact(
        record["purpose"],
        "cpu-benchmark-host-observations",
        "raw host observation purpose",
    )
    _exact(
        record["launchCount"],
        len(expected_launches),
        "raw host observation launch count",
    )
    _exact(
        record["claimBoundary"],
        _HOST_OBSERVATION_CLAIM,
        "raw host observation claim boundary",
    )
    environment = _plain_json_copy(
        _validate_environment(record["environment"], expected_launches)
    )
    normalized = _plain_json_copy(record)
    _exact(
        normalized["environment"],
        environment,
        "raw host observation normalized environment",
    )
    return normalized, environment, digest


def derive_collection(
    *,
    fragment_payloads: Sequence[bytes],
    host_observation_payload: bytes,
    expected_launches: Sequence[tuple[str, int]],
    artifacts: Mapping[str, Any],
    collector_sha256: str,
) -> dict[str, Any]:
    """Validate one exact tuple and derive a path-free, raw-preserving record."""

    if (
        isinstance(fragment_payloads, (str, bytes))
        or not 2 <= len(fragment_payloads) <= MAXIMUM_LAUNCHES
        or len(expected_launches) != len(fragment_payloads)
    ):
        raise CpuBenchmarkCollectionError(
            "collection launch inputs have inconsistent bounded lengths"
        )
    expected: list[tuple[str, int]] = []
    for index, launch in enumerate(expected_launches):
        if type(launch) is not tuple or len(launch) != 2:
            raise CpuBenchmarkCollectionError(
                f"expected launch {index} is not a challenge/process tuple"
            )
        expected.append(
            (
                _digest(launch[0], f"expected launch {index} challenge"),
                _integer(
                    launch[1],
                    f"expected launch {index} process id",
                    minimum=1,
                    maximum=0x7FFF_FFFF,
                ),
            )
        )
    challenges = [launch[0] for launch in expected]
    if len(set(challenges)) != len(challenges):
        raise CpuBenchmarkCollectionError("collection launch challenges are not unique")
    normalized: list[dict[str, Any]] = []
    digests: list[str] = []
    for index, payload in enumerate(fragment_payloads):
        if type(payload) is not bytes or not payload or len(payload) > MAXIMUM_FRAGMENT_BYTES:
            raise CpuBenchmarkCollectionError(
                f"raw fragment {index} is not bounded immutable bytes"
            )
        digest = hashlib.sha256(payload).hexdigest()
        value = strict_json_loads(
            payload,
            label=f"raw fragment {index}",
            maximum_bytes=MAXIMUM_FRAGMENT_BYTES,
        )
        digests.append(digest)
        normalized.append(
            validate_fragment(
                value,
                expected_challenge=expected[index][0],
                expected_process_id=expected[index][1],
                raw_sha256=digest,
            )
        )
    if len(set(digests)) != len(digests):
        raise CpuBenchmarkCollectionError("collection fragment hashes are not unique")

    def identity_tuple(fragment: dict[str, Any]) -> dict[str, Any]:
        resources = fragment["resources"]
        pool = fragment["poolEvidence"]
        pool_resources = pool["resources"]
        pool_stabilization = pool["stabilization"]
        return {
            "protocol": fragment["protocol"],
            "model": fragment["model"],
            "runtime": fragment["runtime"],
            "session": fragment["session"],
            "timing": fragment["timing"],
            "providerAssignment": fragment["providerAssignment"],
            "resourceContract": {
                "rssScope": resources["rssScope"],
                "nativeRss": resources["nativeRss"],
                "cpuUtilization": resources["cpuUtilization"],
                "thermalStart": resources["thermalStart"],
                "thermalEnd": resources["thermalEnd"],
                "powerMode": resources["powerMode"],
            },
            "lifecycle": fragment["lifecycle"],
            "poolEvidenceContract": {
                "executionSurface": pool["executionSurface"],
                "configuration": pool["configuration"],
                "timing": pool["timing"],
                "stabilizationPolicy": {
                    key: pool_stabilization[key]
                    for key in (
                        "method",
                        "batchSize",
                        "thresholdBasisPoints",
                        "requiredConsecutiveTransitions",
                        "maximumRounds",
                        "result",
                    )
                },
                "providerAssignments": pool["providerAssignments"],
                "resourceContract": {
                    "rssScope": pool_resources["rssScope"],
                    "nativeRss": pool_resources["nativeRss"],
                    "cpuUtilization": pool_resources["cpuUtilization"],
                },
                "lifecycle": pool["lifecycle"],
            },
        }

    identity = identity_tuple(normalized[0])
    for index, fragment in enumerate(normalized[1:], start=1):
        _exact(
            identity_tuple(fragment),
            identity,
            f"fragment {index} cross-launch identity tuple",
        )

    raw_host_observation, checked_environment, host_observation_sha256 = (
        _validate_host_observation_payload(host_observation_payload, expected)
    )
    checked_artifacts = _plain_json_copy(_validate_artifacts(artifacts))
    runtime = normalized[0]["runtime"]
    _validate_native_fragment_binding(checked_artifacts, runtime)
    if (
        checked_environment["platform"] != runtime["platform"]
        or checked_environment["architecture"] != runtime["architecture"]
    ):
        raise CpuBenchmarkCollectionError(
            "collection environment does not match the target runtime tuple"
        )
    collector_digest = _digest(collector_sha256, "collector SHA-256")

    measurement_names = (
        "coldRuntimeLoadMicroseconds",
        "sessionCreateMicroseconds",
        "dataPreparationMicroseconds",
        "firstRunMicroseconds",
        "firstOutputMaterializationMicroseconds",
        "warmRunMicroseconds",
        "warmOutputMaterializationMicroseconds",
    )
    measurement_aggregates: dict[str, Any] = {}
    for name in measurement_names:
        samples = [
            sample
            for fragment in normalized
            for sample in fragment["measurements"][name]
        ]
        measurement_aggregates[name] = _series(
            samples, label=f"aggregate measurements.{name}"
        )

    stabilization_launches: list[dict[str, Any]] = []
    stabilization_samples: list[int] = []
    stabilization_medians: list[int] = []
    for index, fragment in enumerate(normalized):
        stabilization = fragment["stabilization"]
        launch = {
            "index": index,
            "actualRuns": stabilization["actualRuns"],
            "inferenceMicroseconds": list(stabilization["inferenceMicroseconds"]),
            "batchMedianMicroseconds": list(
                stabilization["batchMedianMicroseconds"]
            ),
        }
        stabilization_launches.append(launch)
        stabilization_samples.extend(launch["inferenceMicroseconds"])
        stabilization_medians.extend(launch["batchMedianMicroseconds"])

    throughput_windows: list[dict[str, Any]] = []
    throughput_rates: list[int] = []
    total_completed = 0
    total_duration = 0
    for launch_index, fragment in enumerate(normalized):
        for window_index, window in enumerate(fragment["measurements"]["throughput"]):
            rate = throughput_rate_milli(
                window["completedRuns"], window["durationMicroseconds"]
            )
            throughput_windows.append(
                {
                    "launchIndex": launch_index,
                    "windowIndex": window_index,
                    "completedRuns": window["completedRuns"],
                    "durationMicroseconds": window["durationMicroseconds"],
                    "runsPerSecondMilli": rate,
                }
            )
            throughput_rates.append(rate)
            total_completed += window["completedRuns"]
            total_duration += window["durationMicroseconds"]

    rss_current: list[int] = []
    rss_peak: list[int] = []
    rss_by_phase: list[dict[str, Any]] = []
    for phase_index, phase in enumerate(_RSS_PHASES):
        current_values = [
            fragment["resources"]["rssSamples"][phase_index]["currentBytes"]
            for fragment in normalized
        ]
        peak_values = [
            fragment["resources"]["rssSamples"][phase_index]["peakBytes"]
            for fragment in normalized
        ]
        rss_current.extend(current_values)
        rss_peak.extend(peak_values)
        rss_by_phase.append(
            {
                "phase": phase,
                "currentBytes": _series(
                    current_values, label=f"aggregate RSS {phase} current"
                ),
                "peakBytes": _series(
                    peak_values, label=f"aggregate RSS {phase} peak"
                ),
            }
        )

    pool_measurement_aggregates: dict[str, Any] = {}
    for name in ("inputPreparationMicroseconds", "poolStartupMicroseconds"):
        samples = [
            sample
            for fragment in normalized
            for sample in fragment["poolEvidence"]["measurements"][name]
        ]
        pool_measurement_aggregates[name] = _series(
            samples, label=f"aggregate pool measurements.{name}"
        )

    first_concurrent_rounds: list[dict[str, Any]] = []
    first_round_durations: list[int] = []
    first_round_lane_totals = [0, 0]
    first_round_total_completed = 0
    for launch_index, fragment in enumerate(normalized):
        first_round = fragment["poolEvidence"]["measurements"][
            "firstConcurrentRound"
        ]
        first_concurrent_rounds.append(
            {"launchIndex": launch_index, **_plain_json_copy(first_round)}
        )
        first_round_durations.append(first_round["durationMicroseconds"])
        first_round_total_completed += first_round["totalCompletedRuns"]
        for lane_index, count in enumerate(first_round["completedRunsByLane"]):
            first_round_lane_totals[lane_index] += count
    pool_measurement_aggregates["firstConcurrentRound"] = {
        "rounds": first_concurrent_rounds,
        "durationMicroseconds": _series(
            first_round_durations,
            label="aggregate pool first concurrent round durations",
        ),
        "totalCompletedRunsByLane": first_round_lane_totals,
        "totalCompletedRuns": first_round_total_completed,
    }

    pool_stabilization_launches: list[dict[str, Any]] = []
    pool_stabilization_samples: list[int] = []
    pool_stabilization_medians: list[int] = []
    for index, fragment in enumerate(normalized):
        stabilization = fragment["poolEvidence"]["stabilization"]
        launch = {
            "index": index,
            "actualRounds": stabilization["actualRounds"],
            "roundDurationMicroseconds": list(
                stabilization["roundDurationMicroseconds"]
            ),
            "batchMedianMicroseconds": list(
                stabilization["batchMedianMicroseconds"]
            ),
        }
        pool_stabilization_launches.append(launch)
        pool_stabilization_samples.extend(launch["roundDurationMicroseconds"])
        pool_stabilization_medians.extend(launch["batchMedianMicroseconds"])

    pool_throughput_windows: list[dict[str, Any]] = []
    pool_throughput_rates: list[int] = []
    pool_total_completed_by_lane = [0, 0]
    pool_total_completed = 0
    pool_total_duration = 0
    for launch_index, fragment in enumerate(normalized):
        windows = fragment["poolEvidence"]["measurements"]["throughput"]
        for window_index, window in enumerate(windows):
            rate = throughput_rate_milli(
                window["totalCompletedRuns"], window["durationMicroseconds"]
            )
            pool_throughput_windows.append(
                {
                    "launchIndex": launch_index,
                    "windowIndex": window_index,
                    **_plain_json_copy(window),
                    "runsPerSecondMilli": rate,
                }
            )
            pool_throughput_rates.append(rate)
            pool_total_completed += window["totalCompletedRuns"]
            pool_total_duration += window["durationMicroseconds"]
            for lane_index, count in enumerate(window["completedRunsByLane"]):
                pool_total_completed_by_lane[lane_index] += count

    pool_rss_current: list[int] = []
    pool_rss_peak: list[int] = []
    pool_rss_by_phase: list[dict[str, Any]] = []
    for phase_index, phase in enumerate(_POOL_RSS_PHASES):
        current_values = [
            fragment["poolEvidence"]["resources"]["rssSamples"][phase_index][
                "currentBytes"
            ]
            for fragment in normalized
        ]
        peak_values = [
            fragment["poolEvidence"]["resources"]["rssSamples"][phase_index][
                "peakBytes"
            ]
            for fragment in normalized
        ]
        pool_rss_current.extend(current_values)
        pool_rss_peak.extend(peak_values)
        pool_rss_by_phase.append(
            {
                "phase": phase,
                "currentBytes": _series(
                    current_values,
                    label=f"aggregate pool RSS {phase} current",
                ),
                "peakBytes": _series(
                    peak_values,
                    label=f"aggregate pool RSS {phase} peak",
                ),
            }
        )

    raw_fragments = [
        {
            "index": index,
            "sha256": digests[index],
            "launchChallenge": expected[index][0],
            "processId": expected[index][1],
            "fragment": fragment,
        }
        for index, fragment in enumerate(normalized)
    ]
    return {
        "schemaVersion": 3,
        "result": "measured",
        "claimStatus": "measurement-only",
        "purpose": "cpu-benchmark-cross-launch-collection",
        "collector": {
            "id": "fonix-cpu-benchmark-collector-v2",
            "sha256": collector_digest,
        },
        "protocol": _plain_json_copy(normalized[0]["protocol"]),
        "launchCount": len(normalized),
        "rawHostObservation": {
            "sha256": host_observation_sha256,
            "record": raw_host_observation,
        },
        "environment": checked_environment,
        "artifacts": checked_artifacts,
        "identityTuple": _plain_json_copy(identity),
        "rawFragments": raw_fragments,
        "aggregates": {
            "measurements": measurement_aggregates,
            "stabilization": {
                "launches": stabilization_launches,
                "inferenceMicroseconds": _series(
                    stabilization_samples,
                    label="aggregate stabilization inference",
                ),
                "batchMedianMicroseconds": _series(
                    stabilization_medians,
                    label="aggregate stabilization medians",
                ),
            },
            "throughput": {
                "windows": throughput_windows,
                "statisticsRunsPerSecondMilli": canonical_statistics(
                    throughput_rates, label="aggregate throughput rates"
                ),
                "totalCompletedRuns": total_completed,
                "totalDurationMicroseconds": total_duration,
                "aggregateRunsPerSecondMilli": _aggregate_throughput_rate_milli(
                    total_completed, total_duration
                ),
            },
            "resources": {
                "rssCurrentBytes": _series(
                    rss_current, label="aggregate RSS current"
                ),
                "rssPeakBytes": _series(rss_peak, label="aggregate RSS peak"),
                "byPhase": rss_by_phase,
            },
            "poolEvidence": {
                "measurements": pool_measurement_aggregates,
                "stabilization": {
                    "launches": pool_stabilization_launches,
                    "roundDurationMicroseconds": _series(
                        pool_stabilization_samples,
                        label="aggregate pool stabilization rounds",
                    ),
                    "batchMedianMicroseconds": _series(
                        pool_stabilization_medians,
                        label="aggregate pool stabilization medians",
                    ),
                },
                "throughput": {
                    "windows": pool_throughput_windows,
                    "statisticsRunsPerSecondMilli": canonical_statistics(
                        pool_throughput_rates,
                        label="aggregate pool throughput rates",
                    ),
                    "totalCompletedRunsByLane": pool_total_completed_by_lane,
                    "totalCompletedRuns": pool_total_completed,
                    "totalDurationMicroseconds": pool_total_duration,
                    "aggregateRunsPerSecondMilli": _aggregate_throughput_rate_milli(
                        pool_total_completed, pool_total_duration
                    ),
                },
                "resources": {
                    "rssCurrentBytes": _series(
                        pool_rss_current, label="aggregate pool RSS current"
                    ),
                    "rssPeakBytes": _series(
                        pool_rss_peak, label="aggregate pool RSS peak"
                    ),
                    "byPhase": pool_rss_by_phase,
                },
            },
        },
        "claimBoundary": _COLLECTION_CLAIM,
    }


# Concise compatibility aliases for collectors that use the mathematical names.
statistics = canonical_statistics
rate_milli = throughput_rate_milli
