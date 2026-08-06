#!/usr/bin/env python3
"""Generate a fail-closed Fonix/sherpa Android compatibility manifest.

The generator consumes exact input and final artifacts plus target-host test
receipts.  It does not download dependencies or infer compatibility from
Gradle metadata.  Native bytes are inspected with the repository-owned ELF
verifier, and every final loaded segment is tied to its selected source.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable
from urllib.parse import urlparse
import zipfile


sys.dont_write_bytecode = True
REPOSITORY = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY / "templates/android/verify_native_libs.py"
_VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_native_verifier", VERIFIER_PATH
)
if _VERIFIER_SPEC is None or _VERIFIER_SPEC.loader is None:
    raise RuntimeError("could not load the Android native-library verifier")
VERIFIER = importlib.util.module_from_spec(_VERIFIER_SPEC)
_VERIFIER_SPEC.loader.exec_module(VERIFIER)


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_QNN_QUALIFICATION_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOAD_ORDERS = frozenset({"dart-first", "sherpa-first"})
BUILD_TYPES = frozenset({"debug", "release-minified"})
PAGE_SIZES = frozenset({4096, 16384})
RECEIPT_KEYS = frozenset(
    {
        "schemaVersion",
        "result",
        "abi",
        "loadOrder",
        "buildType",
        "finalArtifactSha256",
        "runtimeVersion",
        "requiredOrtApi",
        "ortSha256",
        "pageSizeBytes",
        "device",
        "harnessSha256",
        "fixtures",
        "workload",
    }
)


class CompatibilityManifestError(RuntimeError):
    """An input cannot support the requested compatibility claim."""


@dataclass(frozen=True)
class ArtifactIdentity:
    file_name: str
    kind: str
    size_bytes: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "kind": self.kind,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
        }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CompatibilityManifestError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _regular_file(path: Path, label: str, maximum: int) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise CompatibilityManifestError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(status.st_mode):
        raise CompatibilityManifestError(f"{label} is not a regular file: {path}")
    if status.st_size <= 0 or status.st_size > maximum:
        raise CompatibilityManifestError(f"{label} size is outside its bound")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_identity(path: Path, report: dict[str, Any]) -> ArtifactIdentity:
    _regular_file(path, "artifact", MAX_ARTIFACT_BYTES)
    return ArtifactIdentity(
        file_name=path.name,
        kind=report["kind"],
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
    )


def _inspect(path: Path, label: str) -> dict[str, Any]:
    _regular_file(path, label, MAX_ARTIFACT_BYTES)
    try:
        report = VERIFIER.inspect(path)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise CompatibilityManifestError(
            f"could not inspect {label}: {error}"
        ) from error
    problems: list[str] = []
    if report["duplicate_paths"]:
        problems.append("duplicate archive paths")
    if report["invalid_archive_paths"]:
        problems.append("non-canonical archive paths")
    if report["invalid_libraries"]:
        problems.append("invalid native libraries")
    if problems:
        raise CompatibilityManifestError(f"{label} contains " + ", ".join(problems))
    return report


def _entries(report: dict[str, Any], name: str, abi: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in report["libraries"]
        if entry["name"] == name and entry["abi"] == abi
    ]


def _single(
    report: dict[str, Any], name: str, abi: str, label: str
) -> dict[str, Any]:
    matches = _entries(report, name, abi)
    if len(matches) != 1:
        raise CompatibilityManifestError(
            f"{label} must contain exactly one {name} for {abi}; found {len(matches)}"
        )
    return matches[0]


def _loaded_identity(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "flags": segment["flags"],
            "virtualAddress": segment["virtualAddress"],
            "fileSize": segment["fileSize"],
            "memorySize": segment["memorySize"],
            "sha256": segment["sha256"],
        }
        for segment in entry["elf"]["loadSegments"]
    ]


def _tie_source_to_final(
    source: dict[str, Any], final: dict[str, Any], role: str, abi: str
) -> None:
    if source["elf"]["class"] != final["elf"]["class"] or source["elf"][
        "machine"
    ] != final["elf"]["machine"]:
        raise CompatibilityManifestError(f"final {role} architecture drift for {abi}")
    if source["elf"]["soname"] != final["elf"]["soname"]:
        raise CompatibilityManifestError(f"final {role} SONAME drift for {abi}")
    if source["elf"]["needed"] != final["elf"]["needed"]:
        raise CompatibilityManifestError(f"final {role} dependency drift for {abi}")
    if _loaded_identity(source) != _loaded_identity(final):
        raise CompatibilityManifestError(
            f"final {role} loaded bytes do not match the selected source for {abi}"
        )


def _library_record(
    source: dict[str, Any], final: dict[str, Any]
) -> dict[str, Any]:
    return {
        "source": {
            "path": source["path"],
            "sizeBytes": source["size"],
            "sha256": source["sha256"],
        },
        "final": {
            "path": final["path"],
            "sizeBytes": final["size"],
            "sha256": final["sha256"],
        },
        "elf": {
            "class": final["elf"]["class"],
            "machine": final["elf"]["machine"],
            "soname": final["elf"]["soname"],
            "needed": final["elf"]["needed"],
            "pageSize16KiBCompatible": final["elf"][
                "pageSize16KiBCompatible"
            ],
            "loadedSegments": _loaded_identity(final),
        },
    }


def _validate_final_graph(report: dict[str, Any], abis: tuple[str, ...]) -> None:
    if report["kind"] not in {"apk", "aab"}:
        raise CompatibilityManifestError("final artifact must be an APK or AAB")
    present = {entry["abi"] for entry in report["libraries"]}
    if present != set(abis):
        raise CompatibilityManifestError(
            "final artifact ABI set does not exactly match the declared set"
        )
    for abi in abis:
        abi_entries = [entry for entry in report["libraries"] if entry["abi"] == abi]
        packaged_names = {entry["name"] for entry in abi_entries}
        for entry in abi_entries:
            if entry["elf"]["soname"] != entry["name"]:
                raise CompatibilityManifestError(
                    f"final {entry['path']} SONAME does not match its basename"
                )
            if abi in VERIFIER.ANDROID_16K_ABIS and not entry["elf"][
                "pageSize16KiBCompatible"
            ]:
                raise CompatibilityManifestError(
                    f"final {entry['path']} is not 16 KiB compatible"
                )
            unresolved = [
                needed
                for needed in entry["elf"]["needed"]
                if needed not in VERIFIER.ANDROID_SYSTEM_LIBRARIES
                and needed not in packaged_names
            ]
            if unresolved:
                raise CompatibilityManifestError(
                    f"final {entry['path']} has unresolved dependencies: "
                    + ", ".join(sorted(unresolved))
                )


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise CompatibilityManifestError(f"{label} has an unexpected field set")


def _receipt_text(value: Any, label: str, pattern: re.Pattern[str] = TOKEN) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CompatibilityManifestError(f"receipt {label} is invalid")
    return value


def _receipt_digest(value: Any, label: str) -> str:
    result = _receipt_text(value, label, SHA256)
    return result


def _receipt_count(value: Any, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > 1_000_000
    ):
        raise CompatibilityManifestError(f"receipt {label} is outside its bound")
    return value


def _read_receipt(
    path: Path,
    *,
    abis: tuple[str, ...],
    final_sha256: str,
    runtime_version: str,
    ort_api: int,
    expected_build_type: str,
    final_ort_by_abi: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _regular_file(path, "load-order receipt", MAX_RECEIPT_BYTES)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompatibilityManifestError(
            f"invalid load-order receipt: {path}"
        ) from error
    if not isinstance(value, dict):
        raise CompatibilityManifestError("load-order receipt must be an object")
    _exact_keys(value, RECEIPT_KEYS, "load-order receipt")
    if (
        not isinstance(value["schemaVersion"], int)
        or isinstance(value["schemaVersion"], bool)
        or value["schemaVersion"] != 1
        or value["result"] != "passed"
    ):
        raise CompatibilityManifestError("load-order receipt did not record a pass")
    abi = _receipt_text(value["abi"], "abi")
    if abi not in abis:
        raise CompatibilityManifestError("load-order receipt ABI is not declared")
    load_order = _receipt_text(value["loadOrder"], "loadOrder")
    receipt_build_type = _receipt_text(value["buildType"], "buildType")
    if load_order not in LOAD_ORDERS or receipt_build_type not in BUILD_TYPES:
        raise CompatibilityManifestError("load-order/build type is outside the matrix")
    if receipt_build_type != expected_build_type:
        raise CompatibilityManifestError(
            "load-order receipt belongs to another final build type"
        )
    if value["pageSizeBytes"] not in PAGE_SIZES:
        raise CompatibilityManifestError("receipt page size is outside the matrix")
    if (
        _receipt_digest(value["finalArtifactSha256"], "final artifact digest")
        != final_sha256
        or _receipt_text(value["runtimeVersion"], "runtimeVersion", VERSION)
        != runtime_version
        or value["requiredOrtApi"] != ort_api
        or _receipt_digest(value["ortSha256"], "ORT digest")
        != final_ort_by_abi[abi]["sha256"]
    ):
        raise CompatibilityManifestError(
            "load-order receipt does not match the inspected runtime/final artifact"
        )
    _receipt_digest(value["harnessSha256"], "harness digest")
    device = value["device"]
    if not isinstance(device, dict):
        raise CompatibilityManifestError("receipt device must be an object")
    _exact_keys(device, {"kind", "modelToken", "androidApi"}, "receipt device")
    if _receipt_text(device["kind"], "device.kind") not in {"physical", "emulator"}:
        raise CompatibilityManifestError("receipt device kind is invalid")
    _receipt_text(device["modelToken"], "device.modelToken")
    if (
        not isinstance(device["androidApi"], int)
        or not 24 <= device["androidApi"] <= 100
    ):
        raise CompatibilityManifestError("receipt Android API is outside its bound")
    fixtures = value["fixtures"]
    if not isinstance(fixtures, dict):
        raise CompatibilityManifestError("receipt fixtures must be an object")
    _exact_keys(
        fixtures,
        {"dartModelSha256", "sherpaFixtureSha256"},
        "receipt fixtures",
    )
    _receipt_digest(fixtures["dartModelSha256"], "Dart fixture digest")
    _receipt_digest(fixtures["sherpaFixtureSha256"], "sherpa fixture digest")
    workload = value["workload"]
    if not isinstance(workload, dict):
        raise CompatibilityManifestError("receipt workload must be an object")
    _exact_keys(
        workload,
        {"dartInferenceRuns", "sherpaSmokeRuns", "alternatingCycles"},
        "receipt workload",
    )
    for key in ("dartInferenceRuns", "sherpaSmokeRuns", "alternatingCycles"):
        _receipt_count(workload[key], f"workload.{key}")
    return {**value, "receiptSha256": _sha256(path)}


def _validate_receipt_coverage(
    receipts: list[dict[str, Any]], abis: tuple[str, ...]
) -> None:
    seen: set[tuple[Any, ...]] = set()
    for receipt in receipts:
        identity = (
            receipt["abi"],
            receipt["loadOrder"],
            receipt["buildType"],
            receipt["pageSizeBytes"],
            receipt["device"]["kind"],
        )
        if identity in seen:
            raise CompatibilityManifestError("duplicate load-order evidence tuple")
        seen.add(identity)
    required_pairs = {
        (load_order, page_size)
        for load_order in LOAD_ORDERS
        for page_size in PAGE_SIZES
    }
    for abi in abis:
        abi_receipts = [receipt for receipt in receipts if receipt["abi"] == abi]
        observed_pairs = {
            (receipt["loadOrder"], receipt["pageSizeBytes"])
            for receipt in abi_receipts
        }
        if observed_pairs != required_pairs:
            raise CompatibilityManifestError(
                "the full load-order/page-size Cartesian matrix is not "
                f"proven for {abi}"
            )


def _validate_source_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
        or not value.isascii()
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CompatibilityManifestError("sherpa source must be a plain HTTPS URL")
    if len(value.encode("utf-8")) > 1024:
        raise CompatibilityManifestError("sherpa source URL is oversized")
    return value


def _read_qnn_qualification(
    path: Path,
    *,
    mode: str,
    abis: tuple[str, ...],
    build_type: str,
    final_sha256: str,
    runtime_version: str,
    ort_api: int,
    final_ort_by_abi: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if mode != "aligned":
        raise CompatibilityManifestError(
            "QNN qualification is accepted only for aligned mode"
        )
    _regular_file(path, "QNN qualification record", MAX_QNN_QUALIFICATION_BYTES)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompatibilityManifestError(
            f"invalid QNN qualification record: {path}"
        ) from error
    if not isinstance(value, dict):
        raise CompatibilityManifestError("QNN qualification record must be an object")
    _exact_keys(
        value,
        {
            "schemaVersion",
            "result",
            "validatorSha256",
            "qualificationReceiptSha256",
            "build",
            "model",
            "runtime",
            "qnn",
            "device",
            "assignment",
            "executions",
            "contextCache",
            "loadOrders",
            "evidenceBindings",
            "claimBoundary",
        },
        "QNN qualification record",
    )
    if value["schemaVersion"] != 1 or value["result"] != "passed":
        raise CompatibilityManifestError("QNN qualification record did not pass")
    _receipt_digest(value["validatorSha256"], "QNN validator digest")
    _receipt_digest(
        value["qualificationReceiptSha256"], "QNN qualification receipt digest"
    )
    build = value["build"]
    runtime = value["runtime"]
    device = value["device"]
    qnn = value["qnn"]
    assignment = value["assignment"]
    if not all(
        isinstance(record, dict)
        for record in (build, runtime, device, qnn, assignment)
    ):
        raise CompatibilityManifestError(
            "QNN qualification core bindings must be objects"
        )
    if (
        build.get("buildType") != build_type
        or build.get("finalApkSha256") != final_sha256
        or not isinstance(build.get("finalApk"), dict)
        or build["finalApk"].get("sha256") != final_sha256
    ):
        raise CompatibilityManifestError(
            "QNN qualification belongs to another final artifact/build type"
        )
    abi = device.get("abi")
    if abi not in abis:
        raise CompatibilityManifestError(
            "QNN qualification ABI is not declared by this build"
        )
    if (
        runtime.get("ortVersion") != runtime_version
        or runtime.get("requiredOrtApi") != ort_api
        or runtime.get("ortSha256") != final_ort_by_abi[abi]["sha256"]
    ):
        raise CompatibilityManifestError(
            "QNN qualification runtime does not match the final aligned runtime"
        )
    for field in (
        "sdkManifestSha256",
        "backendLibrarySha256",
        "providerOptionsSha256",
    ):
        _receipt_digest(qnn.get(field), f"QNN qualification {field}")
    _receipt_text(qnn.get("backendId"), "QNN qualification backendId")
    total_nodes = assignment.get("totalNodes")
    if (
        assignment.get("fallbackPolicy") != "reject-cpu"
        or assignment.get("cpuNodes") != 0
        or not isinstance(total_nodes, int)
        or isinstance(total_nodes, bool)
        or not 0 < total_nodes <= 1_000_000
        or assignment.get("qnnNodes") != total_nodes
    ):
        raise CompatibilityManifestError(
            "QNN qualification does not prove zero CPU fallback"
        )
    load_orders = value["loadOrders"]
    if not isinstance(load_orders, list) or len(load_orders) != 2 or any(
        not isinstance(entry, dict) for entry in load_orders
    ):
        raise CompatibilityManifestError(
            "QNN qualification does not prove both load orders with parity"
        )
    if (
        {entry.get("loadOrder") for entry in load_orders} != LOAD_ORDERS
        or any(entry.get("parity") is not True for entry in load_orders)
    ):
        raise CompatibilityManifestError(
            "QNN qualification does not prove both load orders with parity"
        )
    if not isinstance(value["claimBoundary"], str) or not value["claimBoundary"]:
        raise CompatibilityManifestError("QNN qualification claim boundary is missing")
    return {"recordSha256": _sha256(path), **value}


def generate_manifest(arguments: argparse.Namespace) -> dict[str, Any]:
    abis = tuple(sorted(set(arguments.abi)))
    if not abis or len(abis) != len(arguments.abi):
        raise CompatibilityManifestError("--abi must be non-empty and unique")
    if any(abi not in VERIFIER.ANDROID_ABIS for abi in abis):
        raise CompatibilityManifestError("an unsupported Android ABI was requested")
    if arguments.ort_api_required != 27:
        raise CompatibilityManifestError("this snapshot requires ORT C API 27")
    if arguments.build_type not in BUILD_TYPES:
        raise CompatibilityManifestError("final build type is outside the matrix")
    if VERSION.fullmatch(arguments.ort_version_observed) is None:
        raise CompatibilityManifestError("observed ORT version must be strict semver")
    if REVISION.fullmatch(arguments.sherpa_revision) is None:
        raise CompatibilityManifestError("sherpa revision must be a full commit SHA")
    if ISO_DATE.fullmatch(arguments.snapshot_date) is None:
        raise CompatibilityManifestError("snapshot date must be YYYY-MM-DD")
    try:
        date.fromisoformat(arguments.snapshot_date)
    except ValueError as error:
        raise CompatibilityManifestError("snapshot date must be YYYY-MM-DD") from error

    sherpa_path = arguments.sherpa_artifact.resolve(strict=True)
    wrapper_path = arguments.wrapper_artifact.resolve(strict=True)
    final_path = arguments.final_artifact.resolve(strict=True)
    runtime_path = (
        sherpa_path
        if arguments.runtime_artifact is None
        else arguments.runtime_artifact.resolve(strict=True)
    )
    if arguments.mode == "sherpa-owned" and runtime_path != sherpa_path:
        raise CompatibilityManifestError(
            "sherpa-owned mode requires the sherpa artifact to own ORT"
        )
    if arguments.mode == "aligned" and arguments.runtime_artifact is None:
        raise CompatibilityManifestError("aligned mode requires --runtime-artifact")
    if arguments.mode == "aligned" and runtime_path in {
        sherpa_path,
        wrapper_path,
    }:
        raise CompatibilityManifestError(
            "aligned mode requires a separate application-owned runtime artifact"
        )

    sherpa_report = _inspect(sherpa_path, "sherpa artifact")
    wrapper_report = _inspect(wrapper_path, "wrapper artifact")
    runtime_report = (
        sherpa_report
        if runtime_path == sherpa_path
        else _inspect(runtime_path, "runtime artifact")
    )
    final_report = _inspect(final_path, "final artifact")
    _validate_final_graph(final_report, abis)

    if wrapper_report["ort_candidates"]:
        raise CompatibilityManifestError(
            "external Fonix wrapper artifact must own no ORT"
        )
    if arguments.mode == "aligned" and sherpa_report["ort_candidates"]:
        raise CompatibilityManifestError(
            "aligned sherpa artifact must own no ORT; the application runtime "
            "artifact is the sole source owner"
        )

    libraries_by_abi: dict[str, Any] = {}
    final_ort_by_abi: dict[str, dict[str, Any]] = {}
    for abi in abis:
        runtime_ort = _single(runtime_report, VERIFIER.ORT_NAME, abi, "runtime owner")
        sherpa_jni = _single(
            sherpa_report, VERIFIER.SHERPA_JNI_NAME, abi, "sherpa artifact"
        )
        wrapper_shim = _single(
            wrapper_report, VERIFIER.SHIM_NAME, abi, "wrapper artifact"
        )
        final_ort = _single(final_report, VERIFIER.ORT_NAME, abi, "final artifact")
        final_sherpa = _single(
            final_report, VERIFIER.SHERPA_JNI_NAME, abi, "final artifact"
        )
        final_shim = _single(final_report, VERIFIER.SHIM_NAME, abi, "final artifact")
        if runtime_ort["elf"]["soname"] != VERIFIER.ORT_NAME:
            raise CompatibilityManifestError(
                f"runtime owner has wrong ORT SONAME for {abi}"
            )
        if (
            sherpa_jni["elf"]["soname"] != VERIFIER.SHERPA_JNI_NAME
            or VERIFIER.ORT_NAME not in sherpa_jni["elf"]["needed"]
        ):
            raise CompatibilityManifestError(
                f"sherpa JNI does not use the shared ORT contract for {abi}"
            )
        if (
            wrapper_shim["elf"]["soname"] != VERIFIER.SHIM_NAME
            or VERIFIER.ORT_NAME in wrapper_shim["elf"]["needed"]
        ):
            raise CompatibilityManifestError(
                f"Fonix wrapper is not an external/process shim for {abi}"
            )
        _tie_source_to_final(runtime_ort, final_ort, "ORT", abi)
        _tie_source_to_final(sherpa_jni, final_sherpa, "sherpa JNI", abi)
        _tie_source_to_final(wrapper_shim, final_shim, "Fonix shim", abi)
        final_ort_by_abi[abi] = final_ort
        libraries_by_abi[abi] = {
            "onnxruntime": _library_record(runtime_ort, final_ort),
            "sherpaJni": _library_record(sherpa_jni, final_sherpa),
            "fonixShim": _library_record(wrapper_shim, final_shim),
        }

    sherpa_identity = _artifact_identity(sherpa_path, sherpa_report)
    wrapper_identity = _artifact_identity(wrapper_path, wrapper_report)
    runtime_identity = _artifact_identity(runtime_path, runtime_report)
    final_identity = _artifact_identity(final_path, final_report)
    receipts = [
        _read_receipt(
            receipt.resolve(strict=True),
            abis=abis,
            final_sha256=final_identity.sha256,
            runtime_version=arguments.ort_version_observed,
            ort_api=arguments.ort_api_required,
            expected_build_type=arguments.build_type,
            final_ort_by_abi=final_ort_by_abi,
        )
        for receipt in arguments.load_order_receipt
    ]
    _validate_receipt_coverage(receipts, abis)
    qnn_qualification = (
        None
        if arguments.qnn_qualification_record is None
        else _read_qnn_qualification(
            arguments.qnn_qualification_record.resolve(strict=True),
            mode=arguments.mode,
            abis=abis,
            build_type=arguments.build_type,
            final_sha256=final_identity.sha256,
            runtime_version=arguments.ort_version_observed,
            ort_api=arguments.ort_api_required,
            final_ort_by_abi=final_ort_by_abi,
        )
    )

    return {
        "schemaVersion": 1,
        "snapshotDate": arguments.snapshot_date,
        "sherpaOnnx": {
            "source": _validate_source_url(arguments.sherpa_source),
            "revision": arguments.sherpa_revision,
            "artifact": sherpa_identity.to_json(),
        },
        "android": {
            "integrationMode": arguments.mode,
            "buildType": arguments.build_type,
            "abis": list(abis),
            "ortOwner": "sherpa" if arguments.mode == "sherpa-owned" else "application",
            "ortVersionObserved": arguments.ort_version_observed,
            "ortApiRequired": arguments.ort_api_required,
            "artifacts": {
                "wrapper": wrapper_identity.to_json(),
                "runtimeOwner": runtime_identity.to_json(),
                "final": final_identity.to_json(),
            },
            "librariesByAbi": libraries_by_abi,
            "loadOrderEvidence": sorted(
                receipts,
                key=lambda receipt: (
                    receipt["abi"],
                    receipt["loadOrder"],
                    receipt["buildType"],
                    receipt["pageSizeBytes"],
                ),
            ),
            "qnnQualification": qnn_qualification,
        },
        "claimBoundary": (
            "This record ties inspected input and final native bytes to bounded "
            "target-host receipts. It proves only the named artifacts, ABIs, "
            "devices, build types, page sizes, fixtures, and workloads."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sherpa-owned", "aligned"), required=True)
    parser.add_argument("--sherpa-source", required=True)
    parser.add_argument("--sherpa-revision", required=True)
    parser.add_argument("--sherpa-artifact", type=Path, required=True)
    parser.add_argument("--wrapper-artifact", type=Path, required=True)
    parser.add_argument("--runtime-artifact", type=Path)
    parser.add_argument("--final-artifact", type=Path, required=True)
    parser.add_argument("--abi", action="append", required=True)
    parser.add_argument("--ort-version-observed", required=True)
    parser.add_argument("--ort-api-required", type=int, required=True)
    parser.add_argument("--build-type", choices=sorted(BUILD_TYPES), required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument(
        "--load-order-receipt", action="append", type=Path, required=True
    )
    parser.add_argument("--qnn-qualification-record", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = generate_manifest(arguments)
        output = arguments.output
        if output.exists() and (output.is_symlink() or not output.is_file()):
            raise CompatibilityManifestError("output is not a regular file")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        if temporary.exists():
            raise CompatibilityManifestError("temporary output already exists")
        encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(output)
        finally:
            if temporary.exists():
                temporary.unlink()
    except (CompatibilityManifestError, FileNotFoundError, OSError) as error:
        print(f"android_compatibility_manifest: {error}", file=sys.stderr)
        return 1
    print(f"Wrote Android compatibility manifest: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
