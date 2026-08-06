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
SCRIPT = REPOSITORY / "tool/ci/validate_qnn_qualification_receipt.py"
ELF_TESTS = REPOSITORY / "tool/tests/test_verify_native_libs.py"
_ELF_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_elf_test_fixture", ELF_TESTS
)
assert _ELF_SPEC is not None and _ELF_SPEC.loader is not None
ELF_FIXTURE = importlib.util.module_from_spec(_ELF_SPEC)
_ELF_SPEC.loader.exec_module(ELF_FIXTURE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class QnnQualificationReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.abi = "arm64-v8a"

        self.model = self._file("model.onnx", b"synthetic qdq model\n")
        self.qdq = self._file("qdq.json", b'{"fixture":"qdq"}\n')
        self.input_fixture = self._file("input.bin", b"input tensor\n")
        self.reference = self._file("reference.bin", b"reference tensor\n")
        self.cache_artifact = self._file("context.bin", b"synthetic context cache\n")

        self.ort_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libonnxruntime.so",
            needed=("libdl.so", "libc.so"),
        )
        self.sherpa_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-jni.so",
            needed=("libonnxruntime.so", "libc.so"),
        )
        self.backend_bytes = ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libQnnHtp.so",
            needed=("libc.so",),
        )
        self.qnn_root = self.root / "qnn"
        self.backend = self.qnn_root / f"lib/{self.abi}/libQnnHtp.so"
        self.notice = self.qnn_root / "LICENSE.txt"
        self.backend.parent.mkdir(parents=True)
        self.backend.write_bytes(self.backend_bytes)
        self.notice.write_bytes(b"Synthetic fixture license only.\n")
        self.qnn_manifest = self.root / "qnn-sdk.json"
        qnn_manifest_value = {
            "schemaVersion": 1,
            "sdkId": "synthetic-qnn-sdk",
            "sdkVersion": "0.0.1-fixture",
            "source": "https://example.invalid/qnn-sdk",
            "backendId": "synthetic-htp",
            "backendVersion": "0.0.1-fixture",
            "license": {
                "id": "synthetic-license",
                "spdxId": "LicenseRef-Synthetic",
                "redistribution": "prohibited",
                "notice": {
                    "path": "LICENSE.txt",
                    "sizeBytes": self.notice.stat().st_size,
                    "sha256": _sha256(self.notice),
                },
            },
            "artifactsByAbi": {
                self.abi: [
                    {
                        "role": "backend",
                        "path": f"lib/{self.abi}/libQnnHtp.so",
                        "sizeBytes": self.backend.stat().st_size,
                        "sha256": _sha256(self.backend),
                        "licenseId": "synthetic-license",
                    }
                ]
            },
        }
        _write_json(self.qnn_manifest, qnn_manifest_value)

        self.final_apk = self.root / "app-release.apk"
        with zipfile.ZipFile(self.final_apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"synthetic manifest")
            archive.writestr(f"lib/{self.abi}/libonnxruntime.so", self.ort_bytes)
            archive.writestr(
                f"lib/{self.abi}/libsherpa-onnx-jni.so", self.sherpa_bytes
            )
            archive.writestr(f"lib/{self.abi}/libQnnHtp.so", self.backend_bytes)

        self.provider_options = {
            "backend_path": "libQnnHtp.so",
            "context_cache_enable": "1",
            "htp_performance_mode": "burst",
        }
        self.provider_options_hash = _canonical_hash(self.provider_options)
        self.assignment_evidence = self.root / "assignment.json"
        self.assignment_value = {
            "schemaVersion": 1,
            "result": "passed",
            "abi": self.abi,
            "providerOptionsSha256": self.provider_options_hash,
            "fallbackObserved": False,
            "nodes": [
                {"nodeId": "node-0", "provider": "QNNExecutionProvider"},
                {"nodeId": "node-1", "provider": "QNNExecutionProvider"},
            ],
        }
        _write_json(self.assignment_evidence, self.assignment_value)

        self.aligned_receipt = self.root / "aligned-build.json"
        aligned_value = {
            "schemaVersion": 1,
            "result": "passed",
            "onnxRuntime": {
                "owner": "application",
                "linkage": "external-shared",
                "version": "1.27.1",
                "requiredApi": 27,
                "byAbi": {
                    self.abi: {
                        "library": {"sha256": hashlib.sha256(self.ort_bytes).hexdigest()}
                    }
                },
            },
            "qnn": {
                "manifestSha256": _sha256(self.qnn_manifest),
                "backendId": "synthetic-htp",
            },
        }
        _write_json(self.aligned_receipt, aligned_value)

        self.output_hash = _sha256(self.reference)
        self.cold_evidence = self._execution("cold", 120000)
        self.cache_hit_evidence = self._execution("cache-hit", 30000)
        self.dart_first_evidence = self._execution("dart-first", 130000)
        self.sherpa_first_evidence = self._execution("sherpa-first", 135000)

        self.firmware_hash = hashlib.sha256(b"synthetic firmware tuple").hexdigest()
        self.key_inputs = {
            "modelSha256": _sha256(self.model),
            "qdqConfigSha256": _sha256(self.qdq),
            "ortVersion": "1.27.1",
            "ortSha256": hashlib.sha256(self.ort_bytes).hexdigest(),
            "qnnSdkManifestSha256": _sha256(self.qnn_manifest),
            "qnnBackendSha256": _sha256(self.backend),
            "providerOptionsSha256": self.provider_options_hash,
            "socToken": "synthetic-soc",
            "firmwareSha256": self.firmware_hash,
            "driverToken": "synthetic-driver-1",
        }
        self.key_inputs_hash = _canonical_hash(self.key_inputs)
        self.cache_key = "fonix-qnn-v1-" + self.key_inputs_hash
        self.invalidation_evidence = self.root / "invalidation.json"
        _write_json(
            self.invalidation_evidence,
            {
                "schemaVersion": 1,
                "result": "passed",
                "changedInput": "firmware",
                "oldKey": self.cache_key,
                "newKey": "fonix-qnn-v1-" + "f" * 64,
                "staleCacheRejected": True,
            },
        )

        self.receipt_path = self.root / "qualification-receipt.json"
        self.receipt = self._qualification_receipt()
        self._write_receipt()

    def _file(self, name: str, contents: bytes) -> Path:
        path = self.root / name
        path.write_bytes(contents)
        return path

    def _execution(self, phase: str, duration: int) -> Path:
        path = self.root / f"{phase}.json"
        _write_json(
            path,
            {
                "schemaVersion": 1,
                "result": "passed",
                "phase": phase,
                "abi": self.abi,
                "finalApkSha256": _sha256(self.final_apk),
                "outputSha256": self.output_hash,
                "referenceOutputSha256": _sha256(self.reference),
                "durationMicroseconds": duration,
                "providerOptionsSha256": self.provider_options_hash,
                "assignmentEvidenceSha256": _sha256(self.assignment_evidence),
            },
        )
        return path

    def _qualification_receipt(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "result": "passed",
            "build": {
                "buildType": "release-minified",
                "finalApkSha256": _sha256(self.final_apk),
                "alignedBuildReceiptSha256": _sha256(self.aligned_receipt),
            },
            "model": {
                "onnxSha256": _sha256(self.model),
                "qdqConfigSha256": _sha256(self.qdq),
                "inputFixtureSha256": _sha256(self.input_fixture),
                "referenceOutputSha256": _sha256(self.reference),
            },
            "runtime": {
                "ortVersion": "1.27.1",
                "requiredOrtApi": 27,
                "ortSha256": hashlib.sha256(self.ort_bytes).hexdigest(),
            },
            "qnn": {
                "sdkManifestSha256": _sha256(self.qnn_manifest),
                "backendId": "synthetic-htp",
                "backendLibrarySha256": _sha256(self.backend),
                "providerOptions": self.provider_options,
                "providerOptionsSha256": self.provider_options_hash,
            },
            "device": {
                "modelToken": "synthetic-device",
                "socToken": "synthetic-soc",
                "androidApi": 35,
                "abi": self.abi,
                "firmwareSha256": self.firmware_hash,
                "driverToken": "synthetic-driver-1",
            },
            "assignment": {
                "evidenceSha256": _sha256(self.assignment_evidence),
                "totalNodes": 2,
                "qnnNodes": 2,
                "cpuNodes": 0,
                "fallbackPolicy": "reject-cpu",
            },
            "executions": {
                "cold": {
                    "evidenceSha256": _sha256(self.cold_evidence),
                    "outputSha256": self.output_hash,
                    "durationMicroseconds": 120000,
                    "referenceMatch": True,
                },
                "cacheHit": {
                    "evidenceSha256": _sha256(self.cache_hit_evidence),
                    "outputSha256": self.output_hash,
                    "durationMicroseconds": 30000,
                    "referenceMatch": True,
                },
                "parity": True,
            },
            "contextCache": {
                "key": self.cache_key,
                "keyInputsSha256": self.key_inputs_hash,
                "cacheArtifactSha256": _sha256(self.cache_artifact),
                "invalidationEvidenceSha256": _sha256(self.invalidation_evidence),
                "changedInput": "firmware",
                "staleCacheRejected": True,
            },
            "loadOrders": [
                {
                    "loadOrder": "dart-first",
                    "evidenceSha256": _sha256(self.dart_first_evidence),
                    "outputSha256": self.output_hash,
                    "durationMicroseconds": 130000,
                    "parity": True,
                },
                {
                    "loadOrder": "sherpa-first",
                    "evidenceSha256": _sha256(self.sherpa_first_evidence),
                    "outputSha256": self.output_hash,
                    "durationMicroseconds": 135000,
                    "parity": True,
                },
            ],
        }

    def _write_receipt(self) -> None:
        _write_json(self.receipt_path, self.receipt)

    def _arguments(self, suffix: str = "first") -> list[str]:
        return [
            sys.executable,
            str(SCRIPT),
            "--receipt",
            str(self.receipt_path),
            "--aligned-build-receipt",
            str(self.aligned_receipt),
            "--qnn-sdk-manifest",
            str(self.qnn_manifest),
            "--qnn-root",
            str(self.qnn_root),
            "--final-apk",
            str(self.final_apk),
            "--model",
            str(self.model),
            "--qdq-config",
            str(self.qdq),
            "--input-fixture",
            str(self.input_fixture),
            "--reference-output",
            str(self.reference),
            "--assignment-evidence",
            str(self.assignment_evidence),
            "--cold-evidence",
            str(self.cold_evidence),
            "--cache-hit-evidence",
            str(self.cache_hit_evidence),
            "--context-cache-artifact",
            str(self.cache_artifact),
            "--invalidation-evidence",
            str(self.invalidation_evidence),
            "--load-order-evidence",
            f"dart-first={self.dart_first_evidence}",
            "--load-order-evidence",
            f"sherpa-first={self.sherpa_first_evidence}",
            "--output",
            str(self.root / f"qualification-{suffix}.json"),
        ]

    def _run(self, suffix: str = "first") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._arguments(suffix),
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_emits_deterministic_hash_bound_qualification_record(self) -> None:
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = (self.root / "qualification-first.json").read_bytes()
        record = json.loads(first_bytes)
        self.assertEqual(record["result"], "passed")
        self.assertEqual(record["assignment"]["cpuNodes"], 0)
        self.assertEqual(
            [entry["loadOrder"] for entry in record["loadOrders"]],
            ["dart-first", "sherpa-first"],
        )

        second = self._run("second")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.root / "qualification-second.json").read_bytes(), first_bytes)

    def test_rejects_cpu_fallback_in_assignment_evidence(self) -> None:
        self.assignment_value["nodes"][1]["provider"] = "CPUExecutionProvider"
        _write_json(self.assignment_evidence, self.assignment_value)
        self.receipt["assignment"]["evidenceSha256"] = _sha256(
            self.assignment_evidence
        )
        self._write_receipt()
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("non-QNN node", result.stderr)

    def test_rejects_missing_second_load_order(self) -> None:
        arguments = self._arguments()
        first_index = arguments.index("--load-order-evidence")
        del arguments[first_index : first_index + 2]
        result = subprocess.run(
            arguments,
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("both load-order evidence files", result.stderr)

    def test_rejects_context_cache_key_input_drift(self) -> None:
        self.receipt["contextCache"]["keyInputsSha256"] = "e" * 64
        self.receipt["contextCache"]["key"] = "fonix-qnn-v1-" + "e" * 64
        self._write_receipt()
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("key inputs do not match", result.stderr)

    def test_rejects_bound_model_byte_drift(self) -> None:
        self.model.write_bytes(b"different model\n")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ONNX model hash", result.stderr)

    def test_rejects_final_apk_without_exact_qnn_backend(self) -> None:
        with zipfile.ZipFile(self.final_apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"synthetic manifest")
            archive.writestr(f"lib/{self.abi}/libonnxruntime.so", self.ort_bytes)
            archive.writestr(
                f"lib/{self.abi}/libsherpa-onnx-jni.so", self.sherpa_bytes
            )
        self.receipt["build"]["finalApkSha256"] = _sha256(self.final_apk)
        self._write_receipt()
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("exact QNN backend", result.stderr)


if __name__ == "__main__":
    unittest.main()
