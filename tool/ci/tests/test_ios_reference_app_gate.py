from __future__ import annotations

import ast
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "run_ios_reference_app_gate.py"
SPEC = importlib.util.spec_from_file_location("run_ios_reference_app_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_ios_reference_app_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_ios_reference_app_gate)


def _artifact(variant: str) -> dict[str, object]:
    gate = run_ios_reference_app_gate
    return {
        "id": gate.ARTIFACT_IDS[variant],
        "target": {
            "os": "ios",
            "architecture": "arm64",
            "variant": variant,
            "min_os": gate.APPLICATION_MINIMUM_OS,
        },
        "flavor": "cpu",
        "runtime_mode": "linked",
        "source": {
            "url": (
                "https://api.nuget.org/v3-flatcontainer/"
                "microsoft.ml.onnxruntime/1.27.1/"
                "microsoft.ml.onnxruntime.1.27.1.nupkg"
            ),
            "sha256": gate.ARCHIVE_SHA256,
            "size_bytes": gate.ARCHIVE_SIZE_BYTES,
            "archive": "zip",
        },
    }


def _archive(variant: str) -> object:
    gate = run_ios_reference_app_gate
    return gate.PinnedArchive(
        variant=variant,
        artifact_id=gate.ARTIFACT_IDS[variant],
        basename=gate.ARCHIVE_BASENAME,
        sha256=gate.ARCHIVE_SHA256,
        size_bytes=gate.ARCHIVE_SIZE_BYTES,
    )


class IosReferenceBoundedProcessTest(unittest.TestCase):
    def test_successful_command_delegates_exact_posix_bounds(self) -> None:
        gate = run_ios_reference_app_gate
        command = ("tool", "argument")
        cwd = Path("/tmp/fonix-ios-bounded-command")
        environment = {"PATH": "/usr/bin", "LANG": "C"}
        output = gate._BOUNDED_PROCESS.CommandOutput(
            stdout="result\n", stderr="warning\n"
        )
        with mock.patch.object(
            gate._BOUNDED_PROCESS, "run_bounded", return_value=output
        ) as bounded:
            result = gate._run(
                command,
                cwd=cwd,
                environment=environment,
                operation="iOS bounded command contract",
                timeout_seconds=37,
                maximum_output=1234,
            )

        self.assertEqual(result.stdout, "result\n")
        self.assertEqual(result.stderr, "warning\n")
        bounded.assert_called_once_with(
            command,
            operation="iOS bounded command contract",
            cwd=cwd,
            environment=environment,
            timeout_seconds=37,
            maximum_stdout_bytes=1234,
            maximum_stderr_bytes=1234,
        )

    def test_bounded_failure_preserves_diagnostics_and_cause(self) -> None:
        gate = run_ios_reference_app_gate
        failure = gate._BOUNDED_PROCESS.BoundedProcessTimeoutError(
            "iOS timeout fixture",
            3,
            stdout="bounded stdout",
            stderr="bounded stderr",
        )
        with mock.patch.object(
            gate._BOUNDED_PROCESS, "run_bounded", side_effect=failure
        ):
            with self.assertRaises(gate.IosReferenceAppGateError) as context:
                gate._run(
                    ("tool",),
                    operation="iOS timeout fixture",
                    timeout_seconds=3,
                    maximum_output=1024,
                )

        self.assertIs(context.exception.__cause__, failure)
        self.assertIn("timed out after 3 seconds", str(context.exception))
        self.assertIn("bounded stdout", str(context.exception))
        self.assertIn("bounded stderr", str(context.exception))

    def test_default_runners_remove_simulator_child_injection(self) -> None:
        gate = run_ios_reference_app_gate
        output = gate._BOUNDED_PROCESS.CommandOutput(stdout="", stderr="")
        injected = {
            "SIMCTL_CHILD_DYLD_INSERT_LIBRARIES": "/tmp/injected.dylib",
            "SIMCTL_CHILD_OTHER": "untrusted",
            "FONIX_REFERENCE_SMOKE": "host",
            "FONIX_REFERENCE_CHALLENGE": "host",
            "PATH": "/usr/bin:/bin",
        }
        with (
            mock.patch.dict(gate.os.environ, injected, clear=True),
            mock.patch.object(
                gate._BOUNDED_PROCESS,
                "run_bounded",
                return_value=output,
            ) as bounded,
        ):
            gate._run(
                ("xcrun", "simctl", "spawn", "udid", "/usr/bin/arch"),
                operation="simulator architecture probe",
                timeout_seconds=3,
                maximum_output=1024,
            )
            gate._run_status(
                ("xcrun", "simctl", "spawn", "udid", "/bin/ps"),
                operation="simulator process settlement probe",
                timeout_seconds=3,
                maximum_output=1024,
            )

        self.assertEqual(bounded.call_count, 2)
        for call in bounded.call_args_list:
            environment = call.kwargs["environment"]
            self.assertEqual(environment["PATH"], "/usr/bin:/bin")
            self.assertEqual(environment["LC_ALL"], "C")
            self.assertFalse(
                any(key.startswith("SIMCTL_CHILD_") for key in environment)
            )
            self.assertNotIn("FONIX_REFERENCE_SMOKE", environment)
            self.assertNotIn("FONIX_REFERENCE_CHALLENGE", environment)

    def test_status_runner_accepts_only_clean_nonzero_direct_exit(self) -> None:
        gate = run_ios_reference_app_gate
        failure = gate._BOUNDED_PROCESS.BoundedProcessExitError(
            "simulator process probe fixture",
            return_code=1,
            residual_group_members=False,
            stdout="",
            stderr="",
        )
        with mock.patch.object(
            gate._BOUNDED_PROCESS, "run_bounded", side_effect=failure
        ) as bounded:
            status = gate._run_status(
                ("probe",),
                operation="simulator process probe fixture",
                timeout_seconds=7,
                maximum_output=321,
            )

        self.assertEqual(gate.CommandStatus(1, "", ""), status)
        call = bounded.call_args
        self.assertEqual(call.args, (("probe",),))
        self.assertEqual(call.kwargs["timeout_seconds"], 7)
        self.assertEqual(call.kwargs["maximum_stdout_bytes"], 321)
        self.assertEqual(call.kwargs["maximum_stderr_bytes"], 321)

        residual = gate._BOUNDED_PROCESS.BoundedProcessExitError(
            "simulator process probe fixture",
            return_code=1,
            residual_group_members=True,
            stdout="",
            stderr="",
        )
        with mock.patch.object(
            gate._BOUNDED_PROCESS, "run_bounded", side_effect=residual
        ):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "left members"
            ):
                gate._run_status(
                    ("probe",),
                    operation="simulator process probe fixture",
                    timeout_seconds=7,
                    maximum_output=321,
                )

    def test_plist_input_uses_private_file_and_removes_it(self) -> None:
        gate = run_ios_reference_app_gate
        payload = b"{ value = 1; }\n"
        observed_path: Path | None = None

        def run(command: object, **kwargs: object) -> object:
            nonlocal observed_path
            values = tuple(command)  # type: ignore[arg-type]
            observed_path = Path(values[-1])
            self.assertEqual(
                values[:-1],
                ("/usr/bin/plutil", "-convert", "json", "-o", "-", "--"),
            )
            self.assertTrue(observed_path.is_file())
            self.assertEqual(observed_path.read_bytes(), payload)
            self.assertEqual(kwargs["timeout_seconds"], 11)
            self.assertEqual(kwargs["maximum_output"], 99)
            self.assertEqual(kwargs["operation"], "plist conversion fixture")
            return gate._COMMON.CommandOutput(stdout="{}", stderr="")

        with mock.patch.object(gate, "_run", side_effect=run) as bounded:
            output = gate._run_with_input(
                ("/usr/bin/plutil", "-convert", "json", "-o", "-", "--", "-"),
                input_bytes=payload,
                maximum_input=99,
                maximum_output=99,
                timeout_seconds=11,
                operation="plist conversion fixture",
            )

        self.assertEqual(output.stdout, "{}")
        bounded.assert_called_once()
        self.assertIsNotNone(observed_path)
        assert observed_path is not None
        self.assertFalse(observed_path.exists())

    def test_every_local_command_call_declares_deadline_and_stream_cap(self) -> None:
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        missing: list[tuple[str, int, set[str]]] = []
        required = {
            "_run": {"operation", "timeout_seconds", "maximum_output"},
            "_run_status": {"operation", "timeout_seconds", "maximum_output"},
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            expected = required.get(node.func.id)
            if expected is None:
                continue
            supplied = {keyword.arg for keyword in node.keywords if keyword.arg}
            absent = expected - supplied
            if absent:
                missing.append((node.func.id, node.lineno, absent))
        self.assertEqual([], missing)

    def test_final_app_audit_is_in_process_without_nested_runner(self) -> None:
        gate = run_ios_reference_app_gate
        with tempfile.TemporaryDirectory(prefix="fonix-ios-audit-call-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            application = root / "Runner.app"
            application.mkdir()
            shim = root / "libfonix_shim.dylib"
            shim.write_bytes(b"shim")
            reference = gate.ReferenceShim(
                path=shim,
                sha256=hashlib.sha256(b"shim").hexdigest(),
                invocation_hash="a" * 10,
            )
            raw_report = {"result": "raw-audit"}
            validated_report = {"result": "validated-audit"}
            bytecode_modes: list[bool] = []

            def run_audit(*_: object, **__: object) -> dict[str, object]:
                bytecode_modes.append(sys.dont_write_bytecode)
                return raw_report

            audit = mock.Mock(side_effect=run_audit)
            audit_module = SimpleNamespace(
                audit_application_and_optional_probe=audit,
                AppleApplicationAuditError=RuntimeError,
            )
            with (
                mock.patch.object(sys, "dont_write_bytecode", False),
                mock.patch.object(
                    gate,
                    "_load_apple_auditor",
                    return_value=audit_module,
                ) as load_audit,
                mock.patch.object(
                    gate, "_sha256_file", return_value=reference.sha256
                ),
                mock.patch.object(
                    gate,
                    "_validate_audit_binding",
                    return_value=validated_report,
                ) as validate,
                mock.patch.object(gate, "_run") as bounded,
            ):
                result = gate._audit_application(
                    repository=repository,
                    application=application,
                    archive=_archive("simulator"),
                    reference_shim=reference,
                )
                restored_bytecode_mode = sys.dont_write_bytecode

        self.assertEqual(result, validated_report)
        self.assertEqual(bytecode_modes, [True])
        self.assertFalse(restored_bytecode_mode)
        load_audit.assert_called_once_with(repository, "simulator")
        audit.assert_called_once_with(
            application.resolve(strict=False),
            "ios-simulator",
            gate.APPLICATION_MINIMUM_OS,
            repository=repository.resolve(strict=False),
            reference_shim=shim,
            signature_policy="strict",
        )
        validate.assert_called_once()
        bounded.assert_not_called()


def _native_inventory_fixture(
    variant: str,
    reference: object,
) -> dict[str, object]:
    gate = run_ios_reference_app_gate
    assert isinstance(reference, gate.ReferenceShim)
    platform_number = 2 if variant == "device" else 7
    dependency = {
        "kind": "load",
        "path": "/usr/lib/libSystem.B.dylib",
        "currentVersion": 1,
        "compatibilityVersion": 1,
    }
    records: list[dict[str, object]] = []
    for path in gate._IOS_MACHO_RELATIVE_PATHS:
        owner = "application"
        for framework in gate._IOS_FRAMEWORK_INVENTORY:
            if path.startswith(framework["path"] + "/"):
                owner = framework["path"]
        dependencies = [copy.deepcopy(dependency)]
        records.append(
            {
                "path": path,
                "owner": owner,
                "architecture": "arm64",
                "machoPlatform": platform_number,
                "minimumOs": gate._IOS_MACHO_MINIMUM_OS[path],
                "cpuSubtype": 0,
                "fileType": gate._IOS_MACHO_FILE_TYPES[path],
                "headerFlags": gate._IOS_MACHO_HEADER_FLAGS[variant][path],
                "codeSignatureLoadCommands": (
                    0 if variant == "device" and path == "Runner" else 1
                ),
                "dynamicDependencies": [dependency["path"]],
                "dylibDependencies": dependencies,
                "dependencyMetadataSha256": gate._canonical_json_sha256(
                    dependencies
                ),
                "loadCommandKindsSha256": hashlib.sha256(
                    f"{variant}:{path}".encode("utf-8")
                ).hexdigest(),
                "dylibId": copy.deepcopy(
                    gate._IOS_MACHO_DYLIB_IDS[variant][path]
                ),
                "rpaths": copy.deepcopy(gate._IOS_MACHO_RPATHS[path]),
            }
        )
    exports_sha256 = "b" * 64
    archive = _archive(variant)
    return {
        "profile": (
            "ios-device-release"
            if variant == "device"
            else "ios-simulator-debug-no-debug-dylib"
        ),
        "frameworks": copy.deepcopy(gate._IOS_FRAMEWORK_INVENTORY),
        "machOBinaries": records,
        "shimExports": {
            "allowlist": "src/fonix_exports.apple",
            "allowlistSha256": hashlib.sha256(b"allowlist").hexdigest(),
            "symbolCount": 8,
            "nlistSymbolSetSha256": exports_sha256,
            "dyldExportSetSha256": exports_sha256,
        },
        "linkedRuntimeIdentity": {
            "runtimeMode": "linked",
            "packagedShim": "Frameworks/fonix_shim.framework/fonix_shim",
            "referenceShim": str(reference.path),
            "hookInvocationMetadata": gate._expected_hook_invocation_metadata(
                reference
            ),
            "normalizedRuntimeFields": "matched",
            "normalizedRuntimeFieldsSha256": (
                gate._IOS_NORMALIZED_RUNTIME_FIELDS_SHA256[variant]
            ),
            "comparisonScope": gate._IOS_NORMALIZED_COMPARISON_SCOPE,
            "accountedTransformations": copy.deepcopy(
                gate._IOS_ACCOUNTED_TRANSFORMATIONS[variant]
            ),
            "embeddedBuildIdentity": {
                "schemaVersion": 3,
                "artifactId": archive.artifact_id,
                "buildId": archive.artifact_id,
                "status": "matched",
            },
            "nlistAndDyldExports": "matched",
            "separatelyPackagedOrtMachOs": [],
            "auditedOrtLoadCommandDependencies": [],
            "runtimeDlopenBehavior": "not-proved",
            "otherMachOStaticOrtCopies": "not-proved",
            "staticArchiveMultiplicity": "not-provable-from-final-bundle",
            "claimBoundary": gate._IOS_REFERENCE_SHIM_CLAIM_BOUNDARY,
        },
    }


class IosReferenceArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-lock-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = self.root / "versions.lock.yaml"

    def _write(self, artifacts: list[dict[str, object]]) -> None:
        self.lock.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")

    def test_selects_exact_device_and_simulator_from_one_archive(self) -> None:
        self._write([_artifact("device"), _artifact("simulator")])

        selected = run_ios_reference_app_gate._load_pinned_archives(self.lock)

        self.assertEqual(set(selected), {"device", "simulator"})
        self.assertEqual(selected["device"].artifact_id, _artifact("device")["id"])
        self.assertEqual(selected["simulator"].sha256, selected["device"].sha256)
        self.assertEqual(
            selected["simulator"].size_bytes,
            run_ios_reference_app_gate.ARCHIVE_SIZE_BYTES,
        )

    def test_rejects_source_hash_tamper(self) -> None:
        device = _artifact("device")
        source = device["source"]
        assert isinstance(source, dict)
        source["sha256"] = "0" * 64
        self._write([device, _artifact("simulator")])

        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "archive identity changed",
        ):
            run_ios_reference_app_gate._load_pinned_archives(self.lock)

    def test_rejects_duplicate_tuple(self) -> None:
        self._write(
            [_artifact("device"), _artifact("device"), _artifact("simulator")]
        )

        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "duplicate iOS arm64 device",
        ):
            run_ios_reference_app_gate._load_pinned_archives(self.lock)

    def test_archive_copy_rehashes_before_publication(self) -> None:
        data = b"locked archive fixture"
        archive = run_ios_reference_app_gate.PinnedArchive(
            variant="device",
            artifact_id=run_ios_reference_app_gate.ARTIFACT_IDS["device"],
            basename="runtime.nupkg",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        cache = self.root / "cache"
        cache.mkdir()
        (cache / archive.basename).write_bytes(data)
        work = self.root / "work"
        work.mkdir()

        destination = run_ios_reference_app_gate._copy_archive(cache, work, archive)

        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual(destination.parent, work / ".fonix-artifact-cache")
        (cache / archive.basename).write_bytes(b"tampered archive bytes")
        second_work = self.root / "second"
        second_work.mkdir()
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "size or SHA-256 differs from lock",
        ):
            run_ios_reference_app_gate._copy_archive(cache, second_work, archive)

    def _prepare_with_synthetic_cache(
        self, variant: str, *, tamper_host: bool
    ) -> tuple[Path, list[str]]:
        gate = run_ios_reference_app_gate
        variant_bytes = f"{variant} archive".encode("ascii")
        host_expected = b"exact macOS host archive"
        host_bytes = b"tampered macOS archive!!" if tamper_host else host_expected
        self.assertEqual(len(host_bytes), len(host_expected))
        archive = gate.PinnedArchive(
            variant=variant,
            artifact_id=gate.ARTIFACT_IDS[variant],
            basename=f"ios-{variant}.zip",
            sha256=hashlib.sha256(variant_bytes).hexdigest(),
            size_bytes=len(variant_bytes),
        )
        host_archive = SimpleNamespace(
            basename="macos-host.tgz",
            sha256=hashlib.sha256(host_expected).hexdigest(),
            size_bytes=len(host_expected),
        )
        cache = self.root / f"cache-{variant}-{tamper_host}"
        cache.mkdir()
        (cache / archive.basename).write_bytes(variant_bytes)
        (cache / host_archive.basename).write_bytes(host_bytes)
        repository = self.root / "repository"
        repository.mkdir(exist_ok=True)
        source = self.root / "source"
        source.mkdir(exist_ok=True)
        destination = self.root / f"work-{variant}-{tamper_host}"
        operations: list[str] = []

        def copy_example(_: Path, target: Path) -> object:
            target.mkdir()
            (target / "pubspec.yaml").write_text("fixture\n", encoding="utf-8")
            return gate._COMMON.CopySummary(file_count=1, byte_count=8)

        def run_command(*_: object, **kwargs: object) -> object:
            operations.append(str(kwargs.get("operation")))
            return gate._COMMON.CommandOutput(stdout="", stderr="")

        patches = (
            mock.patch.object(gate._COMMON, "_copy_example", side_effect=copy_example),
            mock.patch.object(gate._COMMON, "_patch_fonix_path_dependency"),
            mock.patch.object(gate, "_require_ios_source_contract"),
            mock.patch.object(gate, "_run", side_effect=run_command),
            mock.patch.object(gate, "_validate_prepared_assets", return_value="a" * 64),
            mock.patch.object(gate, "_patch_ios_hook_config"),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            gate._prepare_variant(
                repository=repository,
                source_example=source,
                destination=destination,
                flutter=self.root / "flutter",
                dart=self.root / "dart",
                artifact_cache=cache,
                archive=archive,
                host_archive=host_archive,
                run_source_checks=variant == "device",
            )
        return destination, operations

    def test_prepare_copies_host_archive_for_both_ios_variants(self) -> None:
        for variant in ("device", "simulator"):
            with self.subTest(variant=variant):
                destination, operations = self._prepare_with_synthetic_cache(
                    variant, tamper_host=False
                )
                relative_cache = destination / ".fonix-artifact-cache"
                self.assertTrue((relative_cache / f"ios-{variant}.zip").is_file())
                self.assertTrue((relative_cache / "macos-host.tgz").is_file())
                self.assertIn(
                    f"Fonix iOS {variant} asset preparation", operations
                )

    def test_prepare_rejects_host_archive_tamper_for_both_variants(self) -> None:
        for variant in ("device", "simulator"):
            with self.subTest(variant=variant):
                with self.assertRaisesRegex(
                    run_ios_reference_app_gate.IosReferenceAppGateError,
                    "selected macOS host archive size or SHA-256 differs from lock",
                ):
                    self._prepare_with_synthetic_cache(variant, tamper_host=True)


class IosReferenceSourcePreparationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-source-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "fonix"
        self.repository.mkdir()

    def _write_ios_source_contract(self, work: Path) -> tuple[Path, Path, Path]:
        project = work / "ios/Runner.xcodeproj/project.pbxproj"
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text(
            "\n".join(
                [
                    "IPHONEOS_DEPLOYMENT_TARGET = 15.1;",
                    "IPHONEOS_DEPLOYMENT_TARGET = 15.1;",
                    "IPHONEOS_DEPLOYMENT_TARGET = 15.1;",
                    "PRODUCT_BUNDLE_IDENTIFIER = dev.fonix.fonixReference;",
                ]
            ),
            encoding="utf-8",
        )
        delegate = work / "ios/Runner/AppDelegate.swift"
        delegate.parent.mkdir(parents=True, exist_ok=True)
        delegate.write_text(
            "\n".join(
                [
                    "FlutterImplicitEngineDelegate",
                    'private static let referenceLaunchChannelName = '
                    '"dev.fonix.reference/launch"',
                    'private static let referenceLaunchMethod = '
                    '"readSmokeActivation"',
                    'private static let referenceSmokeKey = '
                    '"FONIX_REFERENCE_SMOKE"',
                    'private static let referenceChallengeKey = '
                    '"FONIX_REFERENCE_CHALLENGE"',
                    "private lazy var referenceLaunchActivation = "
                    "Self.readReferenceLaunchActivation()",
                    "didInitializeImplicitFlutterEngine",
                    "engineBridge.applicationRegistrar.messenger()",
                    "guard call.method == Self.referenceLaunchMethod else",
                    "result(FlutterMethodNotImplemented)",
                    "guard call.arguments == nil else",
                    "switch self.referenceLaunchActivation",
                    "ProcessInfo.processInfo.environment",
                    "if smoke == nil && challenge == nil",
                    'smoke == "1"',
                    "Self.isLowercaseHexChallenge(challenge)",
                    "bytes.count == 64",
                    'result(["schemaVersion": 1, "challenge": challenge])',
                ]
            ),
            encoding="utf-8",
        )
        main = work / "lib/main.dart"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_text(
            "\n".join(
                [
                    "if (Platform.isIOS)",
                    "challenge = await readIosResidentReferenceChallenge()",
                    "_runResidentPackagedSmoke(challenge, pid).catchError",
                    "residentReferenceActivationFailureDiagnostic",
                    "residentReferencePublicationFailureDiagnostic",
                    "if (Platform.isMacOS &&",
                    "Platform.environment[_smokeEnvironmentKey] == '1'",
                ]
            ),
            encoding="utf-8",
        )
        return project, delegate, main

    def test_pubspec_patch_changes_only_runtime_floor_and_repository_path(self) -> None:
        pubspec = self.root / "pubspec.yaml"
        pubspec.write_text(
            "name: fonix_reference\n"
            "dependencies:\n"
            "  fonix:\n"
            "    path: ..\n"
            "hooks:\n"
            "  user_defines:\n"
            "    fonix:\n"
            "      runtime_mode: bundled\n"
            "      artifact_cache: .fonix-artifact-cache\n"
            "      application_minimum_os: '14.0'\n",
            encoding="utf-8",
        )

        run_ios_reference_app_gate._COMMON._patch_fonix_path_dependency(
            pubspec, self.repository
        )
        run_ios_reference_app_gate._patch_ios_hook_config(pubspec)

        result = pubspec.read_text(encoding="utf-8")
        self.assertIn(f"    path: {json.dumps(self.repository.as_posix())}\n", result)
        self.assertIn("      runtime_mode: linked\n", result)
        self.assertIn("      application_minimum_os: '15.1'\n", result)
        self.assertNotIn("runtime_mode: bundled", result)
        self.assertNotIn("application_minimum_os: '14.0'", result)

    def test_pubspec_patch_rejects_already_broadened_floor(self) -> None:
        pubspec = self.root / "pubspec.yaml"
        pubspec.write_text(
            "dependencies:\n"
            "  fonix:\n"
            "    path: ..\n"
            "      runtime_mode: bundled\n"
            "      application_minimum_os: '16.0'\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "one exact source scalar",
        ):
            run_ios_reference_app_gate._patch_ios_hook_config(pubspec)

    def test_ios_project_requires_three_exact_floors_and_bundle_identifier(self) -> None:
        work = self.root / "work"
        project, _, _ = self._write_ios_source_contract(work)

        run_ios_reference_app_gate._require_ios_source_contract(work)

        project.write_text(
            project.read_text(encoding="utf-8").replace("15.1", "15.0", 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "three 15.1",
        ):
            run_ios_reference_app_gate._require_ios_source_contract(work)

    def test_ios_source_contract_rejects_launch_bridge_regressions(self) -> None:
        work = self.root / "work"
        _, delegate, main = self._write_ios_source_contract(work)

        delegate.write_text(
            delegate.read_text(encoding="utf-8").replace(
                '"dev.fonix.reference/launch"', '"dev.fonix.reference/raw"'
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "launch activation contract changed",
        ):
            run_ios_reference_app_gate._require_ios_source_contract(work)

        _, delegate, main = self._write_ios_source_contract(work)
        delegate.write_text(
            f'{delegate.read_text(encoding="utf-8")}\nresult(environment)\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "exposes raw environment data",
        ):
            run_ios_reference_app_gate._require_ios_source_contract(work)

        _, _, main = self._write_ios_source_contract(work)
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                "challenge = await readIosResidentReferenceChallenge()",
                "requireResidentReferenceChallenge(Platform.environment)",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "resident smoke entrypoint contract changed",
        ):
            run_ios_reference_app_gate._require_ios_source_contract(work)

    def test_private_source_copy_omits_ios_generated_state(self) -> None:
        source = self.root / "example"
        source.mkdir()
        included = source / "ios/Runner.xcodeproj/project.pbxproj"
        included.parent.mkdir(parents=True)
        included.write_text("project", encoding="utf-8")
        generated_paths = [
            "ios/.symlinks/plugin",
            "ios/Flutter/ephemeral/generated",
            "ios/Flutter/Generated.xcconfig",
            "ios/Flutter/flutter_export_environment.sh",
            "ios/Podfile.lock",
            "ios/Pods/Manifest.lock",
            "ios/Runner/GeneratedPluginRegistrant.h",
            "ios/Runner/GeneratedPluginRegistrant.m",
        ]
        for relative in generated_paths:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("generated", encoding="utf-8")
        destination = self.root / "copy"

        summary = run_ios_reference_app_gate._COMMON._copy_example(
            source, destination
        )

        self.assertEqual(summary.file_count, 1)
        self.assertTrue((destination / included.relative_to(source)).is_file())
        for relative in generated_paths:
            self.assertFalse((destination / relative).exists())


class IosReferenceAssetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-assets-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _assets(self, variant: str) -> Path:
        gate = run_ios_reference_app_gate
        directory = self.root / variant
        sidecar = directory / gate._COMMON.ANDROID_SIDECAR_DIRECTORY
        sidecar.mkdir(parents=True)
        archive = _archive(variant)
        manifest = {
            "schema": 2,
            "artifactId": archive.artifact_id,
            "target": {
                "os": "ios",
                "architecture": "arm64",
                "variant": variant,
                "minimumOs": gate.APPLICATION_MINIMUM_OS,
                "flavor": "cpu",
                "runtimeMode": "linked",
            },
            "source": {
                "sha256": archive.sha256,
                "sizeBytes": archive.size_bytes,
            },
        }
        (directory / "fonix-native-artifact-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (directory / "ThirdPartyNotices.txt").write_text(
            "notices\n", encoding="utf-8"
        )
        for name in gate._COMMON.ASSET_NAMES:
            (sidecar / name).write_text(f"android {name}\n", encoding="utf-8")
        return directory

    def test_prepared_assets_bind_exact_variant(self) -> None:
        directory = self._assets("device")

        digest = run_ios_reference_app_gate._validate_prepared_assets(
            directory, _archive("device")
        )

        self.assertEqual(
            digest,
            hashlib.sha256(
                (directory / "fonix-native-artifact-manifest.json").read_bytes()
            ).hexdigest(),
        )

    def test_prepared_assets_reject_cross_variant_manifest(self) -> None:
        directory = self._assets("device")
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "does not bind the lock tuple",
        ):
            run_ios_reference_app_gate._validate_prepared_assets(
                directory, _archive("simulator")
            )


class IosHookReferenceShimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-hook-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "source_epoch"
        self.repository.mkdir()

    def _invocation(
        self,
        variant: str,
        invocation_hash: str,
        *,
        linking_enabled: bool | None = None,
        shim_hash: str | None = None,
    ) -> Path:
        gate = run_ios_reference_app_gate
        work = self.root / variant
        invocation = (
            work / ".dart_tool/hooks_runner/fonix" / invocation_hash
        )
        invocation.mkdir(parents=True, exist_ok=True)
        shared_hash = shim_hash or invocation_hash
        shared = (
            work
            / ".dart_tool/hooks_runner/shared/fonix/build"
            / shared_hash
        )
        shared.mkdir(parents=True, exist_ok=True)
        shim = shared / "libfonix_shim.dylib"
        shim.write_bytes(f"shim-{invocation_hash}".encode())
        output_path = invocation / "output.json"
        linking = variant == "device" if linking_enabled is None else linking_enabled
        input_value = {
            "assets": {},
            "config": {
                "build_asset_types": ["code_assets/code"],
                "extensions": {
                    "code_assets": {
                        "c_compiler": {
                            "ar": "/usr/bin/ar",
                            "cc": "/usr/bin/clang",
                            "ld": "/usr/bin/ld",
                        },
                        "ios": {
                            "target_sdk": (
                                "iphoneos"
                                if variant == "device"
                                else "iphonesimulator"
                            ),
                            "target_version": 13,
                        },
                        "link_mode_preference": "dynamic",
                        "target_architecture": "arm64",
                        "target_os": "ios",
                    }
                },
                "linking_enabled": linking,
            },
            "out_dir_shared": str(
                work / ".dart_tool/hooks_runner/shared/fonix/build"
            )
            + "/",
            "out_file": str(output_path),
            "package_name": "fonix",
            "package_root": str(self.repository) + "/",
            "user_defines": {
                "workspace_pubspec": {
                    "base_path": str(work / "pubspec.yaml"),
                    "defines": {
                        "runtime_mode": "linked",
                        "artifact_cache": ".fonix-artifact-cache",
                        "application_minimum_os": gate.APPLICATION_MINIMUM_OS,
                    },
                }
            },
        }
        output_value = {
            "assets": [
                {
                    "encoding": {
                        "file": str(shim),
                        "id": "package:fonix/fonix_shim",
                        "link_mode": {"type": "dynamic_loading_bundle"},
                    },
                    "type": "code_assets/code",
                }
            ],
            "assets_for_linking": {},
            "dependencies": [str(self.repository / "src/dort.h")],
            "status": "success",
            "timestamp": "2026-08-07 12:00:00.000",
        }
        (invocation / "input.json").write_text(
            json.dumps(input_value), encoding="utf-8"
        )
        output_path.write_text(json.dumps(output_value), encoding="utf-8")
        return work

    def test_resolves_exact_device_and_simulator_hook_tuples(self) -> None:
        gate = run_ios_reference_app_gate
        for variant, invocation_hash in (
            ("device", "0123456789"),
            ("simulator", "abcdef0123"),
        ):
            with self.subTest(variant=variant):
                work = self._invocation(variant, invocation_hash)
                reference = gate._resolve_reference_shim(
                    work_directory=work,
                    repository=self.repository,
                    archive=_archive(variant),
                )
                self.assertEqual(reference.invocation_hash, invocation_hash)
                self.assertEqual(
                    reference.path.parent.name, invocation_hash
                )
                self.assertEqual(
                    reference.sha256,
                    hashlib.sha256(reference.path.read_bytes()).hexdigest(),
                )

    def test_rejects_cross_hash_shared_path_and_duplicate_tuple(self) -> None:
        gate = run_ios_reference_app_gate
        work = self._invocation(
            "device", "0123456789", shim_hash="abcdef0123"
        )
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "shim output"):
            gate._resolve_reference_shim(
                work_directory=work,
                repository=self.repository,
                archive=_archive("device"),
            )

        original_root = self.root
        try:
            self.root = self.root / "duplicate-case"
            self.root.mkdir()
            work = self._invocation("device", "1111111111")
            self._invocation("device", "2222222222")
        finally:
            self.root = original_root
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "exactly one"):
            gate._resolve_reference_shim(
                work_directory=work,
                repository=self.repository,
                archive=_archive("device"),
            )

    def test_rejects_wrong_linking_tuple_and_extra_ios_input_key(self) -> None:
        gate = run_ios_reference_app_gate
        work = self._invocation(
            "simulator", "0123456789", linking_enabled=True
        )
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "exactly one"):
            gate._resolve_reference_shim(
                work_directory=work,
                repository=self.repository,
                archive=_archive("simulator"),
            )
        input_path = (
            work / ".dart_tool/hooks_runner/fonix/0123456789/input.json"
        )
        value = json.loads(input_path.read_text(encoding="utf-8"))
        value["config"]["extensions"]["code_assets"]["extra"] = True
        value["config"]["linking_enabled"] = False
        input_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "unexpected shape"):
            gate._resolve_reference_shim(
                work_directory=work,
                repository=self.repository,
                archive=_archive("simulator"),
            )


class IosSourceEpochTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-epoch-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "pubspec.yaml").write_text(
            "name: fonix\n", encoding="utf-8"
        )
        executable = self.repository / "tool/runner.py"
        executable.parent.mkdir()
        executable.write_text("print('ok')\n", encoding="utf-8")
        executable.chmod(0o755)
        manifest = run_ios_reference_app_gate._SOURCE_MANIFEST.build_manifest(
            self.repository
        )
        (self.repository / "MANIFEST.sha256").write_bytes(manifest)

    def test_snapshot_freezes_closed_manifest_bytes_and_executable_state(self) -> None:
        gate = run_ios_reference_app_gate
        snapshot, evidence = gate._snapshot_source_epoch(
            self.repository, self.root / "source_epoch"
        )
        frozen = (snapshot / "tool/runner.py").read_bytes()
        self.assertTrue((snapshot / "tool/runner.py").stat().st_mode & 0o111)
        self.assertRegex(evidence["manifestSha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn(str(snapshot), json.dumps(evidence))
        (self.repository / "tool/runner.py").write_text(
            "print('mutated')\n", encoding="utf-8"
        )
        self.assertEqual((snapshot / "tool/runner.py").read_bytes(), frozen)

    def test_tree_identity_binds_executable_bit(self) -> None:
        gate = run_ios_reference_app_gate
        tree = self.root / "tree"
        tree.mkdir()
        file = tree / "file"
        file.write_bytes(b"same bytes")
        file.chmod(0o600)
        first = gate._tree_identity(tree, "tree")
        file.chmod(0o700)
        second = gate._tree_identity(tree, "tree")
        self.assertNotEqual(first.tree_sha256, second.tree_sha256)
        self.assertFalse(first.files["file"]["executable"])
        self.assertTrue(second.files["file"]["executable"])

    def test_snapshot_rejects_manifest_mismatch(self) -> None:
        gate = run_ios_reference_app_gate
        (self.repository / "pubspec.yaml").write_text(
            "name: tampered\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "closed stable epoch"):
            gate._snapshot_source_epoch(
                self.repository, self.root / "source_epoch"
            )


class IosReferenceSimulatorTest(unittest.TestCase):
    UDID = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"

    def _identity(self) -> object:
        gate = run_ios_reference_app_gate
        return gate.SimulatorIdentity(
            udid=self.UDID,
            udid_sha256="a" * 64,
            name="iPhone 17 Pro",
            runtime=gate.VALIDATED_SIMULATOR_RUNTIME,
            initial_state="Booted",
        )

    def _inventory(self, *, state: str = "Shutdown") -> object:
        gate = run_ios_reference_app_gate
        return gate._COMMON.CommandOutput(
            stdout=json.dumps(
                {
                    "devices": {
                        gate.VALIDATED_SIMULATOR_RUNTIME: [
                            {
                                "name": "iPhone 17 Pro",
                                "udid": self.UDID,
                                "state": state,
                                "isAvailable": True,
                            }
                        ]
                    }
                }
            ),
            stderr="",
        )

    def test_inventory_binds_available_runtime_and_hashes_udid(self) -> None:
        with mock.patch.object(
            run_ios_reference_app_gate, "_run", return_value=self._inventory()
        ):
            identity = run_ios_reference_app_gate._simulator_identity(self.UDID)

        self.assertEqual(identity.runtime, run_ios_reference_app_gate.VALIDATED_SIMULATOR_RUNTIME)
        self.assertEqual(identity.initial_state, "Shutdown")
        self.assertEqual(
            identity.udid_sha256,
            hashlib.sha256(self.UDID.encode("ascii")).hexdigest(),
        )

    def test_inventory_rejects_wrong_runtime(self) -> None:
        output = self._inventory()
        value = json.loads(output.stdout)
        devices = value["devices"].pop(
            run_ios_reference_app_gate.VALIDATED_SIMULATOR_RUNTIME
        )
        value["devices"]["com.apple.CoreSimulator.SimRuntime.iOS-27-0"] = devices
        changed = run_ios_reference_app_gate._COMMON.CommandOutput(
            stdout=json.dumps(value), stderr=""
        )
        with mock.patch.object(run_ios_reference_app_gate, "_run", return_value=changed):
            with self.assertRaisesRegex(
                run_ios_reference_app_gate.IosReferenceAppGateError,
                "validated runtime/state",
            ):
                run_ios_reference_app_gate._simulator_identity(self.UDID)

    def test_reference_environment_removes_host_injection(self) -> None:
        challenge = "a" * 64
        environment = run_ios_reference_app_gate._reference_environment(
            challenge,
            {
                "FONIX_REFERENCE_SMOKE": "host",
                "SIMCTL_CHILD_OTHER": "untrusted",
                "DYLD_INSERT_LIBRARIES": "/tmp/inject.dylib",
                "PATH": "/usr/bin",
            }
        )
        self.assertNotIn("FONIX_REFERENCE_SMOKE", environment)
        self.assertNotIn("SIMCTL_CHILD_OTHER", environment)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", environment)
        self.assertEqual(environment["SIMCTL_CHILD_FONIX_REFERENCE_SMOKE"], "1")
        self.assertEqual(
            environment["SIMCTL_CHILD_FONIX_REFERENCE_CHALLENGE"], challenge
        )
        self.assertEqual(environment["PATH"], "/usr/bin")

    def test_package_presence_checks_are_closed(self) -> None:
        identity = self._identity()
        with mock.patch.object(
            run_ios_reference_app_gate, "_simulator_apps", return_value={}
        ):
            run_ios_reference_app_gate._require_package_absent(identity)
            with self.assertRaisesRegex(
                run_ios_reference_app_gate.IosReferenceAppGateError,
                "absent after",
            ):
                run_ios_reference_app_gate._require_package_present(identity)
        installed = {
            run_ios_reference_app_gate.APPLICATION_BUNDLE_IDENTIFIER: {
                "ApplicationType": "User"
            }
        }
        with mock.patch.object(
            run_ios_reference_app_gate,
            "_simulator_apps",
            return_value=installed,
        ):
            run_ios_reference_app_gate._require_package_present(identity)
            with self.assertRaisesRegex(
                run_ios_reference_app_gate.IosReferenceAppGateError,
                "unexpectedly remains installed",
            ):
                run_ios_reference_app_gate._require_package_absent(identity)

    def test_listapps_openstep_plist_uses_exact_bounded_conversion(self) -> None:
        gate = run_ios_reference_app_gate
        openstep = (
            "{\n"
            f'    "{gate.APPLICATION_BUNDLE_IDENTIFIER}" = '
            "{ ApplicationType = User; };\n"
            "}\n"
        )
        raw = gate._COMMON.CommandOutput(stdout=openstep, stderr="")
        converted = gate._COMMON.CommandOutput(
            stdout=json.dumps(
                {gate.APPLICATION_BUNDLE_IDENTIFIER: {"ApplicationType": "User"}}
            ),
            stderr="",
        )
        with mock.patch.object(gate, "_run", return_value=raw) as run_command:
            with mock.patch.object(
                gate, "_run_with_input", return_value=converted
            ) as convert:
                applications = gate._simulator_apps(self._identity())

        self.assertIn(gate.APPLICATION_BUNDLE_IDENTIFIER, applications)
        self.assertEqual(
            run_command.call_args.args[0],
            ("/usr/bin/xcrun", "simctl", "listapps", self.UDID),
        )
        self.assertNotIn("--json", run_command.call_args.args[0])
        self.assertEqual(
            convert.call_args.args[0],
            ("/usr/bin/plutil", "-convert", "json", "-o", "-", "--", "-"),
        )
        self.assertEqual(convert.call_args.kwargs["input_bytes"], openstep.encode())
        self.assertEqual(
            convert.call_args.kwargs["maximum_input"], gate.MAX_SIMCTL_BYTES
        )
        self.assertEqual(
            convert.call_args.kwargs["maximum_output"], gate.MAX_SIMCTL_BYTES
        )

    def test_listapps_conversion_preserves_valid_absence(self) -> None:
        gate = run_ios_reference_app_gate
        raw = gate._COMMON.CommandOutput(stdout="{ }\n", stderr="")
        converted = gate._COMMON.CommandOutput(stdout="{}", stderr="")
        with mock.patch.object(gate, "_run", return_value=raw):
            with mock.patch.object(
                gate, "_run_with_input", return_value=converted
            ):
                applications = gate._simulator_apps(self._identity())
        self.assertEqual(applications, {})

    def test_listapps_conversion_failure_is_authoritative(self) -> None:
        gate = run_ios_reference_app_gate
        raw = gate._COMMON.CommandOutput(stdout="malformed plist", stderr="")
        with mock.patch.object(gate, "_run", return_value=raw):
            with mock.patch.object(
                gate,
                "_run_with_input",
                side_effect=gate.IosReferenceAppGateError(
                    "simulator application plist conversion failed with exit code 1"
                ),
            ):
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError,
                    "conversion failed",
                ):
                    gate._simulator_apps(self._identity())

    def test_listapps_rejects_malformed_and_oversized_converted_json(self) -> None:
        gate = run_ios_reference_app_gate
        raw = gate._COMMON.CommandOutput(stdout="{ }\n", stderr="")
        for converted, message in (
            ("not-json", "not strict UTF-8 JSON"),
            ("x" * (gate.MAX_SIMCTL_BYTES + 1), "exceeds"),
        ):
            with self.subTest(message=message):
                result = gate._COMMON.CommandOutput(stdout=converted, stderr="")
                with mock.patch.object(gate, "_run", return_value=raw):
                    with mock.patch.object(
                        gate, "_run_with_input", return_value=result
                    ):
                        with self.assertRaisesRegex(
                            gate.IosReferenceAppGateError, message
                        ):
                            gate._simulator_apps(self._identity())

    def test_plist_conversion_rejects_oversized_input_before_spawn(self) -> None:
        gate = run_ios_reference_app_gate
        with mock.patch.object(gate, "_run") as bounded:
            with mock.patch.object(gate.tempfile, "NamedTemporaryFile") as temporary:
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError,
                    "input exceeds",
                ):
                    gate._run_with_input(
                        ("/usr/bin/plutil",),
                        input_bytes=b"x" * 17,
                        maximum_input=16,
                        maximum_output=16,
                        timeout_seconds=1,
                        operation="test conversion",
                    )
        bounded.assert_not_called()
        temporary.assert_not_called()

    def test_arm64_probe_uses_explicit_arch_process(self) -> None:
        gate = run_ios_reference_app_gate
        identity = gate.SimulatorIdentity(
            udid=self.UDID,
            udid_sha256="a" * 64,
            name="iPhone 17 Pro",
            runtime=gate.VALIDATED_SIMULATOR_RUNTIME,
            initial_state="Booted",
        )
        commands: list[tuple[str, ...]] = []

        def run_command(command: object, **_: object) -> object:
            value = tuple(command)  # type: ignore[arg-type]
            commands.append(value)
            stdout = "1\n" if "hw.optional.arm64" in value else "arm64\n"
            return gate._COMMON.CommandOutput(stdout=stdout, stderr="")

        with mock.patch.object(gate, "_run", side_effect=run_command):
            result = gate._verify_simulator_arm64(identity)

        self.assertEqual(
            result,
            {
                "advertisesArm64": True,
                "explicitArm64CapabilityProcess": "passed",
            },
        )
        self.assertIn(
            (
                "/usr/bin/xcrun",
                "simctl",
                "spawn",
                self.UDID,
                "/usr/bin/arch",
                "-arm64",
                "/usr/bin/uname",
                "-m",
            ),
            commands,
        )
        self.assertFalse(
            any(
                command[-2:] == ("/usr/bin/uname", "-m")
                and "/usr/bin/arch" not in command
                for command in commands
            )
        )

    def test_arm64_probe_rejects_missing_capability_and_arch_downgrade(self) -> None:
        gate = run_ios_reference_app_gate
        identity = gate.SimulatorIdentity(
            udid=self.UDID,
            udid_sha256="a" * 64,
            name="iPhone 17 Pro",
            runtime=gate.VALIDATED_SIMULATOR_RUNTIME,
            initial_state="Booted",
        )
        missing = gate._COMMON.CommandOutput(stdout="0\n", stderr="")
        with mock.patch.object(gate, "_run", return_value=missing):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError,
                "does not advertise arm64",
            ):
                gate._verify_simulator_arm64(identity)

        outputs = iter(
            (
                gate._COMMON.CommandOutput(stdout="1\n", stderr=""),
                gate._COMMON.CommandOutput(stdout="x86_64\n", stderr=""),
            )
        )
        with mock.patch.object(gate, "_run", side_effect=lambda *_a, **_k: next(outputs)):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError,
                "could not execute an explicit arm64 process",
            ):
                gate._verify_simulator_arm64(identity)

    def test_installed_application_matches_only_exact_transport_normalization(
        self,
    ) -> None:
        gate = run_ios_reference_app_gate
        temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-installed-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        local = root / "local/Runner.app"
        installed = (
            root
            / "CoreSimulator/Devices"
            / self.UDID
            / "data/Containers/Bundle/Application"
            / "11111111-2222-3333-4444-555555555555"
            / "Runner.app"
        )
        binary_contents = {
            "Runner": b"runner",
            "Frameworks/App.framework/App": b"app",
            "Frameworks/Flutter.framework/Flutter": b"flutter",
            "Frameworks/fonix_shim.framework/fonix_shim": b"shim",
        }
        for application in (local, installed):
            for relative, contents in binary_contents.items():
                path = application / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            (application / "empty-directory").mkdir()
        for relative in binary_contents:
            (local / relative).chmod(0o700)
        (installed / "Runner").chmod(0o700)
        for relative in gate._IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS:
            (installed / relative).chmod(0o600)
        identity = gate.SimulatorIdentity(
            udid=self.UDID,
            udid_sha256="a" * 64,
            name="iPhone 17 Pro",
            runtime=gate.VALIDATED_SIMULATOR_RUNTIME,
            initial_state="Booted",
        )
        output = gate._COMMON.CommandOutput(stdout=f"{installed}\n", stderr="")
        expected = gate._install_transport_identity(
            local,
            "local install transport",
            installed=False,
        )
        expectation = gate.InstalledApplicationExpectation(
            installed.resolve(), expected
        )
        device_root = (root / "CoreSimulator/Devices" / self.UDID).resolve()
        with mock.patch.object(gate, "_run", return_value=output):
            ownership = gate._bind_installed_application(
                identity, expectation, device_root
            )

        self.assertEqual(ownership.tree, expected)
        self.assertEqual(ownership.tree.file_count, 4)
        self.assertRegex(ownership.tree.tree_sha256, r"^[0-9a-f]{64}$")

        (installed / "Runner").write_bytes(b"tampered")
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError,
                "install-transport identity",
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )

    def test_install_transport_identity_rejects_mode_directory_and_container_drift(
        self,
    ) -> None:
        gate = run_ios_reference_app_gate
        temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-transport-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        local = root / "local/Runner.app"
        installed = (
            root
            / "CoreSimulator/Devices"
            / self.UDID
            / "data/Containers/Bundle/Application"
            / "11111111-2222-3333-4444-555555555555"
            / "Runner.app"
        )
        for application in (local, installed):
            for relative in gate._IOS_MACHO_RELATIVE_PATHS:
                path = application / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("utf-8"))
            (application / "assets/data.bin").parent.mkdir()
            (application / "assets/data.bin").write_bytes(b"data")
            (application / "required-empty-directory").mkdir()
        for relative in gate._IOS_MACHO_RELATIVE_PATHS:
            (local / relative).chmod(0o700)
        (installed / "Runner").chmod(0o700)
        for relative in gate._IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS:
            (installed / relative).chmod(0o600)

        source_framework = next(
            iter(gate._IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS)
        )
        (local / source_framework).chmod(0o600)
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError, "executable-path inventory"
        ):
            gate._install_transport_identity(
                local,
                "source missing executable",
                installed=False,
            )
        (local / source_framework).chmod(0o700)
        unexpected_source_executable = local / "unexpected-executable"
        unexpected_source_executable.write_bytes(b"unexpected")
        unexpected_source_executable.chmod(0o700)
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError, "executable-path inventory"
        ):
            gate._install_transport_identity(
                local,
                "source extra executable",
                installed=False,
            )
        unexpected_source_executable.unlink()

        expected = gate._install_transport_identity(
            local,
            "local install transport",
            installed=False,
        )
        identity = gate.SimulatorIdentity(
            udid=self.UDID,
            udid_sha256="a" * 64,
            name="iPhone 17 Pro",
            runtime=gate.VALIDATED_SIMULATOR_RUNTIME,
            initial_state="Booted",
        )
        device_root = (root / "CoreSimulator/Devices" / self.UDID).resolve()
        output = gate._COMMON.CommandOutput(stdout=f"{installed}\n", stderr="")
        expectation = gate.InstalledApplicationExpectation(
            installed.resolve(), expected
        )

        changed_framework = next(
            iter(gate._IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS)
        )
        (installed / changed_framework).chmod(0o700)
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "executable-path inventory"
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )
        (installed / changed_framework).chmod(0o600)

        (installed / "Runner").chmod(0o600)
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "executable-path inventory"
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )
        (installed / "Runner").chmod(0o700)

        (installed / "unexpected-empty-directory").mkdir()
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "install-transport identity"
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )
        (installed / "unexpected-empty-directory").rmdir()

        (installed / "required-empty-directory").rmdir()
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "install-transport identity"
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )
        (installed / "required-empty-directory").mkdir()

        (installed / "assets/data.bin").rename(
            installed / "assets/renamed.bin"
        )
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "install-transport identity"
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )
        (installed / "assets/renamed.bin").rename(
            installed / "assets/data.bin"
        )

        replacement = installed.parent.parent / (
            "22222222-3333-4444-5555-666666666666/Runner.app"
        )
        replacement.parent.mkdir()
        replacement.mkdir()
        replacement_output = gate._COMMON.CommandOutput(
            stdout=f"{replacement}\n", stderr=""
        )
        with mock.patch.object(gate, "_run", return_value=replacement_output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError, "changed containers"
            ):
                gate._bind_installed_application(
                    identity, expectation, device_root
                )


