from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePath
import shutil
import sys
import tempfile
import unittest
from unittest import mock


CI_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CI_DIRECTORY))

import cpu_benchmark_collection as collection  # noqa: E402
import source_checksum_manifest  # noqa: E402


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()


def _selected_macos_artifact() -> tuple[dict[str, object], str]:
    lock_path = REPOSITORY / "native/versions.lock.yaml"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    artifact = next(
        value
        for value in lock["artifacts"]
        if value["id"] == "onnxruntime-1.27.1-macos-arm64-cpu"
    )
    return artifact, hashlib.sha256(lock_path.read_bytes()).hexdigest()


def _build_manifest() -> dict[str, object]:
    artifact, lock_sha256 = _selected_macos_artifact()
    target = artifact["target"]
    third_party = next(
        value for value in artifact["notices"] if value["id"] == "ThirdPartyNotices"
    )
    return {
        "schemaVersion": 3,
        "nativeIdentity": "fonix_shim",
        "shimAbiVersion": 1,
        "requiredOrtApiVersion": 27,
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
            "thirdPartyNoticesSha256": third_party["sha256"],
            "providers": [
                {
                    "wrapperId": value["wrapper_id"],
                    "reportedName": value["reported_name"],
                }
                for value in artifact["providers"]
            ],
        },
    }


def _runtime() -> dict[str, object]:
    manifest = _build_manifest()
    artifact = manifest["artifact"]
    return {
        "packageVersion": "0.1.0-dev.1",
        "runtimeVersion": "1.27.1",
        "runtimeLibraryIdentity": "libonnxruntime.1.dylib",
        "runtimeSource": "bundled",
        "runtimeOwner": "wrapper",
        "artifactFlavor": artifact["flavor"],
        "artifactId": artifact["id"],
        "artifactSourceSha256": artifact["sourceSha256"],
        "platform": artifact["targetOs"],
        "architecture": artifact["targetArchitecture"],
        "shimNativeIdentity": "fonix_shim",
        "shimAbi": manifest["shimAbiVersion"],
        "shimBuildId": manifest["buildId"],
        "requiredOrtApi": manifest["requiredOrtApiVersion"],
        "negotiatedOrtApi": 27,
        "compiledProviders": copy.deepcopy(artifact["providers"]),
    }


def _fragment(challenge: str, process_id: int, *, offset: int = 0) -> dict[str, object]:
    stabilization = [100 + offset] * 20
    warm = [200 + offset + (index % 5) for index in range(100)]
    warm_output = [20 + offset + (index % 3) for index in range(100)]
    rss = [
        {
            "phase": phase,
            "currentBytes": 1_000_000 + offset + index,
            "peakBytes": 2_000_000 + offset + index,
        }
        for index, phase in enumerate(collection._RSS_PHASES)
    ]
    return {
        "schemaVersion": 2,
        "result": "measured",
        "purpose": "measurement-only-target-fragment",
        "protocol": {
            "id": collection.PROTOCOL_ID,
            "version": collection.PROTOCOL_VERSION,
            "descriptorSha256": collection.PROTOCOL_DESCRIPTOR_SHA256,
            "targetFragmentSchemaSha256": (
                collection.TARGET_FRAGMENT_SCHEMA_SHA256
            ),
        },
        "launchChallenge": challenge,
        "processId": process_id,
        "freshProcessRequired": True,
        "executionSurface": "synchronous-public-api",
        "model": copy.deepcopy(collection._MODEL),
        "runtime": _runtime(),
        "session": copy.deepcopy(collection._SESSION),
        "timing": copy.deepcopy(collection._TIMING),
        "stabilization": {
            "method": "bounded-batch-median-relative-change",
            "batchSize": 5,
            "thresholdBasisPoints": 1000,
            "requiredConsecutiveTransitions": 3,
            "maximumRuns": 100,
            "actualRuns": 20,
            "inferenceMicroseconds": stabilization,
            "batchMedianMicroseconds": [100 + offset] * 4,
            "result": "stabilized",
        },
        "measurements": {
            "coldRuntimeLoadMicroseconds": [1000 + offset],
            "sessionCreateMicroseconds": [2000 + offset],
            "dataPreparationMicroseconds": [3000 + offset],
            "firstRunMicroseconds": [4000 + offset],
            "firstOutputMaterializationMicroseconds": [5000 + offset],
            "warmRunMicroseconds": warm,
            "warmOutputMaterializationMicroseconds": warm_output,
            "throughput": [
                {
                    "completedRuns": 10 + index + offset,
                    "durationMicroseconds": 1_000_000 + index,
                }
                for index in range(3)
            ],
        },
        "providerAssignment": {
            "providerId": "cpu",
            "reportedName": "CPUExecutionProvider",
            "discoverable": True,
            "registered": True,
            "requirement": "full",
            "fallbackPolicy": "reject-any",
            "nodeExecutionCount": 1,
            "nodeExecutionsByProvider": {"cpu": 1},
            "fallbackObserved": False,
        },
        "resources": {
            "rssScope": "total-process",
            "rssSamples": rss,
            "nativeRss": {"status": "not-exposed-by-target-api"},
            "cpuUtilization": {"status": "not-exposed-by-target-api"},
            "thermalStart": "host-evidence-required",
            "thermalEnd": "host-evidence-required",
            "powerMode": "host-evidence-required",
        },
        "lifecycle": copy.deepcopy(collection._LIFECYCLE),
        "claimBoundary": collection._FRAGMENT_CLAIM,
    }


