from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "run_macos_reference_app_gate.py"
WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"
SPEC = importlib.util.spec_from_file_location("run_macos_reference_app_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_macos_reference_app_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_macos_reference_app_gate)


class MacOsReferenceAppGateCopyTest(unittest.TestCase):
    def _roots(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-reference-copy-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "example"
        source.mkdir()
        return temporary, source, root / "copied"

    def test_copy_omits_only_exact_generated_paths(self) -> None:
        _, source, destination = self._roots()
        included = {
            "pubspec.yaml": b"name: reference\n",
            "lib/main.dart": b"void main() {}\n",
            ".dart_tool-backup/keep.txt": b"keep\n",
            "build-cache/keep.txt": b"keep\n",
            "macos/Flutter/ephemeral-copy/keep.txt": b"keep\n",
        }
        excluded = {
            ".dart_tool/generated": b"drop\n",
            ".fonix-artifact-cache/archive.tgz": b"drop\n",
            ".flutter-plugins-dependencies": b"drop\n",
            ".idea/workspace.xml": b"drop\n",
            ".pub/generated": b"drop\n",
            ".pub-cache/generated": b"drop\n",
            "build/output": b"drop\n",
            "coverage/lcov.info": b"drop\n",
            "fonix_reference.iml": b"drop\n",
            "macos/Flutter/ephemeral/plugin.dart": b"drop\n",
        }
        for relative, data in {**included, **excluded}.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        summary = run_macos_reference_app_gate._copy_example(source, destination)

        self.assertEqual(summary.file_count, len(included))
        self.assertEqual(summary.byte_count, sum(map(len, included.values())))
        for relative, data in included.items():
            self.assertEqual((destination / relative).read_bytes(), data)
        for relative in excluded:
            self.assertFalse((destination / relative).exists())

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliable on Windows CI")
    def test_copy_rejects_symlink_even_inside_excluded_tree(self) -> None:
        _, source, destination = self._roots()
        generated = source / ".dart_tool"
        generated.mkdir()
        (generated / "target").write_text("target", encoding="utf-8")
        (generated / "link").symlink_to("target")

        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "symbolic link",
        ):
            run_macos_reference_app_gate._copy_example(source, destination)


class MacOsReferenceAppGateConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-reference-config-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "fonix"
        self.repository.mkdir()
        self.pubspec = self.root / "pubspec.yaml"

    def test_patch_changes_only_relative_fonix_path_scalar(self) -> None:
        source = (
            "name: fonix_reference\n"
            "dependencies:\n"
            "  flutter:\n"
            "    sdk: flutter\n"
            "  fonix:\n"
            "    path: ..\n"
            "flutter:\n"
            "  uses-material-design: true\n"
        )
        self.pubspec.write_text(source, encoding="utf-8")

        changed = run_macos_reference_app_gate._patch_fonix_path_dependency(
            self.pubspec, self.repository
        )

        self.assertTrue(changed)
        expected_block = (
            "  fonix:\n"
            f"    path: {json.dumps(self.repository.as_posix())}\n"
        )
        expected = source.replace("  fonix:\n    path: ..\n", expected_block)
        self.assertEqual(self.pubspec.read_text(encoding="utf-8"), expected)
        self.assertFalse(
            run_macos_reference_app_gate._patch_fonix_path_dependency(
                self.pubspec, self.repository
            )
        )

    def test_patch_rejects_another_dependency_path(self) -> None:
        self.pubspec.write_text(
            "dependencies:\n  fonix:\n    path: ../another\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "exactly one Fonix path dependency",
        ):
            run_macos_reference_app_gate._patch_fonix_path_dependency(
                self.pubspec, self.repository
            )

    def test_archive_is_copied_only_after_exact_lock_hash(self) -> None:
        archive_bytes = b"exact synthetic archive bytes"
        digest = hashlib.sha256(archive_bytes).hexdigest()
        lock = {
            "artifacts": [
                {
                    "id": run_macos_reference_app_gate.ARTIFACT_ID,
                    "target": {
                        "os": "macos",
                        "architecture": "arm64",
                        "variant": "default",
                        "min_os": "14.0",
                    },
                    "flavor": "cpu",
                    "runtime_mode": "bundled",
                    "source": {
                        "url": (
                            "https://github.com/microsoft/onnxruntime/releases/"
                            "download/v1.27.1/"
                            "onnxruntime-osx-arm64-1.27.1.tgz"
                        ),
                        "archive": "tgz",
                        "sha256": digest,
                        "size_bytes": len(archive_bytes),
                    },
                }
            ]
        }
        lock_path = self.repository / "native.lock.json"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        archive = run_macos_reference_app_gate._load_pinned_archive(lock_path)
        source_cache = self.root / "source-cache"
        source_cache.mkdir()
        (source_cache / archive.basename).write_bytes(archive_bytes)
        work_directory = self.root / "work"
        work_directory.mkdir()

        destination = (
            run_macos_reference_app_gate._populate_relative_artifact_cache(
                source_cache, work_directory, archive
            )
        )

        self.assertEqual(
            destination,
            work_directory / ".fonix-artifact-cache" / archive.basename,
        )
        self.assertEqual(destination.read_bytes(), archive_bytes)

    def test_application_entrypoint_and_audit_are_bound_to_same_binary(self) -> None:
        application = self.root / "Fonix Reference.app"
        contents = application / "Contents"
        executable = contents / "MacOS/Fonix Reference"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"Mach-O fixture")
        (contents / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleExecutable": "Fonix Reference"})
        )
        resolved_executable = run_macos_reference_app_gate._application_executable(
            application
        )
        archive = run_macos_reference_app_gate.PinnedArchive(
            artifact_id=run_macos_reference_app_gate.ARTIFACT_ID,
            basename="runtime.tgz",
            sha256="a" * 64,
            size_bytes=1,
        )
        macho_paths = [
            str(path)
            for path in run_macos_reference_app_gate._expected_macho_paths(
                application, resolved_executable
            )
        ]
        report = {
            "platform": "macos",
            "application": str(application),
            "artifactId": run_macos_reference_app_gate.ARTIFACT_ID,
            "cpuInference": "passed",
            "machOBinaries": macho_paths,
        }

        self.assertIs(
            run_macos_reference_app_gate._validate_audit_binding(
                report,
                application=application,
                executable=resolved_executable,
                archive=archive,
            ),
            report,
        )
        report["machOBinaries"] = [str(contents / "MacOS/receipt-decoy")]
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "did not bind",
        ):
            run_macos_reference_app_gate._validate_audit_binding(
                report,
                application=application,
                executable=resolved_executable,
                archive=archive,
            )