class IosReferenceReceiptTest(unittest.TestCase):
    CHALLENGE = "a" * 64
    PID = 12345

    def _line(self, variant: str = "simulator") -> str:
        gate = run_ios_reference_app_gate
        return (
            gate.REFERENCE_RECEIPT_PREFIX
            + json.dumps(
                gate._expected_reference_receipt(
                    _archive(variant), self.CHALLENGE, self.PID
                ),
                separators=(",", ":"),
            )
        )

    def test_accepts_exact_canonical_simulator_receipt(self) -> None:
        receipt = run_ios_reference_app_gate._parse_reference_output(
            self._line(),
            _archive("simulator"),
            self.CHALLENGE,
            self.PID,
        )
        self.assertTrue(receipt["fullCpuAssignment"])
        self.assertEqual(receipt["doubleClose"], "passed")
        self.assertEqual(receipt["platform"], "ios")
        self.assertEqual(receipt["challenge"], self.CHALLENGE)
        self.assertEqual(receipt["processId"], self.PID)

    def test_rejects_cross_variant_receipt(self) -> None:
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "shimBuildId",
        ):
            run_ios_reference_app_gate._parse_reference_output(
                self._line("device"),
                _archive("simulator"),
                self.CHALLENGE,
                self.PID,
            )

    def test_rejects_duplicate_marker_on_one_line(self) -> None:
        gate = run_ios_reference_app_gate
        output = gate.REFERENCE_RECEIPT_PREFIX + self._line()
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "ambiguous marker",
        ):
            run_ios_reference_app_gate._parse_reference_output(
                output, _archive("simulator"), self.CHALLENGE, self.PID
            )

    def test_rejects_wrong_or_missing_challenge_and_pid(self) -> None:
        gate = run_ios_reference_app_gate
        for field, value in (
            ("challenge", "b" * 64),
            ("challenge", None),
            ("processId", self.PID + 1),
            ("processId", None),
        ):
            with self.subTest(field=field, value=value):
                receipt = gate._expected_reference_receipt(
                    _archive("simulator"), self.CHALLENGE, self.PID
                )
                if value is None:
                    del receipt[field]
                else:
                    receipt[field] = value
                line = gate.REFERENCE_RECEIPT_PREFIX + json.dumps(
                    receipt, separators=(",", ":")
                )
                with self.assertRaises(gate.IosReferenceAppGateError):
                    gate._parse_reference_output(
                        line,
                        _archive("simulator"),
                        self.CHALLENGE,
                        self.PID,
                    )

    def test_rejects_preamble_suffix_whitespace_and_noncanonical_json(self) -> None:
        gate = run_ios_reference_app_gate
        valid = self._line()
        receipt = gate._expected_reference_receipt(
            _archive("simulator"), self.CHALLENGE, self.PID
        )
        cases = (
            "flutter: " + valid,
            valid + " suffix",
            valid + "\n",
            gate.REFERENCE_RECEIPT_PREFIX + json.dumps(receipt),
        )
        for value in cases:
            with self.subTest(value=value[:40]):
                with self.assertRaises(gate.IosReferenceAppGateError):
                    gate._parse_reference_output(
                        value,
                        _archive("simulator"),
                        self.CHALLENGE,
                        self.PID,
                    )

    def test_failure_marker_is_authoritative(self) -> None:
        failure = {
            "schemaVersion": 1,
            "status": "failed",
            "errorType": "OrtRunException",
            "challenge": self.CHALLENGE,
            "processId": self.PID,
        }
        output = (
            run_ios_reference_app_gate.REFERENCE_FAILURE_PREFIX
            + json.dumps(failure, separators=(",", ":"))
        )
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "OrtRunException",
        ):
            run_ios_reference_app_gate._parse_reference_output(
                output,
                _archive("simulator"),
                self.CHALLENGE,
                self.PID,
            )


