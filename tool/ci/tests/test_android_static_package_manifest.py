from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/android_static_package_manifest.py"
COMPATIBILITY_TESTS = (
    REPOSITORY / "tool/ci/tests/test_android_compatibility_manifest.py"
)

_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_static_package_compatibility_fixture", COMPATIBILITY_TESTS
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
FIXTURE = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(FIXTURE)


class AndroidStaticPackageManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        fixture_type = getattr(FIXTURE, "AndroidCompatibilityManifestTest")
        self.fixture = fixture_type("test_generates_reproducible_lockable_manifest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.abi = self.fixture.abi
        self.aab = self._aab("app-release.aab")

    def _aab(
        self,
        name: str,
        *,
        overrides: dict[str, bytes] | None = None,
        omitted: set[str] | None = None,
        extra: dict[str, bytes] | None = None,
    ) -> Path:
        entries = {
            "BundleConfig.pb": b"bundle",
            "base/manifest/AndroidManifest.xml": b"manifest",
            f"base/lib/{self.abi}/libapp.so": self.fixture.flutter_aot,
            f"base/lib/{self.abi}/libonnxruntime.so": self.fixture.ort,
            f"base/lib/{self.abi}/libsherpa-onnx-c-api.so": (
                self.fixture.sherpa_c_api
            ),
            f"base/lib/{self.abi}/libsherpa-onnx-cxx-api.so": (
                self.fixture.sherpa_cxx_api
            ),
            f"base/lib/{self.abi}/libfonix_shim.so": self.fixture.shim,
        }
        entries.update(overrides or {})
        for path in omitted or set():
            entries.pop(path)
        entries.update(extra or {})
        return self.fixture._archive(name, entries)

    def _arguments(
        self,
        *,
        apk: Path | None = None,
        aab: Path | None = None,
        output_name: str = "static-packages.json",
    ) -> list[str]:
        return [
            sys.executable,
            str(SCRIPT),
            "--sherpa-source",
            "https://github.com/k2-fsa/sherpa-onnx",
            "--sherpa-revision",
            "d" * 40,
            "--sherpa-artifact",
            str(self.fixture.sherpa),
            "--wrapper-artifact",
            str(self.fixture.wrapper),
            "--final-apk",
            str(apk or self.fixture.final),
            "--final-aab",
            str(aab or self.aab),
            "--abi",
            self.abi,
            "--ort-api-required",
            "27",
            "--build-type",
            "release-minified",
            "--snapshot-date",
            "2026-08-07",
            "--output",
            str(self.root / output_name),
        ]

    def test_generates_deterministic_path_free_static_pair_record(self) -> None:
        first = subprocess.run(
            self._arguments(), capture_output=True, text=True, check=False
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_path = self.root / "static-packages.json"
        first_bytes = first_path.read_bytes()
        self.assertEqual(first_path.stat().st_mode & 0o777, 0o600)

        second = subprocess.run(
            self._arguments(output_name="static-packages-second.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            first_bytes, (self.root / "static-packages-second.json").read_bytes()
        )

        manifest = json.loads(first_bytes)
        self.assertEqual(
            set(manifest),
            {
                "android",
                "claimBoundary",
                "claimStatus",
                "result",
                "schemaVersion",
                "sherpaOnnx",
                "snapshotDate",
                "tools",
            },
        )
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["result"], "passed")
        self.assertEqual(manifest["claimStatus"], "static-package-only")
        self.assertEqual(
            manifest["android"]["packageEnvelopes"],
            {
                "apk": {"kind": "apk", "modules": []},
                "aab": {"kind": "aab", "modules": ["base"]},
            },
        )
        libraries = manifest["android"]["librariesByAbi"][self.abi]
        self.assertEqual(
            set(libraries["sherpaRuntimeConsumers"]),
            {"libsherpa-onnx-c-api.so", "libsherpa-onnx-cxx-api.so"},
        )
        self.assertEqual(
            libraries["onnxruntime"]["finals"]["apk"]["path"],
            f"lib/{self.abi}/libonnxruntime.so",
        )
        self.assertEqual(
            libraries["onnxruntime"]["finals"]["aab"]["path"],
            f"base/lib/{self.abi}/libonnxruntime.so",
        )
        self.assertEqual(set(libraries["platformLibraries"]), {"libapp.so"})
        self.assertEqual(
            manifest["sherpaOnnx"]["provenanceBinding"],
            "caller-declared; enclosing-gate-required",
        )
        self.assertEqual(
            manifest["android"]["buildTypeBinding"],
            "caller-declared; enclosing-gate-required",
        )
        self.assertNotIn("loadOrderEvidence", manifest["android"])
        self.assertNotIn(str(self.root), first_bytes.decode("utf-8"))
        self.assertIn(
            "application/version manifest identity", manifest["claimBoundary"]
        )
        self.assertIn("source/build declarations", manifest["claimBoundary"])
        self.assertIn("Runtime receipts remain APK-only", manifest["claimBoundary"])

    def test_cli_has_no_runtime_receipt_input(self) -> None:
        arguments = self._arguments(output_name="unexpected-runtime.json")
        arguments.extend(("--load-order-validation-record", "receipt.json"))
        result = subprocess.run(
            arguments, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertFalse((self.root / "unexpected-runtime.json").exists())

    def test_rejects_incomplete_aab_ffi_pair(self) -> None:
        cxx_path = f"base/lib/{self.abi}/libsherpa-onnx-cxx-api.so"
        aab = self._aab("missing-cxx.aab", omitted={cxx_path})
        result = subprocess.run(
            self._arguments(aab=aab, output_name="missing-cxx.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("profile 'flutter-ffi'", result.stderr)
        self.assertFalse((self.root / "missing-cxx.json").exists())

    def test_rejects_aab_loaded_segment_drift(self) -> None:
        drifted_ort = bytearray(self.fixture.ort)
        drifted_ort[0x180] ^= 1
        ort_path = f"base/lib/{self.abi}/libonnxruntime.so"
        aab = self._aab(
            "drifted.aab", overrides={ort_path: bytes(drifted_ort)}
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="drifted.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("loaded bytes do not match", result.stderr)
        self.assertFalse((self.root / "drifted.json").exists())

    def test_rejects_aab_flutter_aot_loaded_segment_drift(self) -> None:
        drifted_app = bytearray(self.fixture.flutter_aot)
        drifted_app[0x180] ^= 1
        app_path = f"base/lib/{self.abi}/libapp.so"
        aab = self._aab(
            "drifted-app.aab", overrides={app_path: bytes(drifted_app)}
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="drifted-app.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("libapp.so", result.stderr)
        self.assertIn("loaded ELF identity differs", result.stderr)
        self.assertFalse((self.root / "drifted-app.json").exists())

    def test_rejects_different_apk_aab_platform_library_inventory(self) -> None:
        app_path = f"base/lib/{self.abi}/libapp.so"
        aab = self._aab("missing-app.aab", omitted={app_path})
        result = subprocess.run(
            self._arguments(aab=aab, output_name="missing-app.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("platform-library inventory differs", result.stderr)
        self.assertFalse((self.root / "missing-app.json").exists())

    def test_rejects_nonstandard_second_ort_library_name(self) -> None:
        second_runtime = FIXTURE.ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="second_runtime.so",
            defined_symbols=("OrtGetApiBase",),
        )
        aab = self._aab(
            "second-runtime.aab",
            extra={
                f"base/lib/{self.abi}/second_runtime.so": second_runtime,
            },
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="second-runtime.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the closed native inventory", result.stderr)
        self.assertFalse((self.root / "second-runtime.json").exists())

    def test_rejects_hidden_shared_object_payload_outside_native_inventory(
        self,
    ) -> None:
        aab = self._aab(
            "hidden-payload.aab",
            extra={
                "base/assets/libhidden_payload.so": b"not-even-an-elf",
            },
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="hidden-payload.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the closed native inventory", result.stderr)
        self.assertIn("base/assets/libhidden_payload.so", result.stderr)
        self.assertFalse((self.root / "hidden-payload.json").exists())

    def test_rejects_native_library_under_undeclared_aab_module(self) -> None:
        aab = self._aab(
            "undeclared-module.aab",
            extra={
                f"feature/lib/{self.abi}/libhidden.so": (
                    FIXTURE.ELF_FIXTURE.synthetic_elf(
                        self.abi,
                        soname="libhidden.so",
                    )
                ),
            },
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="undeclared-module.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("undeclared top-level module paths: feature", result.stderr)
        self.assertFalse((self.root / "undeclared-module.json").exists())

    def test_rejects_unresolved_aab_native_dependency(self) -> None:
        cxx = FIXTURE.ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsherpa-onnx-cxx-api.so",
            needed=(
                "libsherpa-onnx-c-api.so",
                "libonnxruntime.so",
                "libmissing.so",
                "libdl.so",
                "libc.so",
            ),
        )
        cxx_path = f"base/lib/{self.abi}/libsherpa-onnx-cxx-api.so"
        aab = self._aab(
            "unresolved-dependency.aab", overrides={cxx_path: cxx}
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="unresolved-dependency.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("has unresolved dependencies: libmissing.so", result.stderr)
        self.assertFalse((self.root / "unresolved-dependency.json").exists())

    def test_rejects_non_16k_aab_library(self) -> None:
        unaligned_ort = FIXTURE.ELF_FIXTURE.synthetic_elf(
            self.abi,
            alignment=4096,
            soname="libonnxruntime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        ort_path = f"base/lib/{self.abi}/libonnxruntime.so"
        aab = self._aab(
            "non-16k.aab", overrides={ort_path: unaligned_ort}
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="non-16k.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not 16 KiB compatible", result.stderr)
        self.assertFalse((self.root / "non-16k.json").exists())

    def test_rejects_hidden_renamed_ort_in_aab(self) -> None:
        renamed_ort = FIXTURE.ELF_FIXTURE.synthetic_elf(
            self.abi,
            soname="libsecond_runtime.so",
            needed=("libdl.so", "libc.so"),
            defined_symbols=("OrtGetApiBase",),
        )
        aab = self._aab(
            "renamed-ort.aab",
            extra={
                f"base/lib/{self.abi}/libsecond_runtime.so": renamed_ort
            },
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="renamed-ort.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("hidden or invalid ONNX Runtime candidate", result.stderr)
        self.assertFalse((self.root / "renamed-ort.json").exists())

    def test_rejects_non_base_aab_module_set(self) -> None:
        aab = self._aab(
            "feature-module.aab",
            extra={"feature/manifest/AndroidManifest.xml": b"manifest"},
        )
        result = subprocess.run(
            self._arguments(aab=aab, output_name="feature-module.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("AAB module set must be exactly ['base']", result.stderr)
        self.assertFalse((self.root / "feature-module.json").exists())

    def test_rejects_static_tool_identity_drift(self) -> None:
        manifest_module = FIXTURE.MANIFEST
        arguments = manifest_module.argparse.Namespace(
            mode="sherpa-owned",
            sherpa_library_profile="flutter-ffi",
            sherpa_source="https://github.com/k2-fsa/sherpa-onnx",
            sherpa_revision="d" * 40,
            sherpa_artifact=[self.fixture.sherpa],
            wrapper_artifact=[self.fixture.wrapper],
            final_apk=self.fixture.final,
            final_aab=self.aab,
            abi=[self.abi],
            ort_api_required=27,
            build_type="release-minified",
            snapshot_date="2026-08-07",
            output=self.root / "identity-drift.json",
        )
        original = manifest_module._stable_file_identity
        cases = (
            ("static package generator", "static package generator changed"),
            (
                "static package evidence implementation",
                "static package evidence implementation changed",
            ),
        )
        for drift_label, expected_error in cases:
            with self.subTest(label=drift_label):
                reads = 0

                def drifting_identity(
                    path: Path,
                    label: str,
                    maximum: int,
                ) -> dict[str, object]:
                    nonlocal reads
                    identity = original(path, label, maximum)
                    if label == drift_label:
                        reads += 1
                        if reads == 2:
                            return {**identity, "sha256": "0" * 64}
                    return identity

                with mock.patch.object(
                    manifest_module,
                    "_stable_file_identity",
                    side_effect=drifting_identity,
                ):
                    with self.assertRaisesRegex(
                        manifest_module.CompatibilityManifestError,
                        expected_error,
                    ):
                        manifest_module.generate_static_package_manifest(
                            arguments,
                            generator_path=SCRIPT,
                        )


if __name__ == "__main__":
    unittest.main()
