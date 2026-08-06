from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/android_compatibility_manifest.py"
ELF_TESTS = REPOSITORY / "tool/tests/test_verify_native_libs.py"
_ELF_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_elf_test_fixture", ELF_TESTS
)
assert _ELF_SPEC is not None and _ELF_SPEC.loader is not None
ELF_FIXTURE = importlib.util.module_from_spec(_ELF_SPEC)
_ELF_SPEC.loader.exec_module(ELF_FIXTURE)


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
        )
        self.sherpa_jni = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-jni.so",
            needed=("libonnxruntime.so", "libc.so"),
        )
        self.shim = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libfonix_shim.so",
            needed=("libdl.so", "libc.so"),
        )
        self.sherpa = self._archive(
            "sherpa.aar",
            {
                f"jni/{self.abi}/libonnxruntime.so": self.ort,
                f"jni/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni,
            },
        )
        self.wrapper = self._archive(
            "fonix-external.aar",
            {f"jni/{self.abi}/libfonix_shim.so": self.shim},
        )
        self.final = self._archive(
            "app-release.apk",
            {
                "AndroidManifest.xml": b"manifest",
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

    def _receipt(
        self,
        *,
        load_order: str,
        page_size: int,
        index: int,
        build_type: str = "release-minified",
        final: Path | None = None,
        ort: bytes | None = None,
    ) -> Path:
        final_artifact = final or self.final
        runtime = ort or self.ort
        receipt = {
            "schemaVersion": 1,
            "result": "passed",
            "abi": self.abi,
            "loadOrder": load_order,
            "buildType": build_type,
            "finalArtifactSha256": _sha256_file(final_artifact),
            "runtimeVersion": "1.27.1",
            "requiredOrtApi": 27,
            "ortSha256": _sha256_bytes(runtime),
            "pageSizeBytes": page_size,
            "device": {
                "kind": "physical" if page_size == 4096 else "emulator",
                "modelToken": f"fixture-{index}",
                "androidApi": 35,
            },
            "harnessSha256": "a" * 64,
            "fixtures": {
                "dartModelSha256": "b" * 64,
                "sherpaFixtureSha256": "c" * 64,
            },
            "workload": {
                "dartInferenceRuns": 2,
                "sherpaSmokeRuns": 2,
                "alternatingCycles": 4,
            },
        }
        path = self.root / f"receipt-{index}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def _receipts(
        self,
        *,
        final: Path | None = None,
        ort: bytes | None = None,
    ) -> list[Path]:
        return [
            self._receipt(
                load_order="dart-first",
                page_size=4096,
                index=0,
                final=final,
                ort=ort,
            ),
            self._receipt(
                load_order="sherpa-first",
                page_size=4096,
                index=1,
                final=final,
                ort=ort,
            ),
            self._receipt(
                load_order="dart-first",
                page_size=16384,
                index=2,
                final=final,
                ort=ort,
            ),
            self._receipt(
                load_order="sherpa-first",
                page_size=16384,
                index=3,
                final=final,
                ort=ort,
            ),
        ]

    def _arguments(
        self,
        receipts: list[Path],
        *,
        mode: str = "sherpa-owned",
        sherpa: Path | None = None,
        wrapper: Path | None = None,
        runtime: Path | None = None,
        final: Path | None = None,
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
            "--sherpa-artifact",
            str(sherpa or self.sherpa),
            "--wrapper-artifact",
            str(wrapper or self.wrapper),
            "--final-artifact",
            str(final or self.final),
            "--abi",
            self.abi,
            "--ort-version-observed",
            "1.27.1",
            "--ort-api-required",
            "27",
            "--build-type",
            "release-minified",
            "--snapshot-date",
            "2026-08-06",
            "--output",
            str(self.root / output_name),
        ]
        if runtime is not None:
            arguments.extend(("--runtime-artifact", str(runtime)))
        for receipt in receipts:
            arguments.extend(("--load-order-receipt", str(receipt)))
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
                "ortVersion": "1.27.1",
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
        manifest = json.loads(first_bytes)
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["android"]["ortOwner"], "sherpa")
        self.assertEqual(
            manifest["android"]["librariesByAbi"][self.abi]["onnxruntime"][
                "final"
            ]["sha256"],
            _sha256_bytes(self.ort),
        )
        self.assertEqual(len(manifest["android"]["loadOrderEvidence"]), 4)

        second = subprocess.run(
            self._arguments(receipts), capture_output=True, text=True, check=False
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(output.read_bytes(), first_bytes)

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

    def test_generates_application_owned_aligned_manifest(self) -> None:
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
                self._receipts(),
                mode="aligned",
                sherpa=aligned_sherpa,
                runtime=runtime,
                output_name="aligned.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "aligned.json").read_bytes())
        self.assertEqual(manifest["android"]["integrationMode"], "aligned")
        self.assertEqual(manifest["android"]["ortOwner"], "application")
        self.assertIsNone(manifest["android"]["qnnQualification"])
        self.assertEqual(
            manifest["android"]["artifacts"]["runtimeOwner"]["sha256"],
            _sha256_file(runtime),
        )

    def test_binds_validated_qnn_record_only_to_matching_aligned_build(self) -> None:
        aligned_sherpa = self._archive(
            "sherpa-aligned-qnn.aar",
            {f"jni/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni},
        )
        runtime = self._archive(
            "application-runtime-qnn.aar",
            {f"jni/{self.abi}/libonnxruntime.so": self.ort},
        )
        qnn_record = self._qnn_qualification_record()
        arguments = self._arguments(
            self._receipts(),
            mode="aligned",
            sherpa=aligned_sherpa,
            runtime=runtime,
            output_name="aligned-qnn.json",
        )
        arguments.extend(("--qnn-qualification-record", str(qnn_record)))
        result = subprocess.run(
            arguments, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "aligned-qnn.json").read_bytes())
        self.assertEqual(
            manifest["android"]["qnnQualification"]["qnn"]["backendId"],
            "synthetic-htp",
        )
        self.assertEqual(
            manifest["android"]["qnnQualification"]["recordSha256"],
            _sha256_file(qnn_record),
        )

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
                self._receipts(),
                mode="aligned",
                runtime=runtime,
                output_name="invalid-aligned.json",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("sherpa artifact must own no ORT", result.stderr)

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
                self._receipts(),
                mode="aligned",
                sherpa=aligned_sherpa,
                runtime=runtime,
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
                f"lib/{self.abi}/libonnxruntime.so": drifted_ort,
                f"lib/{self.abi}/libsherpa-onnx-jni.so": self.sherpa_jni,
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


if __name__ == "__main__":
    unittest.main()