class IosReceiptFileRunnerTest(unittest.TestCase):
    UDID = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    CONTAINER = "11111111-2222-3333-4444-555555555555"
    CHALLENGE = "a" * 64
    PID = 12345

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-receipt-file-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identity = run_ios_reference_app_gate.SimulatorIdentity(
            udid=self.UDID,
            udid_sha256="a" * 64,
            name="iPhone 17 Pro",
            runtime=run_ios_reference_app_gate.VALIDATED_SIMULATOR_RUNTIME,
            initial_state="Booted",
        )
        self.data_container = (
            self.root
            / "CoreSimulator/Devices"
            / self.UDID
            / "data/Containers/Data/Application"
            / self.CONTAINER
        )
        self.device_root = (
            self.root / "CoreSimulator/Devices" / self.UDID
        ).resolve()
        (self.data_container / "tmp").mkdir(parents=True)
        self.receipt_path, self.staging_path = (
            run_ios_reference_app_gate._receipt_paths(
                self.data_container, self.CHALLENGE
            )
        )

    def _receipt_bytes(self) -> bytes:
        gate = run_ios_reference_app_gate
        return (
            gate.REFERENCE_RECEIPT_PREFIX
            + json.dumps(
                gate._expected_reference_receipt(
                    _archive("simulator"), self.CHALLENGE, self.PID
                ),
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def test_data_container_is_bound_to_selected_simulator(self) -> None:
        gate = run_ios_reference_app_gate
        output = gate._COMMON.CommandOutput(
            stdout=f"{self.data_container}\n", stderr=""
        )
        with mock.patch.object(gate, "_run", return_value=output) as run_command:
            resolved = gate._simulator_data_container(
                self.identity, self.device_root
            )

        self.assertEqual(resolved, self.data_container.resolve())
        self.assertEqual(
            run_command.call_args.args[0],
            (
                "/usr/bin/xcrun",
                "simctl",
                "get_app_container",
                self.UDID,
                gate.APPLICATION_BUNDLE_IDENTIFIER,
                "data",
            ),
        )

    def test_data_container_rejects_escape(self) -> None:
        gate = run_ios_reference_app_gate
        escaped = self.root / "outside" / self.CONTAINER
        (escaped / "tmp").mkdir(parents=True)
        output = gate._COMMON.CommandOutput(stdout=f"{escaped}\n", stderr="")
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(
                gate.IosReferenceAppGateError,
                "escaped the selected device root",
            ):
                gate._simulator_data_container(self.identity, self.device_root)

    def test_data_container_rejects_lookalike_device_root_prefix(self) -> None:
        gate = run_ios_reference_app_gate
        lookalike = (
            Path(str(self.device_root) + "-lookalike")
            / "data/Containers/Data/Application"
            / self.CONTAINER
        )
        (lookalike / "tmp").mkdir(parents=True)
        output = gate._COMMON.CommandOutput(stdout=f"{lookalike}\n", stderr="")
        with mock.patch.object(gate, "_run", return_value=output):
            with self.assertRaisesRegex(gate.IosReferenceAppGateError, "escaped"):
                gate._simulator_data_container(self.identity, self.device_root)

    def test_launch_uses_exact_bundle_sanitized_environment_and_pid(self) -> None:
        gate = run_ios_reference_app_gate
        output = gate._COMMON.CommandOutput(
            stdout=f"{gate.APPLICATION_BUNDLE_IDENTIFIER}: 12345\n", stderr=""
        )
        with mock.patch.object(gate, "_run", return_value=output) as run_command:
            with mock.patch.dict(
                os.environ,
                {
                    "SIMCTL_CHILD_UNTRUSTED": "1",
                    "DYLD_INSERT_LIBRARIES": "/tmp/inject.dylib",
                },
                clear=False,
            ):
                pid = gate._launch_simulator_application(
                    self.identity, self.CHALLENGE
                )

        self.assertEqual(pid, 12345)
        self.assertEqual(
            run_command.call_args.args[0],
            (
                "/usr/bin/xcrun",
                "simctl",
                "launch",
                self.UDID,
                gate.APPLICATION_BUNDLE_IDENTIFIER,
            ),
        )
        environment = run_command.call_args.kwargs["environment"]
        self.assertEqual(environment["SIMCTL_CHILD_FONIX_REFERENCE_SMOKE"], "1")
        self.assertEqual(
            environment["SIMCTL_CHILD_FONIX_REFERENCE_CHALLENGE"],
            self.CHALLENGE,
        )
        self.assertNotIn("SIMCTL_CHILD_UNTRUSTED", environment)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", environment)

    def test_launch_rejects_malformed_or_unbounded_pid(self) -> None:
        gate = run_ios_reference_app_gate
        for stdout in (
            "",
            f"{gate.APPLICATION_BUNDLE_IDENTIFIER}: 0\n",
            f"{gate.APPLICATION_BUNDLE_IDENTIFIER}: 2147483648\n",
            f"{gate.APPLICATION_BUNDLE_IDENTIFIER}: 12\nextra\n",
            "another.bundle: 12\n",
        ):
            with self.subTest(stdout=stdout):
                output = gate._COMMON.CommandOutput(stdout=stdout, stderr="")
                with mock.patch.object(gate, "_run", return_value=output):
                    with self.assertRaises(gate.IosReferenceAppGateError):
                        gate._launch_simulator_application(
                            self.identity, self.CHALLENGE
                        )

    def test_atomic_receipt_file_returns_path_free_hash_evidence(self) -> None:
        gate = run_ios_reference_app_gate
        data = self._receipt_bytes()
        self.receipt_path.write_bytes(data)

        receipt, evidence = gate._poll_reference_receipt(
            self.receipt_path,
            self.staging_path,
            _archive("simulator"),
            self.CHALLENGE,
            self.PID,
            timeout_seconds=1,
        )

        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(evidence["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(evidence["sizeBytes"], len(data))
        self.assertEqual(evidence["publication"], "atomic-sandbox-file")
        self.assertNotIn(str(self.data_container), json.dumps(evidence))

    def test_prelaunch_rejects_stale_final_and_staging_paths(self) -> None:
        gate = run_ios_reference_app_gate
        for path in (self.receipt_path, self.staging_path):
            with self.subTest(path=path.name):
                path.write_text("stale", encoding="utf-8")
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError,
                    "stale .* exists before launch",
                ):
                    gate._require_receipt_paths_absent(
                        self.receipt_path, self.staging_path
                    )
                path.unlink()

    def test_stale_prior_challenge_is_ignored_but_current_is_rejected(self) -> None:
        gate = run_ios_reference_app_gate
        old_final, old_staging = gate._receipt_paths(
            self.data_container, "b" * 64
        )
        old_final.write_text("stale", encoding="utf-8")
        gate._require_receipt_paths_absent(self.receipt_path, self.staging_path)
        self.receipt_path.write_text("stale", encoding="utf-8")
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "stale"):
            gate._require_receipt_paths_absent(
                self.receipt_path, self.staging_path
            )
        self.assertFalse(old_staging.exists())

    def test_receipt_filename_rejects_traversal_challenge(self) -> None:
        gate = run_ios_reference_app_gate
        for challenge in ("../" + "a" * 61, "A" * 64, "a" * 63):
            with self.subTest(challenge=challenge):
                with self.assertRaisesRegex(gate.IosReferenceAppGateError, "64 lowercase"):
                    gate._receipt_paths(self.data_container, challenge)

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliable on Windows CI")
    def test_receipt_poll_rejects_link_substitution(self) -> None:
        gate = run_ios_reference_app_gate
        target = self.root / "target"
        target.write_bytes(self._receipt_bytes())
        self.receipt_path.symlink_to(target)
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError,
            "symbolic link",
        ):
            gate._poll_reference_receipt(
                self.receipt_path,
                self.staging_path,
                _archive("simulator"),
                self.CHALLENGE,
                self.PID,
                timeout_seconds=1,
            )

    def test_receipt_poll_rejects_oversized_partial_and_malformed_files(self) -> None:
        gate = run_ios_reference_app_gate
        cases = (
            (b"x" * (gate.MAX_REFERENCE_FILE_BYTES + 1), "exceeds"),
            (b"", "partial or empty"),
            (self._receipt_bytes().rstrip(b"\n"), "partial"),
            (gate.REFERENCE_RECEIPT_PREFIX.encode() + b"{bad}\n", "not strict"),
        )
        for data, message in cases:
            with self.subTest(message=message):
                self.receipt_path.write_bytes(data)
                with self.assertRaisesRegex(gate.IosReferenceAppGateError, message):
                    gate._poll_reference_receipt(
                        self.receipt_path,
                        self.staging_path,
                        _archive("simulator"),
                        self.CHALLENGE,
                        self.PID,
                        timeout_seconds=1,
                    )
                self.receipt_path.unlink()

    def test_receipt_poll_times_out_and_names_incomplete_staging(self) -> None:
        gate = run_ios_reference_app_gate
        now = [0.0]

        def clock() -> float:
            return now[0]

        def sleeper(duration: float) -> None:
            now[0] += duration

        self.staging_path.write_text("partial", encoding="utf-8")
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError,
            "timed out with an incomplete staging file",
        ):
            gate._poll_reference_receipt(
                self.receipt_path,
                self.staging_path,
                _archive("simulator"),
                self.CHALLENGE,
                self.PID,
                timeout_seconds=0.1,
                poll_interval_seconds=0.05,
                clock=clock,
                sleeper=sleeper,
            )

    def test_termination_is_pid_settled_after_exact_bundle_command(self) -> None:
        gate = run_ios_reference_app_gate
        with mock.patch.object(
            gate, "_simulator_process_is_live", return_value=True
        ) as live:
            with mock.patch.object(
                gate,
                "_run",
                return_value=gate._COMMON.CommandOutput(stdout="", stderr=""),
            ) as run_command:
                with mock.patch.object(
                    gate, "_wait_simulator_process_settlement"
                ) as wait:
                    gate._terminate_simulator_application(self.identity, 12345)
        self.assertEqual(
            run_command.call_args.args[0],
            (
                "/usr/bin/xcrun",
                "simctl",
                "terminate",
                self.UDID,
                gate.APPLICATION_BUNDLE_IDENTIFIER,
            ),
        )
        live.assert_called_once_with(self.identity, 12345)
        wait.assert_called_once_with(self.identity, 12345)

    def test_termination_accepts_only_exact_owned_pid_already_absent(self) -> None:
        gate = run_ios_reference_app_gate
        with mock.patch.object(
            gate, "_simulator_process_is_live", return_value=False
        ) as live:
            with mock.patch.object(gate, "_run") as run_command:
                with mock.patch.object(
                    gate, "_wait_simulator_process_settlement"
                ) as wait:
                    gate._terminate_simulator_application(self.identity, 12345)
        live.assert_called_once_with(self.identity, 12345)
        run_command.assert_not_called()
        wait.assert_not_called()

    def test_termination_failure_accepts_exit_race_but_not_live_pid(self) -> None:
        gate = run_ios_reference_app_gate
        termination_error = gate.IosReferenceAppGateError("terminate failed")
        for states, should_raise in (((True, False), False), ((True, True), True)):
            with self.subTest(states=states):
                with mock.patch.object(
                    gate,
                    "_simulator_process_is_live",
                    side_effect=states,
                ):
                    with mock.patch.object(
                        gate, "_run", side_effect=termination_error
                    ):
                        if should_raise:
                            with self.assertRaises(gate.IosReferenceAppGateError):
                                gate._terminate_simulator_application(
                                    self.identity, 12345
                                )
                        else:
                            gate._terminate_simulator_application(
                                self.identity, 12345
                            )

    def test_process_settlement_rejects_live_pid_then_accepts_absence(self) -> None:
        gate = run_ios_reference_app_gate
        statuses = iter(
            (
                gate.CommandStatus(return_code=0, stdout=" 12345\n", stderr=""),
                gate.CommandStatus(return_code=1, stdout="", stderr=""),
            )
        )
        now = [0.0]
        with mock.patch.object(
            gate, "_run_status", side_effect=lambda *_a, **_k: next(statuses)
        ):
            gate._wait_simulator_process_settlement(
                self.identity,
                12345,
                timeout_seconds=1,
                clock=lambda: now[0],
                sleeper=lambda duration: now.__setitem__(0, now[0] + duration),
            )

    def test_cleanup_never_uninstalls_when_gate_does_not_own_install(self) -> None:
        gate = run_ios_reference_app_gate
        with mock.patch.object(gate, "_run") as run_command:
            with mock.patch.object(gate, "_terminate_simulator_application") as terminate:
                gate._cleanup_simulator(
                    self.identity,
                    device_root=self.device_root,
                    shutdown_if_started=False,
                    owned_pid=None,
                    install_succeeded=False,
                    installed_expectation=None,
                    installed_ownership=None,
                )
        run_command.assert_not_called()
        terminate.assert_not_called()

    def test_cleanup_terminates_and_uninstalls_only_owned_state(self) -> None:
        gate = run_ios_reference_app_gate
        installed = (
            self.device_root
            / "data/Containers/Bundle/Application"
            / self.CONTAINER
            / "Runner.app"
        )
        installed.mkdir(parents=True)
        (installed / "Runner").write_bytes(b"runner")
        tree = gate._tree_identity(installed, "installed")
        ownership = gate.InstalledApplicationOwnership(installed, tree)
        with mock.patch.object(gate, "_terminate_simulator_application") as terminate:
            with mock.patch.object(
                gate,
                "_run",
                return_value=gate._COMMON.CommandOutput(stdout="", stderr=""),
            ) as run_command:
                with mock.patch.object(gate, "_require_package_absent") as absent:
                    with mock.patch.object(
                        gate, "_revalidate_installed_ownership"
                    ) as revalidate:
                        gate._cleanup_simulator(
                            self.identity,
                            device_root=self.device_root,
                            shutdown_if_started=False,
                            owned_pid=12345,
                            install_succeeded=True,
                            installed_expectation=None,
                            installed_ownership=ownership,
                        )
        terminate.assert_called_once_with(self.identity, 12345)
        self.assertEqual(run_command.call_args.args[0][1:3], ("simctl", "uninstall"))
        absent.assert_called_once_with(self.identity)
        self.assertEqual(revalidate.call_count, 2)

    def test_cleanup_rebinds_exact_preinstall_expectation_before_uninstall(
        self,
    ) -> None:
        gate = run_ios_reference_app_gate
        installed = self.device_root / "owned/Runner.app"
        tree = gate.TreeIdentity({}, 1, 1, "a" * 64)
        expectation = gate.InstalledApplicationExpectation(installed, tree)
        ownership = gate.InstalledApplicationOwnership(installed, tree)
        with mock.patch.object(
            gate, "_bind_installed_application", return_value=ownership
        ) as bind:
            with mock.patch.object(
                gate, "_revalidate_installed_ownership"
            ) as revalidate:
                with mock.patch.object(
                    gate,
                    "_run",
                    return_value=gate._COMMON.CommandOutput(stdout="", stderr=""),
                ) as run_command:
                    with mock.patch.object(
                        gate, "_require_package_absent"
                    ) as absent:
                        gate._cleanup_simulator(
                            self.identity,
                            device_root=self.device_root,
                            shutdown_if_started=False,
                            owned_pid=None,
                            install_succeeded=True,
                            installed_expectation=expectation,
                            installed_ownership=None,
                        )
        bind.assert_called_once_with(
            self.identity, expectation, self.device_root
        )
        revalidate.assert_called_once_with(
            self.identity, ownership, self.device_root
        )
        self.assertEqual(run_command.call_args.args[0][1:3], ("simctl", "uninstall"))
        absent.assert_called_once_with(self.identity)

    def test_cleanup_uninstalls_when_exact_owned_pid_already_exited(self) -> None:
        gate = run_ios_reference_app_gate
        installed = self.device_root / "owned/Runner.app"
        tree = gate.TreeIdentity({}, 1, 1, "a" * 64)
        ownership = gate.InstalledApplicationOwnership(installed, tree)
        with mock.patch.object(
            gate, "_revalidate_installed_ownership"
        ) as revalidate:
            with mock.patch.object(
                gate, "_simulator_process_is_live", return_value=False
            ) as live:
                with mock.patch.object(
                    gate,
                    "_run",
                    return_value=gate._COMMON.CommandOutput(stdout="", stderr=""),
                ) as run_command:
                    with mock.patch.object(
                        gate, "_require_package_absent"
                    ) as absent:
                        gate._cleanup_simulator(
                            self.identity,
                            device_root=self.device_root,
                            shutdown_if_started=False,
                            owned_pid=12345,
                            install_succeeded=True,
                            installed_expectation=None,
                            installed_ownership=ownership,
                        )
        live.assert_called_once_with(self.identity, 12345)
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(run_command.call_args.args[0][1:3], ("simctl", "uninstall"))
        absent.assert_called_once_with(self.identity)
        self.assertEqual(revalidate.call_count, 2)

    def test_cleanup_live_pid_and_failed_terminate_blocks_uninstall(self) -> None:
        gate = run_ios_reference_app_gate
        installed = self.device_root / "owned/Runner.app"
        tree = gate.TreeIdentity({}, 1, 1, "a" * 64)
        ownership = gate.InstalledApplicationOwnership(installed, tree)
        termination_error = gate.IosReferenceAppGateError("terminate failed")
        with mock.patch.object(gate, "_revalidate_installed_ownership"):
            with mock.patch.object(
                gate, "_simulator_process_is_live", side_effect=(True, True)
            ):
                with mock.patch.object(
                    gate, "_run", side_effect=termination_error
                ) as run_command:
                    with self.assertRaises(
                        gate.IosReferenceCleanupError
                    ) as captured:
                        gate._cleanup_simulator(
                            self.identity,
                            device_root=self.device_root,
                            shutdown_if_started=False,
                            owned_pid=12345,
                            install_succeeded=True,
                            installed_expectation=None,
                            installed_ownership=ownership,
                        )
        self.assertIn(termination_error, captured.exception.errors)
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(
            run_command.call_args.kwargs["operation"],
            "simulator reference-app termination",
        )

    def test_cleanup_refuses_uninstall_when_preinstall_rebind_fails(self) -> None:
        gate = run_ios_reference_app_gate
        expectation = gate.InstalledApplicationExpectation(
            self.device_root / "owned/Runner.app",
            gate.TreeIdentity({}, 1, 1, "a" * 64),
        )
        mismatch = gate.IosReferenceAppGateError("transport tree changed")
        with mock.patch.object(
            gate, "_bind_installed_application", side_effect=mismatch
        ):
            with mock.patch.object(gate, "_run") as run_command:
                with self.assertRaises(gate.IosReferenceCleanupError) as captured:
                    gate._cleanup_simulator(
                        self.identity,
                        device_root=self.device_root,
                        shutdown_if_started=False,
                        owned_pid=None,
                        install_succeeded=True,
                        installed_expectation=expectation,
                        installed_ownership=None,
                    )
        self.assertIn(mismatch, captured.exception.errors)
        run_command.assert_not_called()

    def test_cleanup_refuses_replacement_race_before_delete(self) -> None:
        gate = run_ios_reference_app_gate
        ownership = gate.InstalledApplicationOwnership(
            self.data_container,
            gate.TreeIdentity({}, 1, 1, "a" * 64),
        )
        with mock.patch.object(
            gate,
            "_revalidate_installed_ownership",
            side_effect=gate.IosReferenceAppGateError("ownership changed containers"),
        ):
            with mock.patch.object(gate, "_run") as run_command:
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError, "cleanup did not settle"
                ):
                    gate._cleanup_simulator(
                        self.identity,
                        device_root=self.device_root,
                        shutdown_if_started=False,
                        owned_pid=None,
                        install_succeeded=True,
                        installed_expectation=None,
                        installed_ownership=ownership,
                    )
        run_command.assert_not_called()


