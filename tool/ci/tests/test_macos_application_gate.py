from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "run_macos_application_gate.py"
SPEC = importlib.util.spec_from_file_location("run_macos_application_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_macos_application_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_macos_application_gate)


class MacOsApplicationGateConfigurationTest(unittest.TestCase):
    def test_command_runner_uses_owned_posix_bounds(self) -> None:
        command = ("tool", "argument")
        cwd = Path("/tmp/fonix-macos-application-command")
        with (
            mock.patch.dict(
                run_macos_application_gate.os.environ,
                {"DYLD_INSERT_LIBRARIES": "/tmp/injected.dylib"},
            ),
            mock.patch.object(
                run_macos_application_gate._BOUNDED_PROCESS,
                "run_bounded",
                return_value=(
                    run_macos_application_gate._BOUNDED_PROCESS.CommandOutput(
                        stdout="result\n",
                        stderr="warning\n",
                    )
                ),
            ) as bounded,
        ):
            self.assertEqual(
                run_macos_application_gate._run(
                    command,
                    operation="macOS command contract",
                    timeout_seconds=37,
                    cwd=cwd,
                ),
                "result\n",
            )

        bounded.assert_called_once()
        call = bounded.call_args
        self.assertEqual(call.args, (command,))
        self.assertEqual(call.kwargs["operation"], "macOS command contract")
        self.assertEqual(call.kwargs["timeout_seconds"], 37)
        self.assertEqual(call.kwargs["cwd"], cwd)
        self.assertEqual(
            call.kwargs["maximum_stdout_bytes"],
            run_macos_application_gate.MAX_COMMAND_OUTPUT_BYTES,
        )
        self.assertEqual(
            call.kwargs["maximum_stderr_bytes"],
            run_macos_application_gate.MAX_COMMAND_OUTPUT_BYTES,
        )
        self.assertEqual(
            call.kwargs["environment"]["DART_SUPPRESS_ANALYTICS"],
            "true",
        )
        self.assertNotIn("DYLD_INSERT_LIBRARIES", call.kwargs["environment"])
        self.assertEqual(call.kwargs["environment"]["LC_ALL"], "C")

    def test_bounded_command_failure_is_mapped(self) -> None:
        failure = (
            run_macos_application_gate._BOUNDED_PROCESS.BoundedProcessTimeoutError(
                "macOS timeout fixture",
                3,
            )
        )
        with (
            mock.patch.object(
                run_macos_application_gate._BOUNDED_PROCESS,
                "run_bounded",
                side_effect=failure,
            ),
            self.assertRaisesRegex(
                run_macos_application_gate.MacOsApplicationGateError,
                "timed out after 3 seconds",
            ),
        ):
            run_macos_application_gate._run(
                ("tool",),
                operation="macOS timeout fixture",
                timeout_seconds=3,
            )

    def test_apple_audit_runs_in_process_with_exact_closed_tuple(self) -> None:
        class FakeAuditError(RuntimeError):
            pass

        repository = Path("/trusted/fonix")
        application = Path("/trusted/Fonix Gate.app")
        reference_runtime = Path("/trusted/libonnxruntime.dylib")
        model = Path("/trusted/mul_1.onnx")
        report = {"cpuInference": "passed", "platform": "macos"}
        auditor = mock.Mock()
        auditor.AppleApplicationAuditError = FakeAuditError
        bytecode_modes: list[bool] = []

        def audit(*_: object, **__: object) -> dict[str, object]:
            bytecode_modes.append(sys.dont_write_bytecode)
            return report

        auditor.audit_application_and_optional_probe.side_effect = audit
        with (
            mock.patch.object(sys, "dont_write_bytecode", False),
            mock.patch.object(
                run_macos_application_gate,
                "_load_apple_auditor",
                return_value=auditor,
            ),
            mock.patch.object(
                run_macos_application_gate._BOUNDED_PROCESS,
                "run_bounded",
            ) as bounded,
        ):
            result = run_macos_application_gate._run_apple_application_audit(
                repository=repository,
                application=application,
                reference_runtime=reference_runtime,
                model=model,
            )
            restored_bytecode_mode = sys.dont_write_bytecode

        self.assertEqual(result, report)
        self.assertEqual(bytecode_modes, [True])
        self.assertFalse(restored_bytecode_mode)
        auditor.audit_application_and_optional_probe.assert_called_once_with(
            application,
            "macos",
            run_macos_application_gate.APPLICATION_MINIMUM_OS,
            repository=repository,
            reference_runtime=reference_runtime,
            cpu_probe_model=model,
        )
        bounded.assert_not_called()

    def test_apple_audit_preserves_former_cli_report_bound(self) -> None:
        class FakeAuditError(RuntimeError):
            pass

        report = {"cpuInference": "passed", "payload": "x" * 80}
        auditor = mock.Mock()
        auditor.AppleApplicationAuditError = FakeAuditError
        auditor.audit_application_and_optional_probe.return_value = report
        former_cli_bytes = (json.dumps(report, sort_keys=True) + "\n").encode()
        with (
            mock.patch.object(
                run_macos_application_gate,
                "_load_apple_auditor",
                return_value=auditor,
            ),
            mock.patch.object(
                run_macos_application_gate,
                "MAX_COMMAND_OUTPUT_BYTES",
                len(former_cli_bytes) - 1,
            ),
            self.assertRaisesRegex(
                run_macos_application_gate.MacOsApplicationGateError,
                "exceeds its byte bound",
            ),
        ):
            run_macos_application_gate._run_apple_application_audit(
                repository=Path("/trusted/fonix"),
                application=Path("/trusted/Fonix Gate.app"),
                reference_runtime=Path("/trusted/libonnxruntime.dylib"),
                model=Path("/trusted/mul_1.onnx"),
            )

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
