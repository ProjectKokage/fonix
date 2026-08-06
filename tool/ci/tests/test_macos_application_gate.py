from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "run_macos_application_gate.py"
SPEC = importlib.util.spec_from_file_location("run_macos_application_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_macos_application_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_macos_application_gate)


class MacOsApplicationGateConfigurationTest(unittest.TestCase):
    def test_pubspec_contains_exact_hook_floor_and_asset_contract(self) -> None:
        source = run_macos_application_gate._pubspec(
            Path("/absolute/fonix"), Path("/absolute/cache")
        )

        self.assertIn('path: "/absolute/fonix"', source)
        self.assertIn('artifact_cache: "/absolute/cache"', source)
        self.assertIn("application_minimum_os: '14.0'", source)
        self.assertIn("runtime_mode: bundled", source)
        self.assertIn(
            "assets/fonix/fonix-native-artifact-manifest.json", source
        )
        self.assertIn("assets/fonix/ThirdPartyNotices.txt", source)

    def test_xcode_configuration_sets_all_floors_and_arm64_only(self) -> None:
        source = "\n".join(
            [
                "\t\t\t\tENABLE_USER_SCRIPT_SANDBOXING = NO;\n"
                "\t\t\t\tMACOSX_DEPLOYMENT_TARGET = 10.14;"
                for _ in range(3)
            ]
        )

        configured = run_macos_application_gate.configure_xcode_project(source)

        self.assertEqual(configured.count("MACOSX_DEPLOYMENT_TARGET = 14.0;"), 3)
        self.assertEqual(configured.count("EXCLUDED_ARCHS = x86_64;"), 3)
        self.assertNotIn("10.14", configured)

    def test_xcode_configuration_fails_closed_on_generator_drift(self) -> None:
        with self.assertRaises(
            run_macos_application_gate.MacOsApplicationGateError
        ):
            run_macos_application_gate.configure_xcode_project(
                "MACOSX_DEPLOYMENT_TARGET = 10.14;"
            )

    def test_gate_rejects_existing_work_directory_before_mutation(self) -> None:
        if (
            run_macos_application_gate.platform.system() != "Darwin"
            or run_macos_application_gate.platform.machine() != "arm64"
        ):
            self.skipTest("host-only validation")
        temporary = tempfile.TemporaryDirectory(prefix="fonix-app-gate-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        flutter = root / "flutter"
        runtime = root / "runtime"
        flutter.write_bytes(b"flutter")
        runtime.write_bytes(b"runtime")
        cache = root / "cache"
        cache.mkdir()

        with self.assertRaisesRegex(
            run_macos_application_gate.MacOsApplicationGateError,
            "must not already exist",
        ):
            run_macos_application_gate.run_gate(
                repository=Path(__file__).resolve().parents[3],
                flutter=flutter,
                artifact_cache=cache,
                reference_runtime=runtime,
                work_directory=root,
            )


if __name__ == "__main__":
    unittest.main()
