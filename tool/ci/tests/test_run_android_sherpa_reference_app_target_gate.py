from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


REPOSITORY = Path(__file__).resolve().parents[3]
CI_ROOT = REPOSITORY / "tool/ci"
sys.path.insert(0, str(CI_ROOT))
SCRIPT = CI_ROOT / "run_android_sherpa_reference_app_target_gate.py"
SPEC = importlib.util.spec_from_file_location(
    "fonix_android_sherpa_target_gate", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class FakeTargetRunner:
    def __init__(self, fixture: "AndroidSherpaTargetGateTest") -> None:
        self.fixture = fixture
        self.commands: list[tuple[str, ...]] = []
        self.installed = False
        self.pid: int | None = None
        self.uninstalled = False
        self.uninstall_failure = False
        self.logcat = fixture.completion_log
        self.application_id = GATE.APPLICATION_ID
        self.install_stdout = "Performing Push Install\nSuccess\n"
        self.install_stderr = ""
        self.fingerprint_queries = 0
        self.postflight_fingerprint: str | None = None
        self.fingerprint_change_query = 3

    def __call__(
        self,
        command: list[str] | tuple[str, ...],
        *,
        operation: str,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: int,
        maximum_output: int,
    ) -> GATE.CommandOutput:
        del operation, cwd, environment, timeout_seconds, maximum_output
        argv = tuple(command)
        self.commands.append(argv)
        if argv == (
            str(self.fixture.apkanalyzer.resolve()),
            "manifest",
            "application-id",
            str(self.fixture.final_apk.resolve()),
        ):
            return self._output(f"{self.application_id}\n")
        if len(argv) >= 3 and argv[1] == "-B" and argv[2].endswith(
            "validate_android_load_order_receipt.py"
        ):
            output = Path(argv[argv.index("--output") + 1])
            receipt_path = Path(argv[argv.index("--receipt") + 1])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            _write_json(
                output,
                {
                    "schemaVersion": 1,
                    "result": "passed",
                    "claimStatus": "offline-consistency-only",
                    "matrix": receipt["matrix"],
                    "build": receipt["build"],
                    "process": receipt["process"],
                },
            )
            return GATE.CommandOutput("validator passed\n", "")

        adb_arguments = argv[3:] if len(argv) >= 3 and argv[1] == "-s" else argv[1:]
        if adb_arguments == ("devices",):
            return self._output("List of devices attached\nserial-1\tdevice\n")
        if adb_arguments == ("shell", "getprop", "ro.product.cpu.abi"):
            return self._output("arm64-v8a\n")
        if adb_arguments == ("shell", "getprop", "ro.build.version.sdk"):
            return self._output("35\n")
        if adb_arguments == ("shell", "getconf", "PAGE_SIZE"):
            return self._output("16384\n")
        if adb_arguments == ("shell", "getprop", "ro.product.model"):
            return self._output("Pixel Test Device\n")
        if adb_arguments == ("shell", "getprop", "ro.build.fingerprint"):
            self.fingerprint_queries += 1
            if (
                self.fingerprint_queries >= self.fingerprint_change_query
                and self.postflight_fingerprint is not None
            ):
                return self._output(f"{self.postflight_fingerprint}\n")
            return self._output("vendor/product/device:15/build/test-keys\n")
        if adb_arguments == ("shell", "getprop", "ro.kernel.qemu"):
            return self._output("1\n")
        if adb_arguments[:2] == ("install", "--no-streaming"):
            self.installed = True
            return self._output(self.install_stdout, self.install_stderr)
        if adb_arguments == ("shell", "pm", "path", GATE.APPLICATION_ID):
            return self._output(f"package:{self.fixture.installed_path}\n")
        if adb_arguments == (
            "shell",
            "sha256sum",
            self.fixture.installed_path,
        ):
            return self._output(
                f"{_sha256(self.fixture.final_apk)}  {self.fixture.installed_path}\n"
            )
        if adb_arguments == (
            "shell",
            "cmd",
            "package",
            "list",
            "packages",
            "-U",
            GATE.APPLICATION_ID,
        ):
            return self._output(f"package:{GATE.APPLICATION_ID} uid:10123\n")
        if adb_arguments == ("shell", "am", "force-stop", GATE.APPLICATION_ID):
            self.pid = None
            return self._output("")
        if adb_arguments == (
            "shell",
            f"pidof {GATE.APPLICATION_ID} 2>/dev/null || true",
        ):
            return self._output("" if self.pid is None else f"{self.pid}\n")
        if adb_arguments == ("logcat", "-c"):
            return self._output("")
        if adb_arguments[:5] == (
            "shell",
            "am",
            "start",
            "-n",
            GATE.APPLICATION_COMPONENT,
        ):
            self.pid = 4321
            return self._output(
                "Starting: Intent { "
                "cmp=dev.fonix.sherpa_reference/.MainActivity }\n"
            )
        if adb_arguments[:6] == (
            "logcat",
            "-d",
            "-b",
            "main",
            "-v",
            "threadtime,uid,printable",
        ):
            self.fixture.assertEqual(
                adb_arguments[6:],
                (
                    "--uid=10123",
                    "--pid=4321",
                    "-s",
                    "FonixSherpaRef:I",
                    "*:S",
                ),
            )
            return self._output(self.logcat)
        if adb_arguments == (
            "shell",
            "cmd",
            "package",
            "list",
            "packages",
            GATE.APPLICATION_ID,
        ):
            return self._output(
                f"package:{GATE.APPLICATION_ID}\n" if self.installed else ""
            )
        if adb_arguments == ("uninstall", GATE.APPLICATION_ID):
            if self.uninstall_failure:
                return self._output("Failure [DELETE_FAILED_INTERNAL_ERROR]\n")
            self.installed = False
            self.uninstalled = True
            return self._output("Success\n")
        self.fixture.fail(f"unexpected command: {argv!r}")

    @staticmethod
    def _output(stdout: str, stderr: str = "") -> GATE.CommandOutput:
        return GATE.CommandOutput(stdout, stderr)


class AndroidSherpaTargetGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.challenge = b"C" * 32
        self.adb = self.root / "adb"
        self.adb.write_bytes(b"#!/bin/sh\nexit 1\n")
        self.adb.chmod(0o700)
        self.apkanalyzer = self.root / "apkanalyzer"
        self.apkanalyzer.write_bytes(b"#!/bin/sh\nexit 1\n")
        self.apkanalyzer.chmod(0o700)
        self.ort_bytes = b"synthetic packaged ORT bytes\n"
        self.final_apk = self.root / "app-release.apk"
        with zipfile.ZipFile(self.final_apk, "w") as archive:
            archive.writestr(GATE.ORT_ARCHIVE_PATH, self.ort_bytes)

        self.pubspec_lock = self._file("pubspec.lock", b"synthetic lock\n")
        self.fixture_paths = {
            "fonix_model": self._file("fonix.onnx", b"fonix model\n"),
            "fonix_input": self._file("fonix-input.bin", b"fonix input\n"),
            "fonix_reference_output": self._file(
                "fonix-reference.f32", b"\x00\x00\x80?"
            ),
            "fonix_cancellation_model": self.root / "fonix.onnx",
            "fonix_cancellation_input": self._file(
                "fonix-cancellation-input.bin", b"fonix cancellation input\n"
            ),
            "sherpa_model": self._file("silero-vad.onnx", b"sherpa model\n"),
            "sherpa_audio": self._file("speech.wav", b"synthetic wav\n"),
            "sherpa_reference": self._file(
                "sherpa-reference.json", b'{"schemaVersion":1}\n'
            ),
        }
        self.runtime_asset_contents = {
            "fonix_cancellation_input.bin": self.fixture_paths[
                "fonix_cancellation_input"
            ].read_bytes(),
            "fonix_dynamic_matmul_chain.onnx": self.fixture_paths[
                "fonix_model"
            ].read_bytes(),
            "fonix_fixture_manifest.json": b'{"schemaVersion":1}\n',
            "fonix_reference_input.bin": self.fixture_paths[
                "fonix_input"
            ].read_bytes(),
            "fonix_reference_output.bin": self.fixture_paths[
                "fonix_reference_output"
            ].read_bytes(),
            "sherpa_synthetic_speech.wav": self.fixture_paths[
                "sherpa_audio"
            ].read_bytes(),
            "sherpa_vad_reference.json": self.fixture_paths[
                "sherpa_reference"
            ].read_bytes(),
            GATE.staged_build_gate.SHERPA_MODEL_NAME: self.fixture_paths[
                "sherpa_model"
            ].read_bytes(),
        }
        with zipfile.ZipFile(self.final_apk, "a") as archive:
            for name, contents in self.runtime_asset_contents.items():
                archive.writestr(
                    f"{GATE.staged_build_gate.APK_RUNTIME_ASSET_PREFIX}{name}",
                    contents,
                )
        self.fixtures = {
            receipt_key: _sha256(self.fixture_paths[argument_name])
            for argument_name, receipt_key, _maximum in GATE.FIXTURE_ARGUMENTS
        }
        self.runtime = {
            "runtimeOwner": "sherpa",
            "runtimeSource": "process",
            "ortVersion": "1.27.1",
            "requiredOrtApi": 27,
            "negotiatedOrtApi": 27,
            "ortSha256": hashlib.sha256(self.ort_bytes).hexdigest(),
            "shimAbi": 1,
            "shimBuildId": "android-owner-sherpa-source-process",
        }
        self.profile = {
            "id": "silero-vad-load-order-v1",
            **GATE.EXPECTED_PROFILE_VALUES,
        }
        self.harness_contract = self.root / "harness-contract.json"
        _write_json(
            self.harness_contract,
            {
                "schemaVersion": 1,
                "applicationId": GATE.APPLICATION_ID,
                "finalApkSha256": _sha256(self.final_apk),
                "pubspecLockSha256": _sha256(self.pubspec_lock),
                "profileId": self.profile["id"],
                "requestedCycles": 2,
                "runtime": self.runtime,
                "fixtures": self.fixtures,
                "cancellationModes": {
                    "fonix": "active-native-termination",
                    "sherpa": "between-bounded-frames",
                },
            },
        )
        self.static_manifest = self.root / "static-package-manifest.json"
        _write_json(
            self.static_manifest,
            {
                "schemaVersion": 1,
                "result": "passed",
                "claimStatus": "static-package-only",
                "snapshotDate": GATE.STATIC_SNAPSHOT_DATE,
                "tools": {},
                "sherpaOnnx": {
                    "source": GATE.SHERPA_SOURCE,
                    "revision": GATE.SHERPA_REVISION,
                    "provenanceBinding": "caller-declared; enclosing-gate-required",
                    "libraryProfile": "flutter-ffi",
                    "artifacts": [],
                },
                "android": {
                    "integrationMode": "sherpa-owned",
                    "buildType": "release-minified",
                    "buildTypeBinding": "caller-declared; enclosing-gate-required",
                    "abis": ["arm64-v8a"],
                    "ortOwner": "sherpa",
                    "ortApiRequired": 27,
                    "artifacts": {
                        "finalApk": {
                            "fileName": self.final_apk.name,
                            "kind": "apk",
                            "sizeBytes": self.final_apk.stat().st_size,
                            "sha256": _sha256(self.final_apk),
                            "digestScope": "archive-bytes-v1",
                        },
                        "finalAab": {
                            "fileName": "app-release.aab",
                            "kind": "aab",
                            "sizeBytes": len(b"synthetic release aab\n"),
                            "sha256": hashlib.sha256(
                                b"synthetic release aab\n"
                            ).hexdigest(),
                            "digestScope": "archive-bytes-v1",
                        },
                    },
                },
                "claimBoundary": "static fixture",
            },
        )
        self.static_gate_report = self.root / "static-gate-report.json"
        staged_gate = GATE.staged_build_gate
        hosted_packages = []
        for name in sorted(staged_gate.SHERPA_HOSTED_PACKAGE_PINS):
            pin = staged_gate.SHERPA_HOSTED_PACKAGE_PINS[name]
            hosted_packages.append(
                {
                    "name": name,
                    "version": GATE.SHERPA_VERSION,
                    "archiveSha256": pin.archive_sha256,
                    "directoryCount": pin.tree.directory_count,
                    "fileCount": pin.tree.file_count,
                    "byteCount": pin.tree.byte_count,
                    "treeSha256": pin.tree.sha256,
                }
            )
        static_manifest_record = json.loads(
            self.static_manifest.read_text(encoding="utf-8")
        )
        _write_json(
            self.static_gate_report,
            {
                "schemaVersion": 1,
                "result": "passed",
                "mode": "runtime-provisioned",
                "abi": GATE.ABI,
                "buildType": GATE.BUILD_TYPE,
                "sourceManifestSha256": _sha256(
                    REPOSITORY / staged_gate.MANIFEST
                ),
                "sourceCopy": {"fileCount": 25, "byteCount": 4096},
                "stagedSource": {
                    "manifestSchema": 1,
                    "excludedGeneratedPaths": sorted(
                        staged_gate.GENERATED_EXCLUSIONS
                    )
                    + ["android/**/*.iml"],
                    "hostTest": {
                        "entryCount": 30,
                        "fileCount": 25,
                        "byteCount": 5000,
                        "sha256": "1" * 64,
                    },
                    "androidBuild": {
                        "entryCount": 38,
                        "fileCount": 33,
                        "byteCount": 9000,
                        "sha256": "2" * 64,
                    },
                },
                "flutterRevision": staged_gate.VALIDATED_FLUTTER_REVISION,
                "flutterVersion": "3.47.0-0.1.pre",
                "android": {
                    "compileApi": staged_gate.ANDROID_COMPILE_API,
                    "buildToolsVersion": staged_gate.ANDROID_BUILD_TOOLS_VERSION,
                    "ndkVersion": staged_gate.ANDROID_NDK_VERSION,
                },
                "java": {
                    "version": staged_gate.SUPPORTED_JAVA_VERSION,
                    "vendor": "Test OpenJDK Vendor",
                },
                "pubspecLock": {
                    "committedSha256": "3" * 64,
                    "stagedSha256": _sha256(self.pubspec_lock),
                },
                "gradleDependencyVerification": {
                    "mode": GATE.GRADLE_VERIFICATION_MODE,
                    "sizeBytes": 4096,
                    "sha256": "6" * 64,
                },
                "sherpaOnnx": {
                    "source": GATE.SHERPA_SOURCE,
                    "revision": GATE.SHERPA_REVISION,
                    "version": GATE.SHERPA_VERSION,
                    "package": staged_gate.SHERPA_PACKAGE,
                    "libraryProfile": GATE.SHERPA_LIBRARY_PROFILE,
                    "hostedPackageGuard": {
                        "threatModel": "non-hostile-local-build",
                        "treeSchema": 1,
                        "androidPluginPackages": sorted(
                            staged_gate.SHERPA_ANDROID_PACKAGES
                        ),
                        "packages": hosted_packages,
                    },
                    "nativeInputs": [
                        {
                            "abi": GATE.ABI,
                            "fileName": name,
                            "sizeBytes": index + 100,
                            "sha256": staged_gate.SHERPA_NATIVE_SHA256[name],
                        }
                        for index, name in enumerate(
                            sorted(staged_gate.SHERPA_NATIVE_SHA256)
                        )
                    ],
                },
                "rawFonixShim": {
                    "sizeBytes": 1234,
                    "sha256": "4" * 64,
                },
                "releaseApk": {
                    "sizeBytes": self.final_apk.stat().st_size,
                    "sha256": _sha256(self.final_apk),
                },
                "releaseAab": {
                    "sizeBytes": len(b"synthetic release aab\n"),
                    "sha256": hashlib.sha256(
                        b"synthetic release aab\n"
                    ).hexdigest(),
                },
                "rawNativeAudit": {
                    "schema": 4,
                    "policy": "sherpa-audit",
                    "sizeBytes": 2000,
                    "sha256": "5" * 64,
                },
                "staticPackageManifest": {
                    "sizeBytes": self.static_manifest.stat().st_size,
                    "sha256": _sha256(self.static_manifest),
                    "record": static_manifest_record,
                },
                "targetEvidence": None,
                "claimBoundary": "runtime-provisioned static gate fixture",
                "runtimeFixtures": [
                    {
                        "fileName": name,
                        "sizeBytes": len(self.runtime_asset_contents[name]),
                        "sha256": hashlib.sha256(
                            self.runtime_asset_contents[name]
                        ).hexdigest(),
                    }
                    for name in staged_gate.RUNTIME_FIXTURE_NAMES
                ],
            },
        )
        self.payload = {
            "schemaVersion": 1,
            "result": "passed",
            "launchChallengeSha256": hashlib.sha256(self.challenge).hexdigest(),
            "loadOrder": "dart-first",
            "runtime": {
                key: value
                for key, value in self.runtime.items()
                if key != "ortSha256"
            },
            "sherpa": {
                "getVersion": GATE.SHERPA_VERSION,
                "getGitSha1": GATE.SHERPA_REVISION[:12],
                "profile": self.profile,
            },
            "fixtures": self.fixtures,
            "initialization": {},
            "workload": {},
            "lifecycle": {},
        }
        self.completion_log = self._completion_log(self.payload)
        self.installed_path = (
            f"/data/app/~~abc123/{GATE.APPLICATION_ID}-def456/base.apk"
        )

    def _file(self, name: str, contents: bytes) -> Path:
        path = self.root / name
        path.write_bytes(contents)
        return path

    def _arguments(self, capture_name: str = "capture") -> argparse.Namespace:
        return argparse.Namespace(
            adb=self.adb,
            apkanalyzer=self.apkanalyzer,
            serial="serial-1",
            final_apk=self.final_apk,
            static_gate_report=self.static_gate_report,
            static_package_manifest=self.static_manifest,
            load_order="dart-first",
            harness_contract=self.harness_contract,
            pubspec_lock=self.pubspec_lock,
            capture_directory=self.root / capture_name,
            **self.fixture_paths,
        )

    @staticmethod
    def _completion_log(payload: object, *, status: str = "passed") -> str:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
        chunks = [
            raw[index : index + GATE.MAX_LOG_CHUNK_CHARACTERS]
            for index in range(0, len(raw), GATE.MAX_LOG_CHUNK_CHARACTERS)
        ]
        prefix = "08-07 12:34:56.789 10123 4321 4321 I FonixSherpaRef: "
        messages = [
            f"{GATE.BEGIN_MARKER}|1|{status}|{digest}|{len(raw)}|{len(chunks)}",
            *[
                f"{GATE.CHUNK_MARKER}|{digest}|{index}|{chunk}"
                for index, chunk in enumerate(chunks)
            ],
            f"{GATE.END_MARKER}|{digest}",
        ]
        return "--------- beginning of main\n" + "".join(
            f"{prefix}{message}\n" for message in messages
        )

    def test_runs_one_exact_target_and_retains_bound_capture(self) -> None:
        fake = FakeTargetRunner(self)
        manifest = GATE.run_target_gate(
            self._arguments(),
            command_runner=fake,
            challenge_factory=lambda count: self.challenge,
        )

        self.assertTrue(fake.uninstalled)
        self.assertFalse(fake.installed)
        self.assertEqual(manifest["claimStatus"], "trusted-adb-capture")
        self.assertEqual(manifest["matrix"]["pageSizeBytes"], 16384)
        self.assertEqual(manifest["device"]["modelToken"], "Pixel-Test-Device")
        capture = self.root / "capture"
        for name in (
            "launch-challenge.bin",
            "raw-logcat.txt",
            "target-evidence.json",
            "logcat-evidence.json",
            "load-order-receipt.json",
            "validation-record.json",
            "command-transcript.json",
            "capture-manifest.json",
        ):
            self.assertTrue((capture / name).is_file(), name)
        receipt = json.loads(
            (capture / "load-order-receipt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["schemaVersion"], 2)
        self.assertEqual(receipt["runtime"]["ortSha256"], self.runtime["ortSha256"])
        self.assertEqual(receipt["process"]["pid"]["beforeLaunch"], None)
        self.assertEqual(receipt["process"]["pid"]["afterForceStop"], None)
        self.assertEqual(
            manifest["inputBindings"]["stagedBuildGateReport"]["sha256"],
            _sha256(self.static_gate_report),
        )

        launch = next(
            command
            for command in fake.commands
            if "reference" not in command and "am" in command and "start" in command
        )
        self.assertEqual(launch.count("--es"), 2)
        self.assertIn(GATE.LOAD_ORDER_EXTRA, launch)
        self.assertIn(GATE.LAUNCH_CHALLENGE_EXTRA, launch)

        transcript_text = (capture / "command-transcript.json").read_text(
            encoding="utf-8"
        )
        transcript = json.loads(transcript_text)
        self.assertEqual(
            transcript["redactionPolicy"],
            "publishable-command-metadata-v1",
        )
        for private_value in (
            "serial-1",
            "Pixel Test Device",
            "vendor/product/device:15/build/test-keys",
            base64.b64encode(self.challenge).decode("ascii"),
            str(self.root),
            self.installed_path,
        ):
            self.assertNotIn(private_value, transcript_text)
        for command in transcript["commands"]:
            self.assertEqual(set(command["stdout"]), {"sizeBytes", "sha256"})
            self.assertEqual(set(command["stderr"]), {"sizeBytes", "sha256"})

    def test_pid_query_survives_real_adb_shell_argv_join_semantics(self) -> None:
        fake = FakeTargetRunner(self)
        runner = GATE.CapturingRunner(fake)

        self.assertIsNone(
            GATE._query_application_pid(runner, self.adb, "serial-1")
        )

        command = fake.commands[-1]
        shell_index = command.index("shell")
        self.assertEqual(
            command[shell_index:],
            (
                "shell",
                f"pidof {GATE.APPLICATION_ID} 2>/dev/null || true",
            ),
        )
        self.assertEqual(
            " ".join(command[shell_index + 1 :]),
            f"pidof {GATE.APPLICATION_ID} 2>/dev/null || true",
        )
        self.assertNotIn("sh -c", " ".join(command))

    def test_accepts_exact_adb_push_progress_on_stderr(self) -> None:
        fake = FakeTargetRunner(self)
        fake.install_stderr = (
            f"{self.final_apk.resolve()}: 1 file pushed, 0 skipped. "
            f"696.8 MB/s ({self.final_apk.stat().st_size} bytes in 0.062s)\n"
        )

        manifest = GATE.run_target_gate(
            self._arguments("split-install-output-capture"),
            command_runner=fake,
            challenge_factory=lambda count: self.challenge,
        )

        self.assertEqual(manifest["result"], "passed")
        self.assertTrue(fake.uninstalled)
        self.assertFalse(fake.installed)

    def test_rejects_unclosed_install_stderr_and_still_uninstalls(self) -> None:
        fake = FakeTargetRunner(self)
        fake.install_stderr = "unexpected adb warning\n"

        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "stderr is outside the closed adb push receipt",
        ):
            GATE.run_target_gate(
                self._arguments("bad-install-stderr-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )

        self.assertTrue(fake.uninstalled)
        self.assertFalse(fake.installed)

    def test_rejects_foreign_apk_application_id_before_install(self) -> None:
        fake = FakeTargetRunner(self)
        fake.application_id = "dev.example.foreign"
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "application ID is outside the dedicated harness",
        ):
            GATE.run_target_gate(
                self._arguments("foreign-id-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertFalse(fake.installed)
        self.assertFalse(fake.uninstalled)
        self.assertFalse(any("install" in command for command in fake.commands))

    def test_requires_runtime_provisioned_enclosing_static_gate(self) -> None:
        report = json.loads(
            self.static_gate_report.read_text(encoding="utf-8")
        )
        report["mode"] = "static-template"
        _write_json(self.static_gate_report, report)
        fake = FakeTargetRunner(self)
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "does not describe the exact runtime build",
        ):
            GATE.run_target_gate(
                self._arguments("weak-static-report-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertFalse(fake.commands)

    def test_accepts_closed_gradle_dependency_verification_record(self) -> None:
        report = json.loads(
            self.static_gate_report.read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["gradleDependencyVerification"],
            {
                "mode": "strict",
                "sizeBytes": 4096,
                "sha256": "6" * 64,
            },
        )

        fake = FakeTargetRunner(self)
        manifest = GATE.run_target_gate(
            self._arguments("gradle-verification-capture"),
            command_runner=fake,
            challenge_factory=lambda count: self.challenge,
        )

        self.assertEqual(manifest["result"], "passed")

    def test_rejects_gradle_dependency_verification_tamper(self) -> None:
        baseline = json.loads(
            self.static_gate_report.read_text(encoding="utf-8")
        )
        mutations = (
            (
                "mode",
                lambda record: record.__setitem__("mode", "lenient"),
                "mode is not strict",
            ),
            (
                "empty-size",
                lambda record: record.__setitem__("sizeBytes", 0),
                "sizeBytes must be an integer in range",
            ),
            (
                "oversized",
                lambda record: record.__setitem__(
                    "sizeBytes",
                    GATE.MAX_GRADLE_VERIFICATION_METADATA_BYTES + 1,
                ),
                "sizeBytes must be an integer in range",
            ),
            (
                "digest",
                lambda record: record.__setitem__("sha256", "A" * 64),
                "sha256 must be a lowercase SHA-256",
            ),
            (
                "extra-field",
                lambda record: record.__setitem__("unexpected", True),
                "has an unexpected field set",
            ),
            (
                "missing-field",
                lambda record: record.pop("mode"),
                "has an unexpected field set",
            ),
        )

        for index, (name, mutate, expected_error) in enumerate(mutations):
            with self.subTest(name=name):
                report = copy.deepcopy(baseline)
                record = report["gradleDependencyVerification"]
                self.assertIsInstance(record, dict)
                mutate(record)
                _write_json(self.static_gate_report, report)
                fake = FakeTargetRunner(self)

                with self.assertRaisesRegex(
                    GATE.AndroidSherpaTargetGateError,
                    expected_error,
                ):
                    GATE.run_target_gate(
                        self._arguments(f"gradle-tamper-{index}"),
                        command_runner=fake,
                        challenge_factory=lambda count: self.challenge,
                    )

                self.assertFalse(fake.commands)

    def test_enclosing_static_gate_must_bind_every_runtime_fixture(self) -> None:
        report = json.loads(
            self.static_gate_report.read_text(encoding="utf-8")
        )
        report["runtimeFixtures"][0]["sha256"] = "0" * 64
        _write_json(self.static_gate_report, report)
        fake = FakeTargetRunner(self)
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "differs from the exact APK runtime fixtures",
        ):
            GATE.run_target_gate(
                self._arguments("fixture-binding-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertFalse(fake.commands)

    def test_rejects_device_fact_change_at_postflight(self) -> None:
        fake = FakeTargetRunner(self)
        fake.postflight_fingerprint = (
            "other/product/device:15/build/test-keys"
        )
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "device facts changed during the target run",
        ):
            GATE.run_target_gate(
                self._arguments("changed-device-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertTrue(fake.uninstalled)
        self.assertFalse(fake.installed)

    def test_device_swap_before_cleanup_refuses_to_touch_replacement(self) -> None:
        fake = FakeTargetRunner(self)
        fake.fingerprint_change_query = 2
        fake.postflight_fingerprint = (
            "replacement/product/device:15/build/test-keys"
        )
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "changed before cleanup; refusing to modify the replacement target",
        ):
            GATE.run_target_gate(
                self._arguments("swapped-before-cleanup-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertTrue(fake.installed)
        self.assertFalse(fake.uninstalled)

    def test_rejects_duplicate_envelope_and_still_uninstalls(self) -> None:
        fake = FakeTargetRunner(self)
        fake.logcat = self.completion_log + self.completion_log
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError, "more than one framed envelope"
        ):
            GATE.run_target_gate(
                self._arguments("duplicate-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertTrue(fake.uninstalled)
        self.assertFalse(fake.installed)

    def test_rejects_non_passing_target_status_and_still_uninstalls(self) -> None:
        fake = FakeTargetRunner(self)
        fake.logcat = self._completion_log(self.payload, status="unavailable")
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError, "non-passing status"
        ):
            GATE.run_target_gate(
                self._arguments("unavailable-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertTrue(fake.uninstalled)

    def test_refuses_to_replace_or_delete_a_preexisting_harness(self) -> None:
        fake = FakeTargetRunner(self)
        fake.installed = True
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError, "must be absent before the run"
        ):
            GATE.run_target_gate(
                self._arguments("preinstalled-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertTrue(fake.installed)
        self.assertFalse(fake.uninstalled)
        self.assertFalse(any(command[3:4] == ("install",) for command in fake.commands))

    def test_reports_primary_and_cleanup_failures_together(self) -> None:
        fake = FakeTargetRunner(self)
        fake.logcat = self.completion_log + self.completion_log
        fake.uninstall_failure = True
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError,
            "target run failed: .*more than one framed envelope; "
            "cleanup also failed: .*uninstall",
        ):
            GATE.run_target_gate(
                self._arguments("dual-failure-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )

    def test_app_cannot_supply_host_owned_runtime_hash(self) -> None:
        payload = dict(self.payload)
        payload["runtime"] = {**payload["runtime"], "ortSha256": "0" * 64}
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError, "app runtime.*unexpected field"
        ):
            GATE._validate_app_payload(
                payload,
                load_order="dart-first",
                challenge_sha256=hashlib.sha256(self.challenge).hexdigest(),
                harness_contract=json.loads(
                    self.harness_contract.read_text(encoding="utf-8")
                ),
            )

    def test_completion_rejects_out_of_order_chunk(self) -> None:
        raw = json.dumps(self.payload, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
        prefix = "08-07 12:34:56.789 10123 4321 4321 I FonixSherpaRef: "
        log = (
            f"{prefix}{GATE.BEGIN_MARKER}|1|passed|{digest}|{len(raw)}|1\n"
            f"{prefix}{GATE.CHUNK_MARKER}|{digest}|1|{raw}\n"
        )
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError, "out of order"
        ):
            GATE._parse_completion_log(log, expected_uid=10123, expected_pid=4321)

    def test_static_manifest_must_bind_exact_apk(self) -> None:
        manifest = json.loads(self.static_manifest.read_text(encoding="utf-8"))
        manifest["android"]["artifacts"]["finalApk"]["sha256"] = "0" * 64
        _write_json(self.static_manifest, manifest)
        fake = FakeTargetRunner(self)
        with self.assertRaisesRegex(
            GATE.AndroidSherpaTargetGateError, "not bound to the supplied exact APK"
        ):
            GATE.run_target_gate(
                self._arguments("bad-manifest-capture"),
                command_runner=fake,
                challenge_factory=lambda count: self.challenge,
            )
        self.assertFalse(fake.commands)


if __name__ == "__main__":
    unittest.main()