def _repository_evidence() -> dict[str, object]:
    result: dict[str, object] = {
        "sourceManifest": {"sizeBytes": 1, "sha256": "f" * 64}
    }
    for name, (_path, size, sha256) in collection._REPOSITORY_EVIDENCE_FILES.items():
        result[name] = {"sizeBytes": size, "sha256": sha256}
    return result


def _artifacts() -> dict[str, object]:
    manifest = _build_manifest()
    artifact, _lock_sha256 = _selected_macos_artifact()
    runtime_payload = next(
        value
        for value in artifact["expected_files"]
        if value["staged_path"] == "libonnxruntime.1.dylib"
    )
    payloads = sorted(
        (
            {
                "id": PurePath(value["staged_path"]).name,
                "sizeBytes": value["size_bytes"],
                "sha256": value["sha256"],
            }
            for value in artifact["expected_files"]
        ),
        key=lambda value: value["id"],
    )
    return {
        "applicationTree": {
            "format": "canonical-application-tree-v1",
            "fileCount": 4,
            "directoryCount": 3,
            "symbolicLinkCount": 2,
            "byteCount": 100_000_000,
            "sha256": "1" * 64,
        },
        "executable": {
            "id": "Fonix Reference",
            "sizeBytes": 1_000_000,
            "sha256": "3" * 64,
        },
        "shimLibrary": {
            "id": "libdort_core.dylib",
            "sizeBytes": 200_000,
            "sha256": "4" * 64,
        },
        "runtimeLibrary": {
            "id": runtime_payload["staged_path"],
            "sizeBytes": runtime_payload["size_bytes"],
            "sha256": runtime_payload["sha256"],
        },
        "providerDependencies": [],
        "nativeLock": {
            "sizeBytes": 1,
            "sha256": manifest["artifact"]["lockSha256"],
        },
        "packageVersion": "0.1.0-dev.1",
        "runtimeVersion": "1.27.1",
        "resolverManifest": {
            "id": "fonix-native-artifact-manifest.json",
            "sizeBytes": 1,
            "sha256": "5" * 64,
        },
        "embeddedBuildManifestSha256": "6" * 64,
        "embeddedBuildManifestCanonicalSha256": _canonical_hash(manifest),
        "embeddedBuildManifest": manifest,
        "nativePayloadFiles": payloads,
        "nativePayloadRelationship": (
            "lock-resolver-source-bytes;packaged-equivalence-requires-platform-audit"
        ),
        "repositoryEvidence": _repository_evidence(),
    }


def _environment(
    process_ids: list[int], challenges: list[str] | None = None
) -> dict[str, object]:
    if challenges is None:
        challenges = [f"{index + 10:x}" * 64 for index in range(len(process_ids))]
    return {
        "platform": "macos",
        "architecture": "arm64",
        "deviceIdentitySha256": "7" * 64,
        "osVersion": "15.0",
        "osBuild": "24A1",
        "driverIdentity": "platform-managed",
        "firmwareIdentity": "platform-managed",
        "cpuUtilization": {
            "status": "not-exposed",
            "reason": "target-api-not-integrated",
        },
        "launchObservations": [
            {
                "index": index,
                "launchChallenge": challenges[index],
                "processId": process_id,
                "powerModeStart": "not-exposed-by-host-api",
                "powerModeEnd": "not-exposed-by-host-api",
                "thermalStateStart": "nominal",
                "thermalStateEnd": "nominal",
            }
            for index, process_id in enumerate(process_ids)
        ],
        "comparability": {
            "status": "incomplete",
            "powerMode": {
                "availability": "unavailable",
                "stability": "indeterminate",
                "stableValue": None,
            },
            "thermalState": {
                "availability": "available",
                "drift": "none-observed",
                "stableValue": "nominal",
            },
            "reasons": ["power-mode-unavailable"],
        },
    }


