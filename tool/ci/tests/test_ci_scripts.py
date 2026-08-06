from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


CI_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY = CI_DIRECTORY.parents[1]
sys.path.insert(0, str(CI_DIRECTORY))

import check_windows_binary  # noqa: E402
import audit_desktop_bundle  # noqa: E402
import check_macos_runtime  # noqa: E402
import fetch_pinned_macos_ort  # noqa: E402
import run_native_tests  # noqa: E402
import run_phase3_fixture_tests  # noqa: E402
import verify_bindings  # noqa: E402


class WorkflowContractTest(unittest.TestCase):
    def _job(self, source: str, name: str) -> str:
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
            source,
        )
        self.assertIsNotNone(match, f"workflow job is missing: {name}")
        return match.group(0)

    def _step(self, job: str, name: str) -> str:
        match = re.search(
            rf"(?ms)^      - name: {re.escape(name)}\n"
            r".*?(?=^      - name: |\Z)",
            job,
        )
        self.assertIsNotNone(match, f"workflow step is missing: {name}")
        return match.group(0)

    def test_actions_are_sha_pinned_and_failures_are_not_masked(self) -> None:
        source = (REPOSITORY / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        actions = re.findall(r"^\s*uses:\s+([^@\s]+)@([^\s]+)\s*$", source, re.M)

        self.assertGreater(len(actions), 0)
        for action, revision in actions:
            self.assertRegex(
                revision,
                r"^[0-9a-f]{40}$",
                f"{action} is not pinned to a full commit SHA",
            )
        self.assertNotIn("continue-on-error", source)
        self.assertNotIn("upload-artifact", source)
        self.assertRegex(source, r"(?m)^permissions:\n  contents: read$")
        checkout_count = source.count("uses: actions/checkout@")
        self.assertEqual(source.count("persist-credentials: false"), checkout_count)
        self.assertNotIn("actions/cache@", source)
        self.assertIn("--no-tests=error", (CI_DIRECTORY / "run_native_tests.py").read_text())
        self.assertIn("test/cpu_inference_test.dart", source)
        self.assertIn("FONIX_TEST_REAL_ORT_PATH", source)
        self.assertEqual(source.count("FONIX_TEST_SHIM_PATH:"), 1)

    def test_phase3_fixture_gates_cover_every_host_and_real_runtime(self) -> None:
        source = (REPOSITORY / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        byte_job = self._job(source, "phase3-fixture-bytes")
        real_job = self._job(source, "real-ort-macos-arm64")
        bridge_step = self._step(
            real_job,
            "Run the native bridge against the built strict fake runtimes",
        )
        worker_step = self._step(
            real_job,
            "Run worker lifecycle against the fake and exact runtimes",
        )
        cpu_step = self._step(
            real_job,
            "Run the env-gated real runtime and session suite",
        )
        phase3_step = self._step(
            real_job,
            "Run the complete generated Phase-3 Dart corpus against real ORT",
        )
        provider_step = self._step(
            real_job,
            "Prove CPU and CoreML assignment, parity, cache, and worker transport",
        )

        self.assertIn("runs-on: ${{ matrix.runner }}", byte_job)
        for runner in ("ubuntu-24.04", "macos-14", "windows-2022"):
            self.assertEqual(byte_job.count(f"runner: {runner}"), 1)
        self.assertEqual(
            byte_job.count("tool/ci/run_phase3_fixture_tests.py check"), 1
        )
        self.assertIn("python-version: \"3.12.10\"", byte_job)
        self.assertNotIn("if:", byte_job)

        self.assertIn("tool/ci/run_phase3_fixture_tests.py", real_job)
        self.assertIn("tool/ci/check_macos_runtime.py", real_job)
        self.assertIn('--runtime "${{ steps.ort.outputs.ort_path }}"', real_job)
        self.assertIn("--archive-cache-dir", real_job)
        self.assertIn("FONIX_TEST_MACOS_ORT_ARCHIVE_DIR", real_job)
        self.assertIn(
            "bundled hook emits the exact offline macOS ORT code asset",
            real_job,
        )
        self.assertIn("${{ steps.ort.outputs.ort_archive_dir }}", real_job)
        self.assertIn(
            "bd1e75d918605c91b411e8789fb911e6c9a84534",
            real_job,
        )
        self.assertIn("tool/ci/run_macos_application_gate.py", real_job)
        self.assertIn("--reference-runtime", real_job)
        self.assertIn("executable publishes exact locked assets twice", real_job)
        self.assertIn("real-runtime", real_job)
        self.assertIn('--real-ort "${{ steps.ort.outputs.ort_path }}"', real_job)
        self.assertIn('--expected-ort-version "1.27.1"', real_job)
        self.assertIn("test/phase3_cpu_inference_test.dart", real_job)
        self.assertIn("test/runtime_ffi_test.dart", real_job)
        self.assertIn("test/isolate_session_test.dart", real_job)
        for fixture_variable in (
            "FONIX_TEST_SHIM_PATH:",
            "FONIX_TEST_ORT_PATH:",
            "FONIX_TEST_UNSUPPORTED_ORT_PATH:",
        ):
            self.assertEqual(bridge_step.count(fixture_variable), 1)
            self.assertEqual(real_job.count(fixture_variable), 1)
        self.assertIn("test/runtime_ffi_test.dart", bridge_step)
        self.assertNotIn("FONIX_TEST_FAKE_ORT_PATH:", bridge_step)
        self.assertNotIn("FONIX_TEST_REAL_ORT_PATH:", bridge_step)
        self.assertEqual(worker_step.count("FONIX_TEST_FAKE_ORT_PATH:"), 1)
        self.assertEqual(real_job.count("FONIX_TEST_FAKE_ORT_PATH:"), 1)
        for step, expected_test in (
            (worker_step, "test/isolate_session_test.dart"),
            (cpu_step, "test/cpu_inference_test.dart"),
            (phase3_step, "test/phase3_cpu_inference_test.dart"),
            (provider_step, "test/provider_profile_integration_test.dart"),
        ):
            self.assertIn(expected_test, step)
            self.assertEqual(step.count("FONIX_TEST_REAL_ORT_PATH:"), 1)
        for bridge_only_variable in (
            "FONIX_TEST_SHIM_PATH:",
            "FONIX_TEST_ORT_PATH:",
            "FONIX_TEST_UNSUPPORTED_ORT_PATH:",
        ):
            self.assertNotIn(bridge_only_variable, worker_step)
        for step in (worker_step, cpu_step, phase3_step, provider_step):
            self.assertNotIn("FONIX_TEST_SHIM_PATH:", step)
        self.assertEqual(real_job.count("FONIX_TEST_REAL_ORT_PATH:"), 4)
        self.assertNotIn("if:", real_job)
        self.assertIn(
            "--no-tests=error",
            (CI_DIRECTORY / "run_phase3_fixture_tests.py").read_text(),
        )

    def test_python_verifiers_runs_every_standalone_tool_test(self) -> None:
        source = (REPOSITORY / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        verifier_job = self._job(source, "python-verifiers")

        self.assertIn("-s tool/tests -p 'test_*.py' -v", verifier_job)
        self.assertNotIn("-p 'test_verify_native_libs.py'", verifier_job)

    def test_windows_security_contracts_are_required_ctests(self) -> None:
        source = (REPOSITORY / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        windows_job = self._job(source, "native-windows-x64-shim-contract")
        windows_cmake = (
            REPOSITORY / "tool/ci/windows/CMakeLists.txt"
        ).read_text(encoding="utf-8")
        shim_cmake = (REPOSITORY / "src/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        run_source = (REPOSITORY / "src/dort_run.c").read_text(encoding="utf-8")

        self.assertIn("--suite windows-contract", windows_job)
        for test_name in (
            "windows-path-security",
            "windows-profile-security",
        ):
            self.assertIn(
                test_name,
                run_native_tests.REQUIRED_TESTS["windows-contract"],
            )
            self.assertIn(f"NAME {test_name}", windows_cmake)
        self.assertIn("FONIX_WINDOWS_PROFILE_TESTING=1", windows_cmake)
        self.assertIn("bcrypt advapi32", windows_cmake)
        self.assertIn(
            "target_link_libraries(fonix_shim PRIVATE bcrypt advapi32)",
            shim_cmake,
        )
        self.assertNotIn(
            "Secure per-run profiling is not implemented on Windows.",
            run_source,
        )


class BindingConfigurationTest(unittest.TestCase):
    def test_package_language_version_uses_the_exact_sdk_floor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "pubspec.yaml").write_text(
                "name: fixture\nenvironment:\n  sdk: ^3.11.5\n",
                encoding="utf-8",
            )

            self.assertEqual(
                verify_bindings.package_language_version(repository),
                "3.11",
            )

            (repository / "pubspec.yaml").write_text(
                "name: fixture\nenvironment:\n  sdk: '>=3.11.5 <4.0.0'\n",
                encoding="utf-8",
            )
            with self.assertRaises(verify_bindings.BindingVerificationError):
                verify_bindings.package_language_version(repository)

    def test_isolated_package_config_pins_the_formatter_language(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            verify_bindings.write_isolated_package_config(root, "3.11")

            package_config = json.loads(
                (root / ".dart_tool/package_config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(package_config["configVersion"], 2)
            self.assertEqual(
                package_config["packages"],
                [
                    {
                        "name": "fonix_binding_check",
                        "rootUri": "../",
                        "packageUri": "lib/",
                        "languageVersion": "3.11",
                    }
                ],
            )

    def test_isolated_config_uses_absolute_inputs_and_temporary_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary).resolve()
            generated = repository.parent / "generated.dart"
            source = """\
name: Bindings
output: lib/src/ffi/generated.dart
headers:
  entry-points:
    - src/dort.h
compiler-opts:
  - -Isrc
  - -Ithird_party/onnxruntime/include
"""

            transformed, committed = verify_bindings.isolated_config(
                source,
                repository=repository,
                generated_output=generated,
            )

            self.assertEqual(
                committed, repository / "lib/src/ffi/generated.dart"
            )
            self.assertIn(json.dumps(str(generated)), transformed)
            self.assertIn(json.dumps(str(repository / "src/dort.h")), transformed)
            self.assertNotIn("output: lib/src/ffi/generated.dart", transformed)

    def test_isolated_config_rejects_ambiguous_headers(self) -> None:
        source = """\
output: lib/generated.dart
headers:
  entry-points:
    - src/dort.h
    - src/dort.h
compiler-opts:
  - -Isrc
  - -Ithird_party/onnxruntime/include
"""
        with self.assertRaises(verify_bindings.BindingVerificationError):
            verify_bindings.isolated_config(
                source,
                repository=Path.cwd(),
                generated_output=Path.cwd() / "generated.dart",
            )


class NativeRunnerTest(unittest.TestCase):
    def test_ctest_inventory_is_non_empty_and_complete(self) -> None:
        source = json.dumps(
            {
                "tests": [
                    {"name": name}
                    for name in sorted(run_native_tests.REQUIRED_TESTS["posix"])
                ]
            }
        )
        discovered = run_native_tests.parse_ctest_inventory(source)

        run_native_tests.validate_discovered_tests("posix", discovered)

    def test_empty_or_partial_ctest_inventory_fails(self) -> None:
        with self.assertRaises(run_native_tests.NativeTestError):
            run_native_tests.validate_discovered_tests("posix", frozenset())
        with self.assertRaises(run_native_tests.NativeTestError):
            run_native_tests.validate_discovered_tests(
                "windows-contract", frozenset({"windows-shim-contract"})
            )
        with self.assertRaises(run_native_tests.NativeTestError):
            run_native_tests.validate_discovered_tests(
                "posix",
                run_native_tests.REQUIRED_TESTS["posix"],
                require_real_ort=True,
            )
        run_native_tests.validate_discovered_tests(
            "bundled", frozenset({"bundled-runtime-adjacent"})
        )

    def test_unsupported_suite_host_combinations_fail(self) -> None:
        with self.assertRaises(run_native_tests.NativeTestError):
            run_native_tests.validate_suite_for_host("posix", "Windows")
        with self.assertRaises(run_native_tests.NativeTestError):
            run_native_tests.validate_suite_for_host(
                "windows-contract", "Linux"
            )
        for system in ("Darwin", "Linux", "Windows"):
            run_native_tests.validate_suite_for_host("bundled", system)
        with self.assertRaises(run_native_tests.NativeTestError):
            run_native_tests.validate_suite_for_host("bundled", "Android")

    def test_leak_detection_is_linux_only(self) -> None:
        linux = run_native_tests.sanitizer_environment("Linux", {})
        darwin = run_native_tests.sanitizer_environment("Darwin", {})

        self.assertIn("detect_leaks=1", linux["ASAN_OPTIONS"])
        self.assertEqual(darwin["ASAN_OPTIONS"], "halt_on_error=1")
        self.assertIn("halt_on_error=1", darwin["UBSAN_OPTIONS"])


class Phase3FixtureRunnerTest(unittest.TestCase):
    def test_committed_fixture_bytes_match_the_generator(self) -> None:
        run_phase3_fixture_tests.run_byte_check(REPOSITORY)

    def test_real_runtime_inventory_is_non_empty_and_complete(self) -> None:
        self.assertEqual(
            run_phase3_fixture_tests.REQUIRED_REAL_RUNTIME_TESTS,
            frozenset(
                {
                    "phase3_fixture_bytes",
                    "phase3_fixture_real_ort",
                }
            ),
        )
        source = json.dumps(
            {
                "tests": [
                    {"name": name}
                    for name in sorted(
                        run_phase3_fixture_tests.REQUIRED_REAL_RUNTIME_TESTS
                    )
                ]
            }
        )
        discovered = run_phase3_fixture_tests.parse_ctest_inventory(source)

        run_phase3_fixture_tests.validate_real_runtime_tests(discovered)

    def test_empty_or_partial_real_runtime_inventory_fails(self) -> None:
        with self.assertRaises(run_phase3_fixture_tests.Phase3FixtureError):
            run_phase3_fixture_tests.validate_real_runtime_tests(frozenset())
        with self.assertRaises(run_phase3_fixture_tests.Phase3FixtureError):
            run_phase3_fixture_tests.validate_real_runtime_tests(
                frozenset({"phase3_fixture_bytes"})
            )

    def test_real_runtime_version_must_equal_the_repository_pin(self) -> None:
        self.assertEqual(
            run_phase3_fixture_tests.PINNED_ORT_VERSION,
            fetch_pinned_macos_ort.PINNED_VERSION,
        )
        run_phase3_fixture_tests.validate_expected_ort_version("1.27.1")
        with self.assertRaises(run_phase3_fixture_tests.Phase3FixtureError):
            run_phase3_fixture_tests.validate_expected_ort_version("1.27.0")


class WindowsBinaryReportTest(unittest.TestCase):
    def test_matching_x64_external_report_passes(self) -> None:
        definition = "LIBRARY fonix_shim\nEXPORTS\n  dort_alpha\n  dort_beta\n"
        expected = check_windows_binary.parse_expected_exports(definition)
        exports = """\
          ordinal hint RVA      name
                1    0 00001000 dort_alpha
                2    1 00001020 dort_beta
"""

        check_windows_binary.validate_dumpbin_reports(
            exports_report=exports,
            dependents_report=(
                "KERNEL32.dll\nVCRUNTIME140.dll\nbcrypt.dll\nADVAPI32.dll\n"
            ),
            headers_report="8664 machine (x64)\n",
            expected_exports=expected,
        )

    def test_ort_import_and_export_drift_fail(self) -> None:
        expected = frozenset({"dort_alpha"})
        with self.assertRaises(check_windows_binary.WindowsBinaryError):
            check_windows_binary.validate_dumpbin_reports(
                exports_report="1 0 00001000 dort_alpha\n",
                dependents_report="onnxruntime.dll\n",
                headers_report="8664 machine (x64)\n",
                expected_exports=expected,
            )
        with self.assertRaises(check_windows_binary.WindowsBinaryError):
            check_windows_binary.validate_dumpbin_reports(
                exports_report="1 0 00001000 dort_other\n",
                dependents_report="KERNEL32.dll\n",
                headers_report="8664 machine (x64)\n",
                expected_exports=expected,
            )


class DesktopBundleAuditTest(unittest.TestCase):
    def _linux_bundle(
        self, root: Path
    ) -> tuple[Path, dict[str, str], Path]:
        bundle = root / "bundle"
        notices = bundle / "notices"
        notices.mkdir(parents=True)
        files = {
            "libfonix_shim.so": b"shim",
            "libonnxruntime.so.1": b"runtime",
            "libonnxruntime_providers_shared.so": b"provider",
            "notices/LICENSE": b"license",
            "notices/ThirdPartyNotices.txt": b"notices",
        }
        for relative, contents in files.items():
            candidate = bundle / relative
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(contents)
        source = {
            "url": "https://example.invalid/fixture.tgz",
            "source_revision": "a" * 40,
            "archive": "tgz",
            "sha256": "1" * 64,
            "size_bytes": 1,
        }
        payload_entries = [
            {
                "path": f"archive/{name}",
                "staged_path": name,
                "size_bytes": len(files[name]),
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name in (
                "libonnxruntime.so.1",
                "libonnxruntime_providers_shared.so",
            )
        ]
        notice_entries = [
            {
                "id": identifier,
                "container_depth": 0,
                "path": f"archive/{name}",
                "staged_path": name,
                "size_bytes": len(files[name]),
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name, identifier in (
                ("notices/LICENSE", "MIT"),
                ("notices/ThirdPartyNotices.txt", "ThirdPartyNotices"),
            )
        ]
        trusted_lock = {
            "schema": 2,
            "snapshot_date": "2026-08-06",
            "release_state": "unreleased-preview",
            "artifacts": [
                {
                    "id": "fixture-linux-x64",
                    "target": {
                        "os": "linux",
                        "architecture": "x86_64",
                        "variant": "default",
                        "min_os": "glibc-2.27",
                    },
                    "flavor": "cpu",
                    "runtime_mode": "bundled",
                    "source": source,
                    "expected_files": payload_entries,
                    "notices": notice_entries,
                }
            ],
        }
        trusted_lock_path = root / "trusted-lock.json"
        trusted_lock_path.write_text(json.dumps(trusted_lock), encoding="utf-8")
        trusted_lock_sha256 = hashlib.sha256(
            trusted_lock_path.read_bytes()
        ).hexdigest()
        manifest = {
            "schema": 2,
            "artifactId": "fixture-linux-x64",
            "lock": {
                "path": "native/versions.lock.yaml",
                "sha256": trusted_lock_sha256,
                "snapshotDate": "2026-08-06",
                "releaseState": "unreleased-preview",
            },
            "source": {
                "url": source["url"],
                "sourceRevision": source["source_revision"],
                "archive": source["archive"],
                "sha256": source["sha256"],
                "sizeBytes": source["size_bytes"],
            },
            "target": {
                "os": "linux",
                "architecture": "x86_64",
                "variant": "default",
                "minimumOs": "glibc-2.27",
                "flavor": "cpu",
                "runtimeMode": "bundled",
            },
            "payloadFiles": [
                {
                    "archivePath": entry["path"],
                    "stagedPath": entry["staged_path"],
                    "sizeBytes": entry["size_bytes"],
                    "sha256": entry["sha256"],
                }
                for entry in payload_entries
            ],
            "notices": [
                {
                    "id": entry["id"],
                    "containerDepth": entry["container_depth"],
                    "archivePath": entry["path"],
                    "stagedPath": entry["staged_path"],
                    "sizeBytes": entry["size_bytes"],
                    "sha256": entry["sha256"],
                }
                for entry in notice_entries
            ],
            "containers": [],
            "verifiedSymlinks": [],
            "archiveInspections": [],
            "claimBoundary": "Synthetic desktop audit fixture only.",
        }
        (bundle / "fonix-native-artifact-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        common = """\
file format elf64-x86-64
"""
        reports = {
            "libfonix_shim.so": common
            + "  NEEDED       libc.so.6\n"
            + "  NEEDED       libdl.so.2\n"
            + "  SONAME       libfonix_shim.so\n"
            + "  RUNPATH      $ORIGIN\n",
            "libonnxruntime.so.1": common
            + "".join(
                f"  NEEDED       {name}\n"
                for name in sorted(
                    audit_desktop_bundle.POLICIES[
                        "linux-x64"
                    ].runtime_dependencies
                )
            )
            + "  SONAME       libonnxruntime.so.1\n"
            + "  RUNPATH      $ORIGIN\n",
            "libonnxruntime_providers_shared.so": common
            + "".join(
                f"  NEEDED       {name}\n"
                for name in sorted(audit_desktop_bundle.LINUX_PROVIDER_NEEDED)
            )
            + "  SONAME       libonnxruntime_providers_shared.so\n",
        }
        return bundle, reports, trusted_lock_path

    def test_linux_bundle_layout_manifest_and_binary_reports_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, reports, trusted_lock = self._linux_bundle(Path(temporary))
            with mock.patch.object(
                audit_desktop_bundle,
                "_objdump",
                side_effect=lambda _tool, binary: reports[binary.name],
            ):
                result = audit_desktop_bundle.audit_bundle(
                    bundle,
                    audit_desktop_bundle.POLICIES["linux-x64"],
                    Path("objdump"),
                    trusted_lock,
                )
            self.assertEqual(result["target"], "linux/x86_64")
            self.assertEqual(result["artifactId"], "fixture-linux-x64")

    def test_payload_drift_and_missing_adjacency_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, reports, trusted_lock = self._linux_bundle(Path(temporary))
            (bundle / "libonnxruntime.so.1").write_bytes(b"tampered")
            with mock.patch.object(
                audit_desktop_bundle,
                "_objdump",
                side_effect=lambda _tool, binary: reports[binary.name],
            ):
                with self.assertRaises(
                    audit_desktop_bundle.DesktopBundleAuditError
                ):
                    audit_desktop_bundle.audit_bundle(
                        bundle,
                        audit_desktop_bundle.POLICIES["linux-x64"],
                        Path("objdump"),
                        trusted_lock,
                    )

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _reports, trusted_lock = self._linux_bundle(Path(temporary))
            (bundle / "libonnxruntime_providers_shared.so").unlink()
            with self.assertRaises(audit_desktop_bundle.DesktopBundleAuditError):
                audit_desktop_bundle.audit_bundle(
                    bundle,
                    audit_desktop_bundle.POLICIES["linux-x64"],
                    Path("objdump"),
                    trusted_lock,
                )

    def test_manifest_schema_paths_and_trusted_identity_tampering_fail(self) -> None:
        def expect_manifest_failure(mutate: object) -> None:
            with tempfile.TemporaryDirectory() as temporary:
                bundle, reports, trusted_lock = self._linux_bundle(
                    Path(temporary)
                )
                manifest_path = bundle / "fonix-native-artifact-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                assert callable(mutate)
                mutate(manifest)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with mock.patch.object(
                    audit_desktop_bundle,
                    "_objdump",
                    side_effect=lambda _tool, binary: reports[binary.name],
                ):
                    with self.assertRaises(
                        audit_desktop_bundle.DesktopBundleAuditError
                    ):
                        audit_desktop_bundle.audit_bundle(
                            bundle,
                            audit_desktop_bundle.POLICIES["linux-x64"],
                            Path("objdump"),
                            trusted_lock,
                        )

        expect_manifest_failure(lambda manifest: manifest.update({"extra": True}))
        expect_manifest_failure(
            lambda manifest: manifest["lock"].update({"extra": True})
        )
        expect_manifest_failure(
            lambda manifest: manifest["source"].update({"extra": True})
        )
        expect_manifest_failure(
            lambda manifest: manifest["target"].update({"extra": True})
        )
        expect_manifest_failure(
            lambda manifest: manifest["payloadFiles"][0].update({"extra": True})
        )
        expect_manifest_failure(
            lambda manifest: manifest["notices"][0].update({"extra": True})
        )
        expect_manifest_failure(
            lambda manifest: manifest["payloadFiles"][0].update(
                {"stagedPath": "../libonnxruntime.so.1"}
            )
        )
        expect_manifest_failure(
            lambda manifest: manifest["source"].update(
                {"url": "https://attacker.invalid/runtime.tgz"}
            )
        )
        expect_manifest_failure(
            lambda manifest: manifest["lock"].update({"sha256": "f" * 64})
        )

    def test_duplicate_manifest_key_fails_before_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, reports, trusted_lock = self._linux_bundle(Path(temporary))
            manifest_path = bundle / "fonix-native-artifact-manifest.json"
            source = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                f'{source[:-1]},"schema":2}}', encoding="utf-8"
            )
            with mock.patch.object(
                audit_desktop_bundle,
                "_objdump",
                side_effect=lambda _tool, binary: reports[binary.name],
            ):
                with self.assertRaises(audit_desktop_bundle.DesktopBundleAuditError):
                    audit_desktop_bundle.audit_bundle(
                        bundle,
                        audit_desktop_bundle.POLICIES["linux-x64"],
                        Path("objdump"),
                        trusted_lock,
                    )

    def test_pe_import_parser_is_closed_and_case_insensitive(self) -> None:
        imports = "\n".join(
            f"    DLL Name: {name}"
            for name in sorted(audit_desktop_bundle.WINDOWS_PROVIDER_IMPORTS)
        )
        object_format, parsed = audit_desktop_bundle._pe_metadata(
            f"file format coff-x86-64\n{imports}\n"
        )
        self.assertEqual(object_format, "coff-x86-64")
        self.assertEqual(
            parsed, audit_desktop_bundle.WINDOWS_PROVIDER_IMPORTS
        )


class MacOsRuntimeReportTest(unittest.TestCase):
    def _reports(self) -> dict[str, str]:
        dependencies = "\n".join(
            f"\t{path} (compatibility version 1.0.0, current version 1.0.0)"
            for path in sorted(check_macos_runtime.PINNED_DEPENDENCIES)
        )
        return {
            "headers_report": """\
/tmp/libonnxruntime.dylib:
Mach header
      magic  cputype cpusubtype  caps    filetype
MH_MAGIC_64    ARM64        ALL  0x00       DYLIB
""",
            "install_name_report": """\
/tmp/libonnxruntime.dylib:
@rpath/libonnxruntime.1.dylib
""",
            "dependencies_report": f"/tmp/libonnxruntime.dylib:\n{dependencies}\n",
            "load_commands_report": """\
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 14.0
      sdk 26.2
   ntools 1
          cmd LC_RPATH
      cmdsize 32
         path @loader_path (offset 12)
""",
            "exports_report": "\n".join(
                sorted(check_macos_runtime.PINNED_EXPORTS)
            ),
        }

    def test_exact_pinned_macos_runtime_reports_pass(self) -> None:
        check_macos_runtime.validate_reports(**self._reports())
        check_macos_runtime.validate_provider_probe_output(
            "runtimeVersion=1.27.1\n"
            "CoreMLExecutionProvider\n"
            "WebGpuExecutionProvider\n"
            "CPUExecutionProvider\n"
        )

    def test_architecture_global_dependency_and_export_drift_fail(self) -> None:
        reports = self._reports()
        reports["headers_report"] = reports["headers_report"].replace(
            "ARM64", "X86_64"
        )
        with self.assertRaises(check_macos_runtime.MacOsRuntimeError):
            check_macos_runtime.validate_reports(**reports)

        reports = self._reports()
        reports["dependencies_report"] += (
            "\t/usr/local/lib/libinjected.dylib "
            "(compatibility version 1.0.0, current version 1.0.0)\n"
        )
        with self.assertRaises(check_macos_runtime.MacOsRuntimeError):
            check_macos_runtime.validate_reports(**reports)

        reports = self._reports()
        reports["exports_report"] += "\n_Unexpected"
        with self.assertRaises(check_macos_runtime.MacOsRuntimeError):
            check_macos_runtime.validate_reports(**reports)

        with self.assertRaises(check_macos_runtime.MacOsRuntimeError):
            check_macos_runtime.validate_provider_probe_output(
                "runtimeVersion=1.27.1\nCPUExecutionProvider\n"
            )


class PinnedOrtStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime_bytes = b"synthetic-locked-macho"

    def _archive(self, *, traversal: bool = False) -> Path:
        prefix = "onnxruntime-osx-arm64-1.27.1/lib"
        archive_path = self.root / ("traversal.tgz" if traversal else "ort.tgz")
        with tarfile.open(archive_path, "w:gz") as archive:
            for name in (
                f"{prefix}/libonnxruntime.1.27.1.dylib",
                f"{prefix}/libonnxruntime.dylib",
            ):
                member = tarfile.TarInfo(name)
                member.size = len(self.runtime_bytes)
                archive.addfile(member, BytesIO(self.runtime_bytes))
            symlink = tarfile.TarInfo(f"{prefix}/libonnxruntime.1.dylib")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = "libonnxruntime.1.27.1.dylib"
            archive.addfile(symlink)
            if traversal:
                member = tarfile.TarInfo("../outside")
                member.size = 1
                archive.addfile(member, BytesIO(b"x"))
        return archive_path

    def _lock(self, archive_path: Path, *, url: str | None = None) -> Path:
        runtime_digest = hashlib.sha256(self.runtime_bytes).hexdigest()
        archive_bytes = archive_path.read_bytes()
        prefix = "onnxruntime-osx-arm64-1.27.1/lib"
        value = {
            "onnxruntime": {
                "compatibility_floor": {"header": {"version": "1.27.1"}}
            },
            "artifacts": [
                {
                    "id": "onnxruntime-1.27.1-macos-arm64-cpu",
                    "target": {
                        "os": "macos",
                        "architecture": "arm64",
                        "variant": "default",
                        "min_os": "14.0",
                    },
                    "flavor": "cpu",
                    "runtime_mode": "bundled",
                    "source": {
                        "url": url
                        or (
                            "https://github.com/microsoft/onnxruntime/releases/"
                            "download/v1.27.1/"
                            "onnxruntime-osx-arm64-1.27.1.tgz"
                        ),
                        "source_revision": "a" * 40,
                        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                        "size_bytes": len(archive_bytes),
                        "archive": "tgz",
                    },
                    "expected_files": [
                        {
                            "path": f"{prefix}/libonnxruntime.1.27.1.dylib",
                            "sha256": runtime_digest,
                            "size_bytes": len(self.runtime_bytes),
                        },
                        {
                            "path": f"{prefix}/libonnxruntime.dylib",
                            "sha256": runtime_digest,
                            "size_bytes": len(self.runtime_bytes),
                        },
                    ],
                    "expected_symlinks": [
                        {
                            "path": f"{prefix}/libonnxruntime.1.dylib",
                            "target": "libonnxruntime.1.27.1.dylib",
                        }
                    ],
                }
            ],
        }
        lock_path = self.root / "versions.lock.yaml"
        lock_path.write_text(json.dumps(value), encoding="utf-8")
        return lock_path

    def test_exact_archive_is_verified_and_staged(self) -> None:
        archive_path = self._archive()
        runtime = fetch_pinned_macos_ort.load_pinned_runtime(
            self._lock(archive_path)
        )
        output_directory = self.root / "staged"

        staged = fetch_pinned_macos_ort.stage_runtime(
            archive_path, runtime, output_directory
        )

        self.assertEqual(staged.read_bytes(), self.runtime_bytes)
        self.assertEqual(
            [path.name for path in output_directory.iterdir()],
            ["libonnxruntime.1.27.1.dylib"],
        )

    def test_verified_archive_is_retained_for_the_offline_build_hook(self) -> None:
        archive_path = self._archive()
        runtime = fetch_pinned_macos_ort.load_pinned_runtime(
            self._lock(archive_path)
        )
        cache_directory = self.root / "archive-cache"

        retained = fetch_pinned_macos_ort.retain_archive_in_cache(
            archive_path, runtime, cache_directory
        )

        self.assertEqual(
            retained.name, "onnxruntime-osx-arm64-1.27.1.tgz"
        )
        self.assertEqual(retained.read_bytes(), archive_path.read_bytes())
        self.assertEqual(
            fetch_pinned_macos_ort.retain_archive_in_cache(
                archive_path, runtime, cache_directory
            ),
            retained,
        )

        retained.write_bytes(b"corrupt")
        with self.assertRaises(fetch_pinned_macos_ort.ArtifactVerificationError):
            fetch_pinned_macos_ort.retain_archive_in_cache(
                archive_path, runtime, cache_directory
            )

    def test_traversal_member_fails_and_removes_staging_directory(self) -> None:
        archive_path = self._archive(traversal=True)
        runtime = fetch_pinned_macos_ort.load_pinned_runtime(
            self._lock(archive_path)
        )
        output_directory = self.root / "rejected"

        with self.assertRaises(fetch_pinned_macos_ort.ArtifactVerificationError):
            fetch_pinned_macos_ort.stage_runtime(
                archive_path, runtime, output_directory
            )

        self.assertFalse(output_directory.exists())

    def test_moving_latest_url_is_rejected(self) -> None:
        archive_path = self._archive()
        lock_path = self._lock(
            archive_path,
            url=(
                "https://github.com/microsoft/onnxruntime/releases/latest/"
                "download/onnxruntime-osx-arm64.tgz"
            ),
        )

        with self.assertRaises(fetch_pinned_macos_ort.ArtifactVerificationError):
            fetch_pinned_macos_ort.load_pinned_runtime(lock_path)


if __name__ == "__main__":
    unittest.main()
