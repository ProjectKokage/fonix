from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


CI_DIRECTORY = Path(__file__).resolve().parents[1]
if str(CI_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(CI_DIRECTORY))

import generate_release_sbom as release_evidence
import source_checksum_manifest


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


class _EvidenceFixture:
    payload = b"exact-native-payload"
    upstream_license = b"upstream license\n"
    third_party_notice = b"third party notice\n"
    header = b"ort header\n"
    ep_header = b"ort ep header\n"
    archive = b"archive"

    targets = (
        ("ios", "arm64", "device", "linked"),
        ("ios", "arm64", "simulator", "linked"),
        ("macos", "arm64", "default", "bundled"),
        ("android", "arm64-v8a", "default", "bundled"),
        ("android", "x86_64", "default", "bundled"),
        ("linux", "x86_64", "default", "bundled"),
        ("linux", "arm64", "default", "bundled"),
        ("windows", "x64", "default", "bundled"),
    )

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = root / "repository"
        self.stage = root / "stage"
        self.output = root / "output"
        self.repository.mkdir()
        self.stage.mkdir()
        self.output.mkdir()
        self.artifact_id = "onnxruntime-test-macos-arm64-cpu"

        (self.repository / "native").mkdir()
        compatibility_root = self.repository / "third_party" / "onnxruntime"
        (compatibility_root / "include").mkdir(parents=True)
        (compatibility_root / "include" / "onnxruntime_c_api.h").write_bytes(
            self.header
        )
        (compatibility_root / "include" / "onnxruntime_ep_c_api.h").write_bytes(
            self.ep_header
        )
        (compatibility_root / "LICENSE").write_bytes(self.upstream_license)
        (self.repository / "pubspec.yaml").write_text(
            "name: fonix\n"
            "description: fixture\n"
            "version: 0.1.0-dev.1\n"
            "environment:\n"
            "  sdk: ^3.11.5\n",
            encoding="utf-8",
        )
        (self.repository / "pubspec.lock").write_text(
            "# Generated fixture\n"
            "packages:\n"
            "  crypto:\n"
            "    dependency: \"direct main\"\n"
            "    description:\n"
            "      name: crypto\n"
            f"      sha256: {_sha256(b'crypto-archive')}\n"
            "      url: \"https://pub.dev\"\n"
            "    source: hosted\n"
            "    version: \"3.0.7\"\n"
            "  path:\n"
            "    dependency: transitive\n"
            "    description:\n"
            "      name: path\n"
            f"      sha256: {_sha256(b'path-archive')}\n"
            "      url: \"https://pub.dev\"\n"
            "    source: hosted\n"
            "    version: \"1.9.1\"\n"
            "sdks:\n"
            "  dart: \">=3.11.5 <4.0.0\"\n",
            encoding="utf-8",
        )
        self.lock = self._lock()
        self._write_lock_and_source_manifest()
        self._write_stage()

    def _lock(self) -> dict[str, object]:
        compatibility = {
            "c_api": 27,
            "header": self._source_input(
                "third_party/onnxruntime/include/onnxruntime_c_api.h", self.header
            ),
            "ep_header": self._source_input(
                "third_party/onnxruntime/include/onnxruntime_ep_c_api.h",
                self.ep_header,
            ),
            "license": self._source_input(
                "third_party/onnxruntime/LICENSE", self.upstream_license
            ),
        }
        artifacts: list[dict[str, object]] = []
        for operating_system, architecture, variant, runtime_mode in self.targets:
            artifact_id = (
                self.artifact_id
                if operating_system == "macos"
                else f"onnxruntime-test-{operating_system}-{architecture}-{variant}-cpu"
            )
            artifacts.append(
                {
                    "id": artifact_id,
                    "target": {
                        "os": operating_system,
                        "architecture": architecture,
                        "variant": variant,
                        "min_os": "1.0",
                    },
                    "flavor": "cpu",
                    "runtime_mode": runtime_mode,
                    "source": {
                        "url": f"https://example.com/{artifact_id}.tgz",
                        "source_revision": "a" * 40,
                        "sha256": _sha256(self.archive),
                        "size_bytes": len(self.archive),
                        "archive": "tgz",
                    },
                    "containers": [],
                    "expected_files": [
                        {
                            "path": "archive/runtime.bin",
                            "staged_path": "runtime.bin",
                            "sha256": _sha256(self.payload),
                            "size_bytes": len(self.payload),
                        }
                    ],
                    "expected_symlinks": [],
                    "notices": [
                        {
                            "id": "MIT",
                            "container_depth": 0,
                            "path": "archive/LICENSE",
                            "staged_path": "notices/LICENSE",
                            "sha256": _sha256(self.upstream_license),
                            "size_bytes": len(self.upstream_license),
                        },
                        {
                            "id": "ThirdPartyNotices",
                            "container_depth": 0,
                            "path": "archive/ThirdPartyNotices.txt",
                            "staged_path": "notices/ThirdPartyNotices.txt",
                            "sha256": _sha256(self.third_party_notice),
                            "size_bytes": len(self.third_party_notice),
                        },
                    ],
                    "providers": [
                        {
                            "wrapper_id": "cpu",
                            "reported_name": "CPUExecutionProvider",
                        }
                    ],
                    "build": {
                        "source_built": False,
                        "toolchain": "synthetic offline test fixture",
                        "flags": [],
                        "patches": [],
                    },
                    "licenses": [
                        {
                            "id": "MIT",
                            "notice_path": "third_party/onnxruntime/LICENSE",
                            "sha256": _sha256(self.upstream_license),
                            "size_bytes": len(self.upstream_license),
                        }
                    ],
                }
            )
        return {
            "schema": 2,
            "snapshot_date": "2026-08-06",
            "release_state": "unreleased-preview",
            "shim": {"abi": 1, "required_ort_api": 27, "source_revision": None},
            "onnxruntime": {"compatibility_floor": compatibility},
            "release_targets": [
                {
                    "os": operating_system,
                    "architecture": architecture,
                    "variant": variant,
                    "flavor": "cpu",
                }
                for operating_system, architecture, variant, _ in self.targets
            ],
            "artifacts": artifacts,
        }

    def _source_input(self, path: str, contents: bytes) -> dict[str, object]:
        return {
            "version": "1.27.1",
            "source_repository": "https://github.com/microsoft/onnxruntime",
            "source_ref": "v1.27.1",
            "path": path,
            "sha256": _sha256(contents),
            "size_bytes": len(contents),
        }

    @property
    def selected_artifact(self) -> dict[str, object]:
        return next(
            artifact
            for artifact in self.lock["artifacts"]
            if artifact["id"] == self.artifact_id
        )

    def _write_lock_and_source_manifest(self) -> None:
        lock_path = self.repository / release_evidence.NATIVE_LOCK_PATH
        lock_path.write_text(
            json.dumps(self.lock, indent=2) + "\n", encoding="utf-8"
        )
        source_checksum_manifest.generate_manifest(
            self.repository, self.repository / release_evidence.SOURCE_MANIFEST_PATH
        )

    def _write_stage(self) -> None:
        (self.stage / "notices").mkdir(exist_ok=True)
        (self.stage / "runtime.bin").write_bytes(self.payload)
        (self.stage / "notices" / "LICENSE").write_bytes(self.upstream_license)
        (self.stage / "notices" / "ThirdPartyNotices.txt").write_bytes(
            self.third_party_notice
        )
        lock_raw = (self.repository / release_evidence.NATIVE_LOCK_PATH).read_bytes()
        manifest = release_evidence._manifest_projection(
            self.selected_artifact, self.lock, _sha256(lock_raw)
        )
        manifest["archiveInspections"] = [
            {
                "depth": 0,
                "format": "tar",
                "memberCount": 3,
                "regularFileCount": 3,
                "directoryCount": 0,
                "symbolicLinkCount": 0,
                "compressedBytes": len(self.archive),
                "uncompressedBytes": (
                    len(self.payload)
                    + len(self.upstream_license)
                    + len(self.third_party_notice)
                ),
            }
        ]
        (self.stage / release_evidence.STAGED_MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def rewrite_lock(self) -> None:
        self._write_lock_and_source_manifest()
        self._write_stage()


class SourceChecksumManifestTests(unittest.TestCase):
    def test_generation_is_sorted_deterministic_and_checkable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "docs").mkdir()
            (repository / "docs" / "z.md").write_bytes(b"z\n")
            (repository / "docs" / "a.md").write_bytes(b"a\n")
            (repository / ".gitattributes").write_bytes(b"*.onnx binary\n")
            (repository / "pubspec.yaml").write_bytes(b"name: fonix\n")
            output = repository / "MANIFEST.sha256"

            first = source_checksum_manifest.generate_manifest(repository, output)
            first_bytes = output.read_bytes()
            second = source_checksum_manifest.generate_manifest(repository, output)

            self.assertEqual(first, second)
            self.assertEqual(first_bytes, output.read_bytes())
            self.assertEqual(
                [path for path, _ in source_checksum_manifest.parse_manifest(first_bytes)],
                [".gitattributes", "docs/a.md", "docs/z.md", "pubspec.yaml"],
            )
            self.assertEqual(first, source_checksum_manifest.check_manifest(repository, output))

    def test_tamper_duplicate_and_traversal_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "README.md").write_bytes(b"before\n")
            output = repository / "MANIFEST.sha256"
            source_checksum_manifest.generate_manifest(repository, output)
            (repository / "README.md").write_bytes(b"after!\n")
            with self.assertRaises(source_checksum_manifest.SourceManifestError):
                source_checksum_manifest.check_manifest(repository, output)

            digest = "0" * 64
            with self.assertRaises(source_checksum_manifest.SourceManifestError):
                source_checksum_manifest.parse_manifest(
                    f"{digest}  ./README.md\n{digest}  ./README.md\n".encode()
                )
            with self.assertRaises(source_checksum_manifest.SourceManifestError):
                source_checksum_manifest.parse_manifest(
                    f"{digest}  ./../escape\n".encode()
                )

    def test_unknown_top_level_entry_and_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "README.md").write_bytes(b"source\n")
            (repository / "rogue.bin").write_bytes(b"rogue")
            with self.assertRaisesRegex(
                source_checksum_manifest.SourceManifestError, "unknown top-level"
            ):
                source_checksum_manifest.build_manifest(repository)
            (repository / "rogue.bin").unlink()
            if os.name != "nt":
                (repository / "docs").symlink_to(repository / "README.md")
                with self.assertRaisesRegex(
                    source_checksum_manifest.SourceManifestError, "must not be a link"
                ):
                    source_checksum_manifest.build_manifest(repository)

    def test_source_file_size_bound_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "README.md").write_bytes(b"bounded\n")
            with mock.patch.object(source_checksum_manifest, "MAX_FILE_BYTES", 4):
                with self.assertRaisesRegex(
                    source_checksum_manifest.SourceManifestError, "size"
                ):
                    source_checksum_manifest.build_manifest(repository)

    def test_generated_python_state_is_not_admitted_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            generated = repository / "tool" / "__pycache__"
            generated.mkdir(parents=True)
            (generated / "verifier.cpython-312.pyc").write_bytes(b"bytecode")
            with self.assertRaisesRegex(
                source_checksum_manifest.SourceManifestError,
                "generated directory",
            ):
                source_checksum_manifest.build_manifest(repository)


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = _EvidenceFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def generate(self) -> release_evidence.EvidenceDocuments:
        return release_evidence.generate_evidence(
            self.fixture.repository,
            self.fixture.stage,
            self.fixture.artifact_id,
        )

    def test_deterministic_spdx_and_audit_metadata_have_exact_identities(self) -> None:
        first = self.generate()
        second = self.generate()
        self.assertEqual(first.sbom_bytes(), second.sbom_bytes())
        self.assertEqual(first.metadata_bytes(), second.metadata_bytes())
        self.assertEqual(first.sbom["spdxVersion"], "SPDX-2.3")
        self.assertEqual(first.sbom["dataLicense"], "CC0-1.0")
        self.assertTrue(first.sbom["documentNamespace"].startswith("urn:uuid:"))
        self.assertEqual(first.sbom["creationInfo"]["created"], "2026-08-06T00:00:00Z")
        self.assertEqual(first.metadata["releaseReadiness"]["ready"], False)
        self.assertIn(
            "root-license-missing", first.metadata["releaseReadiness"]["blockers"]
        )
        self.assertIn(
            "distribution-not-authorized",
            first.metadata["releaseReadiness"]["blockers"],
        )
        self.assertEqual(
            first.metadata["claimBoundary"], release_evidence.AUDIT_CLAIM_BOUNDARY
        )
        self.assertEqual(first.metadata["dartLock"]["dependencyCount"], 2)
        self.assertEqual(
            first.metadata["nativeSelection"]["source"]["sha256"],
            _sha256(self.fixture.archive),
        )
        packages_by_name = {package["name"]: package for package in first.sbom["packages"]}
        self.assertEqual(
            packages_by_name["crypto"]["checksums"],
            [{"algorithm": "SHA256", "checksumValue": _sha256(b"crypto-archive")}],
        )
        self.assertEqual(packages_by_name["path"]["versionInfo"], "1.9.1")
        self.assertEqual(packages_by_name["fonix"]["licenseDeclared"], "NOASSERTION")
        self.assertEqual(
            packages_by_name[self.fixture.artifact_id]["licenseInfoFromFiles"],
            ["MIT", "NOASSERTION"],
        )
        staged_names = {
            file["fileName"]
            for file in first.sbom["files"]
            if file["fileName"].startswith("./native/staged/")
        }
        self.assertIn("./native/staged/runtime.bin", staged_names)
        self.assertIn("./native/staged/notices/LICENSE", staged_names)
        serialized = first.sbom_bytes() + first.metadata_bytes()
        self.assertNotIn(str(self.fixture.root).encode(), serialized)
        self.assertEqual(
            first.metadata["sbom"]["sha256"], _sha256(first.sbom_bytes())
        )
        non_analyzed_ids = {
            package["SPDXID"]
            for package in first.sbom["packages"]
            if package["filesAnalyzed"] is False
        }
        self.assertFalse(
            any(
                relationship["spdxElementId"] in non_analyzed_ids
                and relationship["relationshipType"] == "CONTAINS"
                for relationship in first.sbom["relationships"]
            )
        )

    def test_cli_writes_audit_metadata_but_release_gate_fails_closed(self) -> None:
        sbom = self.fixture.output / "sbom.spdx.json"
        metadata = self.fixture.output / "release-audit.json"
        arguments = [
            "--repository",
            str(self.fixture.repository),
            "--staged-directory",
            str(self.fixture.stage),
            "--artifact-id",
            self.fixture.artifact_id,
            "--sbom-output",
            str(sbom),
            "--metadata-output",
            str(metadata),
        ]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(release_evidence.main(arguments), 0)
        self.assertTrue(sbom.is_file())
        self.assertTrue(metadata.is_file())

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(
                release_evidence.main([*arguments, "--require-release-ready"]), 1
            )
        self.assertTrue(sbom.is_file())
        self.assertTrue(metadata.is_file())

    def test_duplicate_key_and_unknown_manifest_field_are_rejected(self) -> None:
        manifest_path = self.fixture.stage / release_evidence.STAGED_MANIFEST_NAME
        original = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            original.replace("{\n", "{\n  \"schema\": 2,\n", 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "duplicates key"):
            self.generate()

        self.fixture._write_stage()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["unexpected"] = True
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "unknown=unexpected"):
            self.generate()

        self.fixture._write_stage()
        original = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            original.replace('"schema": 2', '"schema": NaN', 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            release_evidence.ReleaseEvidenceError, "non-standard constant"
        ):
            self.generate()

    def test_same_size_hash_tamper_and_size_tamper_are_rejected(self) -> None:
        payload = self.fixture.stage / "runtime.bin"
        payload.write_bytes(b"x" * len(self.fixture.payload))
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "SHA-256"):
            self.generate()

        payload.write_bytes(self.fixture.payload + b"x")
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "size"):
            self.generate()

    @unittest.skipIf(os.name == "nt", "symlink creation is not generally permitted")
    def test_symlink_substitution_is_rejected(self) -> None:
        payload = self.fixture.stage / "runtime.bin"
        outside = self.fixture.root / "outside.bin"
        outside.write_bytes(self.fixture.payload)
        payload.unlink()
        payload.symlink_to(outside)
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "link"):
            self.generate()

    def test_unknown_staged_file_is_rejected(self) -> None:
        (self.fixture.stage / "extra.bin").write_bytes(b"extra")
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "unknown=extra.bin"):
            self.generate()

    def test_unknown_staged_directory_is_rejected(self) -> None:
        (self.fixture.stage / "empty").mkdir()
        with self.assertRaisesRegex(
            release_evidence.ReleaseEvidenceError, "directory set is not closed"
        ):
            self.generate()

    def test_unsafe_lock_path_and_unknown_lock_field_are_rejected(self) -> None:
        self.fixture.selected_artifact["expected_files"][0]["staged_path"] = "../escape"
        self.fixture.rewrite_lock()
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "safe relative path"):
            self.generate()

        self.fixture = _EvidenceFixture(Path(self.temporary.name) / "second")
        self.fixture.lock["unknown"] = True
        self.fixture._write_lock_and_source_manifest()
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "unknown=unknown"):
            self.generate()

    def test_pubspec_lock_unknown_field_is_rejected(self) -> None:
        lock_path = self.fixture.repository / "pubspec.lock"
        text = lock_path.read_text(encoding="utf-8")
        lock_path.write_text(
            text.replace(
                "    version: \"3.0.7\"\n",
                "    unexpected: value\n    version: \"3.0.7\"\n",
                1,
            ),
            encoding="utf-8",
        )
        source_checksum_manifest.generate_manifest(
            self.fixture.repository,
            self.fixture.repository / release_evidence.SOURCE_MANIFEST_PATH,
        )
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "invalid field set"):
            self.generate()

    def test_selected_artifact_must_match_manifest_and_lock(self) -> None:
        with self.assertRaisesRegex(release_evidence.ReleaseEvidenceError, "absent"):
            release_evidence.generate_evidence(
                self.fixture.repository,
                self.fixture.stage,
                "onnxruntime-not-locked",
            )


class ReleaseEvidenceWorkflowTests(unittest.TestCase):
    def test_ci_has_a_standalone_offline_release_evidence_job(self) -> None:
        repository = CI_DIRECTORY.parents[1]
        workflow = (repository / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        section = workflow.split("  release-evidence:\n", 1)[1].split(
            "\n  phase3-fixture-bytes:", 1
        )[0]
        self.assertIn("Offline deterministic release-evidence verification", section)
        self.assertIn("test_release_evidence.py", section)
        self.assertIn("source_checksum_manifest.py check", section)
        self.assertNotIn("curl ", section)
        self.assertNotIn("wget ", section)
        self.assertNotIn("pub get", section)


if __name__ == "__main__":
    unittest.main()
