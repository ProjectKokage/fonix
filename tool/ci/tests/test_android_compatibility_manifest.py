from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/android_compatibility_manifest.py"
LOAD_ORDER_VALIDATOR = (
    REPOSITORY / "tool/ci/validate_android_load_order_receipt.py"
)
LOAD_ORDER_RECEIPT_SCHEMA = (
    REPOSITORY / "templates/android/load_order_receipt.schema.json"
)
NATIVE_VERIFIER = REPOSITORY / "templates/android/verify_native_libs.py"
_MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_compatibility_manifest_test_target", SCRIPT
)
assert _MANIFEST_SPEC is not None and _MANIFEST_SPEC.loader is not None
MANIFEST = importlib.util.module_from_spec(_MANIFEST_SPEC)
sys.modules[_MANIFEST_SPEC.name] = MANIFEST
_MANIFEST_SPEC.loader.exec_module(MANIFEST)
ELF_TESTS = REPOSITORY / "tool/tests/test_verify_native_libs.py"
_ELF_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_elf_test_fixture", ELF_TESTS
)
assert _ELF_SPEC is not None and _ELF_SPEC.loader is not None
ELF_FIXTURE = importlib.util.module_from_spec(_ELF_SPEC)
_ELF_SPEC.loader.exec_module(ELF_FIXTURE)
VALIDATOR_TESTS = REPOSITORY / "tool/ci/tests/test_validate_android_load_order_receipt.py"
_VALIDATOR_TEST_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_load_order_validator_integration_fixture", VALIDATOR_TESTS
)
assert (
    _VALIDATOR_TEST_SPEC is not None
    and _VALIDATOR_TEST_SPEC.loader is not None
)
VALIDATOR_TEST_FIXTURE = importlib.util.module_from_spec(_VALIDATOR_TEST_SPEC)
_VALIDATOR_TEST_SPEC.loader.exec_module(VALIDATOR_TEST_FIXTURE)

