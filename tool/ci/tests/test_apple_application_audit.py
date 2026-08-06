from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "audit_apple_application.py"
SPEC = importlib.util.spec_from_file_location("audit_apple_application", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit_apple_application = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_apple_application)


class AppleApplicationMetadataAuditTest(unittest.TestCase):
    def _application(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-apple-audit-")
        application = Path(temporary.name) / "Sample.app"
        asset_root = (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix"
        )
        asset_root.mkdir(parents=True)
        notice = b"exact upstream notice fixture\n"
        notice_path = asset_root / "ThirdPartyNotices.txt"
        notice_path.write_bytes(notice)
        manifest = {
            "schema": 2,
            "artifactId": "onnxruntime-1.27.1-macos-arm64-cpu",
            "claimBoundary": audit_apple_application._CLAIM_BOUNDARY,
            "lock": {
                "path": "native/versions.lock.yaml",
                "releaseState": "unreleased-preview",
                "sha256": "a" * 64,
                "snapshotDate": "2026-08-06",
            },
            "target": {
                "os": "macos",
                "architecture": "arm64",
                "variant": "default",
                "minimumOs": "14.0",
                "flavor": "cpu",
                "runtimeMode": "bundled",
            },
            "source": {
                "archive": "tgz",
                "sha256": "b" * 64,
                "sizeBytes": 100,
                "sourceRevision": "c" * 40,
                "url": "https://example.invalid/onnxruntime.tgz",
            },
            "containers": [],
            "payloadFiles": [
                {
                    "archivePath": "runtime/libonnxruntime.1.dylib",
                    "sha256": "d" * 64,
                    "sizeBytes": 10,
                    "stagedPath": "libonnxruntime.1.dylib",
                }
            ],
            "notices": [
                {
                    "archivePath": "runtime/ThirdPartyNotices.txt",
                    "containerDepth": 0,
                    "id": "ThirdPartyNotices",
                    "stagedPath": "notices/ThirdPartyNotices.txt",
                    "sha256": hashlib.sha256(notice).hexdigest(),
                    "sizeBytes": len(notice),
                }
            ],
            "verifiedSymlinks": [],
            "archiveInspections": [
                {
                    "compressedBytes": 100,
                    "depth": 0,
                    "directoryCount": 0,
                    "format": "tar",
                    "memberCount": 2,
                    "regularFileCount": 2,
                    "symbolicLinkCount": 0,
                    "uncompressedBytes": 20,
                }
            ],
        }
        manifest_path = asset_root / "fonix-native-artifact-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return temporary, application, notice_path

    def test_accepts_exact_single_manifest_and_notice(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)

        manifest, manifest_path, notice_path, minimum = (
            audit_apple_application.audit_packaged_metadata(application, "macos")
        )

        self.assertEqual(minimum, (14, 0, 0))
        self.assertEqual(manifest["_auditIdentity"]["sourceSha256"], "b" * 64)
        self.assertEqual(manifest_path.name, "fonix-native-artifact-manifest.json")
        self.assertEqual(notice_path.name, "ThirdPartyNotices.txt")

    def test_fails_when_notice_is_missing_or_changed(self) -> None:
        temporary, application, notice_path = self._application()
        self.addCleanup(temporary.cleanup)
        notice_path.write_bytes(b"substituted notice\n")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")

    def test_fails_on_duplicate_manifest_anywhere_in_app(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        duplicate = application / "unexpected/fonix-native-artifact-manifest.json"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text("{}", encoding="utf-8")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")

    def test_fails_on_extra_manifest_field(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        manifest_path = (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix/fonix-native-artifact-manifest.json"
        )
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["unexpected"] = True
        manifest_path.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")

    def test_fails_on_wrong_target_variant(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        manifest_path = (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix/fonix-native-artifact-manifest.json"
        )
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["target"]["variant"] = "simulator"
        manifest_path.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")


class AppleApplicationMachOAuditTest(unittest.TestCase):
    def _macho(self, cpu_type: int) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-macho-audit-")
        binary = Path(temporary.name) / "binary"
        binary.write_bytes(
            struct.pack(
                "<IiiIIIII",
                0xFEEDFACF,
                cpu_type,
                0,
                2,
                0,
                0,
                0,
                0,
            )
        )
        return temporary, binary

    def test_fails_on_wrong_architecture(self) -> None:
        temporary, binary = self._macho(0x01000007)
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_macho(
                binary,
                architecture="arm64",
                platform_number=1,
                minimum_os=(14, 0, 0),
                otool="unused",
            )

    def test_fails_on_wrong_build_platform(self) -> None:
        temporary, binary = self._macho(0x0100000C)
        self.addCleanup(temporary.cleanup)
        original_run = audit_apple_application._run
        self.addCleanup(setattr, audit_apple_application, "_run", original_run)
        audit_apple_application._run = lambda command: """
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 2
    minos 15.1
      sdk 26.0
   ntools 1
"""
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_macho(
                binary,
                architecture="arm64",
                platform_number=1,
                minimum_os=(14, 0, 0),
                otool="unused",
            )


class AppleApplicationBuildIdentityTest(unittest.TestCase):
    def test_rejects_tampered_probe_identity(self) -> None:
        expected = {
            "schemaVersion": 3,
            "nativeIdentity": "fonix_shim",
            "shimAbiVersion": 1,
            "requiredOrtApiVersion": 27,
            "runtimeProfile": "bundled",
            "androidRuntimeOwner": None,
            "allowedRuntimeSources": ["bundled"],
            "buildId": "artifact",
            "artifact": {
                "id": "artifact",
                "lockSha256": "a" * 64,
                "sourceSha256": "b" * 64,
                "targetOs": "macos",
                "targetArchitecture": "arm64",
                "targetVariant": "default",
                "minimumOs": "14.0",
                "flavor": "cpu",
                "runtimeMode": "bundled",
                "thirdPartyNoticesSha256": "c" * 64,
                "providers": [
                    {
                        "wrapperId": "cpu",
                        "reportedName": "CPUExecutionProvider",
                    }
                ],
            },
        }
        tampered = json.loads(json.dumps(expected))
        tampered["artifact"]["sourceSha256"] = "d" * 64

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._validate_build_manifest(
                tampered, expected, "probe identity"
            )


if __name__ == "__main__":
    unittest.main()
