from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


CI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_ROOT))
SCRIPT = CI_ROOT / "run_android_reference_app_gate.py"
SPEC = importlib.util.spec_from_file_location("run_android_reference_app_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def _logcat_line(message: str, *, uid: str = "10123", pid: int = 2345) -> str:
    return (
        f"08-07 01:02:03.456 {uid} {pid} {pid} I "
        f"{gate.LOG_TAG}: {message}"
    )


class AndroidReferenceCopyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-android-copy-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "example"
        self.source.mkdir()

    def test_copy_omits_only_exact_generated_paths(self) -> None:
        included = {
            "pubspec.yaml": b"name: reference\n",
            "android/.gradle-copy/keep": b"keep\n",
            "android/.kotlin-copy/keep": b"keep\n",
            "android/app/.cxx-copy/keep": b"keep\n",
            "android/captures-copy/keep": b"keep\n",
            "android/local.properties.example": b"keep\n",
        }
        excluded = {
            ".dart_tool/generated": b"drop\n",
            ".fonix-artifact-cache/archive": b"drop\n",
            "android/.gradle/cache": b"drop\n",
            "android/.kotlin/cache": b"drop\n",
            "android/app/.cxx/output": b"drop\n",
            "android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java": b"drop\n",
            "android/captures/capture": b"drop\n",
            "android/fonix_reference_android.iml": b"drop\n",
            "android/local.properties": b"drop\n",
            "build/app.apk": b"drop\n",
        }
        for relative, data in {**included, **excluded}.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        destination = self.root / "copy"
        summary = gate._copy_example(self.source, destination)

        self.assertEqual(summary.file_count, len(included))
        for relative, data in included.items():
            self.assertEqual((destination / relative).read_bytes(), data)
        for relative in excluded:
            self.assertFalse((destination / relative).exists())

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliable on Windows")
    def test_copy_rejects_symlink_inside_excluded_tree(self) -> None:
        generated = self.source / "android/.gradle"
        generated.mkdir(parents=True)
        (generated / "target").write_text("target", encoding="utf-8")
        (generated / "link").symlink_to("target")
        with self.assertRaisesRegex(gate.AndroidReferenceAppGateError, "symbolic link"):
            gate._copy_example(self.source, self.root / "copy")


class AndroidReferenceConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-android-config-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "fonix"
        self.repository.mkdir()

    def test_pubspec_patch_stages_host_tests_then_selects_android_hook(self) -> None:
        pubspec = self.root / "pubspec.yaml"
        source = (
            "dependencies:\n"
            "  fonix:\n"
            "    path: ..\n"
            "hooks:\n"
            "  user_defines:\n"
            "    fonix:\n"
            "      runtime_mode: bundled\n"
            "      artifact_cache: .fonix-artifact-cache\n"
            "      application_minimum_os: '14.0'\n"
        )
        pubspec.write_text(source, encoding="utf-8")

        gate._patch_pubspec(pubspec, self.repository)

        result = pubspec.read_text(encoding="utf-8")
        self.assertIn("runtime_mode: external", result)
        self.assertNotIn("android_runtime_owner", result)
        self.assertNotIn("application_minimum_os", result)
        self.assertIn(json.dumps(self.repository.as_posix()), result)

        gate._select_android_hooks(pubspec)

        result = pubspec.read_text(encoding="utf-8")
        self.assertIn("android_runtime_owner: application", result)
        self.assertIn("runtime_mode: bundled", result)
        self.assertNotIn("runtime_mode: external", result)
        self.assertNotIn("application_minimum_os", result)

    def test_pubspec_lock_patch_is_exact_and_reversible(self) -> None:
        lockfile = self.root / "pubspec.lock"
        source = (
            "packages:\n"
            "  fonix:\n"
            "    dependency: direct main\n"
            "    description:\n"
            '      path: ".."\n'
            "      relative: true\n"
            "    source: path\n"
        )
        lockfile.write_text(source, encoding="utf-8")

        identity = gate._patch_pubspec_lock(lockfile, self.repository)

        updated = lockfile.read_text(encoding="utf-8")
        self.assertIn(
            f"      path: {json.dumps(self.repository.as_posix())}\n",
            updated,
        )
        self.assertIn("      relative: false\n", updated)
        self.assertEqual(identity.sha256, hashlib.sha256(updated.encode()).hexdigest())

    def test_locked_pub_get_enforces_and_preserves_lock_identity(self) -> None:
        work = self.root / "work"
        work.mkdir()
        lockfile = work / "pubspec.lock"
        lockfile.write_bytes(b"locked\n")
        identity = gate._file_identity(
            lockfile,
            "test lock",
            maximum=gate.MAX_PUBSPEC_LOCK_BYTES,
        )
        flutter = self.root / "flutter"
        with mock.patch.object(gate, "_run_command") as run_command:
            gate._run_locked_pub_get(
                flutter,
                work,
                {},
                identity,
                operation="test pub get",
            )
        self.assertEqual(
            run_command.call_args.args[0],
            (
                str(flutter),
                "pub",
                "get",
                "--offline",
                "--enforce-lockfile",
            ),
        )

        def mutate_lock(*_args: object, **_kwargs: object) -> None:
            lockfile.write_bytes(b"changed\n")

        with (
            mock.patch.object(gate, "_run_command", side_effect=mutate_lock),
            self.assertRaisesRegex(
                gate.AndroidReferenceAppGateError,
                "output lockfile identity changed",
            ),
        ):
            gate._run_locked_pub_get(
                flutter,
                work,
                {},
                identity,
                operation="test pub get",
            )

    def test_sdk_environment_and_local_properties_are_exactly_bound(self) -> None:
        sdk = self.root / "sdk"
        sdk.mkdir()
        java = self.root / "jdk"
        java.mkdir()
        with mock.patch.object(
            gate,
            "tool_environment",
            return_value={
                "ANDROID_HOME": "/wrong",
                "ANDROID_SDK_ROOT": "/also-wrong",
            },
        ):
            environment = gate._android_build_environment(java, sdk)
        self.assertEqual(environment["JAVA_HOME"], str(java))
        self.assertEqual(environment["ANDROID_HOME"], str(sdk))
        self.assertEqual(environment["ANDROID_SDK_ROOT"], str(sdk))

        properties = self.root / "local.properties"
        properties.write_text(
            f"sdk.dir={sdk}\nflutter.sdk=/pinned/flutter\n",
            encoding="utf-8",
        )
        gate._verify_android_local_properties(properties, sdk)
        properties.write_text(
            f"sdk.dir={sdk}\nsdk.dir=/spoof\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            gate.AndroidReferenceAppGateError,
            "not bound to --android-sdk",
        ):
            gate._verify_android_local_properties(properties, sdk)

    def test_lock_selection_binds_nested_runtime(self) -> None:
        lock = {
            "artifacts": [
                {
                    "id": gate.ARTIFACT_ID,
                    "target": {
                        "os": "android",
                        "architecture": gate.ABI,
                        "variant": "default",
                        "min_os": "24",
                    },
                    "flavor": "cpu",
                    "runtime_mode": "bundled",
                    "source": {
                        "url": (
                            "https://api.nuget.org/v3-flatcontainer/"
                            "microsoft.ml.onnxruntime/1.27.1/"
                            "microsoft.ml.onnxruntime.1.27.1.nupkg"
                        ),
                        "archive": "zip",
                        "sha256": gate.ARTIFACT_SOURCE_SHA256,
                        "size_bytes": gate.ARTIFACT_SOURCE_SIZE,
                    },
                    "containers": [
                        {
                            "path": gate.ARTIFACT_CONTAINER_PATH,
                            "sha256": gate.ARTIFACT_CONTAINER_SHA256,
                            "size_bytes": gate.ARTIFACT_CONTAINER_SIZE,
                            "archive": "zip",
                        }
                    ],
                    "expected_files": [
                        {
                            "path": gate.ORT_ARCHIVE_PATH,
                            "staged_path": "libonnxruntime.so",
                            "sha256": gate.ORT_SHA256,
                            "size_bytes": gate.ORT_SIZE_BYTES,
                        }
                    ],
                }
            ]
        }
        lock_path = self.root / "versions.lock.yaml"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")

        archive = gate._load_archive(lock_path)

        self.assertEqual(archive.runtime_sha256, gate.ORT_SHA256)
        lock["artifacts"][0]["expected_files"][0]["sha256"] = "0" * 64
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(gate.AndroidReferenceAppGateError, "runtime identity"):
            gate._load_archive(lock_path)

    def test_nested_runtime_extraction_checks_both_zip_layers(self) -> None:
        runtime = b"runtime"
        nested = _zip_bytes({"jni/arm64-v8a/libonnxruntime.so": runtime})
        outer = _zip_bytes({"runtimes/android/native/onnxruntime.aar": nested})
        archive_path = self.root / "runtime.nupkg"
        archive_path.write_bytes(outer)
        archive = gate.PinnedArchive(
            "test",
            archive_path.name,
            hashlib.sha256(outer).hexdigest(),
            len(outer),
            "runtimes/android/native/onnxruntime.aar",
            hashlib.sha256(nested).hexdigest(),
            len(nested),
            "jni/arm64-v8a/libonnxruntime.so",
            hashlib.sha256(runtime).hexdigest(),
            len(runtime),
        )
        work = self.root / "work"
        work.mkdir()

        result = gate._extract_reference_runtime(archive_path, work, archive)

        self.assertEqual(result.read_bytes(), runtime)

    def test_assets_must_reproduce_sidecar_before_publication(self) -> None:
        work = self.root / "work"
        generic = work / "assets/fonix"
        sidecar = generic / "android-arm64-v8a"
        generated = work / "generated"
        for root in (generic, sidecar, generated):
            root.mkdir(parents=True, exist_ok=True)
        expected = {
            "ThirdPartyNotices.txt": b"notice\n",
            "fonix-native-artifact-manifest.json": b"{}\n",
        }
        for name, data in expected.items():
            (generic / name).write_bytes(b"mac\n")
            (sidecar / name).write_bytes(data)
            (generated / name).write_bytes(data)

        identities = gate._verify_and_publish_android_assets(work, generated)

        self.assertEqual({name: (generic / name).read_bytes() for name in expected}, expected)
        self.assertEqual(
            identities,
            {name: hashlib.sha256(data).hexdigest() for name, data in sorted(expected.items())},
        )
        (generated / "ThirdPartyNotices.txt").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(gate.AndroidReferenceAppGateError, "differ"):
            gate._verify_and_publish_android_assets(work, generated)


class AndroidReferenceReceiptTest(unittest.TestCase):
    def test_exact_receipt_is_accepted(self) -> None:
        line = _logcat_line(gate.RECEIPT_PREFIX + gate.EXPECTED_RECEIPT_JSON)

        evidence = gate._parse_logcat_receipt(
            line,
            expected_uid=10123,
            observed_pid=2345,
        )

        self.assertEqual(evidence.receipt, gate.EXPECTED_RECEIPT)
        self.assertEqual(evidence.uid, 10123)
        self.assertEqual(evidence.pid, 2345)
        self.assertTrue(evidence.pid_was_observed)

    def test_uid_only_receipt_survives_immediate_process_exit(self) -> None:
        line = _logcat_line(
            gate.RECEIPT_PREFIX + gate.EXPECTED_RECEIPT_JSON,
            uid="u0_a123",
            pid=3456,
        )

        evidence = gate._parse_logcat_receipt(
            line,
            expected_uid=10123,
            observed_pid=None,
        )

        self.assertEqual(evidence.pid, 3456)
        self.assertFalse(evidence.pid_was_observed)

    def test_other_uid_process_and_metadata_free_spoofs_are_rejected(self) -> None:
        message = gate.RECEIPT_PREFIX + gate.EXPECTED_RECEIPT_JSON
        mutations = (
            _logcat_line(message, uid="10124"),
            _logcat_line(message, pid=9999),
            message,
            f"I/{gate.LOG_TAG}( 2345): {message}",
        )
        for value in mutations:
            with self.subTest(value=value[:100]):
                with self.assertRaises(gate.AndroidReferenceAppGateError):
                    gate._parse_logcat_receipt(
                        value,
                        expected_uid=10123,
                        observed_pid=2345,
                    )

    def test_failure_duplicate_and_noncanonical_json_are_rejected(self) -> None:
        line = _logcat_line(gate.RECEIPT_PREFIX + gate.EXPECTED_RECEIPT_JSON)
        mutations = (
            line + "\n" + line,
            line
            + "\n"
            + _logcat_line(
                gate.FAILURE_PREFIX
                + '{"schemaVersion":1,"status":"failed","errorType":"Failure"}'
            ),
            _logcat_line(
                gate.RECEIPT_PREFIX
                + json.dumps(gate.EXPECTED_RECEIPT, sort_keys=True, separators=(",", ":"))
            ),
            _logcat_line(
                gate.RECEIPT_PREFIX
                + json.dumps(gate.EXPECTED_RECEIPT, separators=(", ", ": "))
            ),
            _logcat_line(
                gate.RECEIPT_PREFIX
                + json.dumps({**gate.EXPECTED_RECEIPT, "unexpected": True})
            ),
        )
        for value in mutations:
            with self.subTest(value=value[-100:]):
                with self.assertRaises(gate.AndroidReferenceAppGateError):
                    gate._parse_logcat_receipt(
                        value,
                        expected_uid=10123,
                        observed_pid=2345,
                    )

    def test_no_completion_is_pending_but_failure_is_terminal(self) -> None:
        with self.assertRaises(gate.AndroidReferenceReceiptPending):
            gate._parse_logcat_receipt(
                "--------- beginning of main",
                expected_uid=10123,
                observed_pid=None,
            )
        failure = _logcat_line(
            gate.FAILURE_PREFIX
            + '{"schemaVersion":1,"status":"failed","errorType":"Failure"}'
        )
        with self.assertRaises(gate.AndroidReferenceAppGateError) as captured:
            gate._parse_logcat_receipt(
                failure,
                expected_uid=10123,
                observed_pid=None,
            )
        self.assertNotIsInstance(captured.exception, gate.AndroidReferenceReceiptPending)

    def test_uid_and_pid_filters_are_explicit(self) -> None:
        self.assertEqual(
            gate._logcat_arguments(10123, 2345),
            (
                "logcat",
                "-d",
                "-b",
                "main",
                "-v",
                "threadtime,uid,printable",
                "--uid=10123",
                "--pid=2345",
                "-s",
                f"{gate.LOG_TAG}:I",
                "*:S",
            ),
        )
        self.assertNotIn("--pid=2345", gate._logcat_arguments(10123, None))


class AndroidReferenceDeviceIntegrityTest(unittest.TestCase):
    def test_package_uid_and_pid_parsing_are_closed(self) -> None:
        self.assertEqual(
            gate._parse_package_uid(
                f"package:{gate.APPLICATION_ID} uid:10123"
            ),
            10123,
        )
        self.assertEqual(gate._parse_application_pid("2345"), 2345)
        self.assertIsNone(gate._parse_application_pid(""))
        for value in (
            f"package:{gate.APPLICATION_ID} uid:10123\npackage:spoof uid:10124",
            f"package:{gate.APPLICATION_ID} uid:9999",
        ):
            with self.subTest(value=value):
                with self.assertRaises(gate.AndroidReferenceAppGateError):
                    gate._parse_package_uid(value)
        for value in ("2345 3456", "0", "2345\nspoof"):
            with self.subTest(value=value):
                with self.assertRaises(gate.AndroidReferenceAppGateError):
                    gate._parse_application_pid(value)

    def test_installed_base_apk_bytes_are_bound_to_audited_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-installed-apk-") as temporary:
            root = Path(temporary)
            apk = root / "app-release.apk"
            apk.write_bytes(b"signed apk")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            installed_path = (
                "/data/app/~~Abc_123==/"
                f"{gate.APPLICATION_ID}-Def_456==/base.apk"
            )

            def adb_response(
                _adb: Path,
                _serial: str,
                arguments: tuple[str, ...],
                _operation: str,
            ) -> str:
                if arguments == ("shell", "pm", "path", gate.APPLICATION_ID):
                    return f"package:{installed_path}"
                if arguments == ("shell", "sha256sum", installed_path):
                    return f"{digest}  {installed_path}"
                self.fail(f"unexpected adb arguments: {arguments!r}")

            with mock.patch.object(gate, "_adb", side_effect=adb_response):
                observed = gate._verify_installed_apk(
                    root / "adb",
                    "emulator-5554",
                    apk,
                    digest,
                )
            self.assertEqual(observed, digest)

            with self.assertRaisesRegex(
                gate.AndroidReferenceAppGateError,
                "closed /data/app",
            ):
                gate._parse_installed_base_apk_path(
                    f"package:/data/local/tmp/{gate.APPLICATION_ID}/base.apk"
                )
            with self.assertRaisesRegex(
                gate.AndroidReferenceAppGateError,
                "checksum response is not exact",
            ):
                gate._parse_device_sha256(
                    f"{'0' * 64}  /data/app/spoof/base.apk",
                    installed_path,
                )

    def test_apk_and_aab_must_have_one_identical_lowercase_signer(self) -> None:
        digest = "a" * 64
        apk = {"signing": {"certificateSha256": digest}}
        aab = {"signing": {"certificateSha256": digest}}
        self.assertEqual(gate._matching_signer_sha256(apk, aab), digest)
        aab["signing"]["certificateSha256"] = "b" * 64
        with self.assertRaisesRegex(
            gate.AndroidReferenceAppGateError,
            "digests differ",
        ):
            gate._matching_signer_sha256(apk, aab)
        aab["signing"]["certificateSha256"] = digest.upper()
        with self.assertRaisesRegex(
            gate.AndroidReferenceAppGateError,
            "lowercase SHA-256",
        ):
            gate._matching_signer_sha256(apk, aab)

    def test_installed_application_cleanup_is_fail_closed(self) -> None:
        calls: list[tuple[str, ...]] = []

        def failed_cleanup(
            _adb: Path,
            _serial: str,
            arguments: tuple[str, ...],
            _operation: str,
        ) -> str:
            calls.append(arguments)
            if arguments[:3] == ("shell", "am", "force-stop"):
                raise gate.AndroidReferenceAppGateError("force-stop failed")
            if arguments == ("uninstall", gate.APPLICATION_ID):
                return "Failure [DELETE_FAILED_INTERNAL_ERROR]"
            if arguments == (
                "shell",
                "cmd",
                "package",
                "list",
                "packages",
                gate.APPLICATION_ID,
            ):
                return f"package:{gate.APPLICATION_ID}"
            self.fail(f"unexpected cleanup arguments: {arguments!r}")

        with (
            mock.patch.object(gate, "_adb", side_effect=failed_cleanup),
            self.assertRaisesRegex(
                gate.AndroidReferenceAppGateError,
                "installed application cleanup failed",
            ),
        ):
            gate._cleanup_installed_application(
                Path("/adb"),
                "emulator-5554",
            )
        self.assertEqual(len(calls), 3)

        def successful_cleanup(
            _adb: Path,
            _serial: str,
            arguments: tuple[str, ...],
            _operation: str,
        ) -> str:
            if arguments == ("uninstall", gate.APPLICATION_ID):
                return "Success"
            if arguments == (
                "shell",
                "cmd",
                "package",
                "list",
                "packages",
                gate.APPLICATION_ID,
            ):
                return ""
            if arguments[:3] == ("shell", "am", "force-stop"):
                return ""
            self.fail(f"unexpected cleanup arguments: {arguments!r}")

        with mock.patch.object(gate, "_adb", side_effect=successful_cleanup):
            gate._cleanup_installed_application(Path("/adb"), "emulator-5554")


class AndroidReferenceAvdOwnershipTest(unittest.TestCase):
    def test_reaper_terminates_then_kills_a_stuck_owned_process(self) -> None:
        process = mock.Mock()
        process.poll.side_effect = (None, None)
        process.wait.side_effect = (
            subprocess.TimeoutExpired("emulator", 10),
            0,
        )

        gate._reap_owned_avd(process)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_start_failure_reaps_the_process_it_spawned(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with (
            mock.patch.object(gate, "_running_named_avd", return_value=None),
            mock.patch.object(gate, "_adb_known_serials", return_value=set()),
            mock.patch.object(
                gate,
                "_adb_devices",
                side_effect=gate.AndroidReferenceAppGateError("adb failed"),
            ),
            mock.patch.object(gate.subprocess, "Popen", return_value=process),
            self.assertRaisesRegex(gate.AndroidReferenceAppGateError, "adb failed"),
        ):
            gate._start_named_avd(Path("/adb"), Path("/emulator"), "test-avd")

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)

    def test_new_emulator_is_bound_to_the_requested_avd_name(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        requested = "requested-avd"

        def avd_name(_adb: Path, serial: str) -> str:
            return {
                "emulator-5554": "unrelated-avd",
                "emulator-5556": requested,
            }[serial]

        with (
            mock.patch.object(gate, "_running_named_avd", return_value=None),
            mock.patch.object(
                gate,
                "_adb_known_serials",
                return_value={"emulator-5552"},
            ),
            mock.patch.object(
                gate,
                "_adb_devices",
                return_value={
                    "emulator-5552",
                    "emulator-5554",
                    "emulator-5556",
                },
            ),
            mock.patch.object(gate, "_query_avd_name", side_effect=avd_name),
            mock.patch.object(gate, "_adb", return_value="1"),
            mock.patch.object(gate.subprocess, "Popen", return_value=process),
        ):
            serial, owned_process = gate._start_named_avd(
                Path("/adb"),
                Path("/emulator"),
                requested,
            )

        self.assertEqual(serial, "emulator-5556")
        self.assertIs(owned_process, process)

    def test_owned_avd_cleanup_requires_serial_disappearance(self) -> None:
        process = mock.Mock()
        process.poll.return_value = 0
        process.wait.return_value = 0
        with (
            mock.patch.object(gate, "_adb", return_value="OK"),
            mock.patch.object(
                gate,
                "_adb_known_serials",
                side_effect=({"emulator-5554"}, set()),
            ),
            mock.patch.object(gate.time, "sleep"),
        ):
            gate._reap_owned_avd(
                process,
                adb=Path("/adb"),
                serial="emulator-5554",
            )

        with (
            mock.patch.object(
                gate,
                "_adb",
                side_effect=gate.AndroidReferenceAppGateError("shutdown failed"),
            ),
            mock.patch.object(
                gate,
                "_adb_known_serials",
                return_value={"emulator-5554"},
            ),
            mock.patch.object(gate, "AVD_SHUTDOWN_TIMEOUT_SECONDS", 0.0),
            self.assertRaisesRegex(
                gate.AndroidReferenceAppGateError,
                "serial remained",
            ),
        ):
            gate._reap_owned_avd(
                process,
                adb=Path("/adb"),
                serial="emulator-5554",
            )


class AndroidReferenceToolIdentityTest(unittest.TestCase):
    def test_static_tool_resolution_does_not_require_adb_or_emulator(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-android-sdk-") as temporary:
            sdk = Path(temporary)
            command_line_tools = (
                sdk / "cmdline-tools" / gate.ANDROID_COMMAND_LINE_TOOLS_VERSION
            )
            command_line_tools.mkdir(parents=True)
            (command_line_tools / "source.properties").write_text(
                "Pkg.Revision=20.0\n"
                "Pkg.Path=cmdline-tools;20.0\n"
                "Pkg.Desc=Android SDK Command-line Tools\n",
                encoding="utf-8",
            )
            paths = (
                command_line_tools / "bin/apkanalyzer",
                sdk
                / "build-tools"
                / gate.ANDROID_BUILD_TOOLS_VERSION
                / "zipalign",
                sdk
                / "build-tools"
                / gate.ANDROID_BUILD_TOOLS_VERSION
                / "apksigner",
                sdk
                / "ndk"
                / gate.ANDROID_NDK_VERSION
                / "toolchains/llvm/prebuilt/test-host/bin/llvm-readelf",
            )
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#!/bin/sh\n", encoding="utf-8")
                path.chmod(0o755)

            tools = gate._default_tools(sdk, include_device_tools=False)

            self.assertEqual(set(tools), {"apkanalyzer", "zipalign", "apksigner", "readelf"})
            self.assertNotIn("adb", tools)
            self.assertNotIn("emulator", tools)

    def test_java_home_is_exactly_bound_to_supported_jdk(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-java-home-") as temporary:
            home = Path(temporary)
            binary = home / "bin"
            binary.mkdir()
            for name in ("java", "jarsigner"):
                path = binary / name
                path.write_text("#!/bin/sh\n", encoding="utf-8")
                path.chmod(0o755)
            properties = SimpleNamespace(
                stdout="",
                stderr=(
                    f"    java.home = {home}\n"
                    "    java.specification.version = 21\n"
                    "    java.vendor = Test Vendor\n"
                    "    java.version = 21.0.12\n"
                    "    java.vm.name = OpenJDK 64-Bit Server VM\n"
                ),
            )
            with mock.patch.object(gate, "run_bounded", return_value=properties):
                tools = gate._validate_java_home(home)
            self.assertEqual(tools.version, "21.0.12")
            self.assertEqual(tools.home, home.resolve())

    def test_bundletool_size_hash_and_version_are_all_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-bundletool-") as temporary:
            root = Path(temporary)
            bundletool = root / "bundletool.jar"
            bundletool.write_bytes(b"jar")
            java = root / "java"
            java.write_bytes(b"java")
            with (
                mock.patch.object(gate, "BUNDLETOOL_SIZE_BYTES", 3),
                mock.patch.object(
                    gate, "BUNDLETOOL_SHA256", hashlib.sha256(b"jar").hexdigest()
                ),
                mock.patch.object(
                    gate,
                    "run_bounded",
                    return_value=SimpleNamespace(stdout="1.18.3\n", stderr=""),
                ),
            ):
                result = gate._validate_bundletool(bundletool, java, {})
            self.assertEqual(result["version"], "1.18.3")

    def test_static_audits_precede_any_avd_execution(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        run_gate = source[source.index("def run_gate(") :]
        self.assertLess(run_gate.index("apk_audit = _audit_package("), run_gate.index("if avd_name"))
        self.assertLess(run_gate.index("aab_audit = _audit_package("), run_gate.index("if avd_name"))


if __name__ == "__main__":
    unittest.main()