class MacOsReferenceAppGateReceiptTest(unittest.TestCase):
    def _receipt_line(self, value: object | None = None) -> str:
        receipt = (
            run_macos_reference_app_gate.EXPECTED_REFERENCE_RECEIPT
            if value is None
            else value
        )
        return (
            run_macos_reference_app_gate.REFERENCE_RECEIPT_PREFIX
            + json.dumps(receipt, separators=(",", ":"))
        )

    def test_accepts_one_exact_strict_receipt_among_bounded_output(self) -> None:
        parsed = run_macos_reference_app_gate._parse_reference_receipt(
            "startup\n" + self._receipt_line() + "\nshutdown\n"
        )
        self.assertEqual(
            parsed, run_macos_reference_app_gate.EXPECTED_REFERENCE_RECEIPT
        )

    def test_rejects_duplicate_json_key(self) -> None:
        line = self._receipt_line()
        tampered = line[:-1] + ',"status":"passed"}'
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "duplicate JSON key",
        ):
            run_macos_reference_app_gate._parse_reference_receipt(tampered)

    def test_rejects_extra_field_wrong_type_and_two_receipts(self) -> None:
        extra = dict(run_macos_reference_app_gate.EXPECTED_REFERENCE_RECEIPT)
        extra["unexpected"] = True
        with self.assertRaises(
            run_macos_reference_app_gate.MacOsReferenceAppGateError
        ):
            run_macos_reference_app_gate._parse_reference_receipt(
                self._receipt_line(extra)
            )

        wrong_type = dict(run_macos_reference_app_gate.EXPECTED_REFERENCE_RECEIPT)
        wrong_type["outputValues"] = [1.0, 4, 9, 16, 25, 36]
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "wrong type|unexpected",
        ):
            run_macos_reference_app_gate._parse_reference_receipt(
                self._receipt_line(wrong_type)
            )

        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "exactly one receipt line",
        ):
            run_macos_reference_app_gate._parse_reference_receipt(
                self._receipt_line() + "\n" + self._receipt_line()
            )


