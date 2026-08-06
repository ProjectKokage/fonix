#!/usr/bin/env python3
"""Validate and normalize exact Android QNN qualification evidence.

The input receipt is deliberately not self-authenticating.  This tool rehashes
every named model/build/evidence input, inspects the final APK native graph,
checks the aligned-build and QNN SDK records, proves zero CPU-assigned nodes,
and requires cold/cache-hit plus both load-order executions.  It emits no
record when any binding is absent or contradictory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Iterable
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


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 4 * 1024 * 1024 * 1024
MAX_EVIDENCE_BYTES = 128 * 1024 * 1024
MAX_NODES = 1_000_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
LOAD_ORDERS = frozenset({"dart-first", "sherpa-first"})
BUILD_TYPES = frozenset({"debug", "release-minified"})
CHANGED_INPUTS = frozenset(
    {
        "model",
        "qdq-config",
        "ort-runtime",
        "qnn-sdk",
        "provider-options",
        "firmware",
        "driver",
    }
)


class QnnQualificationError(RuntimeError):
    """The submitted evidence cannot support a QNN qualification record."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QnnQualificationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise QnnQualificationError(f"{label} has an unexpected field set")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QnnQualificationError(f"{label} must be an object")
    return value


def _token(value: Any, label: str) -> str:
    if not isinstance(value, str) or TOKEN.fullmatch(value) is None:
        raise QnnQualificationError(f"{label} must be a bounded token")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise QnnQualificationError(f"{label} must be a lowercase SHA-256")
    return value


def _positive_integer(value: Any, label: str, maximum: int = 10**12) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > maximum
    ):
        raise QnnQualificationError(f"{label} must be a bounded positive integer")
    return value


def _regular_file(path: Path, label: str, maximum: int) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise QnnQualificationError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(status.st_mode):
        raise QnnQualificationError(f"{label} is not a regular file: {path}")
    if status.st_size <= 0 or status.st_size > maximum:
        raise QnnQualificationError(f"{label} size is outside its bound")
    return path


def _directory(path: Path, label: str) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise QnnQualificationError(f"missing {label}: {path}") from error
    if not stat.S_ISDIR(status.st_mode):
        raise QnnQualificationError(f"{label} is not a directory: {path}")
    return path