def _host_observation_payload(environment: dict[str, object], count: int) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 1,
            "result": "measured",
            "claimStatus": "measurement-only",
            "purpose": "cpu-benchmark-host-observations",
            "launchCount": count,
            "environment": environment,
            "claimBoundary": collection._HOST_OBSERVATION_CLAIM,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class StrictJsonAndMathTests(unittest.TestCase):
    def test_oversized_json_integer_is_a_typed_error(self) -> None:
        payload = b'{"value":' + (b"9" * 5000) + b"}"

        with self.assertRaises(collection.CpuBenchmarkCollectionError):
            collection.strict_json_loads(
                payload,
                label="oversized integer fixture",
                maximum_bytes=8192,
            )

    def test_strict_json_rejects_duplicates_depth_size_and_nonfinite(self) -> None:
        for payload, options in (
            (b'{"x":1,"x":2}', {}),
            (b'[[[[1]]]]', {"maximum_depth": 3}),
            (b'{"x":NaN}', {}),
            (b'{}', {"maximum_bytes": 1}),
            (b'\xff', {}),
        ):
            with self.subTest(payload=payload), self.assertRaises(
                collection.CpuBenchmarkCollectionError
            ):
                collection.strict_json_loads(payload, label="fixture", **options)

    def test_strict_json_ignores_brackets_inside_strings(self) -> None:
        self.assertEqual(
            collection.strict_json_loads(
                b'{"value":"[[[\\\"}]]]"}', label="fixture", maximum_depth=2
            ),
            {"value": '[[["}]]]'},
        )

    def test_nearest_rank_statistics_and_half_up_rate_are_canonical(self) -> None:
        self.assertEqual(
            collection.canonical_statistics([5, 1, 4, 2, 3]),
            {"count": 5, "p50": 3, "p95": 5, "p99": 5, "max": 5},
        )
        self.assertEqual(collection.throughput_rate_milli(1, 3), 333_333_333)
        self.assertEqual(collection.throughput_rate_milli(1, 16), 62_500_000)
        with self.assertRaises(collection.CpuBenchmarkCollectionError):
            collection.canonical_statistics([True])


class FragmentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fragment = _fragment("a" * 64, 123)

    def test_accepts_exact_v2_fragment_and_returns_plain_copy(self) -> None:
        result = collection.validate_fragment(
            self.fragment,
            expected_challenge="a" * 64,
            expected_process_id=123,
            raw_sha256="b" * 64,
        )
        self.assertEqual(result, self.fragment)
        self.assertIsNot(result, self.fragment)

    def test_rejects_unknown_field_and_protocol_hash_drift(self) -> None:
        for mutation in (
            lambda value: value.__setitem__("unknown", True),
            lambda value: value["protocol"].__setitem__(
                "descriptorSha256", "0" * 64
            ),
        ):
            value = copy.deepcopy(self.fragment)
            mutation(value)
            with self.assertRaises(collection.CpuBenchmarkCollectionError):
                collection.validate_fragment(value)

    def test_recomputes_stabilization_medians_and_rejects_trailing_batches(self) -> None:
        tampered = copy.deepcopy(self.fragment)
        tampered["stabilization"]["batchMedianMicroseconds"][0] += 1
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "medians"
        ):
            collection.validate_fragment(tampered)

        trailing = copy.deepcopy(self.fragment)
        trailing["stabilization"]["actualRuns"] = 25
        trailing["stabilization"]["inferenceMicroseconds"].extend([100] * 5)
        trailing["stabilization"]["batchMedianMicroseconds"].append(100)
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "first stop point"
        ):
            collection.validate_fragment(trailing)

    def test_rejects_nonmonotonic_peak_rss(self) -> None:
        tampered = copy.deepcopy(self.fragment)
        tampered["resources"]["rssSamples"][4]["peakBytes"] = 1_999_999
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "peak decreased"
        ):
            collection.validate_fragment(tampered)

    def test_rejects_provider_inventory_assignment_mismatch(self) -> None:
        tampered = copy.deepcopy(self.fragment)
        tampered["providerAssignment"]["reportedName"] = "OtherProvider"
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "compiled provider"
        ):
            collection.validate_fragment(tampered)


