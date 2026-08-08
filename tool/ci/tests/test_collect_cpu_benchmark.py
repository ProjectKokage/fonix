from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
import unittest
from unittest import mock


CI_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_DIRECTORY))

import collect_cpu_benchmark as collector  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FakeCore:
    __file__ = str(CI_DIRECTORY / "cpu_benchmark_collection.py")

    class CpuBenchmarkCollectionError(RuntimeError):
        pass

    def __init__(self) -> None:
        self.tree_calls = 0

    def regular_file_identity(
        self, path: Path, *, label: str, maximum_bytes: int
    ) -> dict[str, object]:
        del label
        data = path.read_bytes()
        if not data or len(data) > maximum_bytes:
            raise self.CpuBenchmarkCollectionError("invalid file")
        return {"sizeBytes": len(data), "sha256": _sha256(data)}

    def canonical_application_tree_identity(
        self, root: Path, *, label: str, include_entries: bool = False
    ) -> dict[str, object]:
        del label
        self.tree_calls += 1
        records: list[dict[str, object]] = []
        file_count = 0
        directory_count = 1
        link_count = 0
        byte_count = 0
        for current, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            directory_names.sort()
            file_names.sort()
            parent = Path(current)
            for name in directory_names:
                path = parent / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    link_count += 1
                    records.append(
                        {"path": relative, "type": "link", "target": os.readlink(path)}
                    )
                else:
                    directory_count += 1
                    records.append({"path": relative, "type": "directory"})
            for name in file_names:
                path = parent / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    link_count += 1
                    records.append(
                        {"path": relative, "type": "link", "target": os.readlink(path)}
                    )
                    continue
                data = path.read_bytes()
                file_count += 1
                byte_count += len(data)
                records.append(
                    {
                        "path": relative,
                        "type": "file",
                        "mode": stat.S_IMODE(path.stat().st_mode),
                        "sizeBytes": len(data),
                        "sha256": _sha256(data),
                    }
                )
        encoded = json.dumps(
            records, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        result: dict[str, object] = {
            "format": "canonical-application-tree-v1",
            "fileCount": file_count,
            "directoryCount": directory_count,
            "symbolicLinkCount": link_count,
            "byteCount": byte_count,
            "sha256": _sha256(encoded),
        }
        if include_entries:
            result["_entries"] = records
        return result

    def validate_native_build_binding(
        self, *, repository: Path, shim_binary: Path, resolver_manifest: Path
    ) -> dict[str, object]:
        shim = self.regular_file_identity(
            shim_binary, label="shim", maximum_bytes=1024 * 1024
        )
        resolver = self.regular_file_identity(
            resolver_manifest, label="resolver", maximum_bytes=1024 * 1024
        )
        native_lock = self.regular_file_identity(
            repository / "native/versions.lock.yaml",
            label="lock",
            maximum_bytes=1024 * 1024,
        )
        return {
            "nativeLock": native_lock,
            "packageVersion": "0.0.0-test",
            "runtimeVersion": "test-runtime",
            "resolverManifest": resolver,
            "shimLibrary": shim,
            "embeddedBuildManifestSha256": "a" * 64,
            "embeddedBuildManifestCanonicalSha256": "b" * 64,
            "buildManifest": {"schemaVersion": 3},
            "nativePayloadFiles": [
                {
                    "id": "libpayload.dylib",
                    "sizeBytes": 7,
                    "sha256": "c" * 64,
                }
            ],
        }

    def repository_evidence_identity(
        self, repository: Path
    ) -> dict[str, object]:
        del repository
        identity = {"sizeBytes": 1, "sha256": "d" * 64}
        return {
            "sourceManifest": dict(identity),
            "sourceManifestValidator": dict(identity),
            "protocolDescriptor": dict(identity),
            "targetFragmentSchema": dict(identity),
            "model": dict(identity),
            "inputFixture": dict(identity),
            "referenceOutput": dict(identity),
            "metadata": dict(identity),
            "generator": dict(identity),
        }

    def strict_json_loads(
        self, data: bytes, *, label: str, maximum_bytes: int
    ) -> object:
        del label
        if not data or len(data) > maximum_bytes:
            raise self.CpuBenchmarkCollectionError("invalid JSON size")
        return json.loads(data)

    def validate_fragment(
        self,
        value: object,
        *,
        expected_challenge: str,
        expected_process_id: int,
        raw_sha256: str,
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            raise self.CpuBenchmarkCollectionError("fragment is not an object")
        if value != {
            "launchChallenge": expected_challenge,
            "processId": expected_process_id,
            "sample": expected_process_id,
        }:
            raise self.CpuBenchmarkCollectionError("launch binding mismatch")
        if len(raw_sha256) != 64:
            raise self.CpuBenchmarkCollectionError("invalid fragment hash")
        return dict(value)

    def derive_collection(
        self,
        *,
        fragment_payloads: Sequence[bytes],
        host_observation_payload: bytes,
        expected_launches: Sequence[tuple[str, int]],
        artifacts: Mapping[str, object],
        collector_sha256: str,
    ) -> dict[str, object]:
        fragments = [json.loads(payload) for payload in fragment_payloads]
        host_observation = json.loads(host_observation_payload)
        if len(fragments) != collector.LAUNCH_COUNT:
            raise self.CpuBenchmarkCollectionError("wrong launch count")
        return {
            "schemaVersion": 1,
            "result": "collected",
            "claimStatus": "measurement-only",
            "purpose": "cpu-benchmark-collection-only",
            "collector": {
                "id": "fonix-cpu-benchmark-collector-v1",
                "sha256": collector_sha256,
            },
            "protocol": "test",
            "launchCount": len(fragments),
            "rawHostObservation": {
                "sha256": _sha256(host_observation_payload),
                "record": host_observation,
            },
            "environment": dict(host_observation["environment"]),
            "artifacts": dict(artifacts),
            "identityTuple": {"test": True},
            "rawFragments": [
                {
                    "index": index,
                    "sha256": _sha256(fragment_payloads[index]),
                    "challenge": expected_launches[index][0],
                    "processId": expected_launches[index][1],
                    "fragment": dict(fragment),
                }
                for index, fragment in enumerate(fragments)
            ],
            "aggregates": {"test": True},
            "claimBoundary": "measurement-only test collection",
        }


class CpuBenchmarkCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fonix-cpu-collector-")
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "native").mkdir(parents=True)
        (self.repository / "native/versions.lock.yaml").write_bytes(b"lock")
        self.application = self.root / "Fonix Reference.app"
        self.application.mkdir()
        self.executable = self.application / "Contents/MacOS/Fonix Reference"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"executable")
        self.executable.chmod(0o700)
        self.resource = self.application / "data.bin"
        self.resource.write_bytes(b"application data")
        self.shim = self.application / "libdort_core.dylib"
        self.shim.write_bytes(b"shim")
        self.runtime = self.application / "libonnxruntime.1.dylib"
        self.runtime.write_bytes(b"runtime")
        self.resolver = self.application / "resolver.json"
        self.resolver.write_bytes(b"resolver")
        self.output = self.root / "collected"
        self.core = _FakeCore()
        self.environments: list[dict[str, str]] = []
        self.calls = 0
        self.challenge_values = [f"{index + 1:064x}" for index in range(5)]
        self.clock_value = 0
        self.usage_value = 0.0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _arguments(self) -> argparse.Namespace:
        return argparse.Namespace(
            repository=self.repository,
            application_root=self.application,
            executable=self.executable,
            shim_artifact=self.shim,
            runtime_artifact=self.runtime,
            resolver_manifest=self.resolver,
            provider_dependency=[],
            display=None,
            output_directory=self.output,
        )

    def _observation(
        self,
        _platform: str,
        _architecture: str,
    ) -> collector.HostObservation:
        return collector.HostObservation(
            device_identity_sha256="d" * 64,
            os_version="26.0",
            os_build="25A1",
            driver_identity="cpu-runtime",
            firmware_identity="not-exposed-by-host-api",
            power_mode="not-exposed-by-host-api",
            thermal_state="not-exposed-by-host-api",
        )

    def _challenge(self, index: int) -> str:
        return self.challenge_values[index]

    def _clock(self) -> int:
        self.clock_value += 1_000_000_000
        return self.clock_value

    def _usage(self) -> collector.UsageSnapshot:
        self.usage_value += 0.25
        return collector.UsageSnapshot(self.usage_value, self.usage_value / 2)

    def _runner(self, _command: object, **options: object) -> object:
        index = self.calls
        self.calls += 1
        environment = dict(options["environment"])  # type: ignore[arg-type]
        self.environments.append(environment)
        process_id = 1000 + index
        options["on_started"](process_id)  # type: ignore[operator]
        value = {
            "launchChallenge": environment[collector.BENCHMARK_CHALLENGE],
            "processId": process_id,
            "sample": process_id,
        }
        return SimpleNamespace(
            stdout=(
                collector.BENCHMARK_PREFIX
                + json.dumps(value, sort_keys=True, separators=(",", ":"))
                + "\n"
            ),
            stderr=collector.MACOS_RELEASE_BOOTSTRAP_STDERR,
        )

    def _collect(
        self,
        arguments: argparse.Namespace | None = None,
        **overrides: object,
    ) -> dict[str, Any]:
        options: dict[str, object] = {
            "core": self.core,
            "runner": self._runner,
            "host_identity": lambda: ("macos", "arm64"),
            "environment_observer": self._observation,
            "challenge_factory": self._challenge,
            "usage_reader": self._usage,
            "clock_reader": self._clock,
        }
        options.update(overrides)
        return collector.collect(  # type: ignore[arg-type]
            arguments if arguments is not None else self._arguments(),
            **options,
        )

    def _assert_no_output(self) -> None:
        self.assertFalse(self.output.exists())

    def _expected_output_names(self) -> set[str]:
        return {
            collector.COLLECTION_FILENAME,
            collector.HOST_OBSERVATIONS_FILENAME,
            *{
                collector.FRAGMENT_FILENAME.format(index=index)
                for index in range(collector.LAUNCH_COUNT)
            },
        }

    def test_linux_cpu_identity_excludes_volatile_frequency_counters(self) -> None:
        common = """processor : 0
vendor_id : GenuineIntel
cpu family : 6
model : 170
model name : Example CPU
stepping : 4
microcode : 0x1
flags : fpu sse
cpu MHz : {frequency}

processor : 1
cpu MHz : 800.000
"""

        def identity(frequency: str) -> bytes:
            with (
                mock.patch.object(
                    collector.platform, "machine", return_value="x86_64"
                ),
                mock.patch.object(
                    collector.platform, "processor", return_value="x86_64"
                ),
                mock.patch.object(
                    collector,
                    "_read_small_file",
                    return_value=common.format(frequency=frequency).encode("ascii"),
                ),
            ):
                return collector._linux_cpu_identity_source()

        first = identity("1200.000")
        second = identity("4200.000")

        self.assertEqual(first, second)
        self.assertNotIn(b"MHz", first)

    def test_macos_device_identity_hashes_one_bounded_platform_uuid(self) -> None:
        raw_uuid = "00112233-4455-6677-8899-AABBCCDDEEFF"
        output = SimpleNamespace(
            stdout=f'    "IOPlatformUUID" = "{raw_uuid}"\n', stderr=""
        )
        with (
            mock.patch.object(
                collector,
                "_regular_file",
                return_value=Path("/usr/sbin/ioreg"),
            ),
            mock.patch.object(
                collector.bounded_process,
                "run_bounded",
                return_value=output,
            ) as run,
            mock.patch.object(
                collector.platform, "mac_ver", return_value=("15.0", ("", "", ""), "")
            ),
            mock.patch.object(collector.platform, "version", return_value="24A1"),
            mock.patch.object(collector.platform, "processor", return_value="arm"),
            mock.patch.object(
                collector,
                "_macos_process_info_state",
                return_value=collector.MacOsProcessInfoState("nominal", False),
            ),
            mock.patch.object(
                collector,
                "_macos_power_mode",
                return_value=(
                    "macos-ac-power-low-power-off-profile-sha256-" + "a" * 64
                ),
            ),
        ):
            first = collector._observe_host_environment("macos", "arm64")
            second = collector._observe_host_environment("macos", "arm64")

        self.assertEqual(first.device_identity_sha256, second.device_identity_sha256)
        self.assertRegex(first.device_identity_sha256, r"^[0-9a-f]{64}$")
        self.assertNotIn(raw_uuid, json.dumps(first._asdict()))
        self.assertEqual(first.thermal_state, "nominal")
        self.assertEqual(
            first.power_mode,
            "macos-ac-power-low-power-off-profile-sha256-" + "a" * 64,
        )
        self.assertEqual(run.call_count, 2)
        command = run.call_args_list[0].args[0]
        self.assertEqual(
            command,
            (
                "/usr/sbin/ioreg",
                "-rd1",
                "-c",
                "IOPlatformExpertDevice",
            ),
        )
        self.assertEqual(run.call_args_list[0].kwargs["timeout_seconds"], 10)
        self.assertEqual(
            run.call_args_list[0].kwargs["maximum_stdout_bytes"], 256 * 1024
        )

    def test_macos_device_identity_rejects_missing_or_ambiguous_uuid(self) -> None:
        for stdout in (
            "no platform UUID\n",
            '"IOPlatformUUID" = "00112233-4455-6677-8899-aabbccddeeff"\n'
            '"IOPlatformUUID" = "ffeeddcc-bbaa-9988-7766-554433221100"\n',
        ):
            with (
                self.subTest(stdout=stdout),
                mock.patch.object(
                    collector,
                    "_regular_file",
                    return_value=Path("/usr/sbin/ioreg"),
                ),
                mock.patch.object(
                    collector.bounded_process,
                    "run_bounded",
                    return_value=SimpleNamespace(stdout=stdout, stderr=""),
                ),
                self.assertRaises(collector.CpuBenchmarkCollectorError),
            ):
                collector._macos_platform_identifier_sha256()

    def test_macos_process_info_uses_fixed_typed_objective_c_contract(self) -> None:
        runtime = SimpleNamespace(
            objc_getClass=mock.Mock(return_value=101),
            class_getName=mock.Mock(return_value=b"NSProcessInfo"),
            sel_registerName=mock.Mock(
                side_effect={
                    b"processInfo": 201,
                    b"thermalState": 202,
                    b"isLowPowerModeEnabled": 203,
                }.__getitem__
            ),
            sel_getName=mock.Mock(
                side_effect={
                    201: b"processInfo",
                    202: b"thermalState",
                    203: b"isLowPowerModeEnabled",
                }.__getitem__
            ),
            class_getClassMethod=mock.Mock(return_value=301),
            class_getInstanceMethod=mock.Mock(return_value=302),
            objc_msgSend=object(),
        )
        foundation = object()

        def message(
            _function: object,
            receiver: int,
            selector: int,
            result_type: object,
        ) -> object:
            if (receiver, selector, result_type) == (
                101,
                201,
                collector.ctypes.c_void_p,
            ):
                return 401
            if (receiver, selector, result_type) == (
                401,
                202,
                collector.ctypes.c_long,
            ):
                return 2
            if (receiver, selector, result_type) == (
                401,
                203,
                collector.ctypes.c_int8,
            ):
                return 1
            self.fail("unexpected Objective-C message")

        with (
            mock.patch.object(collector.sys, "platform", "darwin"),
            mock.patch.object(collector.platform, "machine", return_value="arm64"),
            mock.patch.object(
                collector.ctypes,
                "CDLL",
                side_effect=(foundation, runtime),
            ) as load,
            mock.patch.object(
                collector,
                "_objc_noarg_message",
                side_effect=message,
            ) as send,
        ):
            state = collector._macos_process_info_state()

        self.assertEqual(
            state,
            collector.MacOsProcessInfoState("serious", True),
        )
        self.assertEqual(
            load.call_args_list,
            [
                mock.call(
                    collector.MACOS_FOUNDATION,
                    mode=getattr(collector.ctypes, "RTLD_LOCAL", 0),
                ),
                mock.call(
                    collector.MACOS_OBJC_RUNTIME,
                    mode=getattr(collector.ctypes, "RTLD_LOCAL", 0),
                ),
            ],
        )
        runtime.objc_getClass.assert_called_once_with(b"NSProcessInfo")
        self.assertEqual(
            [call.args[0] for call in runtime.sel_registerName.call_args_list],
            [b"processInfo", b"thermalState", b"isLowPowerModeEnabled"],
        )
        self.assertEqual(runtime.objc_getClass.argtypes, [collector.ctypes.c_char_p])
        self.assertIs(runtime.objc_getClass.restype, collector.ctypes.c_void_p)
        self.assertEqual(send.call_count, 3)

    def test_macos_process_info_rejects_identity_drift_and_unknown_state(self) -> None:
        def runtime(*, class_name: bytes = b"NSProcessInfo") -> SimpleNamespace:
            return SimpleNamespace(
                objc_getClass=mock.Mock(return_value=101),
                class_getName=mock.Mock(return_value=class_name),
                sel_registerName=mock.Mock(
                    side_effect={
                        b"processInfo": 201,
                        b"thermalState": 202,
                        b"isLowPowerModeEnabled": 203,
                    }.__getitem__
                ),
                sel_getName=mock.Mock(
                    side_effect={
                        201: b"processInfo",
                        202: b"thermalState",
                        203: b"isLowPowerModeEnabled",
                    }.__getitem__
                ),
                class_getClassMethod=mock.Mock(return_value=301),
                class_getInstanceMethod=mock.Mock(return_value=302),
                objc_msgSend=object(),
            )

        hostile_runtime = runtime(class_name=b"HostileProcessInfo")
        with (
            mock.patch.object(collector.sys, "platform", "darwin"),
            mock.patch.object(collector.platform, "machine", return_value="arm64"),
            mock.patch.object(
                collector.ctypes,
                "CDLL",
                side_effect=(object(), hostile_runtime),
            ),
            mock.patch.object(collector, "_objc_noarg_message") as send,
        ):
            unavailable = collector._macos_process_info_state()
        self.assertEqual(
            unavailable,
            collector.MacOsProcessInfoState(
                collector.HOST_API_UNAVAILABLE,
                None,
            ),
        )
        send.assert_not_called()

        valid_runtime = runtime()
        with (
            mock.patch.object(collector.sys, "platform", "darwin"),
            mock.patch.object(collector.platform, "machine", return_value="arm64"),
            mock.patch.object(
                collector.ctypes,
                "CDLL",
                side_effect=(object(), valid_runtime),
            ),
            mock.patch.object(
                collector,
                "_objc_noarg_message",
                side_effect=(401, 99, 0),
            ),
        ):
            unknown = collector._macos_process_info_state()
        self.assertEqual(
            unknown,
            collector.MacOsProcessInfoState(
                collector.HOST_API_UNAVAILABLE,
                False,
            ),
        )

        invalid_bool_runtime = runtime()
        with (
            mock.patch.object(collector.sys, "platform", "darwin"),
            mock.patch.object(collector.platform, "machine", return_value="arm64"),
            mock.patch.object(
                collector.ctypes,
                "CDLL",
                side_effect=(object(), invalid_bool_runtime),
            ),
            mock.patch.object(
                collector,
                "_objc_noarg_message",
                side_effect=(401, 0, 2),
            ),
        ):
            invalid_bool = collector._macos_process_info_state()
        self.assertEqual(
            invalid_bool,
            collector.MacOsProcessInfoState(
                "nominal",
                None,
            ),
        )

    @unittest.skipUnless(
        sys.platform == "darwin" and collector.platform.machine() == "arm64",
        "requires the Darwin arm64 Objective-C runtime",
    )
    def test_live_macos_process_info_binding_returns_closed_api_values(self) -> None:
        state = collector._macos_process_info_state()

        self.assertIn(
            state.thermal_state,
            {
                "nominal",
                "fair",
                "serious",
                "critical",
                collector.HOST_API_UNAVAILABLE,
            },
        )
        self.assertIs(type(state.low_power_mode), bool)

    def test_macos_pmset_observer_is_bounded_and_hashes_active_profile_only(
        self,
    ) -> None:
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def observe(
            inactive_sleep: int,
            active_separator: str = " ",
            dynamic_low_power: bool = False,
        ) -> str:
            def runner(command: object, **options: object) -> object:
                typed_command = tuple(command)  # type: ignore[arg-type]
                calls.append((typed_command, dict(options)))
                if typed_command[-1] == "batt":
                    stdout = (
                        "Now drawing from 'AC Power'\n"
                        " -InternalBattery-0 (id=1)\t100%; charged\n"
                    )
                else:
                    stdout = (
                        "Battery Power:\n"
                        " powermode 1\n"
                        f" sleep {inactive_sleep}\n"
                        " hibernatefile /private/hidden/inactive\n"
                        "AC Power:\n"
                        " Sleep On Power Button 1\n"
                        " powermode         0\n"
                        f" hibernatefile{active_separator}/private/hidden/active\n"
                    )
                return SimpleNamespace(stdout=stdout, stderr="")

            with mock.patch.object(
                collector,
                "_regular_file",
                return_value=collector.MACOS_PMSET,
            ):
                return collector._macos_power_mode(
                    dynamic_low_power,
                    runner=runner,
                )

        first = observe(5)
        second = observe(99)
        changed_active_spacing = observe(99, "  ")
        dynamic_low_power = observe(99, dynamic_low_power=True)

        self.assertEqual(first, second)
        self.assertNotEqual(second, changed_active_spacing)
        self.assertRegex(
            dynamic_low_power,
            r"^macos-ac-power-low-power-on-profile-sha256-[0-9a-f]{64}$",
        )
        self.assertEqual(
            second.rsplit("profile-sha256-", 1)[1],
            dynamic_low_power.rsplit("profile-sha256-", 1)[1],
        )
        self.assertRegex(
            first,
            r"^macos-ac-power-low-power-off-profile-sha256-[0-9a-f]{64}$",
        )
        self.assertNotIn("private", first)
        self.assertEqual(
            [call[0] for call in calls[:3]],
            [
                ("/usr/bin/pmset", "-g", "batt"),
                ("/usr/bin/pmset", "-g", "custom"),
                ("/usr/bin/pmset", "-g", "batt"),
            ],
        )
        self.assertEqual(calls[0][1]["cwd"], Path("/"))
        self.assertEqual(
            calls[0][1]["environment"],
            {
                "PATH": "/usr/bin:/bin:/usr/sbin",
                "LC_ALL": "C",
                "LANG": "C",
            },
        )
        self.assertEqual(calls[0][1]["timeout_seconds"], 10)
        self.assertEqual(
            calls[0][1]["maximum_stdout_bytes"],
            collector.MAX_PMSET_BATTERY_STDOUT_BYTES,
        )
        self.assertEqual(
            calls[1][1]["maximum_stdout_bytes"],
            collector.MAX_PMSET_CUSTOM_STDOUT_BYTES,
        )
        self.assertEqual(
            calls[0][1]["maximum_stderr_bytes"],
            collector.MAX_PMSET_STDERR_BYTES,
        )

    def test_macos_pmset_accepts_closed_sources_and_opaque_profiles(self) -> None:
        sources = {
            "AC Power": (
                "ac-power",
                "AC Power:\n sleep 1\nBattery Power:\n",
            ),
            "Battery Power": (
                "battery-power",
                "Battery Power:\n powermode 2\n",
            ),
            "UPS Power": (
                "ups-power",
                "UPS Power:\n lowpowermode 1\n powermode future-value\n",
            ),
        }
        for source, (slug, opaque_profile) in sources.items():
            with self.subTest(source=source):
                outputs = iter(
                    (
                        SimpleNamespace(
                            stdout=f"Now drawing from '{source}'\n",
                            stderr="",
                        ),
                        SimpleNamespace(
                            stdout=opaque_profile,
                            stderr="",
                        ),
                        SimpleNamespace(
                            stdout=f"Now drawing from '{source}'\n",
                            stderr="",
                        ),
                    )
                )
                with mock.patch.object(
                    collector,
                    "_regular_file",
                    return_value=collector.MACOS_PMSET,
                ):
                    value = collector._macos_power_mode(
                        False,
                        runner=lambda *_args, **_kwargs: next(outputs),
                    )
                self.assertRegex(
                    value,
                    rf"^macos-{slug}-low-power-off-profile-sha256-[0-9a-f]{{64}}$",
                )

    def test_macos_pmset_malformed_or_ambiguous_data_is_unavailable(self) -> None:
        valid_battery = "Now drawing from 'AC Power'\n"
        valid_profile = "AC Power:\n sleep 1\n hibernatefile /private/opaque\n"
        too_many_fields = "AC Power:\n" + "".join(
            f" field{index} 1\n"
            for index in range(collector.MAX_PMSET_PROFILE_FIELDS + 1)
        )
        too_long_setting = (
            "AC Power:\n "
            + "x" * (collector.MAX_PMSET_SETTING_BYTES + 1)
            + "\n"
        )
        cases = {
            "carriage-return": (
                valid_battery.replace("\n", "\r\n"),
                valid_profile,
                False,
                "",
            ),
            "non-ascii": (
                valid_battery,
                valid_profile.replace("sleep", "sléep"),
                False,
                "",
            ),
            "unknown-source": (
                "Now drawing from 'Solar Power'\n",
                valid_profile,
                False,
                "",
            ),
            "duplicate-header": (
                valid_battery,
                valid_profile + "AC Power:\n standby 2\n",
                False,
                "",
            ),
            "missing-active-profile": (
                valid_battery,
                "Battery Power:\n lowpowermode 0\n",
                False,
                "",
            ),
            "empty-active-profile": (
                valid_battery,
                "AC Power:\n",
                False,
                "",
            ),
            "duplicate-setting": (
                valid_battery,
                valid_profile + " sleep 1\n",
                False,
                "",
            ),
            "setting-before-header": (
                valid_battery,
                " sleep 1\n" + valid_profile,
                False,
                "",
            ),
            "unknown-profile-header": (
                valid_battery,
                "Solar Power:\n sleep 1\n",
                False,
                "",
            ),
            "malformed-profile-header": (
                valid_battery,
                "AC Power\n sleep 1\n",
                False,
                "",
            ),
            "trailing-setting-whitespace": (
                valid_battery,
                valid_profile.replace("sleep 1", "sleep 1 "),
                False,
                "",
            ),
            "empty-setting": (
                valid_battery,
                "AC Power:\n \n",
                False,
                "",
            ),
            "control-character": (
                valid_battery,
                valid_profile.replace("sleep 1", "sleep\x1f1"),
                False,
                "",
            ),
            "too-long-setting": (
                valid_battery,
                too_long_setting,
                False,
                "",
            ),
            "too-many-fields": (
                valid_battery,
                too_many_fields,
                False,
                "",
            ),
            "unexpected-stderr": (
                valid_battery,
                valid_profile,
                False,
                "warning\n",
            ),
        }
        for name, (battery, profile, low_power, stderr) in cases.items():
            with self.subTest(name=name):
                outputs = iter(
                    (
                        SimpleNamespace(stdout=battery, stderr=stderr),
                        SimpleNamespace(stdout=profile, stderr=""),
                        SimpleNamespace(stdout=battery, stderr=""),
                    )
                )
                with mock.patch.object(
                    collector,
                    "_regular_file",
                    return_value=collector.MACOS_PMSET,
                ):
                    value = collector._macos_power_mode(
                        low_power,
                        runner=lambda *_args, **_kwargs: next(outputs),
                    )
                self.assertEqual(value, collector.HOST_API_UNAVAILABLE)

        changed_source_outputs = iter(
            (
                SimpleNamespace(stdout=valid_battery, stderr=""),
                SimpleNamespace(
                    stdout=(
                        valid_profile
                        + "Battery Power:\n sleep 5\n"
                    ),
                    stderr="",
                ),
                SimpleNamespace(
                    stdout="Now drawing from 'Battery Power'\n",
                    stderr="",
                ),
            )
        )
        with mock.patch.object(
            collector,
            "_regular_file",
            return_value=collector.MACOS_PMSET,
        ):
            changed_source = collector._macos_power_mode(
                False,
                runner=lambda *_args, **_kwargs: next(changed_source_outputs),
            )
        self.assertEqual(changed_source, collector.HOST_API_UNAVAILABLE)

        runner = mock.Mock(side_effect=AssertionError("must not run"))
        self.assertEqual(
            collector._macos_power_mode(None, runner=runner),
            collector.HOST_API_UNAVAILABLE,
        )
        runner.assert_not_called()

    def test_collects_exactly_five_pid_and_challenge_bound_fresh_processes(
        self,
    ) -> None:
        record = self._collect()

        self.assertEqual(self.calls, collector.LAUNCH_COUNT)
        self.assertEqual(record["launchCount"], collector.LAUNCH_COUNT)
        self.assertEqual(self.core.tree_calls, collector.LAUNCH_COUNT + 2)
        self.assertEqual(
            record["artifacts"]["executable"],
            {
                "id": self.executable.name,
                "sizeBytes": len(b"executable"),
                "sha256": _sha256(b"executable"),
            },
        )
        self.assertEqual(
            record["artifacts"]["embeddedBuildManifest"], {"schemaVersion": 3}
        )
        self.assertEqual(
            record["artifacts"]["embeddedBuildManifestCanonicalSha256"],
            "b" * 64,
        )
        self.assertEqual(
            record["artifacts"]["nativePayloadFiles"][0]["id"],
            "libpayload.dylib",
        )
        self.assertEqual(
            set(record["artifacts"]["repositoryEvidence"]),
            {
                "sourceManifest",
                "sourceManifestValidator",
                "protocolDescriptor",
                "targetFragmentSchema",
                "model",
                "inputFixture",
                "referenceOutput",
                "metadata",
                "generator",
            },
        )
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(
            sorted(path.name for path in self.output.iterdir()),
            [
                collector.COLLECTION_FILENAME,
                *[
                    collector.FRAGMENT_FILENAME.format(index=index)
                    for index in range(collector.LAUNCH_COUNT)
                ],
                collector.HOST_OBSERVATIONS_FILENAME,
            ],
        )
        for index in range(collector.LAUNCH_COUNT):
            payload = (
                self.output / collector.FRAGMENT_FILENAME.format(index=index)
            ).read_bytes()
            value = json.loads(payload)
            self.assertEqual(value["launchChallenge"], self.challenge_values[index])
            self.assertEqual(value["processId"], 1000 + index)
        host_payload = (
            self.output / collector.HOST_OBSERVATIONS_FILENAME
        ).read_bytes()
        self.assertEqual(
            record["rawHostObservation"],
            {
                "sha256": _sha256(host_payload),
                "record": json.loads(host_payload),
            },
        )
        self.assertEqual(
            record["environment"]["cpuUtilization"]["launches"][0],
            {
                "index": 0,
                "launchChallenge": self.challenge_values[0],
                "processId": 1000,
                "userMicroseconds": 250000,
                "systemMicroseconds": 125000,
                "wallMicroseconds": 1000000,
            },
        )
        self.assertEqual(
            record["environment"]["comparability"],
            {
                "status": "incomplete",
                "powerMode": {
                    "availability": "unavailable",
                    "stability": "indeterminate",
                    "stableValue": None,
                },
                "thermalState": {
                    "availability": "unavailable",
                    "drift": "indeterminate",
                    "stableValue": None,
                },
                "reasons": [
                    "power-mode-unavailable",
                    "thermal-state-unavailable",
                ],
            },
        )

    def test_replayed_challenge_and_wrong_pid_fail_without_publication(self) -> None:
        for mutation in ("challenge", "pid"):
            with self.subTest(mutation=mutation):
                self.calls = 0
                self.environments.clear()
                self.clock_value = 0
                self.usage_value = 0.0

                def runner(_command: object, **options: object) -> object:
                    index = self.calls
                    self.calls += 1
                    process_id = 2000 + index
                    options["on_started"](process_id)  # type: ignore[operator]
                    environment = options["environment"]
                    challenge = environment[  # type: ignore[index]
                        collector.BENCHMARK_CHALLENGE
                    ]
                    if index == 1 and mutation == "challenge":
                        challenge = self.challenge_values[0]
                    returned_pid = (
                        process_id + 1
                        if index == 1 and mutation == "pid"
                        else process_id
                    )
                    value = {
                        "launchChallenge": challenge,
                        "processId": returned_pid,
                        "sample": returned_pid,
                    }
                    return SimpleNamespace(
                        stdout=collector.BENCHMARK_PREFIX + json.dumps(value) + "\n",
                        stderr=collector.MACOS_RELEASE_BOOTSTRAP_STDERR,
                    )

                with self.assertRaises(self.core.CpuBenchmarkCollectionError):
                    self._collect(runner=runner)
                self.assertEqual(self.calls, 2)
                self._assert_no_output()

    def test_failed_launch_is_not_retried_and_removes_private_directory(self) -> None:
        def runner(command: object, **options: object) -> object:
            if self.calls == 2:
                self.calls += 1
                raise RuntimeError("synthetic launch failure")
            return self._runner(command, **options)

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError, "launch 3 failed"
        ):
            self._collect(runner=runner)

        self.assertEqual(self.calls, 3)
        self._assert_no_output()

    def test_keyboard_interrupt_propagates_while_private_directory_is_cleaned(
        self,
    ) -> None:
        def interrupt(_command: object, **_options: object) -> object:
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._collect(runner=interrupt)

        self._assert_no_output()

    def test_extra_stdout_fails_closed(self) -> None:
        for mutation in ("extra-prefix", "extra-suffix"):
            with self.subTest(mutation=mutation):
                self.calls = 0
                self.clock_value = 0
                self.usage_value = 0.0

                def runner(command: object, **options: object) -> object:
                    output = self._runner(command, **options)
                    if mutation == "extra-prefix":
                        output.stdout = "startup\n" + output.stdout
                    else:
                        output.stdout += "shutdown\n"
                    return output

                with self.assertRaises(collector.CpuBenchmarkCollectorError):
                    self._collect(runner=runner)
                self.assertEqual(self.calls, 1)
                self._assert_no_output()

    def test_macos_stderr_requires_exact_pinned_bootstrap_line(self) -> None:
        exact = collector.MACOS_RELEASE_BOOTSTRAP_STDERR
        cases = {
            "prefix": "warning\n" + exact,
            "suffix": exact + "warning\n",
            "changed-line": exact.replace("MetalSDF", "Metal"),
            "empty": "",
        }
        for mutation, stderr in cases.items():
            with self.subTest(mutation=mutation):
                self.calls = 0
                self.clock_value = 0
                self.usage_value = 0.0

                def runner(command: object, **options: object) -> object:
                    output = self._runner(command, **options)
                    output.stderr = stderr
                    return output

                with self.assertRaisesRegex(
                    collector.CpuBenchmarkCollectorError,
                    "stderr does not match its closed platform contract",
                ):
                    self._collect(runner=runner)
                self.assertEqual(self.calls, 1)
                self._assert_no_output()

    def test_postrun_application_mutation_fails_before_publication(self) -> None:
        def runner(command: object, **options: object) -> object:
            output = self._runner(command, **options)
            if self.calls == 1:
                self.resource.write_bytes(b"mutated application data")
            return output

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError, "identity changed"
        ):
            self._collect(runner=runner)

        self.assertEqual(self.calls, 1)
        self._assert_no_output()

    def test_trusted_tool_mutation_after_launches_fails_before_derivation(self) -> None:
        before = {
            "collector": {"sizeBytes": 10, "sha256": "a" * 64},
            "core": {"sizeBytes": 20, "sha256": "b" * 64},
        }
        for changed_role in ("collector", "core"):
            with self.subTest(changed_role=changed_role):
                self.calls = 0
                self.clock_value = 0
                self.usage_value = 0.0
                after = {
                    role: dict(identity) for role, identity in before.items()
                }
                after[changed_role]["sha256"] = "c" * 64
                with (
                    mock.patch.object(
                        collector,
                        "_trusted_tool_identities",
                        side_effect=(before, after),
                    ),
                    mock.patch.object(
                        self.core,
                        "derive_collection",
                        wraps=self.core.derive_collection,
                    ) as derive,
                    self.assertRaisesRegex(
                        collector.CpuBenchmarkCollectorError,
                        "trusted collector or collection core identity changed",
                    ),
                ):
                    self._collect()

                self.assertEqual(self.calls, collector.LAUNCH_COUNT)
                derive.assert_not_called()
                self._assert_no_output()

    def test_reported_collector_hash_uses_stable_prelaunch_identity(self) -> None:
        identities = {
            "collector": {"sizeBytes": 10, "sha256": "e" * 64},
            "core": {"sizeBytes": 20, "sha256": "f" * 64},
        }
        with mock.patch.object(
            collector,
            "_trusted_tool_identities",
            side_effect=(identities, identities),
        ):
            record = self._collect()

        self.assertEqual(record["collector"]["sha256"], "e" * 64)

    def test_launch_environment_is_a_positive_allowlist(self) -> None:
        hostile = {
            "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
            "LD_PRELOAD": "/tmp/evil.so",
            "ASAN_OPTIONS": "log_path=/tmp/asan",
            "LLVM_PROFILE_FILE": "/tmp/profile",
            "FONIX_OTHER": "ambient",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            self._collect()

        self.assertEqual(len(self.environments), collector.LAUNCH_COUNT)
        for index, environment in enumerate(self.environments):
            for key in hostile:
                self.assertNotIn(key, environment)
            self.assertEqual(environment[collector.BENCHMARK_ACTIVATION], "1")
            self.assertEqual(
                environment[collector.BENCHMARK_CHALLENGE],
                self.challenge_values[index],
            )
            self.assertEqual(
                {key for key in environment if key.startswith("FONIX_")},
                {collector.BENCHMARK_ACTIVATION, collector.BENCHMARK_CHALLENGE},
            )

    def test_power_and_thermal_are_observed_around_every_launch(self) -> None:
        observation_index = 0
        power_modes = (
            "macos-ac-power-low-power-off-profile-sha256-" + "a" * 64,
            "macos-ac-power-low-power-off-profile-sha256-" + "b" * 64,
        )
        thermal_states = ("nominal", "fair")

        def observe(
            _platform: str, _architecture: str
        ) -> collector.HostObservation:
            nonlocal observation_index
            index = observation_index
            observation_index += 1
            return collector.HostObservation(
                device_identity_sha256="d" * 64,
                os_version="26.0",
                os_build="25A1",
                driver_identity="cpu-runtime",
                firmware_identity="not-exposed-by-host-api",
                power_mode=power_modes[index % len(power_modes)],
                thermal_state=thermal_states[index % len(thermal_states)],
            )

        record = self._collect(environment_observer=observe)

        self.assertEqual(observation_index, collector.LAUNCH_COUNT * 2)
        observations = record["environment"]["launchObservations"]
        self.assertEqual(
            observations[0],
            {
                "index": 0,
                "launchChallenge": self.challenge_values[0],
                "processId": 1000,
                "powerModeStart": power_modes[0],
                "powerModeEnd": power_modes[1],
                "thermalStateStart": thermal_states[0],
                "thermalStateEnd": thermal_states[1],
            },
        )
        self.assertEqual(observations[-1]["powerModeEnd"], power_modes[1])
        self.assertEqual(
            record["environment"]["comparability"],
            {
                "status": "non-comparable",
                "powerMode": {
                    "availability": "available",
                    "stability": "changed",
                    "stableValue": None,
                },
                "thermalState": {
                    "availability": "available",
                    "drift": "observed",
                    "stableValue": None,
                },
                "reasons": ["power-mode-changed", "thermal-drift-observed"],
            },
        )

    def test_stable_available_controls_are_explicitly_baseline_comparable(self) -> None:
        power_mode = (
            "macos-ac-power-low-power-off-profile-sha256-" + "a" * 64
        )

        def observe(
            _platform: str, _architecture: str
        ) -> collector.HostObservation:
            return collector.HostObservation(
                device_identity_sha256="d" * 64,
                os_version="26.0",
                os_build="25A1",
                driver_identity="cpu-runtime",
                firmware_identity="firmware",
                power_mode=power_mode,
                thermal_state="nominal",
            )

        record = self._collect(environment_observer=observe)

        self.assertEqual(
            record["environment"]["comparability"],
            {
                "status": "baseline-comparable",
                "powerMode": {
                    "availability": "available",
                    "stability": "stable",
                    "stableValue": power_mode,
                },
                "thermalState": {
                    "availability": "available",
                    "drift": "none-observed",
                    "stableValue": "nominal",
                },
                "reasons": [],
            },
        )

    def test_host_observation_child_cpu_is_outside_app_usage_window(self) -> None:
        user_seconds = 0.0

        def observe(
            _platform: str, _architecture: str
        ) -> collector.HostObservation:
            nonlocal user_seconds
            user_seconds += 10.0
            return self._observation(_platform, _architecture)

        def usage() -> collector.UsageSnapshot:
            return collector.UsageSnapshot(user_seconds, 0.0)

        def runner(command: object, **options: object) -> object:
            nonlocal user_seconds
            result = self._runner(command, **options)
            user_seconds += 0.25
            return result

        record = self._collect(
            environment_observer=observe,
            usage_reader=usage,
            runner=runner,
        )

        self.assertEqual(
            [
                value["userMicroseconds"]
                for value in record["environment"]["cpuUtilization"]["launches"]
            ],
            [250_000] * collector.LAUNCH_COUNT,
        )

    def test_existing_output_collision_fails_before_launch(self) -> None:
        self.output.mkdir(mode=0o700)

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError, "must not already exist"
        ):
            self._collect()

        self.assertEqual(self.calls, 0)
        self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_must_not_overlap_measured_or_repository_trees(self) -> None:
        alias = self.root / "application-alias"
        alias.symlink_to(self.application, target_is_directory=True)
        cases = {
            "inside-application": self.application / "evidence",
            "inside-repository": self.repository / "evidence",
            "equal-application": self.application,
            "ancestor-of-both": self.root,
            "aliased-parent": alias / "evidence",
        }
        for name, output in cases.items():
            with self.subTest(name=name):
                arguments = self._arguments()
                arguments.output_directory = output
                with self.assertRaisesRegex(
                    collector.CpuBenchmarkCollectorError,
                    "inside|equal|contain|alias",
                ):
                    self._collect(arguments)
                self.assertEqual(self.calls, 0)

    def test_output_is_reserved_only_after_every_candidate_finishes(self) -> None:
        private_roots: list[Path] = []

        def runner(command: object, **options: object) -> object:
            private_root = Path(options["cwd"]).parent  # type: ignore[arg-type]
            private_roots.append(private_root)
            self.assertFalse(private_root.is_relative_to(self.root))
            self.assertFalse(self.output.exists())
            environment = options["environment"]
            self.assertTrue(
                all(
                    str(self.output) not in value
                    for value in environment.values()  # type: ignore[union-attr]
                )
            )
            return self._runner(command, **options)

        self._collect(runner=runner)

        self.assertEqual(len(private_roots), collector.LAUNCH_COUNT)
        self.assertTrue(all(not path.exists() for path in private_roots))

    def test_replaced_launch_root_is_not_recursively_removed(self) -> None:
        replacement: Path | None = None
        displaced: Path | None = None
        victim: Path | None = None

        def runner(command: object, **options: object) -> object:
            nonlocal replacement, displaced, victim
            result = self._runner(command, **options)
            replacement = Path(options["cwd"]).parent  # type: ignore[arg-type]
            displaced = replacement.with_name(f"{replacement.name}-displaced")
            replacement.rename(displaced)
            replacement.mkdir(mode=0o700)
            victim = replacement / "must-survive"
            victim.mkdir()
            (victim / "data").write_bytes(b"unrelated")
            return result

        try:
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "private directory was replaced",
            ):
                self._collect(runner=runner)

            assert replacement is not None
            assert displaced is not None
            assert victim is not None
            self.assertEqual((victim / "data").read_bytes(), b"unrelated")
            self.assertTrue(displaced.is_dir())
            self.assertEqual(list(displaced.iterdir()), [])
            self.assertFalse(self.output.exists())
        finally:
            if victim is not None and victim.exists():
                (victim / "data").unlink(missing_ok=True)
                victim.rmdir()
            if replacement is not None and replacement.exists():
                replacement.rmdir()
            if displaced is not None and displaced.exists():
                displaced.rmdir()

    def test_replaced_publication_path_preserves_both_inodes(self) -> None:
        original_write = collector._write_new_file
        displaced = self.root / "displaced-publication"
        victim: Path | None = None
        replaced = False

        def replace_after_first_write(
            descriptor: int, name: str, data: bytes
        ) -> None:
            nonlocal replaced, victim
            original_write(descriptor, name, data)
            if replaced:
                return
            replaced = True
            self.output.rename(displaced)
            self.output.mkdir(mode=0o700)
            victim = self.output / "must-survive"
            victim.mkdir()
            (victim / "data").write_bytes(b"unrelated")

        try:
            with mock.patch.object(
                collector,
                "_write_new_file",
                side_effect=replace_after_first_write,
            ):
                with self.assertRaisesRegex(
                    collector.CpuBenchmarkCollectorError,
                    "output directory path was replaced",
                ):
                    self._collect()

            assert victim is not None
            self.assertEqual((victim / "data").read_bytes(), b"unrelated")
            self.assertEqual(
                {path.name for path in self.output.iterdir()},
                {"must-survive"},
            )
            self.assertTrue(displaced.is_dir())
            self.assertEqual(
                {path.name for path in displaced.iterdir()},
                self._expected_output_names(),
            )
        finally:
            if victim is not None and victim.exists():
                (victim / "data").unlink(missing_ok=True)
                victim.rmdir()
            if self.output.exists():
                self.output.rmdir()
            if displaced.exists():
                for path in displaced.iterdir():
                    path.unlink()
                displaced.rmdir()

    def test_replacement_during_final_inventory_fails_before_success(self) -> None:
        original_verify = collector._verify_output_inventory
        displaced = self.root / "displaced-final-output"
        victim: Path | None = None
        verify_count = 0

        def replace_after_final_inventory(
            owner: collector.OwnedDirectory,
            expected: Mapping[str, bytes],
        ) -> None:
            nonlocal verify_count, victim
            verify_count += 1
            original_verify(owner, expected)
            if verify_count != 2:
                return
            self.output.rename(displaced)
            self.output.mkdir(mode=0o700)
            victim = self.output / "must-survive"
            victim.write_bytes(b"unrelated")

        with mock.patch.object(
            collector,
            "_verify_output_inventory",
            side_effect=replace_after_final_inventory,
        ):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "output directory path was replaced",
            ):
                self._collect()

        assert victim is not None
        self.assertEqual(verify_count, 2)
        self.assertEqual(victim.read_bytes(), b"unrelated")
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {"must-survive"},
        )
        self.assertEqual(
            {path.name for path in displaced.iterdir()},
            self._expected_output_names(),
        )

    def test_output_parent_relocation_before_reservation_fails_closed(self) -> None:
        output_parent = self.root / "publication-parent"
        output_parent.mkdir(mode=0o700)
        self.output = output_parent / "collected"
        relocated = self.application / "relocated-publication-parent"
        original_derive = self.core.derive_collection

        def relocate_parent(**arguments: object) -> dict[str, object]:
            result = original_derive(**arguments)  # type: ignore[arg-type]
            output_parent.rename(relocated)
            output_parent.mkdir(mode=0o700)
            (output_parent / "must-survive").write_bytes(b"unrelated")
            return result

        with mock.patch.object(
            self.core,
            "derive_collection",
            side_effect=relocate_parent,
        ):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "output parent path identity changed",
            ):
                self._collect()

        self.assertEqual(self.calls, collector.LAUNCH_COUNT)
        self.assertEqual(list(relocated.iterdir()), [])
        self.assertEqual(
            (output_parent / "must-survive").read_bytes(),
            b"unrelated",
        )
        self.assertFalse(self.output.exists())

    def test_output_parent_relocation_during_publication_fails_closed(self) -> None:
        output_parent = self.root / "publication-parent"
        output_parent.mkdir(mode=0o700)
        self.output = output_parent / "collected"
        relocated = self.application / "relocated-publication-parent"
        original_write = collector._write_new_file
        moved = False

        def relocate_after_first_write(
            descriptor: int,
            name: str,
            data: bytes,
        ) -> None:
            nonlocal moved
            original_write(descriptor, name, data)
            if moved:
                return
            moved = True
            output_parent.rename(relocated)
            output_parent.mkdir(mode=0o700)
            self.output.mkdir(mode=0o700)
            (self.output / "must-survive").write_bytes(b"unrelated")

        with mock.patch.object(
            collector,
            "_write_new_file",
            side_effect=relocate_after_first_write,
        ):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "output parent path identity changed",
            ):
                self._collect()

        self.assertTrue(moved)
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {"must-survive"},
        )
        self.assertEqual(
            {path.name for path in (relocated / "collected").iterdir()},
            self._expected_output_names(),
        )

    def test_unexpected_publication_entry_fails_exact_inventory(self) -> None:
        original_write = collector._write_new_file
        injected = False

        def inject_entry(descriptor: int, name: str, data: bytes) -> None:
            nonlocal injected
            original_write(descriptor, name, data)
            if name != collector.COLLECTION_FILENAME or injected:
                return
            injected = True
            extra = os.open(
                "unexpected",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=descriptor,
            )
            try:
                os.write(extra, b"unexpected")
            finally:
                os.close(extra)

        with mock.patch.object(
            collector,
            "_write_new_file",
            side_effect=inject_entry,
        ):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "inventory is not exact",
            ):
                self._collect()

        self.assertTrue(injected)
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {*self._expected_output_names(), "unexpected"},
        )

    def test_parent_sync_failure_preserves_retry_blocking_output(self) -> None:
        original_fsync = os.fsync
        parent_status = self.root.stat()

        def fail_parent_sync(descriptor: int) -> None:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) == (
                parent_status.st_dev,
                parent_status.st_ino,
            ):
                raise OSError(errno.EIO, "synthetic parent sync failure")
            original_fsync(descriptor)

        with mock.patch.object(collector.os, "fsync", side_effect=fail_parent_sync):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "publication durability could not be confirmed",
            ):
                self._collect()

        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            self._expected_output_names(),
        )

    def test_partial_write_failure_leaves_residue_and_blocks_retry(self) -> None:
        original_write = collector._write_new_file
        write_count = 0

        def fail_second_write(descriptor: int, name: str, data: bytes) -> None:
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise OSError(errno.EIO, "synthetic output write failure")
            original_write(descriptor, name, data)

        with mock.patch.object(
            collector,
            "_write_new_file",
            side_effect=fail_second_write,
        ):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "publication durability could not be confirmed",
            ):
                self._collect()

        self.assertEqual(self.calls, collector.LAUNCH_COUNT)
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {collector.FRAGMENT_FILENAME.format(index=0)},
        )

        self.calls = 0
        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError,
            "must not already exist",
        ):
            self._collect()
        self.assertEqual(self.calls, 0)

    def test_late_output_reservation_collision_preserves_winner(self) -> None:
        original_create = collector._create_owned_directory

        def collide(
            parent_descriptor: int,
            parent_identity: collector.DirectoryIdentity,
            name: str,
            label: str,
        ) -> collector.OwnedDirectory:
            if label == "output directory":
                self.output.mkdir(mode=0o700)
                (self.output / "winner").write_bytes(b"other collector")
            return original_create(parent_descriptor, parent_identity, name, label)

        with mock.patch.object(
            collector, "_create_owned_directory", side_effect=collide
        ):
            with self.assertRaisesRegex(
                collector.CpuBenchmarkCollectorError,
                "output directory already exists",
            ):
                self._collect()

        self.assertEqual(self.calls, collector.LAUNCH_COUNT)
        self.assertEqual((self.output / "winner").read_bytes(), b"other collector")
        self.assertEqual({path.name for path in self.output.iterdir()}, {"winner"})

    def test_linux_requires_closed_display_and_sets_only_that_display(self) -> None:
        arguments = self._arguments()
        arguments.display = ":123"
        environments: list[dict[str, str]] = []

        def runner(command: object, **options: object) -> object:
            environments.append(dict(options["environment"]))  # type: ignore[arg-type]
            output = self._runner(command, **options)
            output.stderr = ""
            return output

        collector.collect(
            arguments,
            core=self.core,
            runner=runner,
            host_identity=lambda: ("linux", "x86_64"),
            environment_observer=self._observation,
            challenge_factory=self._challenge,
            usage_reader=self._usage,
            clock_reader=self._clock,
        )

        self.assertEqual({value["DISPLAY"] for value in environments}, {":123"})

    def test_linux_rejects_the_macos_bootstrap_line(self) -> None:
        arguments = self._arguments()
        arguments.display = ":123"

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError,
            "stderr does not match its closed platform contract",
        ):
            collector.collect(
                arguments,
                core=self.core,
                runner=self._runner,
                host_identity=lambda: ("linux", "x86_64"),
                environment_observer=self._observation,
                challenge_factory=self._challenge,
                usage_reader=self._usage,
                clock_reader=self._clock,
            )

        self.assertEqual(self.calls, 1)
        self._assert_no_output()

    def test_executable_must_be_a_real_descendant(self) -> None:
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        outside.chmod(0o700)
        arguments = self._arguments()
        arguments.executable = outside

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError, "inside the application root"
        ):
            collector.collect(
                arguments,
                core=self.core,
                runner=self._runner,
                host_identity=lambda: ("macos", "arm64"),
                environment_observer=self._observation,
                challenge_factory=self._challenge,
                usage_reader=self._usage,
                clock_reader=self._clock,
            )

        self.assertEqual(self.calls, 0)

    def test_symlink_leaf_inputs_are_rejected_before_launch(self) -> None:
        for field, target in (
            ("executable", self.executable),
            ("runtime_artifact", self.runtime),
            ("application_root", self.application),
        ):
            with self.subTest(field=field):
                link = self.root / f"{field}-link"
                link.symlink_to(target)
                arguments = self._arguments()
                setattr(arguments, field, link)
                with self.assertRaisesRegex(
                    collector.CpuBenchmarkCollectorError,
                    "must be a (?:regular non-link file|non-link directory)",
                ):
                    collector.collect(
                        arguments,
                        core=self.core,
                        runner=self._runner,
                        host_identity=lambda: ("macos", "arm64"),
                        environment_observer=self._observation,
                        challenge_factory=self._challenge,
                        usage_reader=self._usage,
                        clock_reader=self._clock,
                    )
                self.assertEqual(self.calls, 0)

    def test_packaged_role_basenames_must_be_pairwise_distinct(self) -> None:
        arguments = self._arguments()
        arguments.runtime_artifact = self.shim

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError,
            "artifact basenames must be distinct",
        ):
            self._collect(arguments)

        self.assertEqual(self.calls, 0)
        self._assert_no_output()

    def test_packaged_basename_must_select_one_regular_file(self) -> None:
        duplicate_parent = self.application / "duplicate"
        duplicate_parent.mkdir()
        (duplicate_parent / self.runtime.name).write_bytes(b"other runtime")

        with self.assertRaisesRegex(
            collector.CpuBenchmarkCollectorError,
            "basename must select exactly one regular-file member",
        ):
            self._collect()

        self.assertEqual(self.calls, 0)
        self._assert_no_output()

    def test_symlink_alias_does_not_duplicate_packaged_regular_basename(self) -> None:
        actual_parent = self.application / "nested" / "runtime"
        actual_parent.mkdir(parents=True)
        moved_runtime = actual_parent / self.runtime.name
        self.runtime.rename(moved_runtime)
        self.runtime = moved_runtime
        (self.application / moved_runtime.name).symlink_to(
            moved_runtime.relative_to(self.application)
        )

        record = self._collect()

        self.assertEqual(self.calls, collector.LAUNCH_COUNT)
        self.assertEqual(record["artifacts"]["runtimeLibrary"]["id"], self.runtime.name)


if __name__ == "__main__":
    unittest.main()
