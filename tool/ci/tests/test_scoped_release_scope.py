from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import hashlib
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


REPOSITORY = Path(__file__).resolve().parents[3]
CI_DIRECTORY = REPOSITORY / "tool/ci"
if str(CI_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(CI_DIRECTORY))

import generate_release_sbom as release_evidence


SCRIPT = CI_DIRECTORY / "validate_scoped_release_scope.py"
POLICY = REPOSITORY / "release/scoped-pre-1.0-v1.json"
SCHEMA = REPOSITORY / "templates/ci/scoped_release_scope.schema.json"
NATIVE_LOCK = REPOSITORY / "native/versions.lock.yaml"
PUBSPEC = REPOSITORY / "pubspec.yaml"
SHERPA_PUBSPEC_LOCK = (
    REPOSITORY / "templates/android/sherpa_reference_app/pubspec.lock"
)
RELEASE_EVIDENCE_HELPER = CI_DIRECTORY / "generate_release_sbom.py"
SHERPA_LOCK_HELPER = CI_DIRECTORY / "validate_android_load_order_receipt.py"
SOURCE_CHECKSUM_HELPER = CI_DIRECTORY / "source_checksum_manifest.py"


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class _ScopeFixture:
    repository: Path
    scope: Path
    lock: Path
    pubspec: Path
    sherpa_pubspec_lock: Path
    release_evidence_helper: Path
    sherpa_lock_helper: Path
    source_checksum_helper: Path
    schema: Path


class ScopedReleaseScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name).resolve()
        self.fixture = self._copy_fixture(self.root / "fixture")
        self.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.output_index = 0

    def _copy_fixture(self, destination: Path) -> _ScopeFixture:
        repository = destination / "repository"
        scope = repository / "release/scoped-pre-1.0-v1.json"
        lock = repository / "native/versions.lock.yaml"
        pubspec = repository / "pubspec.yaml"
        sherpa_pubspec_lock = (
            repository / "templates/android/sherpa_reference_app/pubspec.lock"
        )
        release_evidence_helper = repository / "tool/ci/generate_release_sbom.py"
        sherpa_lock_helper = (
            repository / "tool/ci/validate_android_load_order_receipt.py"
        )
        source_checksum_helper = repository / "tool/ci/source_checksum_manifest.py"
        schema = repository / "templates/ci/scoped_release_scope.schema.json"
        for path in (
            scope,
            lock,
            pubspec,
            sherpa_pubspec_lock,
            release_evidence_helper,
            sherpa_lock_helper,
            source_checksum_helper,
            schema,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        scope.write_bytes(POLICY.read_bytes())
        lock.write_bytes(NATIVE_LOCK.read_bytes())
        pubspec.write_bytes(PUBSPEC.read_bytes())
        sherpa_pubspec_lock.write_bytes(SHERPA_PUBSPEC_LOCK.read_bytes())
        release_evidence_helper.write_bytes(RELEASE_EVIDENCE_HELPER.read_bytes())
        sherpa_lock_helper.write_bytes(SHERPA_LOCK_HELPER.read_bytes())
        source_checksum_helper.write_bytes(SOURCE_CHECKSUM_HELPER.read_bytes())
        schema.write_bytes(SCHEMA.read_bytes())
        return _ScopeFixture(
            repository=repository,
            scope=scope,
            lock=lock,
            pubspec=pubspec,
            sherpa_pubspec_lock=sherpa_pubspec_lock,
            release_evidence_helper=release_evidence_helper,
            sherpa_lock_helper=sherpa_lock_helper,
            source_checksum_helper=source_checksum_helper,
            schema=schema,
        )

    def _run(
        self,
        *,
        fixture: _ScopeFixture | None = None,
        scope: Path | None = None,
        repository: Path | None = None,
        output: Path | None = None,
        output_argument: str | None = None,
        script: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        selected = fixture or self.fixture
        self.output_index += 1
        selected_output = output or self.root / f"validation-{self.output_index}.json"
        arguments = [
            sys.executable,
            str(script or SCRIPT),
            "--repository",
            str(repository or selected.repository),
            "--scope",
            str(scope or selected.scope),
            "--output",
            output_argument or str(selected_output),
        ]
        result = subprocess.run(
            arguments,
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )
        return result, selected_output

    def _assert_rejected(
        self,
        *,
        fixture: _ScopeFixture | None = None,
        scope: Path | None = None,
        repository: Path | None = None,
        output: Path | None = None,
        output_argument: str | None = None,
        script: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result, selected_output = self._run(
            fixture=fixture,
            scope=scope,
            repository=repository,
            output=output,
            output_argument=output_argument,
            script=script,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("scoped release scope error:", result.stderr)
        if output is None or not output.exists():
            self.assertFalse(selected_output.exists())
        return result

    def _write_policy(self, policy: dict[str, object]) -> None:
        _write_json(self.fixture.scope, policy)

    def _target(self, policy: dict[str, object], operating_system: str) -> dict[str, object]:
        targets = policy["targets"]
        assert isinstance(targets, list)
        matches = [
            target
            for target in targets
            if isinstance(target, dict) and target.get("os") == operating_system
        ]
        self.assertTrue(matches, f"missing {operating_system} target fixture")
        return matches[0]

    def _android_target(self, policy: dict[str, object]) -> dict[str, object]:
        targets = policy["targets"]
        assert isinstance(targets, list)
        return next(
            target
            for target in targets
            if isinstance(target, dict)
            and target.get("os") == "android"
            and target.get("architecture") == "arm64-v8a"
        )

    def _composition(
        self, target: dict[str, object], kind: str
    ) -> dict[str, object]:
        compositions = target["compositions"]
        assert isinstance(compositions, list)
        return next(
            composition
            for composition in compositions
            if isinstance(composition, dict) and composition.get("kind") == kind
        )

    def _reset_inputs(self) -> None:
        self.fixture.scope.write_bytes(POLICY.read_bytes())
        self.fixture.lock.write_bytes(NATIVE_LOCK.read_bytes())
        self.fixture.pubspec.write_bytes(PUBSPEC.read_bytes())
        self.fixture.sherpa_pubspec_lock.write_bytes(
            SHERPA_PUBSPEC_LOCK.read_bytes()
        )
        self.fixture.release_evidence_helper.write_bytes(
            RELEASE_EVIDENCE_HELPER.read_bytes()
        )
        self.fixture.sherpa_lock_helper.write_bytes(
            SHERPA_LOCK_HELPER.read_bytes()
        )
        self.fixture.source_checksum_helper.write_bytes(
            SOURCE_CHECKSUM_HELPER.read_bytes()
        )

    def test_accepts_exact_cpu_scope_and_emits_path_free_deterministic_record(
        self,
    ) -> None:
        first, first_output = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)

        second_fixture = self._copy_fixture(self.root / "different-absolute-root")
        second, second_output = self._run(fixture=second_fixture)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_output.read_bytes(), second_output.read_bytes())

        raw = first_output.read_bytes()
        record = json.loads(raw)
        self.assertEqual(
            set(record),
            {
                "schemaVersion",
                "result",
                "policyId",
                "claimStatus",
                "validatorSha256",
                "schemaSha256",
                "scopeSha256",
                "nativeLockSha256",
                "sherpaPubspecLockSha256",
                "packageName",
                "packageVersion",
                "selectedTargetKeys",
                "selectedCompositionIds",
                "unsupportedTargetKeys",
                "deferredCapabilityIds",
                "claimBoundary",
            },
        )
        self.assertEqual(record["schemaVersion"], 1)
        self.assertEqual(record["result"], "validated")
        self.assertEqual(record["policyId"], "scoped-pre-1.0-cpu-v1")
        self.assertEqual(record["claimStatus"], "scope-only")
        self.assertEqual(record["validatorSha256"], _sha256(SCRIPT.read_bytes()))
        self.assertEqual(record["packageName"], "fonix")
        self.assertEqual(record["packageVersion"], "0.1.0-dev.1")
        self.assertEqual(record["scopeSha256"], _sha256(POLICY.read_bytes()))
        self.assertEqual(record["nativeLockSha256"], _sha256(NATIVE_LOCK.read_bytes()))
        self.assertEqual(
            record["sherpaPubspecLockSha256"],
            _sha256(SHERPA_PUBSPEC_LOCK.read_bytes()),
        )
        self.assertEqual(record["schemaSha256"], _sha256(SCHEMA.read_bytes()))
        self.assertEqual(
            record["deferredCapabilityIds"],
            [
                "android-qnn",
                "windows-target-host-provider-final-package-installer-clean-machine",
            ],
        )
        self.assertEqual(
            record["selectedTargetKeys"],
            [
                "ios/arm64/device/cpu",
                "macos/arm64/default/cpu",
                "android/arm64-v8a/default/cpu",
                "linux/x86_64/default/cpu",
            ],
        )
        self.assertEqual(
            record["selectedCompositionIds"],
            [
                "ios-arm64-device-cpu-linked",
                "macos-arm64-default-cpu-bundled",
                "android-arm64-v8a-default-cpu-bundled",
                "android-arm64-v8a-default-cpu-sherpa-process",
                "linux-x86_64-default-cpu-bundled",
            ],
        )
        self.assertEqual(
            record["unsupportedTargetKeys"],
            [
                "ios/arm64/simulator/cpu",
                "android/x86_64/default/cpu",
                "linux/arm64/default/cpu",
                "windows/x64/default/cpu",
            ],
        )
        self.assertEqual(
            record["claimBoundary"],
            "Scope validation proves only that the policy is closed and matches "
            "the current package/native-lock baseline. It is not release readiness, "
            "approval, signing evidence, or authorization to publish or distribute.",
        )
        decoded = raw.decode("utf-8")
        self.assertNotIn(str(self.root), decoded)
        self.assertNotIn(str(REPOSITORY), decoded)

    def test_schema_top_level_const_is_the_canonical_policy(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertIn("const", schema)
        self.assertEqual(schema["const"], policy)

    def test_rejects_duplicate_nonfinite_and_unknown_policy_members(self) -> None:
        cases: tuple[tuple[str, bytes], ...] = (
            (
                "duplicate",
                b'{"schemaVersion":1,"schemaVersion":1}\n',
            ),
            ("nonfinite", b'{"schemaVersion":NaN}\n'),
        )
        for name, raw in cases:
            with self.subTest(name=name):
                self.fixture.scope.write_bytes(raw)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_boolean_integer_confusion_and_control_characters(self) -> None:
        def schema_boolean(value: dict[str, object]) -> None:
            value["schemaVersion"] = True

        def abi_boolean(value: dict[str, object]) -> None:
            value["baseline"]["shimAbi"] = True

        def api_boolean(value: dict[str, object]) -> None:
            value["baseline"]["requiredOrtApi"] = True

        def control_character(value: dict[str, object]) -> None:
            value["deferredCapabilities"][0]["reason"] = "deferred\u001b[31m"

        for name, mutate in (
            ("schema-boolean", schema_boolean),
            ("abi-boolean", abi_boolean),
            ("api-boolean", api_boolean),
            ("control-character", control_character),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

        unknown_cases = (
            lambda value: value.update({"unexpected": True}),
            lambda value: value["baseline"].update({"unexpected": True}),
            lambda value: self._composition(
                self._target(value, "macos"), "locked-artifact"
            ).update({"unexpected": True}),
        )
        for index, mutate in enumerate(unknown_cases):
            with self.subTest(unknown=index):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_missing_duplicate_extra_and_reordered_targets(self) -> None:
        def missing(value: dict[str, object]) -> None:
            value["targets"].pop()

        def duplicate(value: dict[str, object]) -> None:
            value["targets"][-1] = copy.deepcopy(value["targets"][0])

        def extra(value: dict[str, object]) -> None:
            target = copy.deepcopy(value["targets"][-1])
            target["architecture"] = "arm64"
            value["targets"].append(target)

        def reordered(value: dict[str, object]) -> None:
            value["targets"][0], value["targets"][1] = (
                value["targets"][1],
                value["targets"][0],
            )

        for name, mutate in (
            ("missing", missing),
            ("duplicate", duplicate),
            ("extra", extra),
            ("reordered", reordered),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_zero_selected_targets_and_changed_deferred_inventory(self) -> None:
        def zero_selected(value: dict[str, object]) -> None:
            for target in value["targets"]:
                if target["disposition"] == "selected":
                    target["disposition"] = "unsupported"
                    target.pop("compositions")
                    target["reason"] = "Deliberately removed from the candidate."

        def missing_deferred(value: dict[str, object]) -> None:
            value["deferredCapabilities"].pop()

        def duplicate_deferred(value: dict[str, object]) -> None:
            value["deferredCapabilities"][1] = copy.deepcopy(
                value["deferredCapabilities"][0]
            )

        def reordered_deferred(value: dict[str, object]) -> None:
            capabilities = value["deferredCapabilities"]
            capabilities[0], capabilities[1] = capabilities[1], capabilities[0]

        for name, mutate in (
            ("zero-selected", zero_selected),
            ("missing-deferred", missing_deferred),
            ("duplicate-deferred", duplicate_deferred),
            ("reordered-deferred", reordered_deferred),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_self_approval_and_readiness_fields(self) -> None:
        for field, value in (
            ("ready", True),
            ("approved", True),
            ("approvedBy", "self"),
            ("publicationAuthorized", True),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.policy)
                candidate[field] = value
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_duplicate_and_reordered_android_compositions(self) -> None:
        def duplicate_id(value: dict[str, object]) -> None:
            compositions = self._android_target(value)["compositions"]
            compositions[1]["id"] = compositions[0]["id"]

        def duplicate_locked_artifact(value: dict[str, object]) -> None:
            compositions = self._android_target(value)["compositions"]
            duplicate = copy.deepcopy(compositions[0])
            duplicate["id"] = "android-arm64-v8a-default-cpu-bundled-copy"
            compositions[1] = duplicate

        def reordered(value: dict[str, object]) -> None:
            compositions = self._android_target(value)["compositions"]
            compositions[0], compositions[1] = compositions[1], compositions[0]

        for name, mutate in (
            ("duplicate-id", duplicate_id),
            ("duplicate-locked-artifact", duplicate_locked_artifact),
            ("reordered", reordered),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_windows_selection_even_with_its_exact_locked_artifact(
        self,
    ) -> None:
        candidate = copy.deepcopy(self.policy)
        windows = self._target(candidate, "windows")
        windows.pop("reason")
        windows["disposition"] = "selected"
        windows["compositions"] = [
            {
                "id": "windows-x64-default-cpu-bundled",
                "kind": "locked-artifact",
                "artifactId": "onnxruntime-1.27.1-windows-x64-cpu",
                "runtimeOwner": "wrapper",
                "runtimeMode": "bundled",
                "advertisedProviders": [
                    {"id": "cpu", "requirement": "full-assignment"}
                ],
            }
        ]
        self._write_policy(candidate)
        self._assert_rejected()

    def test_rejects_any_change_to_the_exact_selected_target_set(self) -> None:
        candidate = copy.deepcopy(self.policy)
        macos = self._target(candidate, "macos")
        macos["disposition"] = "unsupported"
        macos.pop("compositions")
        macos["reason"] = "Deliberately removed from the advertised scope."
        self._write_policy(candidate)
        self._assert_rejected()
        self._reset_inputs()

        additions = (
            (
                "ios",
                "simulator",
                "ios-arm64-simulator-cpu-linked",
                "onnxruntime-1.27.1-ios-arm64-simulator-cpu",
                "linked",
            ),
            (
                "android",
                "x86_64",
                "android-x86_64-default-cpu-bundled",
                "onnxruntime-1.27.1-android-x86_64-cpu",
                "bundled",
            ),
            (
                "linux",
                "arm64",
                "linux-arm64-default-cpu-bundled",
                "onnxruntime-1.27.1-linux-arm64-cpu",
                "bundled",
            ),
        )
        for (
            operating_system,
            architecture_or_variant,
            composition_id,
            artifact_id,
            mode,
        ) in additions:
            with self.subTest(selected=f"{operating_system}/{architecture_or_variant}"):
                candidate = copy.deepcopy(self.policy)
                target = next(
                    row
                    for row in candidate["targets"]
                    if row["os"] == operating_system
                    and (
                        row["architecture"] == architecture_or_variant
                        or row["variant"] == architecture_or_variant
                    )
                )
                target.pop("reason")
                target["disposition"] = "selected"
                target["compositions"] = [
                    {
                        "id": composition_id,
                        "kind": "locked-artifact",
                        "artifactId": artifact_id,
                        "runtimeOwner": "wrapper",
                        "runtimeMode": mode,
                        "advertisedProviders": [
                            {"id": "cpu", "requirement": "full-assignment"}
                        ],
                    }
                ]
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_qnn_non_cpu_and_provider_overclaims(self) -> None:
        def target_qnn(value: dict[str, object]) -> None:
            self._android_target(value)["flavor"] = "qnn"

        def provider_qnn(value: dict[str, object]) -> None:
            composition = self._composition(
                self._android_target(value), "locked-artifact"
            )
            composition["advertisedProviders"][0]["id"] = "qnn"

        def compiled_but_unadvertised_provider(value: dict[str, object]) -> None:
            composition = self._composition(
                self._target(value, "macos"), "locked-artifact"
            )
            composition["advertisedProviders"][0]["id"] = "coreml"

        def multiple_providers(value: dict[str, object]) -> None:
            composition = self._composition(
                self._android_target(value), "locked-artifact"
            )
            composition["advertisedProviders"].append(
                {"id": "xnnpack", "requirement": "full-assignment"}
            )

        def select_qnn_deferral(value: dict[str, object]) -> None:
            value["deferredCapabilities"][0]["disposition"] = "selected"

        for name, mutate in (
            ("target-qnn", target_qnn),
            ("provider-qnn", provider_qnn),
            ("compiled-provider-overclaim", compiled_but_unadvertised_provider),
            ("multiple-providers", multiple_providers),
            ("selected-qnn-deferral", select_qnn_deferral),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_artifact_tuple_minimum_os_and_runtime_mismatches(self) -> None:
        def artifact(value: dict[str, object]) -> None:
            composition = self._composition(
                self._target(value, "macos"), "locked-artifact"
            )
            composition["artifactId"] = "onnxruntime-1.27.1-linux-x86_64-cpu"

        def operating_system(value: dict[str, object]) -> None:
            self._target(value, "macos")["os"] = "linux"

        def architecture(value: dict[str, object]) -> None:
            self._target(value, "macos")["architecture"] = "x86_64"

        def minimum_os(value: dict[str, object]) -> None:
            self._target(value, "macos")["minimumOs"] = "13.0"

        def runtime_mode(value: dict[str, object]) -> None:
            composition = self._composition(
                self._target(value, "ios"), "locked-artifact"
            )
            composition["runtimeMode"] = "bundled"

        for name, mutate in (
            ("artifact", artifact),
            ("os", operating_system),
            ("architecture", architecture),
            ("minimum-os", minimum_os),
            ("runtime-mode", runtime_mode),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_invalid_sherpa_process_compositions(self) -> None:
        def missing_identity(value: dict[str, object]) -> None:
            sherpa = self._composition(
                self._android_target(value), "android-sherpa-process"
            )
            sherpa.pop("runtimeIdentity")

        def wrong_owner_package(value: dict[str, object]) -> None:
            sherpa = self._composition(
                self._android_target(value), "android-sherpa-process"
            )
            sherpa["runtimeIdentity"]["ownerPackage"] = "other"

        def wrong_owner_version(value: dict[str, object]) -> None:
            sherpa = self._composition(
                self._android_target(value), "android-sherpa-process"
            )
            sherpa["runtimeIdentity"]["ownerPackageVersion"] = "1.13.5"

        def wrong_ort_version(value: dict[str, object]) -> None:
            sherpa = self._composition(
                self._android_target(value), "android-sherpa-process"
            )
            sherpa["runtimeIdentity"]["onnxRuntimeVersion"] = "1.27.1"

        def artifact_owned_process(value: dict[str, object]) -> None:
            sherpa = self._composition(
                self._android_target(value), "android-sherpa-process"
            )
            sherpa["artifactId"] = "onnxruntime-1.27.1-android-arm64-v8a-cpu"

        def identity_on_locked_artifact(value: dict[str, object]) -> None:
            android = self._android_target(value)
            locked = self._composition(android, "locked-artifact")
            sherpa = self._composition(android, "android-sherpa-process")
            locked["runtimeIdentity"] = copy.deepcopy(sherpa["runtimeIdentity"])

        def sherpa_on_non_android_target(value: dict[str, object]) -> None:
            sherpa = copy.deepcopy(
                self._composition(
                    self._android_target(value), "android-sherpa-process"
                )
            )
            sherpa["id"] = "macos-arm64-default-cpu-sherpa-process"
            self._target(value, "macos")["compositions"].append(sherpa)

        for name, mutate in (
            ("missing-runtime-identity", missing_identity),
            ("wrong-owner-package", wrong_owner_package),
            ("wrong-owner-version", wrong_owner_version),
            ("wrong-ort-version", wrong_ort_version),
            ("artifact-owned-process", artifact_owned_process),
            ("identity-on-locked-artifact", identity_on_locked_artifact),
            ("sherpa-on-non-android", sherpa_on_non_android_target),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_lock_package_abi_api_and_digest_tamper(self) -> None:
        def wrong_digest(value: dict[str, object]) -> None:
            value["baseline"]["nativeLockSha256"] = "0" * 64

        def wrong_package(value: dict[str, object]) -> None:
            value["baseline"]["packageVersion"] = "0.1.0-dev.2"

        def wrong_package_name(value: dict[str, object]) -> None:
            value["baseline"]["packageName"] = "other"

        def wrong_sherpa_digest(value: dict[str, object]) -> None:
            value["baseline"]["sherpaPubspecLockSha256"] = "0" * 64

        def wrong_abi(value: dict[str, object]) -> None:
            value["baseline"]["shimAbi"] = 2

        def wrong_api(value: dict[str, object]) -> None:
            value["baseline"]["requiredOrtApi"] = 26

        for name, mutate in (
            ("digest", wrong_digest),
            ("package", wrong_package),
            ("package-name", wrong_package_name),
            ("sherpa-digest", wrong_sherpa_digest),
            ("abi", wrong_abi),
            ("api", wrong_api),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.policy)
                mutate(candidate)
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

        with self.subTest(name="lock-bytes"):
            self.fixture.lock.write_bytes(NATIVE_LOCK.read_bytes() + b"\n")
            self._assert_rejected()
            self._reset_inputs()

        with self.subTest(name="pubspec-bytes"):
            self.fixture.pubspec.write_text(
                PUBSPEC.read_text(encoding="utf-8").replace(
                    "version: 0.1.0-dev.1", "version: 0.1.0-dev.2"
                ),
                encoding="utf-8",
            )
            self._assert_rejected()
            self._reset_inputs()

        with self.subTest(name="pubspec-name"):
            self.fixture.pubspec.write_text(
                PUBSPEC.read_text(encoding="utf-8").replace(
                    "name: fonix", "name: substituted_fonix"
                ),
                encoding="utf-8",
            )
            self._assert_rejected()
            self._reset_inputs()

        with self.subTest(name="sherpa-lock-bytes"):
            self.fixture.sherpa_pubspec_lock.write_bytes(
                SHERPA_PUBSPEC_LOCK.read_bytes() + b"\n"
            )
            self._assert_rejected()
            self._reset_inputs()

    def test_rejects_rebound_sherpa_runtime_owner_selection(self) -> None:
        original = SHERPA_PUBSPEC_LOCK.read_text(encoding="utf-8")
        mutations = (
            (
                "version",
                original.replace(
                    'version: "1.13.4"', 'version: "1.13.5"', 1
                ),
            ),
            (
                "hosted-hash",
                original.replace(
                    "889c03cf7a8788795e3a6d35bf9f20b66d1862ea7a73a1b6aaf6e450715c870a",
                    "0" * 64,
                    1,
                ),
            ),
            (
                "dependency-kind",
                original.replace(
                    '  sherpa_onnx:\n    dependency: "direct main"',
                    '  sherpa_onnx:\n    dependency: transitive',
                    1,
                ),
            ),
        )
        for name, sherpa_lock in mutations:
            with self.subTest(name=name):
                self.fixture.sherpa_pubspec_lock.write_text(
                    sherpa_lock, encoding="utf-8"
                )
                candidate = copy.deepcopy(self.policy)
                candidate["baseline"]["sherpaPubspecLockSha256"] = _sha256(
                    sherpa_lock.encode("utf-8")
                )
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_schema_byte_and_semantic_tamper(self) -> None:
        self.fixture.schema.write_bytes(SCHEMA.read_bytes() + b"\n")
        self._assert_rejected()

        self.fixture.schema.write_bytes(SCHEMA.read_bytes())
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema["$defs"]["providerClaim"]["properties"]["id"] = {
            "enum": ["cpu", "qnn"]
        }
        _write_json(self.fixture.schema, schema)
        self._assert_rejected()

    def test_rejects_repository_helper_byte_and_semantic_tamper(self) -> None:
        helpers = (
            (
                "release-evidence",
                "release_evidence_helper",
                RELEASE_EVIDENCE_HELPER,
                b"Generate deterministic, offline Fonix release-audit metadata",
                b"Generate altered, offline Fonix release-audit metadata",
            ),
            (
                "sherpa-lock",
                "sherpa_lock_helper",
                SHERPA_LOCK_HELPER,
                b"Validate Android Fonix/Sherpa load-order and lifecycle evidence",
                b"Validate altered Fonix/Sherpa load-order and lifecycle evidence",
            ),
        )
        for name, attribute, source, before, after in helpers:
            with self.subTest(helper=name, mutation="raw-bytes"):
                destination = getattr(self.fixture, attribute)
                destination.write_bytes(source.read_bytes() + b"\n")
                self._assert_rejected()
                self._reset_inputs()

            with self.subTest(helper=name, mutation="semantic"):
                raw = source.read_bytes()
                self.assertIn(before, raw)
                destination = getattr(self.fixture, attribute)
                destination.write_bytes(raw.replace(before, after, 1))
                self._assert_rejected()
                self._reset_inputs()

    def test_rejects_substituted_actually_imported_helper_modules(self) -> None:
        helper_names = (
            "generate_release_sbom.py",
            "validate_android_load_order_receipt.py",
        )
        for helper_name in helper_names:
            with self.subTest(helper=helper_name):
                fixture = self._copy_fixture(
                    self.root / f"imported-{helper_name.removesuffix('.py')}"
                )
                isolated_repository = (
                    self.root
                    / f"isolated-repository-{helper_name.removesuffix('.py')}"
                )
                isolated_ci = isolated_repository / "tool/ci"
                isolated_ci.mkdir(parents=True)
                copied_validator = isolated_ci / SCRIPT.name
                copied_validator.write_bytes(SCRIPT.read_bytes())
                for source in (
                    RELEASE_EVIDENCE_HELPER,
                    SHERPA_LOCK_HELPER,
                    SOURCE_CHECKSUM_HELPER,
                ):
                    (isolated_ci / source.name).write_bytes(source.read_bytes())
                imported_helper = isolated_ci / helper_name
                imported_helper.write_bytes(
                    imported_helper.read_bytes() + b"\n# substituted import\n"
                )
                isolated_verifier = (
                    isolated_repository / "templates/android/verify_native_libs.py"
                )
                isolated_verifier.parent.mkdir(parents=True)
                isolated_verifier.write_bytes(
                    (REPOSITORY / "templates/android/verify_native_libs.py").read_bytes()
                )
                self._assert_rejected(
                    fixture=fixture,
                    script=copied_validator,
                )

    def test_rejects_unsafe_policy_and_command_paths(self) -> None:
        for field, unsafe in (
            ("nativeLockPath", "../native/versions.lock.yaml"),
            ("nativeLockPath", "/absolute/versions.lock.yaml"),
            ("nativeLockPath", "native/./versions.lock.yaml"),
            ("nativeLockPath", "native\\versions.lock.yaml"),
            ("sherpaPubspecLockPath", "../pubspec.lock"),
            ("sherpaPubspecLockPath", "/absolute/pubspec.lock"),
            ("sherpaPubspecLockPath", "templates/../pubspec.lock"),
            ("sherpaPubspecLockPath", "templates\\pubspec.lock"),
        ):
            with self.subTest(field=field, path=unsafe):
                candidate = copy.deepcopy(self.policy)
                candidate["baseline"][field] = unsafe
                self._write_policy(candidate)
                self._assert_rejected()
                self._reset_inputs()

        with self.subTest(relative_output=True):
            self._assert_rejected(output_argument="relative-validation.json")
            self.assertFalse((REPOSITORY / "relative-validation.json").exists())

        with self.subTest(existing_output=True):
            output = self.root / "existing.json"
            output.write_text("must remain\n", encoding="utf-8")
            self._assert_rejected(output=output)
            self.assertEqual(output.read_text(encoding="utf-8"), "must remain\n")

        with self.subTest(scope_outside_repository=True):
            outside_scope = self.root / "outside-scope.json"
            outside_scope.write_bytes(POLICY.read_bytes())
            self._assert_rejected(scope=outside_scope)

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliable on Windows")
    def test_rejects_symlinked_repository_scope_lock_pubspec_and_output(self) -> None:
        def isolated(name: str) -> _ScopeFixture:
            return self._copy_fixture(self.root / name)

        repository_fixture = isolated("repository-link")
        repository_link = self.root / "repository-alias"
        repository_link.symlink_to(
            repository_fixture.repository, target_is_directory=True
        )
        self._assert_rejected(
            fixture=repository_fixture,
            repository=repository_link,
            scope=repository_link / "release/scoped-pre-1.0-v1.json",
        )

        scope_fixture = isolated("scope-link")
        scope_target = self.root / "real-scope.json"
        scope_target.write_bytes(scope_fixture.scope.read_bytes())
        scope_fixture.scope.unlink()
        scope_fixture.scope.symlink_to(scope_target)
        self._assert_rejected(fixture=scope_fixture)

        lock_fixture = isolated("lock-link")
        lock_target = self.root / "real-lock.json"
        lock_target.write_bytes(lock_fixture.lock.read_bytes())
        lock_fixture.lock.unlink()
        lock_fixture.lock.symlink_to(lock_target)
        self._assert_rejected(fixture=lock_fixture)

        pubspec_fixture = isolated("pubspec-link")
        pubspec_target = self.root / "real-pubspec.yaml"
        pubspec_target.write_bytes(pubspec_fixture.pubspec.read_bytes())
        pubspec_fixture.pubspec.unlink()
        pubspec_fixture.pubspec.symlink_to(pubspec_target)
        self._assert_rejected(fixture=pubspec_fixture)

        sherpa_lock_fixture = isolated("sherpa-lock-link")
        sherpa_lock_target = self.root / "real-sherpa-pubspec.lock"
        sherpa_lock_target.write_bytes(
            sherpa_lock_fixture.sherpa_pubspec_lock.read_bytes()
        )
        sherpa_lock_fixture.sherpa_pubspec_lock.unlink()
        sherpa_lock_fixture.sherpa_pubspec_lock.symlink_to(sherpa_lock_target)
        self._assert_rejected(fixture=sherpa_lock_fixture)

        for label, attribute, source in (
            (
                "release-evidence-helper",
                "release_evidence_helper",
                RELEASE_EVIDENCE_HELPER,
            ),
            ("sherpa-lock-helper", "sherpa_lock_helper", SHERPA_LOCK_HELPER),
        ):
            helper_fixture = isolated(f"{label}-link")
            helper_target = self.root / f"real-{label}.py"
            helper_target.write_bytes(source.read_bytes())
            helper_path = getattr(helper_fixture, attribute)
            helper_path.unlink()
            helper_path.symlink_to(helper_target)
            self._assert_rejected(fixture=helper_fixture)

        output_fixture = isolated("output-link")
        output_target = self.root / "real-output.json"
        output_target.write_text("must remain\n", encoding="utf-8")
        output_link = self.root / "output-link.json"
        output_link.symlink_to(output_target)
        self._assert_rejected(fixture=output_fixture, output=output_link)
        self.assertEqual(
            output_target.read_text(encoding="utf-8"), "must remain\n"
        )

        parent_fixture = isolated("output-parent-link")
        real_parent = self.root / "real-output-parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked-output-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        self._assert_rejected(
            fixture=parent_fixture, output=linked_parent / "validation.json"
        )

    def test_global_release_ready_flag_remains_fail_closed(self) -> None:
        documents = SimpleNamespace(
            metadata={
                "sbom": {"sha256": "0" * 64},
                "releaseReadiness": {
                    "ready": False,
                    "blockers": ["five-platform-release-remains-unapproved"],
                },
            }
        )
        arguments = [
            "--repository",
            str(self.fixture.repository),
            "--staged-directory",
            str(self.fixture.repository),
            "--artifact-id",
            "synthetic-artifact",
            "--sbom-output",
            str(self.root / "global.spdx.json"),
            "--metadata-output",
            str(self.root / "global-audit.json"),
        ]

        with mock.patch.object(
            release_evidence, "generate_evidence", return_value=documents
        ), mock.patch.object(release_evidence, "write_evidence"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(release_evidence.main(arguments), 0)
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                self.assertEqual(
                    release_evidence.main([*arguments, "--require-release-ready"]),
                    1,
                )
            self.assertIn(
                "five-platform-release-remains-unapproved", error.getvalue()
            )


if __name__ == "__main__":
    unittest.main()