class IosRunGateOrchestrationTest(unittest.TestCase):
    UDID = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    CHALLENGE = "a" * 64
    PID = 54321

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-run-gate-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.flutter = self.root / "sdk/bin/flutter"
        self.flutter.parent.mkdir(parents=True)
        self.flutter.write_bytes(b"flutter")
        self.flutter.chmod(0o700)
        self.dart = self.flutter.parent / "cache/dart-sdk/bin/dart"
        self.dart.parent.mkdir(parents=True)
        self.dart.write_bytes(b"dart")
        self.dart.chmod(0o700)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.work = self.root / "work"
        self.operations: list[str] = []
        self.events: list[str] = []

    def _native_audit(
        self, variant: str, reference: object
    ) -> dict[str, object]:
        gate = run_ios_reference_app_gate
        return {
            "signaturePolicy": (
                "ios-device-unsigned-development"
                if variant == "device"
                else "strict"
            ),
            "signatureStatus": "unsigned" if variant == "device" else "verified",
            "nativeInventory": _native_inventory_fixture(variant, reference),
        }

    def _invoke(
        self,
        *,
        simulator_state: str = "Booted",
        launch_error: BaseException | None = None,
        cleanup_error: BaseException | None = None,
        mutate_preinstall: bool = False,
        replacement_race: bool = False,
        boot_error: BaseException | None = None,
    ) -> dict[str, object]:
        gate = run_ios_reference_app_gate
        source_epoch = self.work / "source_epoch"
        device_app = self.work / "device/build/ios/iphoneos/Runner.app"
        simulator_app = (
            self.work / "simulator/build/ios/iphonesimulator/Runner.app"
        )
        device_tree = gate.TreeIdentity(
            {"Runner": {"sizeBytes": 1, "sha256": "1" * 64, "executable": True}},
            1,
            1,
            "1" * 64,
        )
        simulator_tree = gate.TreeIdentity(
            {"Runner": {"sizeBytes": 1, "sha256": "2" * 64, "executable": True}},
            1,
            1,
            "2" * 64,
        )
        changed_tree = simulator_tree._replace(tree_sha256="3" * 64)
        source_tree = gate.TreeIdentity(
            {"pubspec.yaml": {"sizeBytes": 1, "sha256": "4" * 64, "executable": False}},
            1,
            1,
            "4" * 64,
        )
        device_root = self.root / "CoreSimulator/Devices" / self.UDID
        device_root.mkdir(parents=True)
        installed = (
            device_root
            / "data/Containers/Bundle/Application"
            / "11111111-2222-3333-4444-555555555555"
            / "Runner.app"
        )
        data_container = (
            device_root
            / "data/Containers/Data/Application"
            / "22222222-3333-4444-5555-666666666666"
        )
        (data_container / "tmp").mkdir(parents=True)
        identity = gate.SimulatorIdentity(
            self.UDID,
            hashlib.sha256(self.UDID.encode("ascii")).hexdigest(),
            "iPhone 17 Pro",
            gate.VALIDATED_SIMULATOR_RUNTIME,
            simulator_state,
        )
        apple = gate.AppleEnvironment(
            gate.VALIDATED_XCODE_VERSION,
            gate.VALIDATED_XCODE_BUILD,
            gate.VALIDATED_IOS_SDK,
            gate.VALIDATED_IOS_SDK,
            gate.VALIDATED_MACOS_VERSION,
            gate.VALIDATED_MACOS_BUILD,
        )

        def snapshot(_: Path, destination: Path) -> tuple[Path, dict[str, object]]:
            destination.mkdir()
            (destination / "example").mkdir()
            (destination / "native").mkdir()
            return destination, {
                "manifestSha256": "4" * 64,
                **gate._tree_evidence(source_tree),
            }

        def prepare(**kwargs: object) -> tuple[dict[str, int], str]:
            destination = kwargs["destination"]
            assert isinstance(destination, Path)
            destination.mkdir()
            archive = kwargs["archive"]
            return {"fileCount": 1, "byteCount": 1}, (
                "5" * 64 if archive.variant == "device" else "6" * 64
            )

        def run_command(_: object, **kwargs: object) -> object:
            operation = str(kwargs.get("operation"))
            self.operations.append(operation)
            self.events.append(operation)
            if operation == "unsigned iOS arm64 device Release build":
                device_app.mkdir(parents=True)
            if operation == "iOS arm64 simulator Debug build":
                simulator_app.mkdir(parents=True)
            if operation == "simulator boot" and boot_error is not None:
                raise boot_error
            return gate._COMMON.CommandOutput(stdout="", stderr="")

        def tree_identity(path: Path, _: str, **__: object) -> object:
            if path.resolve() == source_epoch.resolve():
                return source_tree
            if path.resolve() == simulator_app.resolve():
                return changed_tree if mutate_preinstall else simulator_tree
            if path.resolve() == device_app.resolve():
                return device_tree
            raise AssertionError(f"unexpected identity path: {path}")

        def resolve_shim(**kwargs: object) -> object:
            archive = kwargs["archive"]
            work_directory = kwargs["work_directory"]
            assert isinstance(work_directory, Path)
            invocation_hash = (
                "0123456789" if archive.variant == "device" else "abcdef0123"
            )
            path = (
                work_directory
                / ".dart_tool/hooks_runner/shared/fonix/build"
                / invocation_hash
                / "libfonix_shim.dylib"
            )
            path.parent.mkdir(parents=True)
            path.write_bytes(archive.variant.encode())
            return gate.ReferenceShim(
                path,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                invocation_hash,
            )

        def audit(**kwargs: object) -> tuple[dict[str, object], object]:
            archive = kwargs["archive"]
            reference = kwargs["reference_shim"]
            if archive.variant == "device":
                return self._native_audit("device", reference), device_tree
            return self._native_audit("simulator", reference), simulator_tree

        def simulator_identity(_: str) -> object:
            self.events.append("state-refresh")
            return identity

        def launch(_: object, __: str) -> int:
            self.events.append("launch")
            if launch_error is not None:
                raise launch_error
            return self.PID

        def poll(*_: object, **__: object) -> tuple[dict[str, object], dict[str, object]]:
            return (
                gate._expected_reference_receipt(
                    _archive("simulator"), self.CHALLENGE, self.PID
                ),
                {
                    "sha256": "7" * 64,
                    "sizeBytes": 1,
                    "publication": "atomic-sandbox-file",
                },
            )

        def bind(*_: object) -> object:
            return gate.InstalledApplicationOwnership(installed, simulator_tree)

        def revalidate(*_: object) -> None:
            if replacement_race:
                raise gate.IosReferenceAppGateError(
                    "installed application ownership changed containers"
                )

        archives = {"device": _archive("device"), "simulator": _archive("simulator")}
        host = SimpleNamespace(
            basename="host.tgz", sha256="8" * 64, size_bytes=1
        )
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(gate.platform, "system", return_value="Darwin"))
            stack.enter_context(mock.patch.object(gate.platform, "machine", return_value="arm64"))
            stack.enter_context(
                mock.patch.object(
                    gate,
                    "_verify_apple_environment",
                    return_value=(
                        {"frameworkRevision": "revision", "frameworkVersion": "version"},
                        apple,
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_snapshot_source_epoch", side_effect=snapshot
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_tree_identity", side_effect=tree_identity
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_load_pinned_archives", return_value=archives
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate._COMMON, "_load_pinned_archive", return_value=host
                )
            )
            stack.enter_context(mock.patch.object(gate, "_prepare_variant", side_effect=prepare))
            stack.enter_context(mock.patch.object(gate, "_run", side_effect=run_command))
            stack.enter_context(
                mock.patch.object(
                    gate, "_resolve_reference_shim", side_effect=resolve_shim
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_audit_stable_application", side_effect=audit
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate,
                    "_canonical_simulator_device_root",
                    return_value=device_root,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_simulator_identity", side_effect=simulator_identity
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate,
                    "_verify_simulator_arm64",
                    return_value={
                        "advertisesArm64": True,
                        "explicitArm64CapabilityProcess": "passed",
                    },
                )
            )
            stack.enter_context(mock.patch.object(gate, "_require_package_absent"))
            stack.enter_context(mock.patch.object(gate, "_require_package_present"))
            stack.enter_context(
                mock.patch.object(
                    gate, "_installed_application_path", return_value=installed
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_bind_installed_application", side_effect=bind
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate,
                    "_simulator_data_container",
                    return_value=data_container,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate.secrets, "token_hex", return_value=self.CHALLENGE
                )
            )
            stack.enter_context(mock.patch.object(gate, "_require_receipt_paths_absent"))
            stack.enter_context(
                mock.patch.object(
                    gate, "_launch_simulator_application", side_effect=launch
                )
            )
            stack.enter_context(
                mock.patch.object(
                    gate, "_poll_reference_receipt", side_effect=poll
                )
            )
            stack.enter_context(mock.patch.object(gate, "_terminate_simulator_application"))
            stack.enter_context(
                mock.patch.object(
                    gate,
                    "_revalidate_installed_ownership",
                    side_effect=revalidate,
                )
            )
            if cleanup_error is not None:
                stack.enter_context(
                    mock.patch.object(
                        gate, "_cleanup_simulator", side_effect=cleanup_error
                    )
                )
            return gate.run_gate(
                repository=self.repository,
                flutter=self.flutter,
                artifact_cache=self.cache,
                simulator_udid=self.UDID,
                work_directory=self.work,
            )

    def test_run_gate_rereads_booted_state_and_never_shuts_down_user_boot(self) -> None:
        gate = run_ios_reference_app_gate
        report = self._invoke(simulator_state="Booted")
        self.assertNotIn("simulator boot", self.operations)
        self.assertNotIn("simulator shutdown", self.operations)
        self.assertEqual(
            self.operations.count(
                "incremental iOS arm64 simulator Debug rebuild"
            ),
            1,
        )
        self.assertGreater(
            self.events.index("state-refresh"),
            self.events.index("iOS arm64 simulator Debug build"),
        )
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(self.CHALLENGE, encoded)
        self.assertNotIn('"processId"', encoded)
        simulator = report["simulator"]
        assert isinstance(simulator, dict)
        self.assertNotIn("architecture", simulator)
        self.assertIn("simulatorArm64CapabilityProbe", simulator)
        installed_binding = simulator["installedApplicationBinding"]
        assert isinstance(installed_binding, dict)
        self.assertEqual(
            installed_binding["identityFormat"],
            "directory-paths-file-bytes-install-transport-executable-map-v1",
        )
        self.assertEqual(
            installed_binding["sourceApplicationTreeSha256"],
            simulator["applicationIdentity"]["treeSha256"],
        )
        normalization = installed_binding["transportNormalization"]
        assert isinstance(normalization, dict)
        self.assertEqual(
            normalization["paths"],
            sorted(gate._IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS),
        )
        claim = report["claimBoundary"]
        assert isinstance(claim, dict)
        does_not_prove = claim["doesNotProve"]
        assert isinstance(does_not_prove, list)
        self.assertIn("absence of runtime dlopen behavior", does_not_prove)
        self.assertIn(
            "absence of an extra static ONNX Runtime copy in another Mach-O",
            does_not_prove,
        )
        self.assertIn(
            "that the selected static archive was linked exactly once",
            does_not_prove,
        )

    def test_run_gate_failed_boot_never_claims_shutdown_ownership(self) -> None:
        gate = run_ios_reference_app_gate
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "boot failed"):
            self._invoke(
                simulator_state="Shutdown",
                boot_error=gate.IosReferenceAppGateError("boot failed"),
            )
        self.assertNotIn("simulator shutdown", self.operations)

    def test_run_gate_launch_failure_still_uninstalls_owned_app(self) -> None:
        gate = run_ios_reference_app_gate
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "launch failed"):
            self._invoke(
                launch_error=gate.IosReferenceAppGateError("launch failed")
            )
        self.assertIn("simulator reference-app uninstall", self.operations)

    def test_run_gate_preserves_body_and_cleanup_failures(self) -> None:
        gate = run_ios_reference_app_gate
        primary = gate.IosReferenceAppGateError("launch failed")
        cleanup = gate.IosReferenceAppGateError("cleanup failed")
        with self.assertRaises(gate.IosReferenceLifecycleError) as captured:
            self._invoke(launch_error=primary, cleanup_error=cleanup)
        self.assertIs(captured.exception.primary_error, primary)
        self.assertIs(captured.exception.cleanup_error, cleanup)

    def test_run_gate_rejects_post_audit_local_mutation_before_install(self) -> None:
        gate = run_ios_reference_app_gate
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "after audit"):
            self._invoke(mutate_preinstall=True)
        self.assertNotIn("simulator reference-app install", self.operations)

    def test_run_gate_refuses_post_install_replacement_before_uninstall(self) -> None:
        gate = run_ios_reference_app_gate
        with self.assertRaises(gate.IosReferenceLifecycleError):
            self._invoke(replacement_race=True)
        self.assertNotIn("simulator reference-app uninstall", self.operations)