@unittest.skipUnless(os.name == "posix", "requires POSIX no-follow descriptors")
class FileAndTreeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_regular_file_identity_rejects_link_and_detects_path_replacement(self) -> None:
        path = self.root / "artifact.bin"
        path.write_bytes(b"trusted bytes")
        identity = collection.regular_file_identity(path, label="artifact")
        self.assertEqual(identity["sizeBytes"], len(b"trusted bytes"))
        self.assertEqual(identity["sha256"], hashlib.sha256(b"trusted bytes").hexdigest())

        link = self.root / "artifact-link"
        link.symlink_to(path.name)
        with self.assertRaises(collection.CpuBenchmarkCollectionError):
            collection.regular_file_identity(link, label="artifact")

        replacement = self.root / "replacement.bin"
        replacement.write_bytes(b"different bytes")
        real_read = collection.os.read
        replaced = False

        def replacing_read(descriptor: int, count: int) -> bytes:
            nonlocal replaced
            value = real_read(descriptor, count)
            if value and not replaced:
                replaced = True
                os.replace(replacement, path)
            return value

        path.write_bytes(b"trusted bytes")
        with (
            mock.patch.object(collection.os, "read", side_effect=replacing_read),
            self.assertRaisesRegex(
                collection.CpuBenchmarkCollectionError, "changed"
            ),
        ):
            collection.regular_file_identity(path, label="artifact")

    def test_tree_hashes_empty_files_and_contained_framework_symlinks(self) -> None:
        app = self.root / "Example.app"
        binary = app / "Frameworks/Foo.framework/Versions/A/Foo"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"framework binary")
        (app / "empty.marker").write_bytes(b"")
        current = app / "Frameworks/Foo.framework/Versions/Current"
        current.symlink_to("A")
        public_binary = app / "Frameworks/Foo.framework/Foo"
        public_binary.symlink_to("Versions/Current/Foo")

        first = collection.canonical_application_tree_identity(app)
        second = collection.canonical_application_tree_identity(app)
        self.assertEqual(first, second)
        self.assertEqual(first["fileCount"], 2)
        self.assertEqual(first["symbolicLinkCount"], 2)
        self.assertEqual(first["byteCount"], len(b"framework binary"))

    def test_tree_rejects_absolute_dangling_and_cyclic_links(self) -> None:
        for target, companion in (
            ("/tmp/outside", None),
            ("missing", None),
            ("other", ("other", "cycle")),
        ):
            with self.subTest(target=target):
                app = self.root / target.replace("/", "_")
                app.mkdir()
                (app / "binary").write_bytes(b"app")
                (app / "cycle").symlink_to(target)
                if companion is not None:
                    (app / companion[0]).symlink_to(companion[1])
                with self.assertRaises(collection.CpuBenchmarkCollectionError):
                    collection.canonical_application_tree_identity(app)


