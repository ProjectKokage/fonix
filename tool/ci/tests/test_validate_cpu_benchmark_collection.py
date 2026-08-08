from __future__ import annotations

import argparse
import copy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping
import unittest
from unittest import mock


CI_DIRECTORY = Path(__file__).resolve().parents[1]
TEST_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(CI_DIRECTORY), str(TEST_DIRECTORY)]

import collect_cpu_benchmark as collector  # noqa: E402
import cpu_benchmark_collection as core  # noqa: E402
import test_cpu_benchmark_collection as collection_fixtures  # noqa: E402
import validate_cpu_benchmark_collection as validator  # noqa: E402


def _encoded(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


class _ArtifactCollector:
    def __init__(self, artifacts: Mapping[str, Any]) -> None:
        self.artifacts = copy.deepcopy(artifacts)
        self.calls = 0

    def _artifact_snapshot(self, **_arguments: object) -> tuple[object, object]:
        self.calls += 1
        return copy.deepcopy(self.artifacts), {
            "applicationTree": copy.deepcopy(self.artifacts["applicationTree"])
        }


@unittest.skipUnless(os.name == "posix", "requires POSIX no-follow descriptors")
class CpuBenchmarkBundleValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="fonix-cpu-bundle-validator-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.application = self.root / "Application.app"
        self.application.mkdir()
        self.executable = self.application / "Contents/MacOS/Fonix Reference"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"executable")
        self.executable.chmod(0o700)
        self.shim = self.application / "libdort_core.dylib"
        self.shim.write_bytes(b"shim")
        self.runtime = self.application / "libonnxruntime.1.dylib"
        self.runtime.write_bytes(b"runtime")
        self.resolver = self.application / "resolver.json"
        self.resolver.write_bytes(b"resolver")
        self.bundle = self.root / "bundle"
        self.bundle.mkdir(mode=0o700)
        self.output = self.root / "validation.json"
        self.process_ids = [1000 + index for index in range(validator.LAUNCH_COUNT)]
        self.fragments = [
            collection_fixtures._fragment(
                f"{index + 1:064x}", self.process_ids[index], offset=index
            )
            for index in range(validator.LAUNCH_COUNT)
        ]
        self.fragment_payloads = [_encoded(value) for value in self.fragments]
        self.artifacts = collection_fixtures._artifacts()
        self.environment = collection_fixtures._environment(
            self.process_ids,
            [value["launchChallenge"] for value in self.fragments],
        )
        self.host_observation_payload = (
            collection_fixtures._host_observation_payload(
                self.environment, validator.LAUNCH_COUNT
            )
        )
        self.fake_collector = _ArtifactCollector(self.artifacts)
        collector_sha256 = core.regular_file_identity(
            Path(collector.__file__).resolve(strict=True),
            label="collector",
            maximum_bytes=validator.MAXIMUM_TOOL_BYTES,
        )["sha256"]
        self.collection = core.derive_collection(
            fragment_payloads=self.fragment_payloads,
            host_observation_payload=self.host_observation_payload,
            expected_launches=[
                (value["launchChallenge"], value["processId"])
                for value in self.fragments
            ],
            artifacts=self.artifacts,
            collector_sha256=collector_sha256,
        )
        self._write_bundle()

    def _write_bundle(self) -> None:
        for index, payload in enumerate(self.fragment_payloads):
            (self.bundle / validator.FRAGMENT_FILENAME.format(index=index)).write_bytes(
                payload
            )
        (self.bundle / validator.COLLECTION_FILENAME).write_bytes(
            _encoded(self.collection)
        )
        (self.bundle / validator.HOST_OBSERVATIONS_FILENAME).write_bytes(
            self.host_observation_payload
        )

    def _arguments(self) -> argparse.Namespace:
        return argparse.Namespace(
            collection_directory=self.bundle,
            repository=REPOSITORY,
            application_root=self.application,
            executable=self.executable,
            shim_artifact=self.shim,
            runtime_artifact=self.runtime,
            resolver_manifest=self.resolver,
            provider_dependency=[],
            output=self.output,
        )

    def _validate(self, **overrides: object) -> dict[str, Any]:
        options: dict[str, object] = {
            "core": core,
            "collector": self.fake_collector,
        }
        options.update(overrides)
        return validator.validate(
            self._arguments(), **options  # type: ignore[arg-type]
        )

    def test_validates_exact_bundle_and_publishes_path_free_record(self) -> None:
        result = self._validate()

        self.assertEqual(result["result"], "validated")
        self.assertEqual(result["claimStatus"], "measurement-only")
        self.assertEqual(result["validationScope"], "offline-consistency-only")
        self.assertTrue(result["rawEvidenceRequiredForEvaluation"])
        self.assertEqual(result["bundle"]["fileCount"], 7)
        self.assertEqual(result["collection"]["launchCount"], 5)
        self.assertEqual(result["recordedTarget"]["platform"], "macos")
        self.assertEqual(
            result["recordedTarget"]["authenticationStatus"],
            "not-independently-authenticated",
        )
        self.assertEqual(self.fake_collector.calls, 2)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.output.read_bytes()), result)
        self.assertNotIn(str(self.root), json.dumps(result, sort_keys=True))
        self.assertIn("not a performance baseline", result["claimBoundary"])

    def test_requires_exact_seven_regular_file_inventory(self) -> None:
        cases = ("extra", "missing", "symlink")
        for mutation in cases:
            with self.subTest(mutation=mutation):
                if mutation == "extra":
                    changed = self.bundle / "extra.json"
                    changed.write_bytes(b"{}")
                elif mutation == "missing":
                    changed = self.bundle / "fragment-04.json"
                    changed.unlink()
                else:
                    changed = self.bundle / "fragment-04.json"
                    changed.unlink()
                    changed.symlink_to("fragment-03.json")
                with self.assertRaisesRegex(
                    validator.CpuBenchmarkValidationError,
                    "(?:seven-file bundle|regular file)",
                ):
                    self._validate()
                if changed.is_symlink() or changed.exists():
                    changed.unlink()
                self.fragment_payloads = [_encoded(value) for value in self.fragments]
                self._write_bundle()
                self.fake_collector.calls = 0

    def test_rejects_aggregate_only_tamper_and_raw_fragment_replay(self) -> None:
        tampered = copy.deepcopy(self.collection)
        tampered["aggregates"]["measurements"][
            "firstRunMicroseconds"
        ]["statistics"]["p50"] += 1
        (self.bundle / validator.COLLECTION_FILENAME).write_bytes(_encoded(tampered))
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "exactly match"
        ):
            self._validate()

        self._write_bundle()
        replayed = self.bundle / "fragment-01.json"
        replayed.write_bytes(self.fragment_payloads[0])
        with self.assertRaises(validator.CpuBenchmarkValidationError):
            self._validate()

    def test_rejects_collection_raw_fragment_path_substitution(self) -> None:
        tampered = copy.deepcopy(self.collection)
        tampered["rawFragments"][0]["sha256"] = "0" * 64
        (self.bundle / validator.COLLECTION_FILENAME).write_bytes(_encoded(tampered))

        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "exactly match"
        ):
            self._validate()

    def test_rejects_host_sidecar_substitution_and_challenge_rebinding(self) -> None:
        changed_host = json.loads(self.host_observation_payload)
        changed_host["environment"]["launchObservations"][0][
            "thermalStateEnd"
        ] = "different-state"
        (self.bundle / validator.HOST_OBSERVATIONS_FILENAME).write_bytes(
            _encoded(changed_host)
        )
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "sidecar does not match"
        ):
            self._validate()

        changed_host = json.loads(self.host_observation_payload)
        changed_host["environment"]["launchObservations"][0][
            "launchChallenge"
        ] = "f" * 64
        changed_payload = _encoded(changed_host)
        changed_collection = copy.deepcopy(self.collection)
        changed_collection["rawHostObservation"] = {
            "sha256": hashlib.sha256(changed_payload).hexdigest(),
            "record": changed_host,
        }
        changed_collection["environment"] = changed_host["environment"]
        (self.bundle / validator.HOST_OBSERVATIONS_FILENAME).write_bytes(
            changed_payload
        )
        (self.bundle / validator.COLLECTION_FILENAME).write_bytes(
            _encoded(changed_collection)
        )
        with self.assertRaises(validator.CpuBenchmarkValidationError):
            self._validate()

    def test_rejects_artifact_change_during_validation(self) -> None:
        original = self.fake_collector._artifact_snapshot

        def changing_artifacts(**arguments: object) -> tuple[object, object]:
            result = original(**arguments)
            if self.fake_collector.calls == 2:
                result[0]["applicationTree"]["sha256"] = "0" * 64  # type: ignore[index]
            return result

        self.fake_collector._artifact_snapshot = (  # type: ignore[method-assign]
            changing_artifacts
        )
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "changed during validation"
        ):
            self._validate()

    def test_validation_is_offline_and_never_observes_the_live_host(self) -> None:
        with (
            mock.patch.object(
                collector,
                "_host_identity",
                side_effect=AssertionError("live host identity must not be read"),
            ) as host_identity,
            mock.patch.object(
                collector,
                "_observe_host_environment",
                side_effect=AssertionError("live host must not be observed"),
            ) as environment_observer,
        ):
            result = self._validate()

        host_identity.assert_not_called()
        environment_observer.assert_not_called()
        self.assertEqual(result["recordedTarget"]["platform"], "macos")
        self.assertEqual(result["recordedTarget"]["architecture"], "arm64")

    def test_detects_bundle_replacement_during_descriptor_read(self) -> None:
        target = self.bundle / "fragment-00.json"
        replacement = self.root / "replacement.json"
        replacement.write_bytes(self.fragment_payloads[0])
        real_read = validator.os.read
        replaced = False

        def replacing_read(descriptor: int, count: int) -> bytes:
            nonlocal replaced
            value = real_read(descriptor, count)
            if value and not replaced:
                replaced = True
                os.replace(replacement, target)
            return value

        with (
            mock.patch.object(validator.os, "read", side_effect=replacing_read),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError, "changed"
            ),
        ):
            self._validate()

    def test_output_collision_is_no_replace_and_preserves_winner(self) -> None:
        self.output.write_bytes(b"winner")
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "must not already exist"
        ):
            self._validate()
        self.assertEqual(self.output.read_bytes(), b"winner")

    def test_rejects_output_in_every_input_tree_before_validation(self) -> None:
        cases = (
            self.bundle / "validation.json",
            self.application / "validation.json",
            REPOSITORY / f".validator-output-{self.root.name}.json",
            self.bundle,
            self.application,
            REPOSITORY,
        )
        for output in cases:
            with self.subTest(output=output):
                arguments = self._arguments()
                arguments.output = output
                with self.assertRaisesRegex(
                    validator.CpuBenchmarkValidationError,
                    "must be outside",
                ):
                    validator.validate(
                        arguments,
                        core=core,
                        collector=self.fake_collector,
                    )
                self.assertEqual(self.fake_collector.calls, 0)

        bundle_alias = self.root / "bundle-alias"
        bundle_alias.symlink_to(self.bundle, target_is_directory=True)
        arguments = self._arguments()
        arguments.output = bundle_alias / "validation.json"
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError,
            "must be outside the raw collection bundle",
        ):
            validator.validate(
                arguments,
                core=core,
                collector=self.fake_collector,
            )

    def test_preexisting_output_hardlink_to_input_is_rejected_and_preserved(
        self,
    ) -> None:
        fragment = self.bundle / "fragment-00.json"
        os.link(fragment, self.output)
        before = fragment.stat()

        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "must not already exist"
        ):
            self._validate()

        after = fragment.stat()
        self.assertTrue(os.path.samefile(fragment, self.output))
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(self.output.read_bytes(), self.fragment_payloads[0])

    def test_racing_output_hardlink_to_input_wins_without_overwrite(self) -> None:
        fragment = self.bundle / "fragment-00.json"
        original = self.fake_collector._artifact_snapshot

        def create_racing_hardlink(**arguments: object) -> tuple[object, object]:
            result = original(**arguments)
            if self.fake_collector.calls == 1:
                os.link(fragment, self.output)
            return result

        self.fake_collector._artifact_snapshot = (  # type: ignore[method-assign]
            create_racing_hardlink
        )
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "already exists"
        ):
            self._validate()

        self.assertTrue(os.path.samefile(fragment, self.output))
        self.assertEqual(self.output.read_bytes(), self.fragment_payloads[0])

    def test_no_replace_publication_preserves_the_winner(self) -> None:
        self.output.write_bytes(b"winner")
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "already exists"
        ):
            validator._publish_no_replace(self.output, {"result": "loser"})

        self.assertEqual(self.output.read_bytes(), b"winner")

    def test_parent_replacement_before_reserve_preserves_both_directories(
        self,
    ) -> None:
        parent = self.root / "publish-parent-before"
        parent.mkdir()
        output = parent / "validation.json"
        retained = parent.lstat()
        moved = self.root / "publish-parent-before-moved"
        parent.rename(moved)
        parent.mkdir()
        sentinel = parent / "replacement-sentinel"
        sentinel.write_bytes(b"replacement")

        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError,
            "parent changed before publication",
        ):
            validator._publish_no_replace(
                output,
                {"result": "must-not-publish"},
                expected_parent_metadata=retained,
            )

        self.assertTrue(validator._same_inode(retained, moved.lstat()))
        self.assertFalse(validator._same_inode(retained, parent.lstat()))
        self.assertEqual(sentinel.read_bytes(), b"replacement")
        self.assertFalse(output.exists())
        self.assertFalse((moved / output.name).exists())

    def test_parent_relocation_after_write_preserves_residue_and_replacement(
        self,
    ) -> None:
        parent = self.root / "publish-parent-after"
        parent.mkdir()
        output = parent / "validation.json"
        retained = parent.lstat()
        moved = self.root / "publish-parent-after-moved"
        real_write = validator.os.write
        relocated = False

        def relocate_after_write(descriptor: int, data: Any) -> int:
            nonlocal relocated
            written = real_write(descriptor, data)
            if not relocated:
                relocated = True
                parent.rename(moved)
                parent.mkdir()
                (parent / "replacement-sentinel").write_bytes(b"replacement")
            return written

        with (
            mock.patch.object(
                validator.os,
                "write",
                side_effect=relocate_after_write,
            ),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError,
                "parent path changed",
            ),
        ):
            validator._publish_no_replace(
                output,
                {"result": "retained-residue"},
                expected_parent_metadata=retained,
            )

        self.assertTrue(validator._same_inode(retained, moved.lstat()))
        self.assertFalse(validator._same_inode(retained, parent.lstat()))
        self.assertEqual(
            (parent / "replacement-sentinel").read_bytes(), b"replacement"
        )
        self.assertFalse(output.exists())
        self.assertEqual(
            json.loads((moved / output.name).read_bytes()),
            {"result": "retained-residue"},
        )

    def test_final_confinement_recheck_rejects_new_protected_alias(self) -> None:
        parent = self.root / "confinement-output"
        collection_directory = self.root / "confinement-bundle"
        application_root = self.root / "confinement-application"
        repository = self.root / "confinement-repository"
        for directory in (
            parent,
            collection_directory,
            application_root,
            repository,
        ):
            directory.mkdir()
        output = parent / "validation.json"
        repository_original = self.root / "confinement-repository-original"
        real_write = validator.os.write
        replaced = False

        def alias_repository_after_write(descriptor: int, data: Any) -> int:
            nonlocal replaced
            written = real_write(descriptor, data)
            if not replaced:
                replaced = True
                repository.rename(repository_original)
                repository.symlink_to(parent, target_is_directory=True)
            return written

        with (
            mock.patch.object(
                validator.os,
                "write",
                side_effect=alias_repository_after_write,
            ),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError,
                "entered a protected input tree",
            ),
        ):
            validator._publish_no_replace(
                output,
                {"result": "confined-residue"},
                expected_parent_metadata=parent.lstat(),
                collection_directory=collection_directory,
                application_root=application_root,
                repository=repository,
            )

        self.assertTrue(repository.is_symlink())
        self.assertTrue(repository_original.is_dir())
        self.assertEqual(
            json.loads(output.read_bytes()), {"result": "confined-residue"}
        )

    def test_substituted_incomplete_output_name_is_never_unlinked(self) -> None:
        victim = self.root / "replacement-victim"
        victim.write_bytes(b"preserve-replacement-victim")
        real_write = validator.os.write
        replaced = False

        def replace_after_write(descriptor: int, data: Any) -> int:
            nonlocal replaced
            written = real_write(descriptor, data)
            if not replaced:
                replaced = True
                self.output.unlink()
                os.link(victim, self.output)
            return written

        with (
            mock.patch.object(
                validator.os,
                "write",
                side_effect=replace_after_write,
            ),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError,
                "was incomplete",
            ),
        ):
            validator._publish_no_replace(self.output, {"result": "exact"})

        self.assertTrue(os.path.samefile(self.output, victim))
        self.assertEqual(victim.read_bytes(), b"preserve-replacement-victim")

    def test_incomplete_retained_output_is_preserved_and_blocks_retry(self) -> None:
        real_write = validator.os.write
        failed = False

        def fail_after_partial_write(descriptor: int, data: Any) -> int:
            nonlocal failed
            if not failed:
                failed = True
                view = memoryview(data)
                real_write(descriptor, view[:1])
                raise OSError(errno.EIO, "simulated write failure")
            return real_write(descriptor, data)

        with (
            mock.patch.object(
                validator.os,
                "write",
                side_effect=fail_after_partial_write,
            ),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError,
                "was incomplete",
            ),
        ):
            validator._publish_no_replace(self.output, {"result": "partial"})

        self.assertEqual(self.output.read_bytes(), b"{")
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "already exists"
        ):
            validator._publish_no_replace(self.output, {"result": "retry"})
        self.assertEqual(self.output.read_bytes(), b"{")

    def test_complete_output_name_replacement_is_detected_and_preserved(
        self,
    ) -> None:
        victim = self.root / "complete-replacement-victim"
        victim.write_bytes(b"preserve-complete-replacement")
        real_fsync = validator.os.fsync
        replaced = False

        def replace_before_parent_sync(descriptor: int) -> None:
            nonlocal replaced
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not replaced:
                replaced = True
                self.output.unlink()
                os.link(victim, self.output)
            real_fsync(descriptor)

        with (
            mock.patch.object(
                validator.os,
                "fsync",
                side_effect=replace_before_parent_sync,
            ),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError,
                "current output name was preserved",
            ),
        ):
            validator._publish_no_replace(self.output, {"result": "complete"})

        self.assertTrue(os.path.samefile(self.output, victim))
        self.assertEqual(victim.read_bytes(), b"preserve-complete-replacement")

    def test_post_publication_durability_failure_preserves_output(self) -> None:
        real_fsync = validator.os.fsync
        for failure_kind in ("file", "directory"):
            with self.subTest(failure_kind=failure_kind):
                output = self.root / f"durability-{failure_kind}.json"

                def fail_sync(descriptor: int) -> None:
                    mode = os.fstat(descriptor).st_mode
                    should_fail = (
                        failure_kind == "file" and stat.S_ISREG(mode)
                    ) or (
                        failure_kind == "directory" and stat.S_ISDIR(mode)
                    )
                    if should_fail:
                        raise OSError(errno.EIO, "simulated durability failure")
                    real_fsync(descriptor)

                with (
                    mock.patch.object(
                        validator.os,
                        "fsync",
                        side_effect=fail_sync,
                    ),
                    self.assertRaisesRegex(
                        validator.CpuBenchmarkValidationError,
                        "retained output was preserved",
                    ),
                ):
                    validator._publish_no_replace(
                        output, {"result": "durable"}
                    )

                self.assertEqual(
                    json.loads(output.read_bytes()), {"result": "durable"}
                )

    def test_symlink_bundle_and_artifact_leaves_are_rejected(self) -> None:
        for field, target in (
            ("collection_directory", self.bundle),
            ("runtime_artifact", self.runtime),
        ):
            with self.subTest(field=field):
                link = self.root / f"{field}-link"
                link.symlink_to(target)
                arguments = self._arguments()
                setattr(arguments, field, link)
                with self.assertRaisesRegex(
                    validator.CpuBenchmarkValidationError, "non-link"
                ):
                    validator.validate(
                        arguments,
                        core=core,
                        collector=self.fake_collector,
                    )

    def test_actual_schemas_are_closed_and_validate_rederived_records(self) -> None:
        registry, _identities = validator._load_schemas(REPOSITORY, core)
        for schema in registry.values():
            self.assertFalse(schema["additionalProperties"])
        validator._validate_schema_instance(
            self.collection,
            registry[validator.COLLECTION_SCHEMA_ID],
            registry[validator.COLLECTION_SCHEMA_ID],
            registry,
            "collection",
        )

    def test_schema_bytes_are_pinned_by_the_validator(self) -> None:
        changed = copy.deepcopy(validator._EXPECTED_SCHEMA_IDENTITIES)
        changed[validator.TARGET_SCHEMA_ID]["sha256"] = "0" * 64

        with (
            mock.patch.object(
                validator, "_EXPECTED_SCHEMA_IDENTITIES", changed
            ),
            self.assertRaisesRegex(
                validator.CpuBenchmarkValidationError, "validator pin"
            ),
        ):
            validator._load_schemas(REPOSITORY, core)

    def test_supplied_repository_tools_and_schemas_must_match_trusted_bytes(
        self,
    ) -> None:
        relatives = (
            validator.TARGET_SCHEMA_RELATIVE,
            validator.COLLECTION_SCHEMA_RELATIVE,
            validator.VALIDATION_SCHEMA_RELATIVE,
            "tool/ci/collect_cpu_benchmark.py",
            "tool/ci/cpu_benchmark_collection.py",
            "tool/ci/validate_cpu_benchmark_collection.py",
        )
        for changed_relative in (
            validator.COLLECTION_SCHEMA_RELATIVE,
            "tool/ci/validate_cpu_benchmark_collection.py",
        ):
            with self.subTest(relative=changed_relative):
                candidate = self.root / (
                    "candidate-" + changed_relative.replace("/", "-")
                )
                for relative in relatives:
                    destination = candidate.joinpath(*relative.split("/"))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = REPOSITORY.joinpath(*relative.split("/"))
                    destination.write_bytes(source.read_bytes())
                changed = candidate.joinpath(*changed_relative.split("/"))
                changed.write_bytes(changed.read_bytes() + b"\n")
                arguments = self._arguments()
                arguments.repository = candidate

                with self.assertRaisesRegex(
                    validator.CpuBenchmarkValidationError,
                    "does not match the trusted validator",
                ):
                    validator.validate(
                        arguments,
                        core=core,
                        collector=self.fake_collector,
                    )

    def test_main_returns_one_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(
                validator,
                "validate",
                side_effect=validator.CpuBenchmarkValidationError("closed failure"),
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            result = validator.main(
                [
                    "--collection-directory",
                    str(self.bundle),
                    "--repository",
                    str(REPOSITORY),
                    "--application-root",
                    str(self.application),
                    "--executable",
                    str(self.executable),
                    "--shim-artifact",
                    str(self.shim),
                    "--runtime-artifact",
                    str(self.runtime),
                    "--resolver-manifest",
                    str(self.resolver),
                    "--output",
                    str(self.output),
                ]
            )
        self.assertEqual(result, 1)
        self.assertIn("closed failure", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class SchemaEngineTests(unittest.TestCase):
    def test_rejects_unsupported_keyword_and_external_reference(self) -> None:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": validator.COLLECTION_SCHEMA_ID,
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
            "description": "unsupported on purpose",
        }
        registry = {
            validator.COLLECTION_SCHEMA_ID: schema,
            validator.VALIDATION_SCHEMA_ID: {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": validator.VALIDATION_SCHEMA_ID,
            },
            validator.TARGET_SCHEMA_ID: {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": validator.TARGET_SCHEMA_ID,
            },
        }
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "unsupported schema"
        ):
            validator._validate_schema_documents(registry)

        del schema["description"]
        schema["properties"] = {"value": {"$ref": "https://evil.invalid/x"}}
        with self.assertRaisesRegex(
            validator.CpuBenchmarkValidationError, "unresolved external"
        ):
            validator._validate_schema_documents(registry)

    def test_strict_integer_and_prefix_item_validation(self) -> None:
        schema = {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "prefixItems": [
                {"type": "integer", "multipleOf": 5},
                {"const": "tail"},
            ],
        }
        registry: dict[str, dict[str, Any]] = {}
        validator._validate_schema_instance(
            [10, "tail"], schema, schema, registry, "fixture"
        )
        for value in ([True, "tail"], [11, "tail"], [10, "wrong"]):
            with self.subTest(value=value), self.assertRaises(
                validator.CpuBenchmarkValidationError
            ):
                validator._validate_schema_instance(
                    value, schema, schema, registry, "fixture"
                )


if __name__ == "__main__":
    unittest.main()