class IosLifecycleSettlementTest(unittest.TestCase):
    def test_dual_failure_retains_primary_and_cleanup_errors(self) -> None:
        gate = run_ios_reference_app_gate
        primary = gate.IosReferenceAppGateError("launch failed")
        cleanup = gate.IosReferenceAppGateError("uninstall failed")

        def body() -> None:
            raise primary

        def settle() -> None:
            raise cleanup

        with self.assertRaises(gate.IosReferenceLifecycleError) as captured:
            gate._run_owned_lifecycle(body, settle)
        self.assertIs(captured.exception.primary_error, primary)
        self.assertIs(captured.exception.cleanup_error, cleanup)
        self.assertEqual(
            str(captured.exception),
            "simulator work and owned-state cleanup both failed",
        )
        self.assertIs(captured.exception.__cause__, cleanup)

    def test_dual_failure_message_never_exposes_unbounded_private_details(self) -> None:
        gate = run_ios_reference_app_gate
        private = "/private/user/path/" + "x" * 100_000
        primary = gate.IosReferenceAppGateError(private)
        cleanup = gate.IosReferenceAppGateError(private)

        with self.assertRaises(gate.IosReferenceLifecycleError) as captured:
            gate._run_owned_lifecycle(
                lambda: (_ for _ in ()).throw(primary),
                lambda: (_ for _ in ()).throw(cleanup),
            )
        rendered = str(captured.exception)
        self.assertLess(len(rendered), 128)
        self.assertNotIn("/private/user/path", rendered)
        self.assertIs(captured.exception.primary_error, primary)
        self.assertIs(captured.exception.cleanup_error, cleanup)

    def test_launch_failure_still_runs_cleanup(self) -> None:
        gate = run_ios_reference_app_gate
        calls: list[str] = []

        def body() -> None:
            calls.append("launch")
            raise gate.IosReferenceAppGateError("launch failed")

        def settle() -> None:
            calls.append("cleanup")

        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "launch failed"):
            gate._run_owned_lifecycle(body, settle)
        self.assertEqual(calls, ["launch", "cleanup"])


class IosAuditBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.application = Path(self.temporary.name) / "Runner.app"
        self.reference_path = (
            Path(self.temporary.name)
            / "work/.dart_tool/hooks_runner/shared/fonix/build"
            / "0123456789/libfonix_shim.dylib"
        )
        self.reference_path.parent.mkdir(parents=True)
        self.reference_path.write_bytes(b"reference shim")
        self.reference = run_ios_reference_app_gate.ReferenceShim(
            self.reference_path,
            hashlib.sha256(b"reference shim").hexdigest(),
            "0123456789",
        )

    def test_stable_audit_rejects_post_audit_application_mutation(self) -> None:
        gate = run_ios_reference_app_gate
        self.application.mkdir()
        (self.application / "Runner").write_bytes(b"runner")
        (self.application / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleExecutable": gate.APPLICATION_EXECUTABLE_NAME,
                    "CFBundleIdentifier": gate.APPLICATION_BUNDLE_IDENTIFIER,
                    "MinimumOSVersion": gate.APPLICATION_MINIMUM_OS,
                }
            )
        )

        def mutate(**_: object) -> dict[str, object]:
            (self.application / "Runner").write_bytes(b"mutated")
            return {}

        with mock.patch.object(gate, "_audit_application", side_effect=mutate):
            with self.assertRaisesRegex(gate.IosReferenceAppGateError, "changed during"):
                gate._audit_stable_application(
                    repository=Path(self.temporary.name),
                    application=self.application,
                    archive=_archive("device"),
                    reference_shim=self.reference,
                )

    def _base(self, variant: str) -> dict[str, object]:
        gate = run_ios_reference_app_gate
        platform = "ios-device" if variant == "device" else "ios-simulator"
        archive = _archive(variant)
        native = _native_inventory_fixture(variant, self.reference)
        build_manifest = {
            "schemaVersion": 3,
            "nativeIdentity": "fonix_shim",
            "shimAbiVersion": 1,
            "requiredOrtApiVersion": 27,
            "runtimeProfile": "linked",
            "androidRuntimeOwner": None,
            "allowedRuntimeSources": ["linked"],
            "buildId": archive.artifact_id,
            "artifact": {
                "id": archive.artifact_id,
                "lockSha256": "d" * 64,
                "sourceSha256": archive.sha256,
                "targetOs": "ios",
                "targetArchitecture": "arm64",
                "targetVariant": variant,
                "minimumOs": gate.APPLICATION_MINIMUM_OS,
                "flavor": "cpu",
                "runtimeMode": "linked",
                "thirdPartyNoticesSha256": "e" * 64,
                "providers": [{"wrapperId": "cpu", "reportedName": "CPUExecutionProvider"}],
            },
        }
        return {
            "platform": platform,
            "application": str(self.application),
            "artifactId": gate.ARTIFACT_IDS[variant],
            "lockedMinimumOs": gate.APPLICATION_MINIMUM_OS,
            "declaredMinimumOs": gate.APPLICATION_MINIMUM_OS,
            "plistMinimumOs": gate.APPLICATION_MINIMUM_OS,
            "machOBinaries": gate._expected_macho_paths(self.application),
            "buildManifest": build_manifest,
            "nativeInventory": native,
        }

    def _device(self) -> dict[str, object]:
        value = self._base("device")
        value.update(
            {
                "signaturePolicy": "ios-device-unsigned-development",
                "signatureStatus": "unsigned",
                "claimStatus": "static-only",
                "signatureDetails": {
                    "rootBundle": "unsigned",
                    "rootExecutable": "unsigned",
                    "provisioningProfile": "absent",
                    "nestedFrameworks": [
                        {
                            "path": "Frameworks/App.framework",
                            "binary": "Frameworks/App.framework/App",
                            "status": "verified-adhoc",
                            "teamIdentifier": None,
                            "authorities": [],
                        },
                        {
                            "path": "Frameworks/Flutter.framework",
                            "binary": "Frameworks/Flutter.framework/Flutter",
                            "status": "verified-adhoc",
                            "teamIdentifier": None,
                            "authorities": [],
                        },
                        {
                            "path": "Frameworks/fonix_shim.framework",
                            "binary": "Frameworks/fonix_shim.framework/fonix_shim",
                            "status": "verified-adhoc",
                            "teamIdentifier": None,
                            "authorities": [],
                        },
                    ],
                },
            }
        )
        return value

    def test_device_binding_requires_unsigned_and_linked_ownership_details(self) -> None:
        value = self._device()
        self.assertIs(
            run_ios_reference_app_gate._validate_audit_binding(
                value,
                application=self.application,
                archive=_archive("device"),
                unsigned_device=True,
                reference_shim=self.reference,
            ),
            value,
        )

        tampered = copy.deepcopy(value)
        inventory = tampered["nativeInventory"]
        assert isinstance(inventory, dict)
        runtime = inventory["linkedRuntimeIdentity"]
        assert isinstance(runtime, dict)
        runtime["separatelyPackagedOrtMachOs"] = [
            "Frameworks/onnxruntime.framework/onnxruntime"
        ]
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "linked-runtime identity",
        ):
            run_ios_reference_app_gate._validate_audit_binding(
                tampered,
                application=self.application,
                archive=_archive("device"),
                unsigned_device=True,
                reference_shim=self.reference,
            )

    def test_binding_pins_each_platform_transformation_boundary(self) -> None:
        gate = run_ios_reference_app_gate
        for variant in ("device", "simulator"):
            with self.subTest(variant=variant):
                inventory = _native_inventory_fixture(variant, self.reference)
                self.assertIs(
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive(variant),
                        reference_shim=self.reference,
                    ),
                    inventory,
                )
                linked = inventory["linkedRuntimeIdentity"]
                assert isinstance(linked, dict)
                transformations = linked["accountedTransformations"]
                assert isinstance(transformations, list)
                transformations.reverse()
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError,
                    "linked-runtime identity",
                ):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive(variant),
                        reference_shim=self.reference,
                    )

    def test_normalized_runtime_identity_pins_are_workdir_independent(self) -> None:
        gate = run_ios_reference_app_gate
        self.assertEqual(
            gate._IOS_NORMALIZED_RUNTIME_FIELDS_SHA256,
            {
                "device": (
                    "268705a45b764c01370f6e78b5e28d7d403dac008ee685391b666f9d1a3880ab"
                ),
                "simulator": (
                    "303896da46bab6fef75f29e4a3ff15a9f6b0042ba06187940e086090114c7892"
                ),
            },
        )
        self.assertEqual(
            gate._IOS_NORMALIZED_COMPARISON_SCOPE,
            (
                "Mach header CPU/subtype/filetype/flags, pair-matched LC_UUID, "
                "non-LINKEDIT loadable segment and section metadata/bytes/padding "
                "outside the mutable load-command region, retained external/undefined "
                "symbols and string semantics, canonical LC_DYSYMTAB table payloads, "
                "immutable load commands, and referenced dyld fixup/export payloads"
            ),
        )

    def test_binding_rejects_each_hook_metadata_mismatch(self) -> None:
        gate = run_ios_reference_app_gate
        mutations = {
            "referenceShim": lambda linked: linked.__setitem__(
                "referenceShim", "/private/tampered/reference.dylib"
            ),
            "invocationId": lambda linked: linked[
                "hookInvocationMetadata"
            ].__setitem__("invocationId", "ffffffffff"),
            "input": lambda linked: linked["hookInvocationMetadata"].__setitem__(
                "input", "/private/tampered/input.json"
            ),
            "output": lambda linked: linked["hookInvocationMetadata"].__setitem__(
                "output", "/private/tampered/output.json"
            ),
            "status": lambda linked: linked["hookInvocationMetadata"].__setitem__(
                "status", "unchecked"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                inventory = _native_inventory_fixture("device", self.reference)
                linked = inventory["linkedRuntimeIdentity"]
                assert isinstance(linked, dict)
                mutate(linked)
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError,
                    "linked-runtime identity",
                ):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive("device"),
                        reference_shim=self.reference,
                    )

    def test_native_evidence_omits_private_hook_paths(self) -> None:
        gate = run_ios_reference_app_gate
        inventory = _native_inventory_fixture("device", self.reference)
        linked = inventory["linkedRuntimeIdentity"]
        assert isinstance(linked, dict)
        hook = linked["hookInvocationMetadata"]
        assert isinstance(hook, dict)

        evidence = gate._native_inventory_evidence(
            {"nativeInventory": inventory}
        )
        encoded = json.dumps(evidence, sort_keys=True)

        self.assertNotIn(str(self.reference.path), encoded)
        self.assertNotIn(str(hook["input"]), encoded)
        self.assertNotIn(str(hook["output"]), encoded)
        self.assertNotIn(self.temporary.name, encoded)
        public_linked = evidence["linkedRuntimeIdentity"]
        assert isinstance(public_linked, dict)
        self.assertEqual(
            public_linked["hookInvocationMetadata"],
            {"invocationId": self.reference.invocation_hash, "status": "validated"},
        )

    def test_binding_rejects_each_richer_macho_record_tamper(self) -> None:
        gate = run_ios_reference_app_gate

        def mutate_dependency(records: list[dict[str, object]]) -> None:
            dependencies = records[0]["dylibDependencies"]
            assert isinstance(dependencies, list)
            dependency = dependencies[0]
            assert isinstance(dependency, dict)
            dependency["currentVersion"] = 2

        mutations = {
            "cpuSubtype": lambda records: records[0].__setitem__(
                "cpuSubtype", 1
            ),
            "fileType": lambda records: records[0].__setitem__("fileType", 2),
            "headerFlags": lambda records: records[0].__setitem__(
                "headerFlags", 0
            ),
            "dylibId": lambda records: records[0].__setitem__(
                "dylibId", "@rpath/tampered"
            ),
            "dylibDependencies": mutate_dependency,
            "dependencyMetadataSha256": lambda records: records[0].__setitem__(
                "dependencyMetadataSha256", "f" * 64
            ),
            "loadCommandKindsSha256": lambda records: records[0].__setitem__(
                "loadCommandKindsSha256", "F" * 64
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                inventory = _native_inventory_fixture("device", self.reference)
                records = inventory["machOBinaries"]
                assert isinstance(records, list)
                mutate(records)
                with self.assertRaises(gate.IosReferenceAppGateError):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive("device"),
                        reference_shim=self.reference,
                    )

        inventory = _native_inventory_fixture("device", self.reference)
        records = inventory["machOBinaries"]
        assert isinstance(records, list)
        records[0]["unexpectedField"] = "tampered"
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError, "unexpected shape"
        ):
            gate._validate_native_inventory(
                inventory,
                archive=_archive("device"),
                reference_shim=self.reference,
            )

    def test_binding_rejects_dylib_dependency_schema_or_path_drift(self) -> None:
        gate = run_ios_reference_app_gate
        for field, value in (
            ("kind", "unknown"),
            ("currentVersion", True),
            ("compatibilityVersion", -1),
        ):
            with self.subTest(field=field):
                inventory = _native_inventory_fixture("device", self.reference)
                records = inventory["machOBinaries"]
                assert isinstance(records, list)
                dependencies = records[0]["dylibDependencies"]
                assert isinstance(dependencies, list)
                dependency = dependencies[0]
                assert isinstance(dependency, dict)
                dependency[field] = value
                records[0]["dependencyMetadataSha256"] = (
                    gate._canonical_json_sha256(dependencies)
                )
                with self.assertRaises(gate.IosReferenceAppGateError):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive("device"),
                        reference_shim=self.reference,
                    )

        inventory = _native_inventory_fixture("device", self.reference)
        records = inventory["machOBinaries"]
        assert isinstance(records, list)
        records[0]["dynamicDependencies"] = ["/usr/lib/libobjc.A.dylib"]
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError, "dependency paths"
        ):
            gate._validate_native_inventory(
                inventory,
                archive=_archive("device"),
                reference_shim=self.reference,
            )

    def test_binding_rejects_dylib_id_versions_and_rpath_order_tamper(self) -> None:
        gate = run_ios_reference_app_gate
        for variant in ("device", "simulator"):
            for field in (
                "timestamp",
                "currentVersion",
                "compatibilityVersion",
            ):
                with self.subTest(variant=variant, dylib_id_field=field):
                    inventory = _native_inventory_fixture(variant, self.reference)
                    records = inventory["machOBinaries"]
                    assert isinstance(records, list)
                    dylib_id = records[0]["dylibId"]
                    assert isinstance(dylib_id, dict)
                    value = dylib_id[field]
                    assert isinstance(value, int)
                    dylib_id[field] = value + 1
                    with self.assertRaisesRegex(
                        gate.IosReferenceAppGateError, "dylib ID"
                    ):
                        gate._validate_native_inventory(
                            inventory,
                            archive=_archive(variant),
                            reference_shim=self.reference,
                        )

            for record_index in (0, 3):
                with self.subTest(variant=variant, rpath_record=record_index):
                    inventory = _native_inventory_fixture(variant, self.reference)
                    records = inventory["machOBinaries"]
                    assert isinstance(records, list)
                    rpaths = records[record_index]["rpaths"]
                    assert isinstance(rpaths, list)
                    rpaths.reverse()
                    with self.assertRaisesRegex(
                        gate.IosReferenceAppGateError, "RPATH order"
                    ):
                        gate._validate_native_inventory(
                            inventory,
                            archive=_archive(variant),
                            reference_shim=self.reference,
                        )

    def test_binding_rejects_nlist_or_dyld_export_drift(self) -> None:
        gate = run_ios_reference_app_gate
        for field, value in (
            ("nlistSymbolSetSha256", "not-a-digest"),
            ("dyldExportSetSha256", "d" * 64),
        ):
            with self.subTest(field=field):
                inventory = _native_inventory_fixture("device", self.reference)
                exports = inventory["shimExports"]
                assert isinstance(exports, dict)
                exports[field] = value
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError, "shim export evidence"
                ):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive("device"),
                        reference_shim=self.reference,
                    )

    def test_binding_rejects_linked_identity_overclaims(self) -> None:
        gate = run_ios_reference_app_gate
        mutations = {
            "separatelyPackagedOrtMachOs": ["Frameworks/onnxruntime"],
            "auditedOrtLoadCommandDependencies": ["@rpath/onnxruntime"],
            "runtimeDlopenBehavior": "proved-absent",
            "otherMachOStaticOrtCopies": "proved-absent",
            "staticArchiveMultiplicity": "exactly-once",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                inventory = _native_inventory_fixture("device", self.reference)
                linked = inventory["linkedRuntimeIdentity"]
                assert isinstance(linked, dict)
                linked[field] = value
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError, "linked-runtime identity"
                ):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive("device"),
                        reference_shim=self.reference,
                    )

    def test_binding_rejects_richer_linked_identity_drift(self) -> None:
        gate = run_ios_reference_app_gate
        mutations = {
            "normalizedRuntimeFields": "unchecked",
            "normalizedRuntimeFieldsSha256": "d" * 64,
            "comparisonScope": "headers only",
            "nlistAndDyldExports": "unchecked",
            "claimBoundary": "overclaim",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                inventory = _native_inventory_fixture("device", self.reference)
                linked = inventory["linkedRuntimeIdentity"]
                assert isinstance(linked, dict)
                linked[field] = value
                with self.assertRaisesRegex(
                    gate.IosReferenceAppGateError, "linked-runtime identity"
                ):
                    gate._validate_native_inventory(
                        inventory,
                        archive=_archive("device"),
                        reference_shim=self.reference,
                    )

        inventory = _native_inventory_fixture("device", self.reference)
        linked = inventory["linkedRuntimeIdentity"]
        assert isinstance(linked, dict)
        linked["unexpectedField"] = "tampered"
        with self.assertRaisesRegex(
            gate.IosReferenceAppGateError, "unexpected shape"
        ):
            gate._validate_native_inventory(
                inventory,
                archive=_archive("device"),
                reference_shim=self.reference,
            )

    def test_native_evidence_digest_covers_every_macho_record_field(self) -> None:
        gate = run_ios_reference_app_gate
        original = _native_inventory_fixture("device", self.reference)
        changed = copy.deepcopy(original)
        records = changed["machOBinaries"]
        assert isinstance(records, list)
        records[0]["loadCommandKindsSha256"] = "f" * 64

        original_evidence = gate._native_inventory_evidence(
            {"nativeInventory": original}
        )
        changed_evidence = gate._native_inventory_evidence(
            {"nativeInventory": changed}
        )
        original_machos = original_evidence["machOBinaries"]
        changed_machos = changed_evidence["machOBinaries"]
        assert isinstance(original_machos, dict)
        assert isinstance(changed_machos, dict)
        self.assertNotEqual(
            original_machos["sha256"], changed_machos["sha256"]
        )

    def test_device_binding_rejects_nested_signature_reordering(self) -> None:
        value = self._device()
        details = value["signatureDetails"]
        assert isinstance(details, dict)
        frameworks = details["nestedFrameworks"]
        assert isinstance(frameworks, list)
        frameworks.reverse()
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "unsigned static-only policy",
        ):
            run_ios_reference_app_gate._validate_audit_binding(
                value,
                application=self.application,
                archive=_archive("device"),
                unsigned_device=True,
                reference_shim=self.reference,
            )

    def test_simulator_binding_requires_strict_signature(self) -> None:
        value = self._base("simulator")
        value.update(
            {
                "signaturePolicy": "strict",
                "signatureStatus": "verified",
                "claimStatus": "signature-verified-package-audit",
                "signatureDetails": {
                    "rootBundle": "verified",
                    "rootExecutable": "verified",
                    "verifiedCodeObjects": sorted(
                        [
                            *run_ios_reference_app_gate._IOS_MACHO_RELATIVE_PATHS,
                            *(
                                item["path"]
                                for item in run_ios_reference_app_gate._IOS_FRAMEWORK_INVENTORY
                            ),
                        ]
                    ),
                },
            }
        )
        self.assertIs(
            run_ios_reference_app_gate._validate_audit_binding(
                value,
                application=self.application,
                archive=_archive("simulator"),
                unsigned_device=False,
                reference_shim=self.reference,
            ),
            value,
        )
        value["signatureStatus"] = "unsigned"
        with self.assertRaisesRegex(
            run_ios_reference_app_gate.IosReferenceAppGateError,
            "strict signature",
        ):
            run_ios_reference_app_gate._validate_audit_binding(
                value,
                application=self.application,
                archive=_archive("simulator"),
                unsigned_device=False,
                reference_shim=self.reference,
            )

    def test_binding_rejects_x86_universal_or_incomplete_macho_reports(self) -> None:
        gate = run_ios_reference_app_gate
        for architecture in ("x86_64", "universal"):
            with self.subTest(architecture=architecture):
                value = self._device()
                inventory = value["nativeInventory"]
                assert isinstance(inventory, dict)
                records = inventory["machOBinaries"]
                assert isinstance(records, list)
                records[0]["architecture"] = architecture
                with self.assertRaisesRegex(gate.IosReferenceAppGateError, "arm64"):
                    gate._validate_audit_binding(
                        value,
                        application=self.application,
                        archive=_archive("device"),
                        unsigned_device=True,
                        reference_shim=self.reference,
                    )
        value = self._device()
        inventory = value["nativeInventory"]
        assert isinstance(inventory, dict)
        records = inventory["machOBinaries"]
        assert isinstance(records, list)
        records.pop()
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "four records"):
            gate._validate_audit_binding(
                value,
                application=self.application,
                archive=_archive("device"),
                unsigned_device=True,
                reference_shim=self.reference,
            )

    def test_binding_rejects_build_manifest_architecture_tamper(self) -> None:
        gate = run_ios_reference_app_gate
        value = self._device()
        build = value["buildManifest"]
        assert isinstance(build, dict)
        artifact = build["artifact"]
        assert isinstance(artifact, dict)
        artifact["targetArchitecture"] = "x86_64"
        with self.assertRaisesRegex(gate.IosReferenceAppGateError, "arm64 linked"):
            gate._validate_audit_binding(
                value,
                application=self.application,
                archive=_archive("device"),
                unsigned_device=True,
                reference_shim=self.reference,
            )


if __name__ == "__main__":
    unittest.main()