@unittest.skipUnless(os.name == "posix", "requires POSIX no-follow descriptors")
class NativeAndRepositoryBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def _shim(self, manifest: dict[str, object]) -> Path:
        path = self.root / "libdort_core.dylib"
        encoded = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        path.write_bytes(b"synthetic-binary\x00" + encoded + b"\x00tail")
        return path

    def test_native_binding_matches_current_lock_resolver_and_embedded_manifest(self) -> None:
        shim = self._shim(_build_manifest())
        result = collection.validate_native_build_binding(
            repository=REPOSITORY,
            shim_binary=shim,
            resolver_manifest=(
                REPOSITORY
                / "example/assets/fonix/fonix-native-artifact-manifest.json"
            ),
        )
        self.assertEqual(result["buildManifest"], _build_manifest())
        self.assertEqual(result["packageVersion"], "0.1.0-dev.1")
        self.assertEqual(result["runtimeVersion"], "1.27.1")
        self.assertEqual(
            [value["id"] for value in result["nativePayloadFiles"]],
            ["libonnxruntime.1.dylib", "libonnxruntime.dylib"],
        )
        self.assertEqual(
            result["embeddedBuildManifestCanonicalSha256"],
            _canonical_hash(_build_manifest()),
        )

    def test_native_binding_accepts_spaced_payload_basename(self) -> None:
        synthetic = self.root / "repository"
        (synthetic / "native").mkdir(parents=True)
        shutil.copyfile(REPOSITORY / "pubspec.yaml", synthetic / "pubspec.yaml")
        lock = json.loads(
            (REPOSITORY / "native/versions.lock.yaml").read_text(encoding="utf-8")
        )
        artifact = next(
            value
            for value in lock["artifacts"]
            if value["id"] == "onnxruntime-1.27.1-macos-arm64-cpu"
        )
        artifact["expected_files"][0]["staged_path"] = "Runtime Library.dylib"
        lock_bytes = (
            json.dumps(lock, ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        (synthetic / "native/versions.lock.yaml").write_bytes(lock_bytes)
        lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
        resolver = collection._selected_artifact_resolver_identity(
            lock,
            artifact,
            lock_sha256,
        )
        current_resolver = json.loads(
            (
                REPOSITORY
                / "example/assets/fonix/fonix-native-artifact-manifest.json"
            ).read_text(encoding="utf-8")
        )
        resolver["archiveInspections"] = current_resolver["archiveInspections"]
        resolver_path = self.root / "spaced-resolver.json"
        resolver_path.write_text(json.dumps(resolver), encoding="utf-8")
        manifest = collection._embedded_manifest_expected(
            lock,
            artifact,
            lock_sha256,
        )

        result = collection.validate_native_build_binding(
            repository=synthetic,
            shim_binary=self._shim(manifest),
            resolver_manifest=resolver_path,
        )

        self.assertIn(
            "Runtime Library.dylib",
            [value["id"] for value in result["nativePayloadFiles"]],
        )

    def test_native_binding_rejects_resolver_and_embedded_tampering(self) -> None:
        manifest_path = self.root / "resolver.json"
        resolver = json.loads(
            (
                REPOSITORY
                / "example/assets/fonix/fonix-native-artifact-manifest.json"
            ).read_text(encoding="utf-8")
        )
        resolver["lock"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(resolver), encoding="utf-8")
        with self.assertRaises(collection.CpuBenchmarkCollectionError):
            collection.validate_native_build_binding(
                repository=REPOSITORY,
                shim_binary=self._shim(_build_manifest()),
                resolver_manifest=manifest_path,
            )

        tampered = _build_manifest()
        tampered["artifact"]["sourceSha256"] = "0" * 64
        with self.assertRaises(collection.CpuBenchmarkCollectionError):
            collection.validate_native_build_binding(
                repository=REPOSITORY,
                shim_binary=self._shim(tampered),
                resolver_manifest=(
                    REPOSITORY
                    / "example/assets/fonix/fonix-native-artifact-manifest.json"
                ),
            )

    def test_repository_evidence_verifies_closed_manifest_and_exact_assets(self) -> None:
        synthetic = self.root / "repository"
        source_helper = synthetic / "tool/ci/source_checksum_manifest.py"
        source_helper.parent.mkdir(parents=True)
        shutil.copyfile(
            REPOSITORY / "tool/ci/source_checksum_manifest.py", source_helper
        )
        for relative, _size, _sha256 in collection._REPOSITORY_EVIDENCE_FILES.values():
            target = synthetic.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPOSITORY.joinpath(*relative.split("/")), target)
        (synthetic / "MANIFEST.sha256").write_bytes(
            source_checksum_manifest.build_manifest(synthetic)
        )

        result = collection.repository_evidence_identity(synthetic)
        self.assertEqual(
            result["protocolDescriptor"]["sha256"],
            collection.PROTOCOL_DESCRIPTOR_SHA256,
        )
        marker = self.root / "candidate-helper-executed"
        source_helper.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('unsafe')\n"
            "def check_manifest(repository, manifest):\n"
            "    return '0' * 64\n",
            encoding="utf-8",
        )
        (synthetic / "MANIFEST.sha256").write_bytes(
            source_checksum_manifest.build_manifest(synthetic)
        )
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError,
            "sourceManifestValidator",
        ):
            collection.repository_evidence_identity(synthetic)
        self.assertFalse(marker.exists())

        with (synthetic / "example/assets/models/cpu_benchmark_matmul.json").open(
            "ab"
        ) as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "does not match"
        ):
            collection.repository_evidence_identity(synthetic)