EXPECTED_SHERPA_PROFILE = {
    "provider": "cpu",
    "sampleRateHz": 16000,
    "windowSamples": 512,
    "numThreads": 1,
    "thresholdMillionths": 500000,
    "minimumSpeechMilliseconds": 250,
    "minimumSilenceMilliseconds": 800,
    "maximumSpeechMilliseconds": 30000,
    "bufferMilliseconds": 60000,
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AndroidCompatibilityManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.abi = "arm64-v8a"
        self.ort = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        self.sherpa_jni = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-jni.so",
            needed=("libonnxruntime.so", "libc.so"),
        )
        self.sherpa_c_api = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-c-api.so",
            needed=("libonnxruntime.so", "libdl.so", "libc.so"),
        )
        self.sherpa_cxx_api = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-cxx-api.so",
            needed=(
                "libsherpa-onnx-c-api.so",
                "libonnxruntime.so",
                "libdl.so",
                "libc.so",
            ),
        )
        self.shim = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libfonix_shim.so",
            needed=("libdl.so", "libc.so"),
        )
        self.flutter_aot = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname=None,
            needed=("libc.so",),
        )
        self.sherpa = self._native_directory(
            "sherpa-jniLibs",
            {
                f"{self.abi}/libonnxruntime.so": self.ort,
                f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
            },
        )
        self.wrapper = self._native_directory(
            "fonix-native-assets",
            {f"{self.abi}/libfonix_shim.so": self.shim},
        )
        self.final = self._archive(
            "app-release.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        self.jni_final = self._archive(
            "app-jni-release.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )

    def _archive(self, name: str, entries: dict[str, bytes]) -> Path:
        output = self.root / name
        with zipfile.ZipFile(output, "w") as archive:
            for path, contents in entries.items():
                archive.writestr(path, contents)
        return output

    def _native_directory(self, name: str, entries: dict[str, bytes]) -> Path:
        output = self.root / name
        for path, contents in entries.items():
            destination = output / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents)
        return output

    def _libcxx_case(
        self,
        name: str,
        *,
        source_owner: bool = True,
        final_owner: bool = True,
        consumer_uses_owner: bool = True,
        duplicate_wrapper_owner: bool = False,
        final_owner_bytes: bytes | None = None,
    ) -> tuple[Path, Path, Path, bytes]:
        libcxx = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libc++_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        cxx_needed = [
            "libsherpa-onnx-c-api.so",
            "libonnxruntime.so",
            "libdl.so",
            "libc.so",
        ]
        if consumer_uses_owner:
            cxx_needed.insert(2, "libc++_shared.so")
        cxx = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-cxx-api.so",
            needed=tuple(cxx_needed),
        )
        sherpa_entries = {
            f"{self.abi}/libonnxruntime.so": self.ort,
            f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
            f"{self.abi}/libsherpa-onnx-cxx-api.so": cxx,
        }
        if source_owner:
            sherpa_entries[f"{self.abi}/libc++_shared.so"] = libcxx
        sherpa = self._native_directory(f"{name}-sherpa", sherpa_entries)
        wrapper_entries = {f"{self.abi}/libfonix_shim.so": self.shim}
        if duplicate_wrapper_owner:
            wrapper_entries[f"{self.abi}/libc++_shared.so"] = libcxx
        wrapper = self._native_directory(f"{name}-wrapper", wrapper_entries)
        final_entries = {
            "AndroidManifest.xml": b"manifest",
            f"lib/{self.abi}/libapp.so": self.flutter_aot,
            f"lib/{self.abi}/libonnxruntime.so": self.ort,
            f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
            f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": cxx,
            f"lib/{self.abi}/libfonix_shim.so": self.shim,
        }
        if final_owner:
            final_entries[f"lib/{self.abi}/libc++_shared.so"] = (
                final_owner_bytes or libcxx
            )
        final = self._archive(f"{name}.apk", final_entries)
        return sherpa, wrapper, final, libcxx

    def _receipt(
        self,
        *,
        load_order: str,
        page_size: int,
        index: int,
        build_type: str = "release-minified",
        final: Path | None = None,
        ort: bytes | None = None,
        mode: str = "sherpa-owned",
        profile: str = "flutter-ffi",
        abi: str | None = None,
    ) -> Path:
        final_artifact = final or self.final
        runtime = ort or self.ort
        selected_abi = abi or self.abi
        challenge_hash = _sha256_bytes(f"challenge-{selected_abi}-{index}".encode())
        target_hash = _sha256_bytes(f"target-{selected_abi}-{index}".encode())
        logcat_hash = _sha256_bytes(f"logcat-{selected_abi}-{index}".encode())
        receipt_hash = _sha256_bytes(f"receipt-{selected_abi}-{index}".encode())
        harness_hash = "a" * 64
        pubspec_hash = "b" * 64
        fixture_hashes = {
            "fonixModelSha256": "1" * 64,
            "fonixInputSha256": "2" * 64,
            "fonixReferenceOutputSha256": _sha256_bytes(
                b"\x00\x00\x80?\x00\x00\x00@"
            ),
            "fonixCancellationModelSha256": "4" * 64,
            "fonixCancellationInputSha256": "5" * 64,
            "sherpaModelSha256": "6" * 64,
            "sherpaAudioSha256": "7" * 64,
            "sherpaReferenceSha256": "8" * 64,
        }

        def identity(sha256: str, size: int = 1) -> dict[str, object]:
            return {"sizeBytes": size, "sha256": sha256}

        fonix_reference = b"\x00\x00\x80?\x00\x00\x00@"
        fonix_reference_base64 = base64.b64encode(fonix_reference).decode("ascii")

        def fonix_observation(ordinal: int | None = None) -> dict[str, object]:
            value: dict[str, object] = {
                "outputEncoding": "float32-le",
                "outputBytesBase64": fonix_reference_base64,
                "outputSha256": fixture_hashes["fonixReferenceOutputSha256"],
            }
            if ordinal is not None:
                value.update({"ordinal": ordinal, "engine": "fonix"})
            return value

        def sherpa_observation(ordinal: int | None = None) -> dict[str, object]:
            value: dict[str, object] = {
                "sourceSamples": 16000,
                "submittedSamples": 16384,
                "segments": [{"startSample": 1000, "sampleCount": 4000}],
                "queueEmptyAfterDrain": True,
                "detectedAfterDrain": False,
            }
            if ordinal is not None:
                value.update({"ordinal": ordinal, "engine": "sherpa"})
            return value

        final_report = MANIFEST.VERIFIER.inspect(final_artifact)

        def native_library(name: str) -> dict[str, object]:
            matches = [
                entry
                for entry in final_report["libraries"]
                if entry["abi"] == selected_abi and entry["name"] == name
            ]
            self.assertEqual(len(matches), 1, (selected_abi, name))
            entry = matches[0]
            return {
                "sizeBytes": entry["size"],
                "sha256": entry["sha256"],
                "soname": entry["elf"]["soname"],
                "needed": entry["elf"]["needed"],
                "pageSize16KiBCompatible": entry["elf"][
                    "pageSize16KiBCompatible"
                ],
            }

        native_libraries = {
            "onnxRuntime": native_library("libonnxruntime.so"),
            "fonixShim": native_library("libfonix_shim.so"),
        }
        if profile == "flutter-ffi":
            native_libraries.update(
                {
                    "sherpaCapi": native_library(
                        "libsherpa-onnx-c-api.so"
                    ),
                    "sherpaCxxApi": native_library(
                        "libsherpa-onnx-cxx-api.so"
                    ),
                }
            )
        else:
            native_libraries["sherpaJni"] = native_library(
                "libsherpa-onnx-jni.so"
            )

        owner = "sherpa" if mode == "sherpa-owned" else "application"
        source = "process" if mode == "sherpa-owned" else "bundled"
        receipt = {
            "schemaVersion": 1,
            "result": "passed",
            "claimStatus": "offline-consistency-only",
            "targetEvidenceProvenance": "unverified",
            "validatorSha256": _sha256_file(LOAD_ORDER_VALIDATOR),
            "receiptSchemaSha256": _sha256_file(LOAD_ORDER_RECEIPT_SCHEMA),
            "nativeVerifierSha256": _sha256_file(NATIVE_VERIFIER),
            "loadOrderReceiptSha256": receipt_hash,
            "matrix": {
                "abi": selected_abi,
                "loadOrder": load_order,
                "buildType": build_type,
                "pageSizeBytes": page_size,
            },
            "build": {
                "applicationId": "dev.fonix.compatibility",
                "finalApkSha256": _sha256_file(final_artifact),
                "harnessContractSha256": harness_hash,
                "pubspecLockSha256": pubspec_hash,
                "targetEvidenceSha256": target_hash,
                "logcatEvidenceSha256": logcat_hash,
            },
            "device": {
                "kind": "physical" if page_size == 4096 else "emulator",
                "modelToken": f"fixture-{index}",
                "androidApi": 35,
                "fingerprintSha256": "c" * 64,
            },
            "process": {
                "launchChallengeSha256": challenge_hash,
                "uid": {
                    "packageManager": 10123,
                    "logcatFilter": 10123,
                    "receiptLine": 10123,
                },
                "pid": {
                    "beforeLaunch": None,
                    "observedAfterLaunch": 4000 + index,
                    "logcatFilter": 4000 + index,
                    "receiptLine": 4000 + index,
                    "afterReceipt": 4000 + index,
                    "afterForceStop": None,
                },
            },
            "runtime": {
                "runtimeOwner": owner,
                "runtimeSource": source,
                "ortVersion": "1.27.0",
                "requiredOrtApi": 27,
                "negotiatedOrtApi": 27,
                "ortSha256": _sha256_bytes(runtime),
                "shimAbi": 1,
                "shimBuildId": f"android-owner-{owner}-source-{source}",
            },
            "sherpa": {
                "packageVersion": "1.13.4",
                "nativeVersion": "1.13.4",
                "sourceRevision": "d" * 40,
                "nativeRevision": "d" * 12,
                "profile": {
                    "id": "silero-vad-load-order-v1",
                    **EXPECTED_SHERPA_PROFILE,
                },
            },
            "fixtures": fixture_hashes,
            "initialization": {
                "events": (
                    ["fonix-session-ready", "sherpa-vad-ready"]
                    if load_order == "dart-first"
                    else ["sherpa-vad-ready", "fonix-session-ready"]
                ),
                "firstOwnerAliveWhenSecondReady": True,
            },
            "workload": {
                "requestedCycles": 2,
                "completedCycles": 2,
                "steps": [
                    fonix_observation(1),
                    sherpa_observation(2),
                    fonix_observation(3),
                    sherpa_observation(4),
                ],
            },
            "lifecycle": {
                "fonixCancellation": {
                    "mode": "active-native-termination",
                    "requestCount": 1,
                    "nativeRequestAcceptedCount": 1,
                    "settlementCount": 1,
                    "cancelledResultCount": 1,
                    "publishedOutputCount": 0,
                    "outstandingRunsAfterSettlement": 0,
                    "settledBeforeRecovery": True,
                },
                "sherpaCancellation": {
                    "mode": "between-bounded-frames",
                    "requestCount": 1,
                    "framesAcceptedBeforeRequest": 2,
                    "framesAcceptedAfterRequest": 0,
                    "flushCallsAfterRequest": 0,
                    "segmentsPublishedAfterRequest": 0,
                    "detectorRetiredBeforeRecovery": True,
                },
                "staleCompletion": {
                    "inducedCount": 1,
                    "observedCount": 1,
                    "suppressedCount": 1,
                    "publishedOutputCount": 0,
                    "retiredGeneration": 1,
                    "authoritativeGeneration": 2,
                },
                "recovery": {
                    "fonixOutputEncoding": "float32-le",
                    "fonixOutputBytesBase64": fonix_reference_base64,
                    "fonixOutputSha256": fixture_hashes[
                        "fonixReferenceOutputSha256"
                    ],
                    "sherpa": sherpa_observation(),
                },
                "disposal": {
                    "ordersTested": [
                        "fonix-then-sherpa",
                        "sherpa-then-fonix",
                    ],
                    "fonixSessionsCreated": 2,
                    "fonixSessionsClosed": 2,
                    "sherpaDetectorsCreated": 2,
                    "sherpaDetectorsFreed": 2,
                    "fonixDoubleClose": "passed",
                    "sherpaDoubleFree": "passed",
                    "pendingFonixRuns": 0,
                    "queuedSherpaSegments": 0,
                    "temporaryRootsCreated": 2,
                    "temporaryRootsRemoved": 2,
                    "temporaryRootsRemaining": 0,
                },
            },
            "evidenceBindings": {
                "receipt": identity(receipt_hash),
                "receiptSchema": identity(
                    _sha256_file(LOAD_ORDER_RECEIPT_SCHEMA),
                    LOAD_ORDER_RECEIPT_SCHEMA.stat().st_size,
                ),
                "nativeVerifier": identity(
                    _sha256_file(NATIVE_VERIFIER), NATIVE_VERIFIER.stat().st_size
                ),
                "finalApk": identity(
                    _sha256_file(final_artifact), final_artifact.stat().st_size
                ),
                "harnessContract": identity(harness_hash),
                "pubspecLock": identity(pubspec_hash),
                "targetEvidence": identity(target_hash),
                "logcatEvidence": identity(logcat_hash),
                "launchChallenge": identity(challenge_hash),
                "fonixModel": identity(fixture_hashes["fonixModelSha256"]),
                "fonixInput": identity(fixture_hashes["fonixInputSha256"]),
                "fonixReferenceOutput": identity(
                    fixture_hashes["fonixReferenceOutputSha256"],
                    len(fonix_reference),
                ),
                "fonixCancellationModel": identity(
                    fixture_hashes["fonixCancellationModelSha256"]
                ),
                "fonixCancellationInput": identity(
                    fixture_hashes["fonixCancellationInputSha256"]
                ),
                "sherpaModel": identity(fixture_hashes["sherpaModelSha256"]),
                "sherpaAudio": identity(fixture_hashes["sherpaAudioSha256"]),
                "sherpaReference": identity(
                    fixture_hashes["sherpaReferenceSha256"]
                ),
            },
            "nativeLibraries": native_libraries,
            "claimBoundary": "Synthetic validator-output fixture.",
        }
        path = self.root / f"receipt-{index}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def _receipts(
        self,
        *,
        final: Path | None = None,
        ort: bytes | None = None,
        mode: str = "sherpa-owned",
        profile: str = "flutter-ffi",
        abi: str | None = None,
        index_offset: int = 0,
    ) -> list[Path]:
        return [
            self._receipt(
                load_order="dart-first",
                page_size=4096,
                index=index_offset,
                final=final,
                ort=ort,
                mode=mode,
                profile=profile,
                abi=abi,
            ),
            self._receipt(
                load_order="sherpa-first",
                page_size=4096,
                index=index_offset + 1,
                final=final,
                ort=ort,
                mode=mode,
                profile=profile,
                abi=abi,
            ),
            self._receipt(
                load_order="dart-first",
                page_size=16384,
                index=index_offset + 2,
                final=final,
                ort=ort,
                mode=mode,
                profile=profile,
                abi=abi,
            ),
            self._receipt(
                load_order="sherpa-first",
                page_size=16384,
                index=index_offset + 3,
                final=final,
                ort=ort,
                mode=mode,
                profile=profile,
                abi=abi,
            ),
        ]

    def _arguments(
        self,
        receipts: list[Path],
        *,
        mode: str = "sherpa-owned",
        profile: str | None = "flutter-ffi",
        sherpa: Path | list[Path] | None = None,
        wrapper: Path | list[Path] | None = None,
        runtime: Path | list[Path] | None = None,
        final: Path | None = None,
        abis: list[str] | None = None,
        output_name: str = "compatibility.json",
    ) -> list[str]:
        arguments = [
            sys.executable,
            str(SCRIPT),
            "--mode",
            mode,
            "--sherpa-source",
            "https://github.com/k2-fsa/sherpa-onnx",
            "--sherpa-revision",
            "d" * 40,
            "--final-artifact",
            str(final or self.final),
            "--ort-version-observed",
            "1.27.0",
            "--ort-api-required",
            "27",
            "--build-type",
            "release-minified",
            "--snapshot-date",
            "2026-08-06",
            "--output",
            str(self.root / output_name),
        ]
        for abi in abis or [self.abi]:
            arguments.extend(("--abi", abi))
        if profile is not None:
            arguments.extend(("--sherpa-library-profile", profile))
        sherpa_artifacts = sherpa or self.sherpa
        if isinstance(sherpa_artifacts, Path):
            sherpa_artifacts = [sherpa_artifacts]
        for artifact in sherpa_artifacts:
            arguments.extend(("--sherpa-artifact", str(artifact)))
        wrapper_artifacts = wrapper or self.wrapper
        if isinstance(wrapper_artifacts, Path):
            wrapper_artifacts = [wrapper_artifacts]
        for artifact in wrapper_artifacts:
            arguments.extend(("--wrapper-artifact", str(artifact)))
        if runtime is not None:
            runtime_artifacts = [runtime] if isinstance(runtime, Path) else runtime
            for artifact in runtime_artifacts:
                arguments.extend(("--runtime-artifact", str(artifact)))
        for receipt in receipts:
            arguments.extend(("--load-order-validation-record", str(receipt)))
        return arguments

    def _qnn_qualification_record(
        self,
        *,
        final: Path | None = None,
        ort: bytes | None = None,
        build_type: str = "release-minified",
    ) -> Path:
        final_artifact = final or self.final
        runtime = ort or self.ort
        value = {
            "schemaVersion": 1,
            "result": "passed",
            "validatorSha256": "0" * 64,
            "qualificationReceiptSha256": "1" * 64,
            "build": {
                "buildType": build_type,
                "finalApkSha256": _sha256_file(final_artifact),
                "alignedBuildReceiptSha256": "2" * 64,
                "finalApk": {
                    "fileName": final_artifact.name,
                    "sizeBytes": final_artifact.stat().st_size,
                    "sha256": _sha256_file(final_artifact),
                },
            },
            "model": {"onnxSha256": "3" * 64},
            "runtime": {
                "ortVersion": "1.27.0",
                "requiredOrtApi": 27,
                "ortSha256": _sha256_bytes(runtime),
            },
            "qnn": {
                "sdkManifestSha256": "4" * 64,
                "backendId": "synthetic-htp",
                "backendLibrarySha256": "5" * 64,
                "providerOptionsSha256": "6" * 64,
            },
            "device": {"abi": self.abi},
            "assignment": {
                "totalNodes": 2,
                "qnnNodes": 2,
                "cpuNodes": 0,
                "fallbackPolicy": "reject-cpu",
            },
            "executions": {},
            "contextCache": {},
            "loadOrders": [
                {"loadOrder": "dart-first", "parity": True},
                {"loadOrder": "sherpa-first", "parity": True},
            ],
            "evidenceBindings": {},
            "claimBoundary": "Synthetic test-only QNN qualification boundary.",
        }
        path = self.root / "qnn-qualification.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_generates_reproducible_lockable_manifest(self) -> None:
        receipts = self._receipts()
        first = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        output = self.root / "compatibility.json"
        first_bytes = output.read_bytes()
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        manifest = json.loads(first_bytes)
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["android"]["ortOwner"], "sherpa")
        self.assertEqual(manifest["sherpaOnnx"]["libraryProfile"], "flutter-ffi")
        self.assertEqual(
            manifest["sherpaOnnx"]["artifacts"][0]["digestScope"],
            "loadable-native-library-inventory-v1",
        )
        self.assertEqual(
            manifest["android"]["librariesByAbi"][self.abi]["onnxruntime"][
                "final"
            ]["sha256"],
            _sha256_bytes(self.ort),
        )
        source_artifact_sha = manifest["android"]["librariesByAbi"][self.abi][
            "onnxruntime"
        ]["source"]["artifactSha256"]
        self.assertIn(
            source_artifact_sha,
            {
                artifact["sha256"]
                for artifact in manifest["sherpaOnnx"]["artifacts"]
            },
        )
        self.assertEqual(len(manifest["android"]["loadOrderEvidence"]), 4)
        self.assertNotIn(str(self.root), first_bytes.decode("utf-8"))

        second = subprocess.run(
            self._arguments(receipts, output_name="compatibility-second.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            (self.root / "compatibility-second.json").read_bytes(), first_bytes
        )

    def test_schema_two_output_shape_is_closed_by_regression(self) -> None:
        result = subprocess.run(
            self._arguments(self._receipts()),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "compatibility.json").read_bytes())
        self.assertEqual(
            set(manifest),
            {
                "schemaVersion",
                "claimStatus",
                "snapshotDate",
                "sherpaOnnx",
                "android",
                "claimBoundary",
            },
        )
        self.assertEqual(
            set(manifest["sherpaOnnx"]),
            {"source", "revision", "libraryProfile", "artifacts"},
        )
        android = manifest["android"]
        self.assertEqual(
            set(android),
            {
                "integrationMode",
                "buildType",
                "abis",
                "ortOwner",
                "ortVersionObserved",
                "ortApiRequired",
                "artifacts",
                "librariesByAbi",
                "loadOrderEvidence",
                "qnnQualification",
            },
        )
        self.assertEqual(
            set(android["artifacts"]),
            {"wrapperInputs", "runtimeOwnerInputs", "final"},
        )
        libraries = android["librariesByAbi"][self.abi]
        self.assertEqual(
            set(libraries),
            {
                "onnxruntime",
                "sherpaRuntimeConsumers",
                "fonixShim",
                "libcxxShared",
                "companionLibraries",
            },
        )
        evidence = android["loadOrderEvidence"][0]
        self.assertEqual(
            set(evidence),
            {
                "validationRecord",
                "claimStatus",
                "targetEvidenceProvenance",
                "validatorSha256",
                "receiptSchemaSha256",
                "nativeVerifierSha256",
                "loadOrderReceiptSha256",
                "matrix",
                "build",
                "device",
                "process",
                "runtime",
                "sherpa",
                "fixtures",
                "initialization",
                "workload",
                "lifecycle",
                "evidenceBindings",
                "nativeLibraries",
                "claimBoundary",
            },
        )
        self.assertEqual(
            set(evidence["evidenceBindings"]), MANIFEST.EVIDENCE_BINDING_KEYS
        )

    def test_accepts_records_actually_emitted_by_current_validator(self) -> None:
        fixture = VALIDATOR_TEST_FIXTURE.AndroidLoadOrderReceiptTest(
            methodName="test_accepts_complete_evidence_and_output_is_path_free_deterministic"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        records: list[Path] = []
        for index, (load_order, page_size) in enumerate(
            (
                ("dart-first", 4096),
                ("sherpa-first", 4096),
                ("dart-first", 16384),
                ("sherpa-first", 16384),
            )
        ):
            challenge = f"validator-integration-{index}\n".encode()
            fixture.launch_challenge.write_bytes(challenge)
            fixture.receipt["process"]["launchChallengeSha256"] = _sha256_bytes(
                challenge
            )
            fixture.receipt["matrix"].update(
                {"loadOrder": load_order, "pageSizeBytes": page_size}
            )
            fixture.receipt["device"]["modelToken"] = f"validator-device-{index}"
            fixture.receipt["initialization"]["events"] = (
                ["fonix-session-ready", "sherpa-vad-ready"]
                if load_order == "dart-first"
                else ["sherpa-vad-ready", "fonix-session-ready"]
            )
            fixture._rewrite_evidence()
            output = fixture.root / f"manifest-input-{index}.json"
            result = fixture._run(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            records.append(output)

        sherpa = self._native_directory(
            "validator-output-sherpa",
            {
                f"{self.abi}/libonnxruntime.so": fixture.ort_bytes,
                f"{self.abi}/libsherpa-onnx-c-api.so": fixture.sherpa_capi_bytes,
                f"{self.abi}/libsherpa-onnx-cxx-api.so": fixture.sherpa_cxx_bytes,
            },
        )
        wrapper = self._native_directory(
            "validator-output-wrapper",
            {f"{self.abi}/libfonix_shim.so": fixture.shim_bytes},
        )
        arguments = self._arguments(
            records,
            sherpa=sherpa,
            wrapper=wrapper,
            final=fixture.final_apk,
            output_name="validator-output-manifest.json",
        )
        revision_index = arguments.index("--sherpa-revision") + 1
        arguments[revision_index] = fixture.revision
        result = subprocess.run(
            arguments, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(
            (self.root / "validator-output-manifest.json").read_bytes()
        )
        self.assertEqual(manifest["claimStatus"], "offline-consistency-only")
        self.assertEqual(len(manifest["android"]["loadOrderEvidence"]), 4)

    def test_rejects_wrapper_owned_ort(self) -> None:
        wrapper = self._archive(
            "bad-wrapper.aar",
            {
                f"jni/{self.abi}/libfonix_shim.so": self.shim,
                f"jni/{self.abi}/libonnxruntime.so": self.ort,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(), wrapper=wrapper),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("must own no ORT", result.stderr)

    def test_rejects_source_library_that_defines_a_renamed_ort(self) -> None:
        renamed_ort = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsecond_runtime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        wrapper = self._native_directory(
            "renamed-ort-wrapper",
            {
                f"{self.abi}/libfonix_shim.so": self.shim,
                f"{self.abi}/libsecond_runtime.so": renamed_ort,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(), wrapper=wrapper),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("hidden or invalid ONNX Runtime candidate", result.stderr)

    def test_rejects_final_library_that_defines_a_renamed_ort(self) -> None:
        renamed_ort = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsecond_runtime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        final = self._archive(
            "renamed-ort.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsecond_runtime.so": renamed_ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(final=final), final=final),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("hidden or invalid ONNX Runtime candidate", result.stderr)

    def test_rejects_canonical_ort_without_ort_api_export(self) -> None:
        non_runtime = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime.so",
            needed=("libdl.so", "libc.so"),
        )
        sherpa = self._native_directory(
            "non-runtime-sherpa",
            {
                f"{self.abi}/libonnxruntime.so": non_runtime,
                f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
            },
        )
        final = self._archive(
            "non-runtime.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": non_runtime,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final, ort=non_runtime),
                sherpa=sherpa,
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("without OrtGetApiBase", result.stderr)

    def test_accepts_repeated_split_native_directory_inputs(self) -> None:
        runtime_and_c_api = self._native_directory(
            "sherpa-runtime-and-c-api",
            {
                f"{self.abi}/libonnxruntime.so": self.ort,
                f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
            },
        )
        cxx_api = self._native_directory(
            "sherpa-cxx-api",
            {f"{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api},
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(), sherpa=[runtime_and_c_api, cxx_api]
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / "compatibility.json"
        first_bytes = output.read_bytes()
        manifest = json.loads(first_bytes)
        self.assertEqual(len(manifest["sherpaOnnx"]["artifacts"]), 2)
        reversed_result = subprocess.run(
            self._arguments(
                self._receipts(),
                sherpa=[cxx_api, runtime_and_c_api],
                output_name="compatibility-reversed.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(reversed_result.returncode, 0, reversed_result.stderr)
        self.assertEqual(
            (self.root / "compatibility-reversed.json").read_bytes(), first_bytes
        )

    def test_rejects_selected_native_archive_without_loadable_libraries(
        self,
    ) -> None:
        empty = self._archive("empty-sherpa.aar", {"AndroidManifest.xml": b"manifest"})
        result = subprocess.run(
            self._arguments(self._receipts(), sherpa=[self.sherpa, empty]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("contains no valid loadable native libraries", result.stderr)

    def test_accepts_reproducible_split_inputs_for_two_abis(self) -> None:
        other_abi = "x86_64"
        other_ort = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libonnxruntime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        other_c_api = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libsherpa-onnx-c-api.so",
            needed=("libonnxruntime.so", "libdl.so", "libc.so"),
        )
        other_cxx_api = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libsherpa-onnx-cxx-api.so",
            needed=(
                "libsherpa-onnx-c-api.so",
                "libonnxruntime.so",
                "libdl.so",
                "libc.so",
            ),
        )
        other_shim = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libfonix_shim.so",
            needed=("libdl.so", "libc.so"),
        )
        other_app = ELF_FIXTURE.synthetic_elf(
            other_abi, soname=None, needed=("libc.so",)
        )
        other_sherpa = self._native_directory(
            "sherpa-x86_64-jniLibs",
            {
                f"{other_abi}/libonnxruntime.so": other_ort,
                f"{other_abi}/libsherpa-onnx-c-api.so": other_c_api,
                f"{other_abi}/libsherpa-onnx-cxx-api.so": other_cxx_api,
            },
        )
        other_wrapper = self._native_directory(
            "fonix-x86_64-native-assets",
            {f"{other_abi}/libfonix_shim.so": other_shim},
        )
        final = self._archive(
            "two-abi-release.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{other_abi}/libapp.so": other_app,
                f"lib/{other_abi}/libonnxruntime.so": other_ort,
                f"lib/{other_abi}/libsherpa-onnx-c-api.so": other_c_api,
                f"lib/{other_abi}/libsherpa-onnx-cxx-api.so": other_cxx_api,
                f"lib/{other_abi}/libfonix_shim.so": other_shim,
            },
        )
        receipts = [
            *self._receipts(final=final),
            *self._receipts(
                final=final,
                ort=other_ort,
                abi=other_abi,
                index_offset=10,
            ),
        ]
        first = subprocess.run(
            self._arguments(
                receipts,
                sherpa=[self.sherpa, other_sherpa],
                wrapper=[self.wrapper, other_wrapper],
                final=final,
                abis=[self.abi, other_abi],
                output_name="two-abi.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = (self.root / "two-abi.json").read_bytes()
        manifest = json.loads(first_bytes)
        self.assertEqual(manifest["android"]["abis"], [self.abi, other_abi])
        self.assertEqual(len(manifest["android"]["loadOrderEvidence"]), 8)

        reversed_result = subprocess.run(
            self._arguments(
                list(reversed(receipts)),
                sherpa=[other_sherpa, self.sherpa],
                wrapper=[other_wrapper, self.wrapper],
                final=final,
                abis=[other_abi, self.abi],
                output_name="two-abi-reversed.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(reversed_result.returncode, 0, reversed_result.stderr)
        self.assertEqual(
            (self.root / "two-abi-reversed.json").read_bytes(), first_bytes
        )

    def test_rejects_mixed_sherpa_profiles_across_abis(self) -> None:
        other_abi = "x86_64"
        other_ort = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libonnxruntime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        other_jni = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libsherpa-onnx-jni.so",
            needed=("libonnxruntime.so", "libc.so"),
        )
        other_shim = ELF_FIXTURE.synthetic_elf(
            other_abi,
            soname="libfonix_shim.so",
            needed=("libdl.so", "libc.so"),
        )
        mixed_sherpa = self._native_directory(
            "mixed-profile-x86_64",
            {
                f"{other_abi}/libonnxruntime.so": other_ort,
                f"{other_abi}/libsherpa-onnx-jni.so": other_jni,
            },
        )
        mixed_wrapper = self._native_directory(
            "mixed-profile-wrapper-x86_64",
            {f"{other_abi}/libfonix_shim.so": other_shim},
        )
        final = self._archive(
            "mixed-profile.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{other_abi}/libonnxruntime.so": other_ort,
                f"lib/{other_abi}/libsherpa-onnx-jni.so": other_jni,
                f"lib/{other_abi}/libfonix_shim.so": other_shim,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=[self.sherpa, mixed_sherpa],
                wrapper=[self.wrapper, mixed_wrapper],
                final=final,
                abis=[self.abi, other_abi],
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("source library profile 'flutter-ffi'", result.stderr)

    def test_rejects_duplicate_consumer_across_repeated_inputs(self) -> None:
        duplicate = self._native_directory(
            "duplicate-c-api",
            {f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api},
        )
        result = subprocess.run(
            self._arguments(self._receipts(), sherpa=[self.sherpa, duplicate]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "multiple input artifacts own native libraries", result.stderr
        )

    def test_rejects_flutter_ffi_profile_with_legacy_jni_consumer(self) -> None:
        mixed = self._native_directory(
            "mixed-sherpa-jniLibs",
            {
                f"{self.abi}/libonnxruntime.so": self.ort,
                f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(), sherpa=mixed),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("source library profile 'flutter-ffi'", result.stderr)

    def test_rejects_cxx_consumer_without_c_api_dependency(self) -> None:
        bad_cxx = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-cxx-api.so",
            needed=("libonnxruntime.so", "libc.so"),
        )
        sherpa = self._native_directory(
            "bad-cxx-jniLibs",
            {
                f"{self.abi}/libonnxruntime.so": self.ort,
                f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"{self.abi}/libsherpa-onnx-cxx-api.so": bad_cxx,
            },
        )
        final = self._archive(
            "bad-cxx.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": bad_cxx,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final), sherpa=sherpa, final=final
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing libsherpa-onnx-c-api.so", result.stderr)

    def test_rejects_unselected_final_native_library(self) -> None:
        java_ort_jni = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime4j_jni.so",
            needed=("libonnxruntime.so", "libc.so"),
        )
        final = self._archive(
            "unexpected-java-ort.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{self.abi}/libonnxruntime4j_jni.so": java_ort_jni,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(final=final), final=final),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("without a selected source input", result.stderr)
        self.assertIn("libonnxruntime4j_jni.so", result.stderr)

    def test_binds_every_selected_companion_library(self) -> None:
        companion = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime_providers_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        companion_source = self._native_directory(
            "provider-companion",
            {
                f"{self.abi}/libonnxruntime_providers_shared.so": companion,
            },
        )
        final = self._archive(
            "with-provider-companion.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{self.abi}/libonnxruntime_providers_shared.so": companion,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=[self.sherpa, companion_source],
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "compatibility.json").read_bytes())
        companions = manifest["android"]["librariesByAbi"][self.abi][
            "companionLibraries"
        ]
        self.assertEqual(
            companions["libonnxruntime_providers_shared.so"]["final"]["sha256"],
            _sha256_bytes(companion),
        )

    def test_rejects_missing_selected_companion_in_final(self) -> None:
        companion = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime_providers_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        source = self._native_directory(
            "missing-final-companion",
            {f"{self.abi}/libonnxruntime_providers_shared.so": companion},
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(), sherpa=[self.sherpa, source]
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "final artifact must contain exactly one "
            "libonnxruntime_providers_shared.so",
            result.stderr,
        )

    def test_rejects_companion_dependency_drift(self) -> None:
        source_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime_providers_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        final_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime_providers_shared.so",
            needed=("liblog.so", "libc.so"),
        )
        source = self._native_directory(
            "dependency-drift-companion",
            {f"{self.abi}/libonnxruntime_providers_shared.so": source_bytes},
        )
        final = self._archive(
            "dependency-drift-companion.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{self.abi}/libonnxruntime_providers_shared.so": final_bytes,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=[self.sherpa, source],
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependency drift", result.stderr)

    def test_rejects_companion_soname_drift(self) -> None:
        source_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime_providers_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        final_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libwrong-provider.so",
            needed=("libdl.so", "libc.so"),
        )
        source = self._native_directory(
            "soname-drift-companion",
            {f"{self.abi}/libonnxruntime_providers_shared.so": source_bytes},
        )
        final = self._archive(
            "soname-drift-companion.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{self.abi}/libonnxruntime_providers_shared.so": final_bytes,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=[self.sherpa, source],
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("SONAME does not match its basename", result.stderr)

    def test_rejects_companion_loaded_segment_drift(self) -> None:
        source_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime_providers_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        drifted = bytearray(source_bytes)
        drifted[0x180] ^= 1
        source = self._native_directory(
            "loaded-drift-companion",
            {f"{self.abi}/libonnxruntime_providers_shared.so": source_bytes},
        )
        final = self._archive(
            "loaded-drift-companion.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
                f"lib/{self.abi}/libonnxruntime_providers_shared.so": bytes(
                    drifted
                ),
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=[self.sherpa, source],
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("loaded bytes do not match", result.stderr)

    def test_binds_single_used_libcxx_owner(self) -> None:
        sherpa, wrapper, final, libcxx = self._libcxx_case("libcxx-success")
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=sherpa,
                wrapper=wrapper,
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "compatibility.json").read_bytes())
        self.assertEqual(
            manifest["android"]["librariesByAbi"][self.abi]["libcxxShared"][
                "final"
            ]["sha256"],
            _sha256_bytes(libcxx),
        )

    def test_rejects_duplicate_libcxx_source_owners(self) -> None:
        sherpa, wrapper, final, _ = self._libcxx_case(
            "libcxx-duplicate", duplicate_wrapper_owner=True
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=sherpa,
                wrapper=wrapper,
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("multiple input artifacts own native libraries", result.stderr)
        self.assertIn("libc++_shared.so", result.stderr)

    def test_rejects_missing_final_libcxx_owner(self) -> None:
        sherpa, wrapper, final, _ = self._libcxx_case(
            "libcxx-missing-final", final_owner=False
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(), sherpa=sherpa, wrapper=wrapper, final=final
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("unresolved dependencies", result.stderr)
        self.assertIn("libc++_shared.so", result.stderr)

    def test_rejects_unselected_final_libcxx_owner(self) -> None:
        sherpa, wrapper, final, _ = self._libcxx_case(
            "libcxx-unselected-final", source_owner=False
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=sherpa,
                wrapper=wrapper,
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("without a selected source input", result.stderr)
        self.assertIn("libc++_shared.so", result.stderr)

    def test_rejects_unused_final_libcxx_owner(self) -> None:
        sherpa, wrapper, final, _ = self._libcxx_case(
            "libcxx-unused", consumer_uses_owner=False
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=sherpa,
                wrapper=wrapper,
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("packages unused libc++_shared.so", result.stderr)

    def test_rejects_final_libcxx_loaded_byte_drift(self) -> None:
        original = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libc++_shared.so",
            needed=("libdl.so", "libc.so"),
        )
        drifted = bytearray(original)
        drifted[0x180] ^= 1
        sherpa, wrapper, final, _ = self._libcxx_case(
            "libcxx-drift", final_owner_bytes=bytes(drifted)
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final),
                sherpa=sherpa,
                wrapper=wrapper,
                final=final,
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("libc++_shared.so loaded bytes do not match", result.stderr)

    def test_rejects_aligned_manifest_without_supported_validation_records(
        self,
    ) -> None:
        aligned_sherpa = self._archive(
            "sherpa-aligned.aar",
            {f"jni/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni},
        )
        runtime = self._archive(
            "application-runtime.aar",
            {f"jni/{self.abi}/libonnxruntime.so": self.ort},
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(
                    final=self.jni_final, mode="aligned", profile="jni"
                ),
                mode="aligned",
                profile="jni",
                sherpa=aligned_sherpa,
                runtime=runtime,
                final=self.jni_final,
                output_name="aligned.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "current load-order validator accepts only sherpa-owned Flutter FFI",
            result.stderr,
        )

    def test_legacy_jni_profile_cannot_claim_current_target_validation(
        self,
    ) -> None:
        aligned_sherpa = self._archive(
            "default-profile-sherpa.aar",
            {f"jni/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni},
        )
        runtime = self._archive(
            "default-profile-runtime.aar",
            {f"jni/{self.abi}/libonnxruntime.so": self.ort},
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(
                    final=self.jni_final, mode="aligned", profile="jni"
                ),
                mode="aligned",
                profile=None,
                sherpa=aligned_sherpa,
                runtime=runtime,
                final=self.jni_final,
                output_name="default-profile.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "current load-order validator accepts only sherpa-owned Flutter FFI",
            result.stderr,
        )

    def test_native_directory_identity_is_relocation_stable(self) -> None:
        relocated = self._native_directory(
            "relocated-sherpa-jniLibs",
            {
                f"{self.abi}/libonnxruntime.so": self.ort,
                f"{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
            },
        )
        first = subprocess.run(
            self._arguments(self._receipts(), output_name="identity-first.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        second = subprocess.run(
            self._arguments(
                self._receipts(),
                sherpa=relocated,
                output_name="identity-second.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_manifest = json.loads(
            (self.root / "identity-first.json").read_bytes()
        )
        second_manifest = json.loads(
            (self.root / "identity-second.json").read_bytes()
        )
        self.assertEqual(
            first_manifest["sherpaOnnx"]["artifacts"][0]["sha256"],
            second_manifest["sherpaOnnx"]["artifacts"][0]["sha256"],
        )

    def test_rejects_cross_role_native_input_reuse(self) -> None:
        result = subprocess.run(
            self._arguments(self._receipts(), wrapper=self.sherpa),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("sherpa and wrapper inputs must not overlap", result.stderr)

    def test_validates_qnn_record_for_matching_static_aligned_build(self) -> None:
        qnn_record = self._qnn_qualification_record(final=self.jni_final)
        report = MANIFEST._inspect(self.jni_final, "aligned final artifact")
        final_ort = MANIFEST._single(
            report, "libonnxruntime.so", self.abi, "aligned final artifact"
        )
        qualification = MANIFEST._read_qnn_qualification(
            qnn_record,
            mode="aligned",
            abis=(self.abi,),
            build_type="release-minified",
            final_sha256=report["_fonixArtifactIdentity"].sha256,
            runtime_version="1.27.0",
            ort_api=27,
            final_ort_by_abi={self.abi: final_ort},
        )
        self.assertEqual(
            qualification["qnn"]["backendId"], "synthetic-htp"
        )
        self.assertEqual(qualification["recordSha256"], _sha256_file(qnn_record))

    def test_rejects_qnn_record_for_sherpa_owned_mode(self) -> None:
        qnn_record = self._qnn_qualification_record()
        arguments = self._arguments(self._receipts())
        arguments.extend(("--qnn-qualification-record", str(qnn_record)))
        result = subprocess.run(
            arguments, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("only for aligned mode", result.stderr)

    def test_aligned_mode_rejects_sherpa_runtime_owner(self) -> None:
        runtime = self._archive(
            "application-runtime.aar",
            {f"jni/{self.abi}/libonnxruntime.so": self.ort},
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(
                    final=self.jni_final, mode="aligned", profile="jni"
                ),
                mode="aligned",
                profile="jni",
                runtime=runtime,
                final=self.jni_final,
                output_name="invalid-aligned.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("sherpa artifacts must own no ORT", result.stderr)

    def test_aligned_mode_rejects_runtime_loaded_byte_drift(self) -> None:
        aligned_sherpa = self._archive(
            "sherpa-aligned.aar",
            {f"jni/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni},
        )
        drifted = bytearray(self.ort)
        drifted[0x180] ^= 1
        runtime = self._archive(
            "drifted-application-runtime.aar",
            {f"jni/{self.abi}/libonnxruntime.so": bytes(drifted)},
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(
                    final=self.jni_final, mode="aligned", profile="jni"
                ),
                mode="aligned",
                profile="jni",
                sherpa=aligned_sherpa,
                runtime=runtime,
                final=self.jni_final,
                output_name="drifted-aligned.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("loaded bytes do not match", result.stderr)

    def test_rejects_final_loaded_segment_drift(self) -> None:
        drifted = bytearray(self.ort)
        drifted[0x180] ^= 1
        drifted_ort = bytes(drifted)
        final = self._archive(
            "drifted.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": self.flutter_aot,
                f"lib/{self.abi}/libonnxruntime.so": drifted_ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(
                self._receipts(final=final, ort=drifted_ort), final=final
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("loaded bytes do not match", result.stderr)

    def test_rejects_incomplete_target_host_matrix(self) -> None:
        complete = self._receipts()
        receipts = [complete[0], complete[2]]
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cartesian matrix is not proven", result.stderr)

    def test_rejects_cross_paired_load_order_page_size_matrix(self) -> None:
        complete = self._receipts()
        receipts = [complete[0], complete[3]]
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cartesian matrix is not proven", result.stderr)

    def test_rejects_receipt_for_another_build(self) -> None:
        receipts = self._receipts()
        receipts[0] = self._receipt(
            load_order="dart-first",
            page_size=4096,
            index=8,
            build_type="debug",
        )
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("another final build type", result.stderr)

    def test_rejects_raw_or_wrong_validator_load_order_evidence(self) -> None:
        for index, mutation in enumerate(
            (
                lambda value: value.update({"schemaVersion": 2}),
                lambda value: value.update({"validatorSha256": "0" * 64}),
            )
        ):
            with self.subTest(index=index):
                receipts = self._receipts()
                value = json.loads(receipts[0].read_text(encoding="utf-8"))
                mutation(value)
                receipts[0].write_text(json.dumps(value), encoding="utf-8")
                result = subprocess.run(
                    self._arguments(
                        receipts, output_name=f"invalid-validator-{index}.json"
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)

    def test_rejects_validation_record_native_library_tamper(self) -> None:
        receipts = self._receipts()
        value = json.loads(receipts[0].read_text(encoding="utf-8"))
        value["nativeLibraries"]["onnxRuntime"]["sha256"] = "0" * 64
        receipts[0].write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the inspected final artifact", result.stderr)

    def test_rejects_incomplete_or_impossible_validation_record_contract(
        self,
    ) -> None:
        mutations = (
            (
                "boolean-schema",
                lambda value: value.update({"schemaVersion": True}),
                "schemaVersion must be integer 1",
            ),
            (
                "float-api",
                lambda value: value["runtime"].update({"requiredOrtApi": 27.0}),
                "runtime does not match",
            ),
            (
                "float-completed",
                lambda value: value["workload"].update({"completedCycles": 2.0}),
                "must be an integer",
            ),
            (
                "partial-step",
                lambda value: value["workload"]["steps"].__setitem__(
                    0, {"ordinal": 1, "engine": "fonix"}
                ),
                "unexpected field set",
            ),
            (
                "bad-vad-padding",
                lambda value: value["workload"]["steps"][1].update(
                    {"submittedSamples": 16000}
                ),
                "submittedSamples must be an integer",
            ),
            (
                "partial-lifecycle",
                lambda value: value["lifecycle"].update(
                    {"fonixCancellation": {"mode": "active-native-termination"}}
                ),
                "unexpected field set",
            ),
            (
                "boolean-sherpa-count",
                lambda value: value["lifecycle"]["sherpaCancellation"].update(
                    {"requestCount": True}
                ),
                "must be an integer",
            ),
            (
                "float-disposal-count",
                lambda value: value["lifecycle"]["disposal"].update(
                    {"fonixSessionsClosed": 2.0}
                ),
                "must be an integer",
            ),
            (
                "cancel-after-final-vad-frame",
                lambda value: value["lifecycle"]["sherpaCancellation"].update(
                    {"framesAcceptedBeforeRequest": 32}
                ),
                "must be an integer in range",
            ),
        )
        for index, (name, mutate, message) in enumerate(mutations):
            with self.subTest(name=name):
                receipts = self._receipts(index_offset=100 + (index * 10))
                value = json.loads(receipts[0].read_text(encoding="utf-8"))
                mutate(value)
                receipts[0].write_text(json.dumps(value), encoding="utf-8")
                result = subprocess.run(
                    self._arguments(
                        receipts, output_name=f"invalid-contract-{index}.json"
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    def test_reference_bytes_keep_the_validator_size_bound(self) -> None:
        raw = b"123456789"
        with mock.patch.object(MANIFEST, "MAX_REFERENCE_BYTES", 8):
            with self.assertRaisesRegex(
                MANIFEST.CompatibilityManifestError,
                "does not bind the reference bytes",
            ):
                MANIFEST._record_reference_bytes(
                    base64.b64encode(raw).decode("ascii"),
                    _sha256_bytes(raw),
                    "oversized-reference",
                )

    def test_rejects_ort_version_too_old_for_api_27(self) -> None:
        receipts = self._receipts()
        for receipt in receipts:
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["runtime"]["ortVersion"] = "1.26.0"
            receipt.write_text(json.dumps(value), encoding="utf-8")
        arguments = self._arguments(receipts)
        version_index = arguments.index("--ort-version-observed") + 1
        arguments[version_index] = "1.26.0"
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot expose the required C API 27", result.stderr)

    def test_rejects_validation_record_tool_identity_drift(self) -> None:
        mutations = (
            lambda value: value.update({"receiptSchemaSha256": "0" * 64}),
            lambda value: value.update({"nativeVerifierSha256": "0" * 64}),
            lambda value: value["evidenceBindings"]["receiptSchema"].update(
                {"sizeBytes": 1}
            ),
            lambda value: value["evidenceBindings"]["nativeVerifier"].update(
                {"sizeBytes": 1}
            ),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                receipts = self._receipts(index_offset=200 + (index * 10))
                value = json.loads(receipts[0].read_text(encoding="utf-8"))
                mutate(value)
                receipts[0].write_text(json.dumps(value), encoding="utf-8")
                result = subprocess.run(
                    self._arguments(
                        receipts, output_name=f"invalid-tool-{index}.json"
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)

    def test_rejects_reused_launch_challenge_across_matrix_runs(self) -> None:
        receipts = self._receipts()
        first = json.loads(receipts[0].read_text(encoding="utf-8"))
        second = json.loads(receipts[1].read_text(encoding="utf-8"))
        challenge = first["process"]["launchChallengeSha256"]
        second["process"]["launchChallengeSha256"] = challenge
        second["evidenceBindings"]["launchChallenge"]["sha256"] = challenge
        receipts[1].write_text(json.dumps(second), encoding="utf-8")
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("distinct challenge and evidence tuple", result.stderr)

    def test_rejects_reused_raw_receipt_across_matrix_runs(self) -> None:
        receipts = self._receipts()
        first = json.loads(receipts[0].read_text(encoding="utf-8"))
        first_hash = first["loadOrderReceiptSha256"]
        second = json.loads(receipts[1].read_text(encoding="utf-8"))
        second["loadOrderReceiptSha256"] = first_hash
        second["evidenceBindings"]["receipt"]["sha256"] = first_hash
        receipts[1].write_text(json.dumps(second), encoding="utf-8")
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("distinct challenge and evidence tuple", result.stderr)

    def test_rejects_inconsistent_stable_evidence_binding_sizes(self) -> None:
        receipts = self._receipts()
        value = json.loads(receipts[0].read_text(encoding="utf-8"))
        value["evidenceBindings"]["sherpaAudio"]["sizeBytes"] += 1
        receipts[0].write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("one build and fixture set", result.stderr)

    def test_rejects_inconsistent_audio_length_across_matrix_runs(self) -> None:
        receipts = self._receipts()
        value = json.loads(receipts[0].read_text(encoding="utf-8"))
        for step in value["workload"]["steps"]:
            if step["engine"] == "sherpa":
                step["sourceSamples"] = 17000
                step["submittedSamples"] = 17408
        recovery = value["lifecycle"]["recovery"]["sherpa"]
        recovery["sourceSamples"] = 17000
        recovery["submittedSamples"] = 17408
        receipts[0].write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("one build and fixture set", result.stderr)

    def test_rejects_symbolic_link_native_input_root(self) -> None:
        linked = self.root / "linked-sherpa-jniLibs"
        try:
            linked.symlink_to(self.sherpa, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        result = subprocess.run(
            self._arguments(self._receipts(), sherpa=linked),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("must not be a symbolic link", result.stderr)

    def test_rejects_extracted_directory_as_final_artifact(self) -> None:
        extracted = self._native_directory(
            "extracted-final",
            {
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(), final=extracted),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("final artifact is not a regular file", result.stderr)

    def test_rejects_aab_as_runtime_load_order_artifact(self) -> None:
        final = self._archive(
            "app-release.aab",
            {
                "BundleConfig.pb": b"bundle",
                "base/manifest/AndroidManifest.xml": b"manifest",
                f"base/lib/{self.abi}/libapp.so": self.flutter_aot,
                f"base/lib/{self.abi}/libonnxruntime.so": self.ort,
                f"base/lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"base/lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"base/lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(final=final), final=final),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("accepts only final APK records", result.stderr)

    def test_rejects_symbolic_link_final_artifact(self) -> None:
        linked = self.root / "linked-final.apk"
        try:
            linked.symlink_to(self.final)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        result = subprocess.run(
            self._arguments(self._receipts(final=linked), final=linked),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("final artifact is not a regular file", result.stderr)

    def test_rejects_output_that_would_overwrite_final_artifact(self) -> None:
        original = self.final.read_bytes()
        result = subprocess.run(
            self._arguments(self._receipts(), output_name=self.final.name),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("output must not overwrite an input", result.stderr)
        self.assertEqual(self.final.read_bytes(), original)

    def test_rejects_output_that_would_overwrite_receipt(self) -> None:
        receipts = self._receipts()
        original = receipts[0].read_bytes()
        result = subprocess.run(
            self._arguments(receipts, output_name=receipts[0].name),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("output must not overwrite an input", result.stderr)
        self.assertEqual(receipts[0].read_bytes(), original)

    def test_rejects_existing_output_without_overwriting_it(self) -> None:
        output = self.root / "already-exists.json"
        output.write_bytes(b"preserve-me")
        result = subprocess.run(
            self._arguments(self._receipts(), output_name=output.name),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("output must not already exist", result.stderr)
        self.assertEqual(output.read_bytes(), b"preserve-me")

    def test_rejects_symbolic_link_output_without_touching_target(self) -> None:
        target = self.root / "output-target.json"
        target.write_bytes(b"preserve-target")
        output = self.root / "linked-output.json"
        try:
            output.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        result = subprocess.run(
            self._arguments(self._receipts(), output_name=output.name),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("output must not already exist", result.stderr)
        self.assertEqual(target.read_bytes(), b"preserve-target")

    def test_rejects_symbolic_link_temporary_output(self) -> None:
        target = self.root / "temporary-target.json"
        target.write_bytes(b"preserve-temporary-target")
        output = self.root / "new-output.json"
        temporary = output.with_name(output.name + ".tmp")
        try:
            temporary.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        result = subprocess.run(
            self._arguments(self._receipts(), output_name=output.name),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("temporary output already exists", result.stderr)
        self.assertEqual(target.read_bytes(), b"preserve-temporary-target")
        self.assertFalse(output.exists())

    def test_writer_rejects_corrupted_temporary_bytes(self) -> None:
        output = self.root / "corrupted-write.json"
        temporary = output.with_name(output.name + ".tmp")
        real_write = MANIFEST.os.write

        def corrupt_write(descriptor: int, value: bytes) -> int:
            corrupted = b"x" * len(value)
            return real_write(descriptor, corrupted)

        with mock.patch.object(MANIFEST.os, "write", side_effect=corrupt_write):
            with self.assertRaisesRegex(
                MANIFEST.CompatibilityManifestError,
                "temporary output bytes are not the completed manifest",
            ):
                MANIFEST._write_new(output, {"safe": True})
        self.assertFalse(output.exists())
        self.assertFalse(temporary.exists())

    def test_writer_removes_destination_after_temporary_path_swap(self) -> None:
        output = self.root / "swapped-write.json"
        temporary = output.with_name(output.name + ".tmp")
        real_link = MANIFEST.os.link

        def swap_before_link(
            source: Path,
            destination: Path,
            *,
            follow_symlinks: bool,
        ) -> None:
            source_path = Path(source)
            source_path.unlink()
            source_path.write_bytes(b"attacker")
            real_link(
                source_path,
                destination,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(MANIFEST.os, "link", side_effect=swap_before_link):
            with self.assertRaisesRegex(
                MANIFEST.CompatibilityManifestError,
                "published output is not the completed manifest",
            ):
                MANIFEST._write_new(output, {"safe": True})
        self.assertFalse(output.exists())
        self.assertEqual(temporary.read_bytes(), b"attacker")

    def test_writer_removes_destination_modified_after_link(self) -> None:
        output = self.root / "modified-after-link.json"
        temporary = output.with_name(output.name + ".tmp")
        real_link = MANIFEST.os.link

        def link_then_modify(
            source: Path,
            destination: Path,
            *,
            follow_symlinks: bool,
        ) -> None:
            real_link(source, destination, follow_symlinks=follow_symlinks)
            destination_path = Path(destination)
            destination_path.write_bytes(b"x" * destination_path.stat().st_size)

        with mock.patch.object(MANIFEST.os, "link", side_effect=link_then_modify):
            with self.assertRaisesRegex(
                MANIFEST.CompatibilityManifestError,
                "published output is not the completed manifest",
            ):
                MANIFEST._write_new(output, {"safe": True})
        self.assertFalse(output.exists())
        self.assertFalse(temporary.exists())

    def test_rejects_output_inside_native_directory_input(self) -> None:
        arguments = self._arguments(self._receipts())
        output_index = arguments.index("--output") + 1
        arguments[output_index] = str(self.sherpa / "compatibility.json")
        result = subprocess.run(
            arguments, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("inside a native-directory input", result.stderr)
        self.assertFalse((self.sherpa / "compatibility.json").exists())

    def test_rejects_archive_that_changes_during_inspection(self) -> None:
        artifact = self._archive(
            "changing.aar",
            {f"jni/{self.abi}/libonnxruntime.so": self.ort},
        )
        original_inspect = MANIFEST.VERIFIER.inspect

        def inspect_and_mutate(path: Path) -> dict[str, object]:
            report = original_inspect(path)
            path.write_bytes(path.read_bytes() + b"drift")
            return report

        with mock.patch.object(
            MANIFEST.VERIFIER, "inspect", side_effect=inspect_and_mutate
        ):
            with self.assertRaisesRegex(
                MANIFEST.CompatibilityManifestError,
                "changed while it was being inspected",
            ):
                MANIFEST._inspect(artifact, "changing artifact")

    def test_archive_inspection_rejects_an_aba_path_swap(self) -> None:
        artifact = self._archive(
            "aba.aar", {f"jni/{self.abi}/libonnxruntime.so": self.ort}
        )
        original_bytes = artifact.read_bytes()
        replacement = self._archive(
            "replacement.aar", {f"jni/{self.abi}/libonnxruntime.so": self.shim}
        )
        replacement_bytes = replacement.read_bytes()
        original_inspect = MANIFEST.VERIFIER.inspect
        inspected_paths: list[Path] = []

        def inspect_during_aba(snapshot: Path) -> dict[str, object]:
            inspected_paths.append(snapshot)
            parked = self.root / "parked-original.aar"
            artifact.replace(parked)
            artifact.write_bytes(replacement_bytes)
            artifact.unlink()
            parked.replace(artifact)
            return original_inspect(snapshot)

        with mock.patch.object(
            MANIFEST.VERIFIER, "inspect", side_effect=inspect_during_aba
        ):
            with self.assertRaisesRegex(
                MANIFEST.CompatibilityManifestError,
                "changed while its snapshot was inspected",
            ):
                MANIFEST._inspect(artifact, "ABA artifact")
        self.assertEqual(artifact.read_bytes(), original_bytes)
        self.assertEqual(len(inspected_paths), 1)
        self.assertNotEqual(inspected_paths[0], artifact)

    def test_rejects_wrong_soname_on_flutter_aot_library(self) -> None:
        wrong_app = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libwrong.so",
            needed=("libc.so",),
        )
        final = self._archive(
            "wrong-app-soname.apk",
            {
                "AndroidManifest.xml": b"manifest",
                f"lib/{self.abi}/libapp.so": wrong_app,
                f"lib/{self.abi}/libonnxruntime.so": self.ort,
                f"lib/{self.abi}/libsherpa-onnx-c-api.so": self.sherpa_c_api,
                f"lib/{self.abi}/libsherpa-onnx-cxx-api.so": self.sherpa_cxx_api,
                f"lib/{self.abi}/libfonix_shim.so": self.shim,
            },
        )
        result = subprocess.run(
            self._arguments(self._receipts(final=final), final=final),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("libapp.so SONAME does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()
