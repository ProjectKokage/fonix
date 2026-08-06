from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/validate_benchmark_receipt.py"
RECEIPT_SCHEMA = REPOSITORY / "templates/ci/benchmark_receipt.schema.json"
ASSIGNMENT_SCHEMA = (
    REPOSITORY / "templates/ci/provider_assignment_evidence.schema.json"
)
VALIDATION_SCHEMA = (
    REPOSITORY / "templates/ci/benchmark_validation_record.schema.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
    ).hexdigest()


def _statistics(samples: list[int]) -> dict[str, int]:
    ordered = sorted(samples)

    def nearest(percentile: int) -> int:
        return ordered[((percentile * len(ordered) + 99) // 100) - 1]

    return {
        "count": len(ordered),
        "p50": nearest(50),
        "p95": nearest(95),
        "p99": nearest(99),
        "max": ordered[-1],
    }


def _series(samples: list[int]) -> dict[str, object]:
    return {"samples": samples, "statistics": _statistics(samples)}


def _rate_milli(completed: int, duration: int) -> int:
    numerator = completed * 1_000_000_000
    return (numerator + duration // 2) // duration


class BenchmarkReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

        self.model = self._file("model.onnx", b"synthetic benchmark model\n")
        self.input_fixture = self._file("input.bin", b"synthetic input tensors\n")
        self.reference_output = self._file(
            "reference.bin", b"synthetic reference output\n"
        )
        self.runtime = self._file("libonnxruntime.so", b"synthetic runtime\n")
        self.final_artifact = self._file(
            "application.tar", b"synthetic final application archive\n"
        )

        self.build_manifest = self.root / "native-build.json"
        self.build_manifest_value = {
            "schemaVersion": 3,
            "nativeIdentity": "fonix_shim",
            "shimAbiVersion": 1,
            "requiredOrtApiVersion": 27,
            "runtimeProfile": "bundled",
            "androidRuntimeOwner": None,
            "allowedRuntimeSources": ["bundled"],
            "buildId": "synthetic-linux-x86_64-cpu",
            "artifact": {
                "id": "synthetic-linux-x86_64-cpu",
                "lockSha256": "a" * 64,
                "sourceSha256": "b" * 64,
                "targetOs": "linux",
                "targetArchitecture": "x86_64",
                "targetVariant": "default",
                "minimumOs": "glibc-2.27",
                "flavor": "cpu",
                "runtimeMode": "bundled",
                "thirdPartyNoticesSha256": "c" * 64,
                "providers": [
                    {
                        "wrapperId": "synthetic",
                        "reportedName": "SyntheticExecutionProvider",
                    }
                ],
            },
        }
        self._write_json(self.build_manifest, self.build_manifest_value)

        self.assignment_evidence = self.root / "assignment.json"
        self.assignment_value = {
            "schemaVersion": 1,
            "nodeExecutionCount": 4,
            "nodeExecutionsByProvider": {"synthetic": 4},
        }
        self._write_json(self.assignment_evidence, self.assignment_value)

        self.options = [
            {"name": "device_id", "encoding": "plain", "value": "0"},
            {
                "name": "model_cache_directory",
                "encoding": "sha256",
                "value": "d" * 64,
            },
        ]
        self.warm_samples = list(range(100, 200))
        self.throughput_windows = [
            {"completedRuns": 100, "durationMicroseconds": 1_000_000},
            {"completedRuns": 250, "durationMicroseconds": 2_000_000},
            {"completedRuns": 90, "durationMicroseconds": 600_000},
        ]
        throughput_rates = [
            _rate_milli(value["completedRuns"], value["durationMicroseconds"])
            for value in self.throughput_windows
        ]
        total_runs = sum(value["completedRuns"] for value in self.throughput_windows)
        total_duration = sum(
            value["durationMicroseconds"] for value in self.throughput_windows
        )

        self.receipt_path = self.root / "benchmark.json"
        self.receipt = {
            "schemaVersion": 1,
            "result": "measured",
            "purpose": "benchmark-evidence-only",
            "receiptId": "synthetic-linux-cpu-1",
            "model": {
                "id": "synthetic-model",
                "onnxSha256": _sha256(self.model),
                "inputFixtureSha256": _sha256(self.input_fixture),
                "referenceOutputSha256": _sha256(self.reference_output),
                "opset": 18,
            },
            "build": {
                "packageVersion": "0.1.0-dev.1",
                "nativeBuildManifestSha256": _sha256(self.build_manifest),
                "finalArtifactType": "linux-application-archive",
                "finalArtifactSha256": _sha256(self.final_artifact),
            },
            "runtime": {
                "ortVersion": "1.27.1",
                "requiredOrtApi": 27,
                "negotiatedOrtApi": 27,
                "shimAbi": 1,
                "shimBuildId": "synthetic-linux-x86_64-cpu",
                "runtimeOwner": "wrapper",
                "runtimeSource": "bundled",
                "artifactFlavor": "cpu",
                "artifactId": "synthetic-linux-x86_64-cpu",
                "artifactSourceSha256": "b" * 64,
                "compiledProviders": [
                    {
                        "wrapperId": "synthetic",
                        "reportedName": "SyntheticExecutionProvider",
                    }
                ],
                "librarySha256": _sha256(self.runtime),
            },
            "provider": {
                "id": "synthetic",
                "reportedName": "SyntheticExecutionProvider",
                "discoverable": True,
                "registered": True,
                "requirement": "full",
                "fallbackPolicy": "reject-any",
                "options": self.options,
                "optionsSha256": _canonical_hash(self.options),
                "assignmentEvidenceSha256": _sha256(self.assignment_evidence),
                "nodeExecutionCount": 4,
                "nodeExecutionsByProvider": {"synthetic": 4},
                "fallbackObserved": False,
            },
            "environment": {
                "platform": "linux",
                "architecture": "x86_64",
                "deviceIdentitySha256": "f" * 64,
                "osVersion": "6.8.0",
                "osBuild": "synthetic-build",
                "driverIdentity": "platform-managed",
                "firmwareIdentity": "platform-managed",
                "powerMode": "performance",
                "thermalState": "platform-not-exposed",
            },
            "workload": {
                "precision": "float32",
                "batchSize": 1,
                "concurrency": 2,
                "intraOpThreads": 2,
                "interOpThreads": 1,
                "warmupRuns": 10,
                "measuredWarmRuns": len(self.warm_samples),
                "throughputWindowCount": len(self.throughput_windows),
                "cacheState": "not-applicable",
                "inputShapeSignatureSha256": "e" * 64,
            },
            "measurements": {
                "coldRuntimeLoadMicroseconds": _series(
                    [500, 100, 300, 200, 400]
                ),
                "sessionCreateMicroseconds": _series([5000, 4000, 3000]),
                "firstRunMicroseconds": _series([900, 700, 800]),
                "warmRunMicroseconds": _series(self.warm_samples),
                "throughput": {
                    "windows": self.throughput_windows,
                    "statisticsRunsPerSecondMilli": _statistics(throughput_rates),
                    "totalCompletedRuns": total_runs,
                    "totalDurationMicroseconds": total_duration,
                    "aggregateRunsPerSecondMilli": _rate_milli(
                        total_runs, total_duration
                    ),
                },
            },
            "resources": {
                "rssBytes": {
                    "status": "measured",
                    **_series([1_000_000, 2_000_000, 3_000_000]),
                },
                "binaryBytes": {
                    "finalArtifact": self.final_artifact.stat().st_size,
                    "runtimeLibrary": self.runtime.stat().st_size,
                    "shimLibrary": 4096,
                    "providerDependencies": 0,
                },
                "cacheBytes": {
                    "status": "not-applicable",
                    "reason": "provider-has-no-cache",
                },
            },
        }
        self._write_receipt()

    def _file(self, name: str, contents: bytes) -> Path:
        path = self.root / name
        path.write_bytes(contents)
        return path

    def _write_json(self, path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _write_receipt(self) -> None:
        self._write_json(self.receipt_path, self.receipt)

    def _command(self, output: Path) -> list[str]:
        return [
            sys.executable,
            str(SCRIPT),
            "--receipt",
            str(self.receipt_path),
            "--model",
            str(self.model),
            "--input-fixture",
            str(self.input_fixture),
            "--reference-output",
            str(self.reference_output),
            "--build-manifest",
            str(self.build_manifest),
            "--runtime-artifact",
            str(self.runtime),
            "--final-artifact",
            str(self.final_artifact),
            "--assignment-evidence",
            str(self.assignment_evidence),
            "--output",
            str(output),
        ]

    def _run(self, name: str = "validated.json") -> tuple[subprocess.CompletedProcess[str], Path]:
        output = self.root / name
        result = subprocess.run(
            self._command(output),
            check=False,
            capture_output=True,
            text=True,
        )
        return result, output

    def _assert_rejected(self) -> str:
        result, output = self._run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(output.exists())
        return result.stderr

    def test_validates_exact_tuple_and_recomputes_statistics(self) -> None:
        result, output = self._run("first.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["claimStatus"], "measurement-only")
        self.assertEqual(
            record["statistics"]["warmRunMicroseconds"],
            {"count": 100, "p50": 149, "p95": 194, "p99": 198, "max": 199},
        )
        self.assertEqual(
            record["statistics"]["throughput"]["aggregateRunsPerSecondMilli"],
            122222,
        )
        self.assertNotIn(str(self.root), output.read_text(encoding="utf-8"))

        result_two, output_two = self._run("second.json")
        self.assertEqual(result_two.returncode, 0, result_two.stderr)
        self.assertEqual(output.read_bytes(), output_two.read_bytes())

    def test_accepts_explicit_rss_not_applicable(self) -> None:
        self.receipt["resources"]["rssBytes"] = {
            "status": "not-applicable",
            "reason": "target-does-not-expose-rss",
        }
        self._write_receipt()
        result, output = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["resources"]["rssBytes"]["status"], "not-applicable")

    def test_accepts_bound_cache_size_measurement(self) -> None:
        self.receipt["workload"]["cacheState"] = "hit"
        self.receipt["resources"]["cacheBytes"] = {
            "status": "measured",
            "state": "hit",
            "before": 2048,
            "after": 4096,
        }
        self._write_receipt()
        result, _ = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_schemas_are_closed_draft_2020_12_json(self) -> None:
        for path in (RECEIPT_SCHEMA, ASSIGNMENT_SCHEMA, VALIDATION_SCHEMA):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                value["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            self.assertFalse(value["additionalProperties"])

    def test_rejects_duplicate_receipt_key(self) -> None:
        source = self.receipt_path.read_text(encoding="utf-8").rstrip()
        self.receipt_path.write_text(
            source[:-1] + ',"schemaVersion":1}\n', encoding="utf-8"
        )
        self.assertIn("duplicate JSON key", self._assert_rejected())

    def test_rejects_duplicate_assignment_key_before_hash_binding(self) -> None:
        source = self.assignment_evidence.read_text(encoding="utf-8").rstrip()
        self.assignment_evidence.write_text(
            source[:-1] + ',"schemaVersion":1}\n', encoding="utf-8"
        )
        self.receipt["provider"]["assignmentEvidenceSha256"] = _sha256(
            self.assignment_evidence
        )
        self._write_receipt()
        self.assertIn("duplicate JSON key", self._assert_rejected())

    def test_rejects_unknown_nested_field(self) -> None:
        self.receipt["workload"]["unstatedShortcut"] = True
        self._write_receipt()
        self.assertIn("unexpected field set", self._assert_rejected())

    def test_rejects_latency_percentile_tamper(self) -> None:
        self.receipt["measurements"]["warmRunMicroseconds"]["statistics"][
            "p95"
        ] += 1
        self._write_receipt()
        self.assertIn("does not match raw samples", self._assert_rejected())

    def test_rejects_throughput_tamper(self) -> None:
        self.receipt["measurements"]["throughput"][
            "aggregateRunsPerSecondMilli"
        ] += 1
        self._write_receipt()
        self.assertIn("aggregate rate is inconsistent", self._assert_rejected())

    def test_rejects_derived_throughput_outside_bound(self) -> None:
        windows = [{"completedRuns": 10**12, "durationMicroseconds": 1}]
        rate = _rate_milli(10**12, 1)
        self.receipt["workload"]["throughputWindowCount"] = 1
        self.receipt["measurements"]["throughput"] = {
            "windows": windows,
            "statisticsRunsPerSecondMilli": _statistics([rate]),
            "totalCompletedRuns": 10**12,
            "totalDurationMicroseconds": 1,
            "aggregateRunsPerSecondMilli": rate,
        }
        self._write_receipt()
        self.assertIn("derived throughput rate", self._assert_rejected())

    def test_rejects_assignment_summary_tamper(self) -> None:
        self.receipt["provider"]["nodeExecutionCount"] = 3
        self.receipt["provider"]["nodeExecutionsByProvider"] = {"synthetic": 3}
        self._write_receipt()
        self.assertIn("contradicts assignment evidence", self._assert_rejected())

    def test_rejects_fallback_policy_contradiction(self) -> None:
        self.assignment_value["nodeExecutionCount"] = 5
        self.assignment_value["nodeExecutionsByProvider"] = {
            "synthetic": 4,
            "cpu": 1,
        }
        self._write_json(self.assignment_evidence, self.assignment_value)
        self.receipt["provider"].update(
            {
                "requirement": "active",
                "fallbackPolicy": "reject-any",
                "assignmentEvidenceSha256": _sha256(self.assignment_evidence),
                "nodeExecutionCount": 5,
                "nodeExecutionsByProvider": {"synthetic": 4, "cpu": 1},
                "fallbackObserved": True,
            }
        )
        self._write_receipt()
        self.assertIn("reject-any policy", self._assert_rejected())

    def test_rejects_target_absent_from_compiled_inventory(self) -> None:
        self.build_manifest_value["artifact"]["providers"] = [
            {"wrapperId": "cpu", "reportedName": "CPUExecutionProvider"}
        ]
        self._write_json(self.build_manifest, self.build_manifest_value)
        self.receipt["build"]["nativeBuildManifestSha256"] = _sha256(
            self.build_manifest
        )
        self.receipt["runtime"]["compiledProviders"] = [
            {"wrapperId": "cpu", "reportedName": "CPUExecutionProvider"}
        ]
        self._write_receipt()
        self.assertIn("compiled artifact inventory", self._assert_rejected())

    def test_rejects_duplicate_compiled_provider_inventory(self) -> None:
        self.build_manifest_value["artifact"]["providers"].append(
            {
                "wrapperId": "synthetic",
                "reportedName": "SyntheticExecutionProvider",
            }
        )
        self._write_json(self.build_manifest, self.build_manifest_value)
        self.receipt["build"]["nativeBuildManifestSha256"] = _sha256(
            self.build_manifest
        )
        self._write_receipt()
        self.assertIn("provider IDs must be unique", self._assert_rejected())

    def test_rejects_private_path_in_plain_provider_option(self) -> None:
        self.receipt["provider"]["options"] = [
            {"name": "cache_directory", "encoding": "plain", "value": "/private/a"}
        ]
        self.receipt["provider"]["optionsSha256"] = _canonical_hash(
            self.receipt["provider"]["options"]
        )
        self._write_receipt()
        self.assertIn("path-free", self._assert_rejected())

    def test_rejects_plain_secret_bearing_option(self) -> None:
        self.receipt["provider"]["options"] = [
            {"name": "api_token", "encoding": "plain", "value": "not-a-real-secret"}
        ]
        self.receipt["provider"]["optionsSha256"] = _canonical_hash(
            self.receipt["provider"]["options"]
        )
        self._write_receipt()
        self.assertIn("must use SHA-256 encoding", self._assert_rejected())

    def test_rejects_missing_explicit_cache_record(self) -> None:
        del self.receipt["resources"]["cacheBytes"]
        self._write_receipt()
        self.assertIn("unexpected field set", self._assert_rejected())

    def test_rejects_raw_sample_outside_bound(self) -> None:
        self.receipt["measurements"]["firstRunMicroseconds"] = _series(
            [10**15 + 1]
        )
        self._write_receipt()
        self.assertIn("outside its integer bound", self._assert_rejected())

    def test_rejects_model_byte_drift(self) -> None:
        self.model.write_bytes(b"tampered model\n")
        self.assertIn("model hash does not match", self._assert_rejected())

    def test_rejects_provider_reported_name_manifest_drift(self) -> None:
        self.receipt["provider"]["reportedName"] = "OtherExecutionProvider"
        self._write_receipt()
        self.assertIn("reported name contradicts", self._assert_rejected())


if __name__ == "__main__":
    unittest.main()