class CollectionDerivationTests(unittest.TestCase):
    def _derive(
        self,
        fragments: list[dict[str, object]],
        *,
        process_ids: list[int] | None = None,
        artifacts: dict[str, object] | None = None,
        environment: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if process_ids is None:
            process_ids = [value["processId"] for value in fragments]
        launches = [
            (value["launchChallenge"], process_ids[index])
            for index, value in enumerate(fragments)
        ]
        payloads = [
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            for value in fragments
        ]
        checked_environment = environment or _environment(
            process_ids,
            [challenge for challenge, _process_id in launches],
        )
        return collection.derive_collection(
            fragment_payloads=payloads,
            host_observation_payload=_host_observation_payload(
                checked_environment,
                len(fragments),
            ),
            expected_launches=launches,
            artifacts=artifacts or _artifacts(),
            collector_sha256="d" * 64,
        )

    def test_derives_raw_preserving_stats_rates_resources_and_zero_dependencies(self) -> None:
        fragments = [_fragment("a" * 64, 42), _fragment("b" * 64, 43, offset=1)]
        result = self._derive(fragments)
        self.assertEqual(result["launchCount"], 2)
        self.assertEqual(result["artifacts"]["providerDependencies"], [])
        self.assertEqual(result["rawFragments"][0]["fragment"], fragments[0])
        self.assertEqual(
            result["rawHostObservation"]["record"]["environment"],
            result["environment"],
        )
        cold = result["aggregates"]["measurements"][
            "coldRuntimeLoadMicroseconds"
        ]
        self.assertEqual(cold["samples"], [1000, 1001])
        self.assertEqual(cold["statistics"]["p50"], 1000)
        throughput = result["aggregates"]["throughput"]
        self.assertEqual(len(throughput["windows"]), 6)
        self.assertEqual(
            throughput["aggregateRunsPerSecondMilli"],
            collection.throughput_rate_milli(
                throughput["totalCompletedRuns"],
                throughput["totalDurationMicroseconds"],
            ),
        )
        self.assertNotIn(tempfile.gettempdir(), json.dumps(result))

    def test_packaged_labels_allow_spaces_but_runtime_identity_stays_token(
        self,
    ) -> None:
        fragments = [_fragment("a" * 64, 1), _fragment("b" * 64, 2)]

        result = self._derive(fragments)

        self.assertEqual(result["artifacts"]["executable"]["id"], "Fonix Reference")

        invalid_fragments = copy.deepcopy(fragments)
        for fragment in invalid_fragments:
            fragment["runtime"]["runtimeLibraryIdentity"] = "runtime library"
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError,
            "runtimeLibraryIdentity is not a bounded token",
        ):
            self._derive(invalid_fragments)

        contradictory_artifacts = _artifacts()
        contradictory_artifacts["runtimeLibrary"]["id"] = "runtime library"
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError,
            "runtime/native build binding",
        ):
            self._derive(fragments, artifacts=contradictory_artifacts)

        unsafe_artifacts = _artifacts()
        unsafe_artifacts["executable"]["id"] = "Fonix/Reference"
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError,
            "path-free label",
        ):
            self._derive(fragments, artifacts=unsafe_artifacts)

    def test_accepts_reused_pid_with_distinct_challenges(self) -> None:
        fragments = [_fragment("a" * 64, 77), _fragment("b" * 64, 77, offset=1)]
        result = self._derive(fragments, process_ids=[77, 77])
        self.assertEqual(
            [value["processId"] for value in result["rawFragments"]], [77, 77]
        )

    def test_rejects_duplicate_challenge_raw_tamper_and_cross_launch_tuple_drift(
        self,
    ) -> None:
        duplicate = [_fragment("a" * 64, 1), _fragment("a" * 64, 2)]
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "challenges are not unique"
        ):
            self._derive(duplicate)

        fragments = [_fragment("a" * 64, 1), _fragment("b" * 64, 2)]
        payloads = [
            json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")
            for value in fragments
        ]
        tampered = payloads[1].replace(b'"processId":2', b'"processId":3')
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "process id"
        ):
            collection.derive_collection(
                fragment_payloads=[payloads[0], tampered],
                host_observation_payload=_host_observation_payload(
                    _environment([1, 2], ["a" * 64, "b" * 64]),
                    2,
                ),
                expected_launches=[("a" * 64, 1), ("b" * 64, 2)],
                artifacts=_artifacts(),
                collector_sha256="d" * 64,
            )

        drift = copy.deepcopy(fragments)
        drift[1]["runtime"]["runtimeVersion"] = "1.27.2"
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "identity tuple"
        ):
            self._derive(drift)

        for key, value in (
            ("runtimeVersion", "999.999.0"),
            ("runtimeLibraryIdentity", "unused-runtime.dylib"),
            ("packageVersion", "9.9.9"),
            ("shimNativeIdentity", "unused-shim"),
            ("negotiatedOrtApi", 999),
        ):
            with self.subTest(key=key):
                contradictory = copy.deepcopy(fragments)
                contradictory[0]["runtime"][key] = value
                contradictory[1]["runtime"][key] = value
                with self.assertRaisesRegex(
                    collection.CpuBenchmarkCollectionError,
                    "runtime/native build binding",
                ):
                    self._derive(contradictory)

    def test_rejects_native_manifest_and_observation_tampering(self) -> None:
        fragments = [_fragment("a" * 64, 1), _fragment("b" * 64, 2)]
        artifacts = _artifacts()
        artifacts["embeddedBuildManifest"]["artifact"]["sourceSha256"] = "0" * 64
        artifacts["embeddedBuildManifestCanonicalSha256"] = _canonical_hash(
            artifacts["embeddedBuildManifest"]
        )
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "runtime/native build binding"
        ):
            self._derive(fragments, artifacts=artifacts)

        environment = _environment([1, 2])
        environment["launchObservations"][1]["index"] = 0
        with self.assertRaises(collection.CpuBenchmarkCollectionError):
            self._derive(fragments, environment=environment)

        environment = _environment([1, 2])
        environment["comparability"]["status"] = "baseline-comparable"
        with self.assertRaisesRegex(
            collection.CpuBenchmarkCollectionError, "comparability"
        ):
            self._derive(fragments, environment=environment)

    def test_host_power_and_thermal_values_use_platform_specific_grammars(
        self,
    ) -> None:
        challenges = ["a" * 64, "b" * 64]
        launches = [(challenges[0], 1), (challenges[1], 2)]
        macos = _environment([1, 2], challenges)
        power_mode = (
            "macos-ac-power-low-power-off-profile-sha256-" + "8" * 64
        )
        for observation in macos["launchObservations"]:
            observation["powerModeStart"] = power_mode
            observation["powerModeEnd"] = power_mode
        macos["comparability"] = {
            "status": "baseline-comparable",
            "powerMode": {
                "availability": "available",
                "stability": "stable",
                "stableValue": power_mode,
            },
            "thermalState": {
                "availability": "available",
                "drift": "none-observed",
                "stableValue": "nominal",
            },
            "reasons": [],
        }
        collection._validate_environment(macos, launches)

        for key, value in (
            ("powerModeStart", "performance"),
            ("thermalStateStart", "cool"),
        ):
            with self.subTest(platform="macos", key=key):
                invalid = copy.deepcopy(macos)
                invalid["launchObservations"][0][key] = value
                with self.assertRaisesRegex(
                    collection.CpuBenchmarkCollectionError,
                    "closed host",
                ):
                    collection._validate_environment(invalid, launches)

        linux = _environment([1, 2], challenges)
        linux["platform"] = "linux"
        linux["architecture"] = "x86_64"
        for observation in linux["launchObservations"]:
            observation["powerModeStart"] = "cpu-governor-performance"
            observation["powerModeEnd"] = "cpu-governor-performance"
            observation["thermalStateStart"] = "not-exposed-by-host-api"
            observation["thermalStateEnd"] = "not-exposed-by-host-api"
        linux["comparability"] = {
            "status": "incomplete",
            "powerMode": {
                "availability": "available",
                "stability": "stable",
                "stableValue": "cpu-governor-performance",
            },
            "thermalState": {
                "availability": "unavailable",
                "drift": "indeterminate",
                "stableValue": None,
            },
            "reasons": ["thermal-state-unavailable"],
        }
        collection._validate_environment(linux, launches)

        for key, value in (
            ("powerModeStart", "performance"),
            ("thermalStateStart", "nominal"),
        ):
            with self.subTest(platform="linux", key=key):
                invalid = copy.deepcopy(linux)
                invalid["launchObservations"][0][key] = value
                with self.assertRaisesRegex(
                    collection.CpuBenchmarkCollectionError,
                    "closed host",
                ):
                    collection._validate_environment(invalid, launches)


if __name__ == "__main__":
    unittest.main()