class MacOsReferenceAppGateProcessTest(unittest.TestCase):
    def test_reference_environment_scrubs_loader_paths_and_sets_smoke(self) -> None:
        base = {
            "KEEP": "yes",
            "DYLD_LIBRARY_PATH": "/untrusted",
            "DYLD_FALLBACK_LIBRARY_PATH": "/also-untrusted",
            "DYLD_FRAMEWORK_PATH": "/untrusted-frameworks",
            "DYLD_INSERT_LIBRARIES": "/untrusted/injected.dylib",
            "FONIX_REFERENCE_SMOKE": "stale",
        }

        result = run_macos_reference_app_gate._reference_environment(base)

        self.assertEqual(base["DYLD_LIBRARY_PATH"], "/untrusted")
        self.assertNotIn("DYLD_LIBRARY_PATH", result)
        self.assertNotIn("DYLD_FALLBACK_LIBRARY_PATH", result)
        self.assertNotIn("DYLD_FRAMEWORK_PATH", result)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", result)
        self.assertFalse(any(key.startswith("DYLD_") for key in result))
        self.assertEqual(result["FONIX_REFERENCE_SMOKE"], "1")
        self.assertEqual(result["KEEP"], "yes")
        self.assertEqual(result["DART_SUPPRESS_ANALYTICS"], "true")

    def test_reference_timeout_is_a_closed_failure(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-reference-timeout-")
        self.addCleanup(temporary.cleanup)
        executable = Path(temporary.name) / "reference"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        with mock.patch.object(
            run_macos_reference_app_gate,
            "_execute_process",
            side_effect=subprocess.TimeoutExpired([str(executable)], 7),
        ):
            with self.assertRaisesRegex(
                run_macos_reference_app_gate.MacOsReferenceAppGateError,
                "reference application timed out after 7 seconds",
            ):
                run_macos_reference_app_gate._run_reference_application(
                    executable, timeout_seconds=7
                )

    def test_process_output_is_bounded_while_child_runs(self) -> None:
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "stdout exceeds 1024 bytes",
        ):
            run_macos_reference_app_gate._run(
                (sys.executable, "-c", "print('x' * 4096)"),
                maximum_output=1024,
                operation="bounded fixture",
            )

    def test_codesign_parser_requires_exact_runtime_flag_token(self) -> None:
        run_macos_reference_app_gate._require_hardened_runtime_codesign(
            "CodeDirectory v=20500 size=100 "
            "flags=0x10001(host,runtime) hashes=1+0"
        )
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "hardened-runtime",
        ):
            run_macos_reference_app_gate._require_hardened_runtime_codesign(
                "CodeDirectory v=20400 size=100 flags=0x0(none) hashes=1+0"
            )
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "hardened-runtime",
        ):
            run_macos_reference_app_gate._require_hardened_runtime_codesign(
                "CodeDirectory v=20500 size=100 "
                "flags=0x10000(runtime-library) hashes=1+0"
            )
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "hardened-runtime",
        ):
            run_macos_reference_app_gate._require_hardened_runtime_codesign(
                "CodeDirectory v=20500 size=100 flags=0x0(runtime) hashes=1+0"
            )
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "exactly one",
        ):
            run_macos_reference_app_gate._require_hardened_runtime_codesign(
                "untrusted flags=0x10000(runtime) text"
            )
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "exactly one",
        ):
            run_macos_reference_app_gate._require_hardened_runtime_codesign(
                "CodeDirectory v=20500 flags=0x10000(runtime)\n"
                "CodeDirectory v=20500 flags=0x10000(runtime)"
            )

    def test_signed_entitlements_pin_sandbox_and_local_adhoc_exception(self) -> None:
        valid = plistlib.dumps(
            {
                "com.apple.security.app-sandbox": True,
                "com.apple.security.cs.disable-library-validation": True,
                "com.apple.security.get-task-allow": True,
            }
        ).decode("utf-8")
        run_macos_reference_app_gate._require_reference_entitlements(
            "Executable=/tmp/reference\n" + valid
        )
        missing = plistlib.dumps(
            {"com.apple.security.app-sandbox": True}
        ).decode("utf-8")
        with self.assertRaisesRegex(
            run_macos_reference_app_gate.MacOsReferenceAppGateError,
            "library-validation exception",
        ):
            run_macos_reference_app_gate._require_reference_entitlements(missing)


class MacOsReferenceAppGateWorkflowTest(unittest.TestCase):
    def test_bound_final_audit_precedes_direct_app_execution(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        run_gate = source[source.index("def run_gate(") : source.index("def _parser()")]
        self.assertLess(
            run_gate.index("_validate_audit_binding("),
            run_gate.index("receipt = _run_reference_application(executable)"),
        )

    def test_exact_macos_lane_runs_the_committed_reference_gate(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        invocation = (
            "python -B tool/ci/run_macos_reference_app_gate.py\n"
            '          --repository "${{ github.workspace }}"\n'
            '          --flutter "$RUNNER_TEMP/flutter/bin/flutter"\n'
            "          --artifact-cache "
            '"${{ steps.ort.outputs.ort_archive_dir }}"\n'
            "          --reference-runtime "
            '"${{ steps.ort.outputs.ort_path }}"\n'
            '          --work-dir "$RUNNER_TEMP/'
            'fonix-macos-reference-app-gate"'
        )
        self.assertEqual(workflow.count(invocation), 1)
        self.assertLess(
            workflow.index("python -B tool/ci/run_macos_application_gate.py"),
            workflow.index(invocation),
        )


if __name__ == "__main__":
    unittest.main()
