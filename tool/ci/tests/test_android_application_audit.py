from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


CI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_ROOT))
SCRIPT = CI_ROOT / "audit_android_application.py"
SPEC = importlib.util.spec_from_file_location("audit_android_application", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _zip_info(path: str) -> zipfile.ZipInfo:
    return zipfile.ZipInfo(path)


def _jar_manifest(paths: list[str]) -> bytes:
    result = "Manifest-Version: 1.0\r\nCreated-By: test\r\n\r\n"
    for path in paths:
        digest = base64.b64encode(hashlib.sha256(path.encode()).digest()).decode()
        result += f"Name: {path}\r\nSHA-256-Digest: {digest}\r\n\r\n"
    return result.encode()


def _manifest() -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="{audit.ANDROID_NS}"
 android:versionCode="1" android:versionName="0.1.0"
 android:compileSdkVersion="36" android:compileSdkVersionCodename="16"
 package="{audit.APPLICATION_ID}" platformBuildVersionCode="36"
 platformBuildVersionName="16">
 <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36" />
 <queries><intent><action android:name="android.intent.action.PROCESS_TEXT" />
 <data android:mimeType="text/plain" /></intent></queries>
 <permission android:name="{audit.PRIVATE_RECEIVER_PERMISSION}"
  android:protectionLevel="0x00000002" />
 <uses-permission android:name="{audit.PRIVATE_RECEIVER_PERMISSION}" />
 <application android:allowBackup="false"
  android:appComponentFactory="androidx.core.app.CoreComponentFactory"
  android:extractNativeLibs="false" android:icon="@mipmap/ic_launcher"
  android:label="Fonix Reference" android:name="android.app.Application"
  android:usesCleartextTraffic="false">
  <activity android:configChanges="0x40003fb4" android:exported="true"
   android:hardwareAccelerated="true" android:launchMode="1"
   android:name="{audit.MAIN_ACTIVITY}" android:taskAffinity=""
   android:theme="@style/LaunchTheme" android:windowSoftInputMode="0x00000010">
   <meta-data android:name="io.flutter.embedding.android.NormalTheme"
    android:resource="@style/NormalTheme" />
   <intent-filter><action android:name="android.intent.action.MAIN" />
    <category android:name="android.intent.category.LAUNCHER" /></intent-filter>
  </activity>
  <meta-data android:name="flutterEmbedding" android:value="2" />
  <uses-library android:name="androidx.window.extensions" android:required="false" />
  <uses-library android:name="androidx.window.sidecar" android:required="false" />
  <provider android:authorities="{audit.APPLICATION_ID}.androidx-startup"
   android:exported="false" android:name="androidx.startup.InitializationProvider">
   <meta-data android:name="androidx.lifecycle.ProcessLifecycleInitializer"
    android:value="androidx.startup" />
   <meta-data android:name="androidx.profileinstaller.ProfileInstallerInitializer"
    android:value="androidx.startup" />
  </provider>
  <receiver android:directBootAware="false" android:enabled="true"
   android:exported="true"
   android:name="androidx.profileinstaller.ProfileInstallReceiver"
   android:permission="android.permission.DUMP">
   <intent-filter><action android:name="androidx.profileinstaller.action.INSTALL_PROFILE" /></intent-filter>
   <intent-filter><action android:name="androidx.profileinstaller.action.SKIP_FILE" /></intent-filter>
   <intent-filter><action android:name="androidx.profileinstaller.action.SAVE_PROFILE" /></intent-filter>
   <intent-filter><action android:name="androidx.profileinstaller.action.BENCHMARK_OPERATION" /></intent-filter>
  </receiver>
 </application>
</manifest>
"""


class AndroidManifestAuditTest(unittest.TestCase):
    def test_accepts_exact_release_manifest_contract(self) -> None:
        report = audit._audit_manifest_xml(_manifest())

        self.assertEqual(report["networkPermissions"], [])
        self.assertEqual(
            report["requestedPermissions"], [audit.PRIVATE_RECEIVER_PERMISSION]
        )
        self.assertEqual(
            report["components"]["providers"],
            ["androidx.startup.InitializationProvider"],
        )

    def test_rejects_manifest_security_and_component_drift(self) -> None:
        source = _manifest()
        mutations = (
            source.replace(
                "<application ",
                '<uses-permission android:name="android.permission.INTERNET" />\n <application ',
                1,
            ),
            source.replace('android:allowBackup="false"', 'android:allowBackup="true"'),
            source.replace(
                'android:usesCleartextTraffic="false"',
                'android:usesCleartextTraffic="true"',
            ),
            source.replace(
                'android:exported="false" android:name="androidx.startup',
                'android:exported="true" android:name="androidx.startup',
            ),
            source.replace('android:permission="android.permission.DUMP"', ""),
            source.replace(
                "</application>",
                '<service android:name="dev.fonix.Unexpected" /></application>',
            ),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated[:100]):
                with self.assertRaises(audit.AndroidApplicationAuditError):
                    audit._audit_manifest_xml(mutated)


class AndroidNativeAuditTest(unittest.TestCase):
    def test_reference_inventory_is_closed(self) -> None:
        report = {
            "libraries": [
                {"abi": audit.ABI, "name": name}
                for name in sorted(audit.EXPECTED_NATIVE_NAMES)
            ]
        }
        verifier = mock.Mock()
        verifier.inspect.return_value = report
        verifier.validate.return_value = []
        with mock.patch.object(audit, "_load_native_verifier", return_value=verifier):
            self.assertIs(audit._audit_native_libraries(Path("."), Path("app.apk")), report)

        report["libraries"].append({"abi": audit.ABI, "name": "libplugin.so"})
        with mock.patch.object(audit, "_load_native_verifier", return_value=verifier):
            with self.assertRaisesRegex(
                audit.AndroidApplicationAuditError, "inventory changed"
            ):
                audit._audit_native_libraries(Path("."), Path("app.apk"))

    def test_runtime_identity_requires_ordered_load_segment_parity(self) -> None:
        metadata = {
            "class": 64,
            "machine": 183,
            "programHeaderCount": 2,
            "pageSize16KiBCompatible": True,
            "soname": "libonnxruntime.so",
            "needed": ["libc.so"],
            "loadSegments": [
                {
                    "index": 1,
                    "flags": 5,
                    "offset": 0,
                    "virtualAddress": 0,
                    "fileSize": 3,
                    "memorySize": 3,
                    "alignment": 16384,
                    "sha256": "a" * 64,
                    "offsetVaddrCongruent": True,
                }
            ],
        }
        verifier = mock.Mock()
        verifier.inspect_elf.return_value = (metadata, None)
        with tempfile.TemporaryDirectory(prefix="fonix-runtime-identity-") as temporary:
            reference = Path(temporary) / "libonnxruntime.so"
            reference.write_bytes(b"ref")
            packaged = b"pkg"
            entry = {
                "elf": json.loads(json.dumps(metadata)),
                "sha256": hashlib.sha256(packaged).hexdigest(),
            }
            with (
                mock.patch.object(audit, "ORT_SIZE_BYTES", 3),
                mock.patch.object(
                    audit, "ORT_SHA256", hashlib.sha256(b"ref").hexdigest()
                ),
                mock.patch.object(audit, "_load_native_verifier", return_value=verifier),
            ):
                result = audit._audit_runtime_identity(
                    Path("."), reference, packaged, entry
                )
                self.assertEqual(
                    result["packagedSha256"], hashlib.sha256(packaged).hexdigest()
                )
                entry["elf"]["loadSegments"][0]["sha256"] = "b" * 64
                with self.assertRaisesRegex(
                    audit.AndroidApplicationAuditError, "PT_LOAD"
                ):
                    audit._audit_runtime_identity(Path("."), reference, packaged, entry)

    def test_gnu_dynamic_symbol_parser_ignores_undefined_symbols(self) -> None:
        output = """
Symbol table '.dynsym' contains 3 entries:
   Num:    Value          Size Type    Bind   Vis       Ndx Name
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT   UND malloc@LIBC
     2: 0000000000001234    12 FUNC    GLOBAL DEFAULT    12 dort_runtime_open
     3: 0000000000005678    12 FUNC    GLOBAL DEFAULT    13 OrtGetApiBase@@VERS_1
"""
        self.assertEqual(
            audit._parse_exports(output), {"dort_runtime_open", "OrtGetApiBase"}
        )


class AndroidBundleIntegrityAuditTest(unittest.TestCase):
    def test_aab_module_set_is_exactly_base(self) -> None:
        base = {
            "BundleConfig.pb": _zip_info("BundleConfig.pb"),
            "base/manifest/AndroidManifest.xml": _zip_info(
                "base/manifest/AndroidManifest.xml"
            ),
            "META-INF/MANIFEST.MF": _zip_info("META-INF/MANIFEST.MF"),
        }
        self.assertEqual(audit._audit_aab_module_set(base), ["base"])

        for unexpected in (
            "feature/manifest/AndroidManifest.xml",
            "asset_pack/assets/data.bin",
        ):
            with self.subTest(unexpected=unexpected):
                tampered = dict(base)
                tampered[unexpected] = _zip_info(unexpected)
                with self.assertRaisesRegex(
                    audit.AndroidApplicationAuditError, "module set"
                ):
                    audit._audit_aab_module_set(tampered)

    def test_every_signable_aab_entry_requires_manifest_coverage(self) -> None:
        signable = ["BundleConfig.pb", "base/manifest/AndroidManifest.xml"]
        index = {
            path: _zip_info(path)
            for path in (
                *signable,
                "META-INF/MANIFEST.MF",
                "META-INF/ANDROIDD.SF",
                "META-INF/ANDROIDD.RSA",
            )
        }
        self.assertEqual(
            audit._audit_aab_signed_entries(index, _jar_manifest(signable)), 3
        )

        index["base/dex/classes.dex"] = _zip_info("base/dex/classes.dex")
        with self.assertRaisesRegex(
            audit.AndroidApplicationAuditError, "every signable AAB entry"
        ):
            audit._audit_aab_signed_entries(index, _jar_manifest(signable))

    def test_aab_signature_metadata_inventory_is_closed(self) -> None:
        signable = ["BundleConfig.pb", "base/manifest/AndroidManifest.xml"]
        paths = (
            *signable,
            "META-INF/MANIFEST.MF",
            "META-INF/ANDROIDD.SF",
            "META-INF/ANDROIDD.RSA",
        )
        index = {path: _zip_info(path) for path in paths}
        self.assertEqual(
            audit._audit_aab_signed_entries(index, _jar_manifest(signable)), 3
        )

        index["META-INF/SIG-EVIL"] = _zip_info("META-INF/SIG-EVIL")
        with self.assertRaisesRegex(
            audit.AndroidApplicationAuditError,
            "signature metadata inventory changed",
        ):
            audit._audit_aab_signed_entries(index, _jar_manifest(signable))

    def test_jarsigner_unsigned_entry_warning_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-jarsigner-warning-") as temporary:
            root = Path(temporary)
            artifact = root / "app.aab"
            artifact.write_bytes(b"aab")
            jarsigner = root / "jarsigner"
            jarsigner.write_bytes(b"tool")
            jarsigner.chmod(0o700)
            command_output = mock.Mock(
                stdout=(
                    "jar verified.\nWarning: This jar contains unsigned entries "
                    "which have not been integrity-checked.\n"
                ),
                stderr="",
            )
            with (
                mock.patch.object(audit, "_audit_aab_signed_entries", return_value=2),
                mock.patch.object(audit, "run_bounded", return_value=command_output),
            ):
                with self.assertRaisesRegex(
                    audit.AndroidApplicationAuditError, "unsigned entries"
                ):
                    audit._audit_signature_and_alignment(
                        artifact,
                        "aab",
                        zipalign=None,
                        apksigner=None,
                        jarsigner=jarsigner,
                        aab_index={"base/file": _zip_info("base/file")},
                        aab_manifest_bytes=b"manifest",
                    )

    def test_aab_signer_certificate_digest_is_bound_to_der(self) -> None:
        certificate = b"exact signer certificate DER"
        encoded = base64.b64encode(certificate).decode()
        output = (
            "Signer #1:\n\nCertificate #1:\n"
            "-----BEGIN CERTIFICATE-----\n"
            f"{encoded}\n"
            "-----END CERTIFICATE-----\n"
        )
        self.assertEqual(
            audit._parse_keytool_signer_certificate(output),
            hashlib.sha256(certificate).hexdigest(),
        )
        with self.assertRaisesRegex(
            audit.AndroidApplicationAuditError, "exactly one"
        ):
            audit._parse_keytool_signer_certificate(
                output + output.replace("Signer #1:", "Signer #2:")
            )

    def test_archive_inspections_are_closed_and_exact(self) -> None:
        exact = [dict(entry) for entry in audit.EXPECTED_ARCHIVE_INSPECTIONS]
        audit._validate_archive_inspections(exact)

        for index in range(len(exact)):
            with self.subTest(index=index, mutation="value"):
                tampered = [dict(entry) for entry in exact]
                tampered[index]["memberCount"] += 1
                with self.assertRaises(audit.AndroidApplicationAuditError):
                    audit._validate_archive_inspections(tampered)
            with self.subTest(index=index, mutation="field"):
                tampered = [dict(entry) for entry in exact]
                tampered[index]["unexpected"] = 1
                with self.assertRaises(audit.AndroidApplicationAuditError):
                    audit._validate_archive_inspections(tampered)
        type_tamper = [dict(entry) for entry in exact]
        type_tamper[0]["directoryCount"] = False
        with self.assertRaises(audit.AndroidApplicationAuditError):
            audit._validate_archive_inspections(type_tamper)


class AndroidArtifactSnapshotTest(unittest.TestCase):
    def test_final_hash_rejects_in_place_tamper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-artifact-snapshot-") as temporary:
            root = Path(temporary)
            source = root / "app.aab"
            source.write_bytes(b"original")
            snapshot = audit._copy_artifact_snapshot(source, root / "snapshot.aab")
            audit._verify_artifact_snapshot(snapshot)

            source.write_bytes(b"tampered")
            with self.assertRaisesRegex(
                audit.AndroidApplicationAuditError, "bytes changed"
            ):
                audit._verify_artifact_snapshot(snapshot)

    def test_final_inode_rejects_same_byte_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-artifact-replacement-") as temporary:
            root = Path(temporary)
            source = root / "app.aab"
            source.write_bytes(b"original")
            snapshot = audit._copy_artifact_snapshot(source, root / "snapshot.aab")

            replacement = root / "replacement.aab"
            replacement.write_bytes(b"original")
            os.replace(replacement, source)
            with self.assertRaisesRegex(
                audit.AndroidApplicationAuditError, "replaced"
            ):
                audit._verify_artifact_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