def _child_without_symlinks(root: Path, relative: str, label: str) -> Path:
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        try:
            status = candidate.lstat()
        except FileNotFoundError as error:
            raise QnnQualificationError(f"missing {label}: {candidate}") from error
        if stat.S_ISLNK(status.st_mode):
            raise QnnQualificationError(f"{label} traverses a symbolic link")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    return {
        "fileName": path.name,
        "sizeBytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    _regular_file(path, label, MAX_JSON_BYTES)
    raw = path.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QnnQualificationError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise QnnQualificationError(f"{label} must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise QnnQualificationError(f"{label} must be a canonical relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise QnnQualificationError(f"{label} must be a canonical relative path")
    return value


def _validate_file_hash(path: Path, expected: str, label: str, maximum: int) -> dict[str, Any]:
    _regular_file(path, label, maximum)
    if _sha256(path) != expected:
        raise QnnQualificationError(f"{label} hash does not match the receipt")
    return _identity(path)


def _validate_receipt(receipt: dict[str, Any]) -> None:
    _exact_keys(
        receipt,
        {
            "schemaVersion",
            "result",
            "build",
            "model",
            "runtime",
            "qnn",
            "device",
            "assignment",
            "executions",
            "contextCache",
            "loadOrders",
        },
        "QNN qualification receipt",
    )
    if receipt["schemaVersion"] != 1 or receipt["result"] != "passed":
        raise QnnQualificationError("receipt must be a passed schemaVersion 1 record")

    build = _object(receipt["build"], "build")
    _exact_keys(build, {"buildType", "finalApkSha256", "alignedBuildReceiptSha256"}, "build")
    if build["buildType"] not in BUILD_TYPES:
        raise QnnQualificationError("build.buildType is outside the closed set")
    _digest(build["finalApkSha256"], "build.finalApkSha256")
    _digest(build["alignedBuildReceiptSha256"], "build.alignedBuildReceiptSha256")

    model = _object(receipt["model"], "model")
    _exact_keys(
        model,
        {"onnxSha256", "qdqConfigSha256", "inputFixtureSha256", "referenceOutputSha256"},
        "model",
    )
    for key, value in model.items():
        _digest(value, f"model.{key}")

    runtime = _object(receipt["runtime"], "runtime")
    _exact_keys(runtime, {"ortVersion", "requiredOrtApi", "ortSha256"}, "runtime")
    if not isinstance(runtime["ortVersion"], str) or SEMVER.fullmatch(runtime["ortVersion"]) is None:
        raise QnnQualificationError("runtime.ortVersion must be strict semver")
    if runtime["requiredOrtApi"] != 27:
        raise QnnQualificationError("runtime.requiredOrtApi must be 27")
    _digest(runtime["ortSha256"], "runtime.ortSha256")

    qnn = _object(receipt["qnn"], "qnn")
    _exact_keys(
        qnn,
        {
            "sdkManifestSha256",
            "backendId",
            "backendLibrarySha256",
            "providerOptions",
            "providerOptionsSha256",
        },
        "qnn",
    )
    _digest(qnn["sdkManifestSha256"], "qnn.sdkManifestSha256")
    _token(qnn["backendId"], "qnn.backendId")
    _digest(qnn["backendLibrarySha256"], "qnn.backendLibrarySha256")
    provider_options = _object(qnn["providerOptions"], "qnn.providerOptions")
    if not provider_options or len(provider_options) > 64:
        raise QnnQualificationError("qnn.providerOptions must be a bounded non-empty object")
    for key, value in provider_options.items():
        _token(key, "QNN provider option name")
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
            raise QnnQualificationError("QNN provider option value is outside its bound")
    expected_options_hash = _canonical_hash(provider_options)
    if qnn["providerOptionsSha256"] != expected_options_hash:
        raise QnnQualificationError("QNN provider-options canonical hash mismatch")

    device = _object(receipt["device"], "device")
    _exact_keys(device, {"modelToken", "socToken", "androidApi", "abi", "firmwareSha256", "driverToken"}, "device")
    for key in ("modelToken", "socToken", "driverToken"):
        _token(device[key], f"device.{key}")
    if device["abi"] not in VERIFIER.ANDROID_ABIS:
        raise QnnQualificationError("device.abi is unsupported")
    if not isinstance(device["androidApi"], int) or isinstance(device["androidApi"], bool) or not 24 <= device["androidApi"] <= 100:
        raise QnnQualificationError("device.androidApi is outside its bound")
    _digest(device["firmwareSha256"], "device.firmwareSha256")

    assignment = _object(receipt["assignment"], "assignment")
    _exact_keys(assignment, {"evidenceSha256", "totalNodes", "qnnNodes", "cpuNodes", "fallbackPolicy"}, "assignment")
    _digest(assignment["evidenceSha256"], "assignment.evidenceSha256")
    total_nodes = _positive_integer(assignment["totalNodes"], "assignment.totalNodes", MAX_NODES)
    if assignment["qnnNodes"] != total_nodes or assignment["cpuNodes"] != 0:
        raise QnnQualificationError("QNN qualification forbids any CPU-assigned node")
    if assignment["fallbackPolicy"] != "reject-cpu":
        raise QnnQualificationError("assignment.fallbackPolicy must be reject-cpu")

    executions = _object(receipt["executions"], "executions")
    _exact_keys(executions, {"cold", "cacheHit", "parity"}, "executions")
    if executions["parity"] is not True:
        raise QnnQualificationError("cold/cache-hit parity must be true")
    for phase in ("cold", "cacheHit"):
        execution = _object(executions[phase], f"executions.{phase}")
        _exact_keys(execution, {"evidenceSha256", "outputSha256", "durationMicroseconds", "referenceMatch"}, f"executions.{phase}")
        _digest(execution["evidenceSha256"], f"executions.{phase}.evidenceSha256")
        _digest(execution["outputSha256"], f"executions.{phase}.outputSha256")
        _positive_integer(execution["durationMicroseconds"], f"executions.{phase}.durationMicroseconds")
        if execution["referenceMatch"] is not True:
            raise QnnQualificationError(f"executions.{phase} did not match the reference")
    if executions["cold"]["outputSha256"] != executions["cacheHit"]["outputSha256"]:
        raise QnnQualificationError("cold and cache-hit output hashes differ")
    if executions["cold"]["outputSha256"] != model["referenceOutputSha256"]:
        raise QnnQualificationError(
            "cold/cache-hit output does not equal the exact reference output"
        )

    context_cache = _object(receipt["contextCache"], "contextCache")
    _exact_keys(
        context_cache,
        {
            "key",
            "keyInputsSha256",
            "cacheArtifactSha256",
            "invalidationEvidenceSha256",
            "changedInput",
            "staleCacheRejected",
        },
        "contextCache",
    )
    _digest(context_cache["keyInputsSha256"], "contextCache.keyInputsSha256")
    _digest(context_cache["cacheArtifactSha256"], "contextCache.cacheArtifactSha256")
    _digest(context_cache["invalidationEvidenceSha256"], "contextCache.invalidationEvidenceSha256")
    if context_cache["key"] != "fonix-qnn-v1-" + context_cache["keyInputsSha256"]:
        raise QnnQualificationError("context-cache key is not derived from keyInputsSha256")
    if context_cache["changedInput"] not in CHANGED_INPUTS:
        raise QnnQualificationError("contextCache.changedInput is outside the closed set")
    if context_cache["staleCacheRejected"] is not True:
        raise QnnQualificationError("stale QNN context cache was not rejected")

    load_orders = receipt["loadOrders"]
    if not isinstance(load_orders, list) or len(load_orders) != 2:
        raise QnnQualificationError("loadOrders must contain exactly two entries")
    observed: set[str] = set()
    for index, load_order in enumerate(load_orders):
        load_order = _object(load_order, f"loadOrders[{index}]")
        _exact_keys(load_order, {"loadOrder", "evidenceSha256", "outputSha256", "durationMicroseconds", "parity"}, f"loadOrders[{index}]")
        if load_order["loadOrder"] not in LOAD_ORDERS or load_order["loadOrder"] in observed:
            raise QnnQualificationError("loadOrders must contain both closed load orders once")
        observed.add(load_order["loadOrder"])
        _digest(load_order["evidenceSha256"], "load-order evidence hash")
        _digest(load_order["outputSha256"], "load-order output hash")
        _positive_integer(load_order["durationMicroseconds"], "load-order duration")
        if load_order["parity"] is not True:
            raise QnnQualificationError("load-order output parity must be true")
        if load_order["outputSha256"] != executions["cold"]["outputSha256"]:
            raise QnnQualificationError("load-order output differs from the qualified output")
    if observed != LOAD_ORDERS:
        raise QnnQualificationError("both Dart-first and sherpa-first evidence are required")


def _validate_aligned_receipt(receipt: dict[str, Any], qualification: dict[str, Any]) -> None:
    if receipt.get("schemaVersion") != 1 or receipt.get("result") != "passed":
        raise QnnQualificationError("aligned-build receipt is not a passed schemaVersion 1 record")
    runtime = _object(receipt.get("onnxRuntime"), "aligned-build onnxRuntime")
    if runtime.get("owner") != "application" or runtime.get("linkage") != "external-shared":
        raise QnnQualificationError("aligned-build receipt does not prove application-owned external shared ORT")
    if runtime.get("version") != qualification["runtime"]["ortVersion"] or runtime.get("requiredApi") != qualification["runtime"]["requiredOrtApi"]:
        raise QnnQualificationError("aligned-build ORT version/API does not match qualification")
    abi = qualification["device"]["abi"]
    by_abi = _object(runtime.get("byAbi"), "aligned-build onnxRuntime.byAbi")
    abi_record = _object(by_abi.get(abi), f"aligned-build ORT for {abi}")
    library = _object(abi_record.get("library"), f"aligned-build ORT library for {abi}")
    if library.get("sha256") != qualification["runtime"]["ortSha256"]:
        raise QnnQualificationError("aligned-build ORT hash does not match qualification")
    qnn = _object(receipt.get("qnn"), "aligned-build qnn")
    if qnn.get("manifestSha256") != qualification["qnn"]["sdkManifestSha256"] or qnn.get("backendId") != qualification["qnn"]["backendId"]:
        raise QnnQualificationError("aligned-build QNN SDK/backend does not match qualification")


def _validate_qnn_manifest(
    manifest: dict[str, Any],
    *,
    manifest_hash: str,
    sdk_root: Path,
    qualification: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    _exact_keys(
        manifest,
        {"schemaVersion", "sdkId", "sdkVersion", "source", "backendId", "backendVersion", "license", "artifactsByAbi"},
        "QNN SDK manifest",
    )
    if manifest["schemaVersion"] != 1:
        raise QnnQualificationError("QNN SDK manifest schemaVersion must be 1")
    for key in ("sdkId", "sdkVersion", "backendId", "backendVersion"):
        _token(manifest[key], f"QNN SDK manifest {key}")
    if (
        not isinstance(manifest["source"], str)
        or not manifest["source"].startswith("https://")
        or len(manifest["source"]) > 2048
    ):
        raise QnnQualificationError("QNN SDK manifest source must be bounded HTTPS")
    if manifest_hash != qualification["qnn"]["sdkManifestSha256"]:
        raise QnnQualificationError("QNN SDK manifest hash does not match qualification")
    if manifest["backendId"] != qualification["qnn"]["backendId"]:
        raise QnnQualificationError("QNN SDK backendId does not match qualification")
    license_record = _object(manifest["license"], "QNN license")
    _exact_keys(
        license_record,
        {"id", "spdxId", "redistribution", "notice"},
        "QNN license",
    )
    license_id = _token(license_record["id"], "QNN license.id")
    _token(license_record["spdxId"], "QNN license.spdxId")
    if license_record["redistribution"] not in {
        "prohibited",
        "restricted",
        "permitted",
    }:
        raise QnnQualificationError(
            "QNN license redistribution is outside the closed set"
        )
    notice = _object(license_record["notice"], "QNN license notice")
    _exact_keys(notice, {"path", "sizeBytes", "sha256"}, "QNN license notice")
    notice_relative = _relative_path(notice["path"], "QNN license notice path")
    notice_size = _positive_integer(
        notice["sizeBytes"], "QNN license notice size", MAX_JSON_BYTES
    )
    notice_hash = _digest(notice["sha256"], "QNN license notice hash")
    notice_path = _child_without_symlinks(
        sdk_root, notice_relative, "QNN license notice"
    )
    _regular_file(notice_path, "QNN license notice", MAX_JSON_BYTES)
    if notice_path.stat().st_size != notice_size or _sha256(notice_path) != notice_hash:
        raise QnnQualificationError(
            "QNN license notice identity does not match its SDK manifest"
        )

    artifacts_by_abi = _object(manifest["artifactsByAbi"], "QNN artifactsByAbi")
    abi = qualification["device"]["abi"]
    if (
        not artifacts_by_abi
        or len(artifacts_by_abi) > len(VERIFIER.ANDROID_ABIS)
        or any(key not in VERIFIER.ANDROID_ABIS for key in artifacts_by_abi)
    ):
        raise QnnQualificationError("QNN artifactsByAbi has an invalid ABI set")
    entries = artifacts_by_abi.get(abi)
    if not isinstance(entries, list) or not 0 < len(entries) <= 128:
        raise QnnQualificationError(f"QNN SDK manifest omits artifacts for {abi}")
    backend_entries: list[tuple[str, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        entry = _object(entry, f"QNN artifact {index} for {abi}")
        _exact_keys(entry, {"role", "path", "sizeBytes", "sha256", "licenseId"}, f"QNN artifact {index} for {abi}")
        role = _token(entry["role"], f"QNN artifact {index} role")
        if entry["licenseId"] != license_id:
            raise QnnQualificationError("QNN artifact license ID mismatch")
        relative = _relative_path(entry["path"], "QNN artifact path")
        expected_hash = _digest(entry["sha256"], "QNN artifact hash")
        size = _positive_integer(
            entry["sizeBytes"], "QNN artifact size", MAX_INPUT_BYTES
        )
        path = _child_without_symlinks(
            sdk_root, relative, f"QNN artifact {index} for {abi}"
        )
        _regular_file(path, "QNN backend library", MAX_INPUT_BYTES)
        if path.stat().st_size != size or _sha256(path) != expected_hash:
            raise QnnQualificationError(
                "QNN artifact identity does not match its SDK manifest"
            )
        if role == "backend":
            backend_entries.append((PurePosixPath(relative).name, _identity(path)))
    if len(backend_entries) != 1:
        raise QnnQualificationError(f"QNN SDK manifest must select exactly one backend for {abi}")
    name, identity = backend_entries[0]
    if identity["sha256"] != qualification["qnn"]["backendLibrarySha256"]:
        raise QnnQualificationError("QNN backend hash does not match qualification")
    return name, identity


def _validate_final_apk(path: Path, qualification: dict[str, Any], backend_name: str) -> dict[str, Any]:
    _regular_file(path, "final APK", MAX_INPUT_BYTES)
    if path.suffix.lower() != ".apk":
        raise QnnQualificationError("final application artifact must be an APK")
    if _sha256(path) != qualification["build"]["finalApkSha256"]:
        raise QnnQualificationError("final APK hash does not match qualification")
    try:
        report = VERIFIER.inspect(path)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise QnnQualificationError(f"could not inspect final APK: {error}") from error
    if (
        report["kind"] != "apk"
        or report["duplicate_paths"]
        or report["invalid_archive_paths"]
        or report["invalid_libraries"]
    ):
        raise QnnQualificationError("final APK native inventory is malformed")
    inventoried_ort_paths = sorted(
        entry["path"]
        for entry in report["libraries"]
        if entry["name"] == VERIFIER.ORT_NAME
    )
    if report["ort_candidates"] != inventoried_ort_paths:
        raise QnnQualificationError(
            "final APK contains an ORT outside the loadable native inventory"
        )
    abi = qualification["device"]["abi"]
    def matches(name: str) -> list[dict[str, Any]]:
        return [entry for entry in report["libraries"] if entry["abi"] == abi and entry["name"] == name]
    ort = matches(VERIFIER.ORT_NAME)
    sherpa = matches(VERIFIER.SHERPA_JNI_NAME)
    backend = matches(backend_name)
    if len(ort) != 1 or ort[0]["sha256"] != qualification["runtime"]["ortSha256"]:
        raise QnnQualificationError(f"final APK does not contain the exact sole ORT for {abi}")
    if len(sherpa) != 1 or sherpa[0]["elf"]["needed"].count(VERIFIER.ORT_NAME) != 1:
        raise QnnQualificationError(f"final APK sherpa JNI does not consume the exact shared ORT contract for {abi}")
    if len(backend) != 1 or backend[0]["sha256"] != qualification["qnn"]["backendLibrarySha256"]:
        raise QnnQualificationError(f"final APK does not contain the exact QNN backend for {abi}")
    return _identity(path)


def _validate_assignment_evidence(path: Path, qualification: dict[str, Any]) -> dict[str, Any]:
    evidence, evidence_hash = _read_json(path, "QNN assignment evidence")
    if evidence_hash != qualification["assignment"]["evidenceSha256"]:
        raise QnnQualificationError("QNN assignment evidence hash does not match qualification")
    _exact_keys(evidence, {"schemaVersion", "result", "abi", "providerOptionsSha256", "fallbackObserved", "nodes"}, "QNN assignment evidence")
    if evidence["schemaVersion"] != 1 or evidence["result"] != "passed":
        raise QnnQualificationError("QNN assignment evidence is not passed schemaVersion 1")
    if evidence["abi"] != qualification["device"]["abi"] or evidence["providerOptionsSha256"] != qualification["qnn"]["providerOptionsSha256"]:
        raise QnnQualificationError("QNN assignment evidence targets another ABI/options tuple")
    if evidence["fallbackObserved"] is not False:
        raise QnnQualificationError("QNN assignment evidence observed fallback")
    nodes = evidence["nodes"]
    if not isinstance(nodes, list) or not 0 < len(nodes) <= MAX_NODES:
        raise QnnQualificationError("QNN assignment node list is outside its bound")
    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        node = _object(node, f"QNN assignment node {index}")
        _exact_keys(node, {"nodeId", "provider"}, f"QNN assignment node {index}")
        node_id = _token(node["nodeId"], "QNN assignment node ID")
        if node_id in node_ids:
            raise QnnQualificationError("QNN assignment node IDs must be unique")
        node_ids.add(node_id)
        if node["provider"] != "QNNExecutionProvider":
            raise QnnQualificationError("QNN assignment evidence contains a non-QNN node")
    if len(nodes) != qualification["assignment"]["totalNodes"]:
        raise QnnQualificationError("QNN assignment node count does not match qualification")
    return {"fileName": path.name, "sizeBytes": path.stat().st_size, "sha256": evidence_hash}


def _validate_execution_evidence(
    path: Path,
    qualification: dict[str, Any],
    *,
    phase: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    evidence, evidence_hash = _read_json(path, f"{phase} execution evidence")
    if evidence_hash != expected["evidenceSha256"]:
        raise QnnQualificationError(f"{phase} evidence hash does not match qualification")
    _exact_keys(
        evidence,
        {
            "schemaVersion",
            "result",
            "phase",
            "abi",
            "finalApkSha256",
            "outputSha256",
            "referenceOutputSha256",
            "durationMicroseconds",
            "providerOptionsSha256",
            "assignmentEvidenceSha256",
        },
        f"{phase} execution evidence",
    )
    if evidence["schemaVersion"] != 1 or evidence["result"] != "passed" or evidence["phase"] != phase:
        raise QnnQualificationError(f"{phase} execution evidence has wrong identity/result")
    bindings = {
        "abi": qualification["device"]["abi"],
        "finalApkSha256": qualification["build"]["finalApkSha256"],
        "outputSha256": expected["outputSha256"],
        "referenceOutputSha256": qualification["model"]["referenceOutputSha256"],
        "durationMicroseconds": expected["durationMicroseconds"],
        "providerOptionsSha256": qualification["qnn"]["providerOptionsSha256"],
        "assignmentEvidenceSha256": qualification["assignment"]["evidenceSha256"],
    }
    if any(evidence[key] != value for key, value in bindings.items()):
        raise QnnQualificationError(f"{phase} execution evidence binding mismatch")
    return {"fileName": path.name, "sizeBytes": path.stat().st_size, "sha256": evidence_hash}


def _validate_invalidation_evidence(path: Path, qualification: dict[str, Any]) -> dict[str, Any]:
    evidence, evidence_hash = _read_json(path, "context-cache invalidation evidence")
    context = qualification["contextCache"]
    if evidence_hash != context["invalidationEvidenceSha256"]:
        raise QnnQualificationError("context-cache invalidation evidence hash mismatch")
    _exact_keys(evidence, {"schemaVersion", "result", "changedInput", "oldKey", "newKey", "staleCacheRejected"}, "context-cache invalidation evidence")
    if evidence["schemaVersion"] != 1 or evidence["result"] != "passed":
        raise QnnQualificationError("context-cache invalidation evidence is not passed schemaVersion 1")
    if evidence["changedInput"] != context["changedInput"] or evidence["oldKey"] != context["key"]:
        raise QnnQualificationError("context-cache invalidation evidence binding mismatch")
    if not isinstance(evidence["newKey"], str) or not evidence["newKey"].startswith("fonix-qnn-v1-") or evidence["newKey"] == evidence["oldKey"] or SHA256.fullmatch(evidence["newKey"][len("fonix-qnn-v1-"):]) is None:
        raise QnnQualificationError("context-cache invalidation did not derive a distinct valid key")
    if evidence["staleCacheRejected"] is not True:
        raise QnnQualificationError("context-cache invalidation evidence accepted stale cache")
    return {"fileName": path.name, "sizeBytes": path.stat().st_size, "sha256": evidence_hash}


def validate(arguments: argparse.Namespace) -> dict[str, Any]:
    receipt_path = arguments.receipt.resolve(strict=True)
    receipt, receipt_hash = _read_json(receipt_path, "QNN qualification receipt")
    _validate_receipt(receipt)

    file_bindings = {
        "model": _validate_file_hash(arguments.model.resolve(strict=True), receipt["model"]["onnxSha256"], "ONNX model", MAX_INPUT_BYTES),
        "qdqConfig": _validate_file_hash(arguments.qdq_config.resolve(strict=True), receipt["model"]["qdqConfigSha256"], "QDQ configuration", MAX_JSON_BYTES),
        "inputFixture": _validate_file_hash(arguments.input_fixture.resolve(strict=True), receipt["model"]["inputFixtureSha256"], "input fixture", MAX_INPUT_BYTES),
        "referenceOutput": _validate_file_hash(arguments.reference_output.resolve(strict=True), receipt["model"]["referenceOutputSha256"], "reference output", MAX_INPUT_BYTES),
    }

    aligned_path = arguments.aligned_build_receipt.resolve(strict=True)
    aligned, aligned_hash = _read_json(aligned_path, "aligned-build receipt")
    if aligned_hash != receipt["build"]["alignedBuildReceiptSha256"]:
        raise QnnQualificationError("aligned-build receipt hash does not match qualification")
    _validate_aligned_receipt(aligned, receipt)

    qnn_manifest_path = arguments.qnn_sdk_manifest.resolve(strict=True)
    qnn_manifest, qnn_manifest_hash = _read_json(qnn_manifest_path, "QNN SDK manifest")
    qnn_root = _directory(arguments.qnn_root.resolve(strict=True), "QNN SDK root")
    backend_name, backend_identity = _validate_qnn_manifest(
        qnn_manifest,
        manifest_hash=qnn_manifest_hash,
        sdk_root=qnn_root,
        qualification=receipt,
    )
    final_identity = _validate_final_apk(arguments.final_apk.resolve(strict=True), receipt, backend_name)

    key_inputs = {
        "modelSha256": receipt["model"]["onnxSha256"],
        "qdqConfigSha256": receipt["model"]["qdqConfigSha256"],
        "ortVersion": receipt["runtime"]["ortVersion"],
        "ortSha256": receipt["runtime"]["ortSha256"],
        "qnnSdkManifestSha256": receipt["qnn"]["sdkManifestSha256"],
        "qnnBackendSha256": receipt["qnn"]["backendLibrarySha256"],
        "providerOptionsSha256": receipt["qnn"]["providerOptionsSha256"],
        "socToken": receipt["device"]["socToken"],
        "firmwareSha256": receipt["device"]["firmwareSha256"],
        "driverToken": receipt["device"]["driverToken"],
    }
    if _canonical_hash(key_inputs) != receipt["contextCache"]["keyInputsSha256"]:
        raise QnnQualificationError("context-cache key inputs do not match the qualified tuple")

    assignment_binding = _validate_assignment_evidence(
        arguments.assignment_evidence.resolve(strict=True), receipt
    )
    cold_binding = _validate_execution_evidence(
        arguments.cold_evidence.resolve(strict=True),
        receipt,
        phase="cold",
        expected=receipt["executions"]["cold"],
    )
    cache_hit_binding = _validate_execution_evidence(
        arguments.cache_hit_evidence.resolve(strict=True),
        receipt,
        phase="cache-hit",
        expected=receipt["executions"]["cacheHit"],
    )
    cache_artifact_binding = _validate_file_hash(
        arguments.context_cache_artifact.resolve(strict=True),
        receipt["contextCache"]["cacheArtifactSha256"],
        "QNN context-cache artifact",
        MAX_INPUT_BYTES,
    )
    invalidation_binding = _validate_invalidation_evidence(
        arguments.invalidation_evidence.resolve(strict=True), receipt
    )

    load_paths: dict[str, Path] = {}
    for raw in arguments.load_order_evidence:
        key, separator, value = raw.partition("=")
        if not separator or key not in LOAD_ORDERS or key in load_paths or not Path(value).is_absolute():
            raise QnnQualificationError("--load-order-evidence must provide each LOAD_ORDER=/absolute/path once")
        load_paths[key] = Path(value)
    if set(load_paths) != LOAD_ORDERS:
        raise QnnQualificationError("both load-order evidence files are required")
    expected_by_order = {entry["loadOrder"]: entry for entry in receipt["loadOrders"]}
    load_bindings = {
        order: _validate_execution_evidence(
            load_paths[order].resolve(strict=True),
            receipt,
            phase=order,
            expected=expected_by_order[order],
        )
        for order in sorted(LOAD_ORDERS)
    }

    return {
        "schemaVersion": 1,
        "result": "passed",
        "validatorSha256": _sha256(Path(__file__)),
        "qualificationReceiptSha256": receipt_hash,
        "build": {**receipt["build"], "finalApk": final_identity},
        "model": receipt["model"],
        "runtime": receipt["runtime"],
        "qnn": {**receipt["qnn"], "backendLibrary": backend_identity},
        "device": receipt["device"],
        "assignment": receipt["assignment"],
        "executions": receipt["executions"],
        "contextCache": receipt["contextCache"],
        "loadOrders": sorted(receipt["loadOrders"], key=lambda value: value["loadOrder"]),
        "evidenceBindings": {
            **file_bindings,
            "alignedBuildReceipt": {"fileName": aligned_path.name, "sizeBytes": aligned_path.stat().st_size, "sha256": aligned_hash},
            "qnnSdkManifest": {"fileName": qnn_manifest_path.name, "sizeBytes": qnn_manifest_path.stat().st_size, "sha256": qnn_manifest_hash},
            "assignment": assignment_binding,
            "cold": cold_binding,
            "cacheHit": cache_hit_binding,
            "contextCacheArtifact": cache_artifact_binding,
            "invalidation": invalidation_binding,
            "loadOrders": load_bindings,
        },
        "claimBoundary": (
            "This record validates QNN assignment, output parity, timings, context-cache "
            "invalidation, and both load orders only for the exact APK/model/runtime/SDK/"
            "device/firmware/options/evidence tuple above. It is not transferable to "
            "another build, model, device, firmware, provider option, or SDK artifact."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--aligned-build-receipt", type=Path, required=True)
    parser.add_argument("--qnn-sdk-manifest", type=Path, required=True)
    parser.add_argument("--qnn-root", type=Path, required=True)
    parser.add_argument("--final-apk", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--qdq-config", type=Path, required=True)
    parser.add_argument("--input-fixture", type=Path, required=True)
    parser.add_argument("--reference-output", type=Path, required=True)
    parser.add_argument("--assignment-evidence", type=Path, required=True)
    parser.add_argument("--cold-evidence", type=Path, required=True)
    parser.add_argument("--cache-hit-evidence", type=Path, required=True)
    parser.add_argument("--context-cache-artifact", type=Path, required=True)
    parser.add_argument("--invalidation-evidence", type=Path, required=True)
    parser.add_argument("--load-order-evidence", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise QnnQualificationError("--output must be absolute")
    if path.exists() or path.is_symlink():
        raise QnnQualificationError("--output must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise QnnQualificationError("temporary output already exists")
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
    except (QnnQualificationError, FileNotFoundError, OSError, UnicodeError) as error:
        print(f"validate_qnn_qualification_receipt: {error}", file=sys.stderr)
        return 1
    print(f"Wrote QNN qualification record: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
