#!/usr/bin/env python3
"""Focused tests for the clean Linux target-host reference gate."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/run_linux_reference_app_gate.py"
SPEC = importlib.util.spec_from_file_location("fonix_test_linux_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def _run_gate_arguments(root: Path) -> dict[str, Path]:
    return {
        "repository": root / "repository",
        "flutter": root / "flutter",
        "artifact_cache": root / "cache",
        "pub_cache": root / "pub-cache",
        "work_directory": root / "work",
        "clang": root / "clang",
        "clangxx": root / "clang++",
        "archiver": root / "llvm-ar",
        "linker": root / "ld.lld",
        "cmake": root / "cmake",
        "ninja": root / "ninja",
        "readelf": root / "readelf",
        "pkg_config": root / "pkg-config",
        "dpkg_query": root / "dpkg-query",
        "xvfb": root / "Xvfb",
    }


def _audit_report(tree: gate.TreeIdentity) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "result": "passed",
        "target": {
            "os": "linux",
            "architecture": "x86_64",
            "minimumOs": "glibc-2.27",
        },
        "artifact": {
            "id": gate.ARTIFACT_ID,
            "sourceSha256": gate.ARCHIVE_SHA256,
            "resolverManifestSha256": "1" * 64,
        },
        "applicationTree": {
            "fileCount": tree.file_count,
            "byteCount": tree.byte_count,
            "sha256": tree.tree_sha256,
            "identityFormat": "canonical-root-directory-file-size-sha256-mode-v2",
        },
        "assets": {
            "modelSha256": gate._AUDITOR.MODEL_SHA256,
            "xnnpackModelSha256": gate._AUDITOR.XNNPACK_MODEL_SHA256,
            "nativeAssetsManifestSha256": "2" * 64,
            "noticesSha256": "3" * 64,
        },
        "flutterRuntime": {
            "engineSha256": gate._AUDITOR.FLUTTER_ENGINE_SHA256,
            "icuSha256": gate._AUDITOR.FLUTTER_ICU_SHA256,
        },
        "hook": {
            "invocation": "123456789a",
            "inputSha256": "4" * 64,
            "outputSha256": "5" * 64,
            "compilerSha256": {
                "ar": "6" * 64,
                "cc": "7" * 64,
                "ld": "8" * 64,
            },
        },
        "shimBuildManifestSha256": "9" * 64,
        "elf": [
            {
                "path": path,
                "sha256": format(index + 10, "x") * 64,
                "sizeBytes": index + 1,
                "soname": gate._AUDITOR.EXPECTED_SONAMES[path],
                "needed": list(gate._AUDITOR.EXPECTED_NEEDED[path]),
                "runpath": list(gate._AUDITOR.EXPECTED_RUNPATHS[path]),
                "buildId": "a" * 40,
                "versionMaxima": {
                    "GLIBC": "2.27",
                    "GLIBCXX": None,
                    "CXXABI": None,
                },
            }
            for index, path in enumerate(gate._AUDITOR.ELF_PATHS)
        ],
        "dortExportCount": 67,
        "hardening": {
            "relro": "full",
            "bindNow": True,
            "nxStack": True,
            "buildIds": "present-closed-per-elf-runner-sha1",
        },
        "claimBoundary": (
            "Closed installed bytes, hook provenance, ELF metadata, symbol/version floors, "
            "and hardening only; target-host execution is required separately."
        ),
    }


class HostProfileTests(unittest.TestCase):
    def test_foreign_host_refuses_before_creating_work_or_inspecting_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = _run_gate_arguments(root)
            with (
                mock.patch.object(gate.platform, "system", return_value="Darwin"),
                mock.patch.object(gate, "_verify_toolchain") as verify_tools,
            ):
                with self.assertRaisesRegex(
                    gate.LinuxReferenceAppGateError, "exact Ubuntu 18.04"
                ):
                    gate.run_gate(**arguments)
            verify_tools.assert_not_called()
            self.assertFalse(arguments["work_directory"].exists())

    def test_exact_profile_binds_point_release_codename_arch_and_glibc(self) -> None:
        exact = {
            "ID": "ubuntu",
            "VERSION_ID": "18.04",
            "VERSION": "18.04.6 LTS (Bionic Beaver)",
            "VERSION_CODENAME": "bionic",
            "UBUNTU_CODENAME": "bionic",
        }
        with (
            mock.patch.object(gate.platform, "system", return_value="Linux"),
            mock.patch.object(gate.platform, "machine", return_value="x86_64"),
            mock.patch.object(gate.platform, "libc_ver", return_value=("glibc", "2.27")),
            mock.patch.object(gate, "_read_os_release", return_value=exact),
        ):
            gate._verify_exact_host()
        changed = dict(exact, VERSION="18.04.5 LTS (Bionic Beaver)")
        with (
            mock.patch.object(gate.platform, "system", return_value="Linux"),
            mock.patch.object(gate.platform, "machine", return_value="x86_64"),
            mock.patch.object(gate.platform, "libc_ver", return_value=("glibc", "2.27")),
            mock.patch.object(gate, "_read_os_release", return_value=changed),
        ):
            with self.assertRaises(gate.LinuxReferenceAppGateError):
                gate._verify_exact_host()

    def test_profile_is_required_not_prevalidated_evidence(self) -> None:
        self.assertTrue(gate.TARGET_PROFILE.startswith("required-"))
        self.assertNotIn("validated", gate.TARGET_PROFILE)
        self.assertEqual(gate.REQUIRED_PYTHON_VERSION, "3.11.9")
        self.assertEqual(
            gate.REQUIRED_XVFB_PACKAGE_VERSION, "2:1.19.6-1ubuntu4.15"
        )


class SourceDerivationTests(unittest.TestCase):
    def test_external_pubspec_changes_only_path_and_retains_normal_defines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            pubspec = root / "pubspec.yaml"
            original = """name: fixture
dependencies:
  fonix:
    path: ..
hooks:
  user_defines:
    fonix:
      runtime_mode: bundled
      artifact_cache: .fonix-artifact-cache
      application_minimum_os: '14.0'
"""
            pubspec.write_text(original, encoding="utf-8")
            gate._patch_linux_pubspec(pubspec, repository)
            result = pubspec.read_text(encoding="utf-8")
            self.assertIn(f"    path: {json.dumps(repository.as_posix())}\n", result)
            self.assertEqual(result.count("application_minimum_os: '14.0'"), 1)
            self.assertEqual(result.count("runtime_mode: bundled"), 1)
            self.assertEqual(result.count("artifact_cache: .fonix-artifact-cache"), 1)

    def test_path_patch_rejects_missing_normal_floor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            pubspec = root / "pubspec.yaml"
            pubspec.write_text(
                "dependencies:\n  fonix:\n    path: ..\n"
                "hooks:\n  user_defines:\n    fonix:\n"
                "      runtime_mode: bundled\n"
                "      artifact_cache: .fonix-artifact-cache\n",
                encoding="utf-8",
            )
            with self.assertRaises(gate.LinuxReferenceAppGateError):
                gate._patch_linux_pubspec(pubspec, repository)

    def test_lockfile_path_patch_is_exact_and_epoch_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            application = root / "application"
            application.mkdir()
            pubspec = application / "pubspec.yaml"
            lockfile = application / "pubspec.lock"
            pubspec.write_text("name: fixture\n", encoding="utf-8")
            lockfile.write_text(
                'packages:\n  fonix:\n    description:\n      path: ".."\n'
                "      relative: true\n    source: path\n",
                encoding="utf-8",
            )
            gate._patch_linux_lockfile(lockfile, repository)
            self.assertIn(
                f"      path: {json.dumps(repository.as_posix())}\n"
                "      relative: false\n",
                lockfile.read_text(encoding="utf-8"),
            )
            pubspec_sha = gate._sha256(pubspec)
            lock_sha = gate._sha256(lockfile)
            gate._require_dependency_epoch(
                application,
                pubspec_sha256=pubspec_sha,
                lock_sha256=lock_sha,
            )
            lockfile.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(
                gate.LinuxReferenceAppGateError, "dependency epoch"
            ):
                gate._require_dependency_epoch(
                    application,
                    pubspec_sha256=pubspec_sha,
                    lock_sha256=lock_sha,
                )


class TreeIdentityTests(unittest.TestCase):
    def _closed_bundle(self, root: Path) -> Path:
        bundle = root / "bundle"
        bundle.mkdir(mode=0o755)
        for relative in sorted(gate._AUDITOR.EXPECTED_FILES):
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(relative.encode("utf-8"))
            path.chmod(
                0o755
                if relative == gate._AUDITOR.APPLICATION_EXECUTABLE
                else 0o644
            )
        for current, directories, _ in os.walk(bundle):
            Path(current).chmod(0o755)
            for name in directories:
                (Path(current) / name).chmod(0o755)
        return bundle

    def test_gate_and_auditor_use_the_same_v2_tree_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._closed_bundle(Path(temporary))
            gate_identity = gate._tree_identity(bundle, "fixture bundle")
            audit_identity = gate._AUDITOR._tree_identity(bundle)
            self.assertEqual(
                gate_identity,
                gate.TreeIdentity(
                    audit_identity.file_count,
                    audit_identity.byte_count,
                    audit_identity.tree_sha256,
                ),
            )

    def test_linux_hash_accepts_pinned_runtime_sized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.so"
            path.write_bytes(b"x" * (9 * 1024 * 1024))
            self.assertEqual(
                gate._sha256(path), hashlib.sha256(path.read_bytes()).hexdigest()
            )

    def test_empty_directory_directory_mode_and_root_mode_change_identity_or_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._closed_bundle(root)
            baseline = gate._tree_identity(bundle, "fixture bundle")
            extra = bundle / "empty"
            extra.mkdir(mode=0o755)
            self.assertNotEqual(
                gate._tree_identity(bundle, "fixture bundle"), baseline
            )
            extra.chmod(0o700)
            self.assertNotEqual(
                gate._tree_identity(bundle, "fixture bundle"), baseline
            )
            bundle.chmod(0o777)
            with self.assertRaisesRegex(
                gate.LinuxReferenceAppGateError, "root mode"
            ):
                gate._tree_identity(bundle, "fixture bundle")


class EnvironmentAndReceiptTests(unittest.TestCase):
    def test_build_environment_is_a_positive_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = {
                name: root / name
                for name in ("home", "tmp", "pub-cache", "tool-bin")
            }
            for directory in directories.values():
                directory.mkdir(mode=0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "PYTHONPATH": "/tmp/evil",
                    "PYTHONHOME": "/tmp/evil-python",
                    "PUB_HOSTED_URL": "https://evil.invalid",
                    "CMAKE_TOOLCHAIN_FILE": "/tmp/evil.cmake",
                },
                clear=False,
            ):
                environment = gate._build_environment(
                    home=directories["home"],
                    temporary=directories["tmp"],
                    pub_cache=directories["pub-cache"],
                    tool_bin=directories["tool-bin"],
                    clang=root / "clang",
                    clangxx=root / "clang++",
                    archiver=root / "llvm-ar",
                    linker=root / "ld.lld",
                )
            for forbidden in (
                "PYTHONPATH",
                "PYTHONHOME",
                "PUB_HOSTED_URL",
                "CMAKE_TOOLCHAIN_FILE",
            ):
                self.assertNotIn(forbidden, environment)
            self.assertEqual(
                environment["PATH"], f'{directories["tool-bin"]}:/usr/bin:/bin'
            )

    def test_multicall_tools_keep_distinct_compiler_and_linker_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            driver = root / "llvm-driver"
            driver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            driver.chmod(0o755)
            tool_bin = gate._prepare_tool_bin(
                root / "tool-bin",
                {
                    "clang": driver,
                    "clang++": driver,
                    "llvm-ar": driver,
                    "llvm-ar-10": driver,
                    "ld.lld": driver,
                },
            )
            self.assertEqual((tool_bin / "clang").resolve(), driver.resolve())
            self.assertEqual((tool_bin / "clang++").resolve(), driver.resolve())
            self.assertEqual((tool_bin / "llvm-ar").resolve(), driver.resolve())
            self.assertEqual((tool_bin / "llvm-ar-10").resolve(), driver.resolve())
            self.assertEqual((tool_bin / "ld.lld").resolve(), driver.resolve())
            self.assertNotEqual(tool_bin / "clang", tool_bin / "clang++")
            self.assertNotEqual(tool_bin / "llvm-ar", tool_bin / "llvm-ar-10")

    def test_reference_environment_has_env_i_semantics_and_private_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(
                os.environ,
                {
                    "LD_PRELOAD": "/tmp/evil.so",
                    "DBUS_SESSION_BUS_ADDRESS": "evil",
                    "GDK_BACKEND": "broadway",
                    "DART_VM_OPTIONS": "--observe",
                },
                clear=False,
            ):
                environment, directories = gate._private_environment(
                    Path(temporary) / "profile"
                )
            self.assertEqual(
                set(environment),
                {
                    "PATH",
                    "LC_ALL",
                    "LANG",
                    "HOME",
                    "TMPDIR",
                    "TMP",
                    "TEMP",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                    "XDG_STATE_HOME",
                    "XDG_RUNTIME_DIR",
                    "FONIX_REFERENCE_SMOKE",
                    "GSETTINGS_BACKEND",
                    "MESA_GLSL_CACHE_DISABLE",
                    "MESA_SHADER_CACHE_DISABLE",
                    "NO_AT_BRIDGE",
                },
            )
            self.assertEqual(environment["FONIX_REFERENCE_SMOKE"], "1")
            self.assertEqual(environment["GSETTINGS_BACKEND"], "memory")
            launch = Path(temporary) / "launch"
            launch.mkdir(mode=0o700)
            profile = Path(temporary) / "profile"
            gate._assert_private_profile_empty(profile, directories, launch)
            (directories["cache"] / "residual").write_bytes(b"x")
            with self.assertRaisesRegex(
                gate.LinuxReferenceAppGateError, "residual"
            ):
                gate._assert_private_profile_empty(profile, directories, launch)
            (directories["cache"] / "residual").unlink()
            (profile / "unrelated").write_bytes(b"x")
            with self.assertRaisesRegex(
                gate.LinuxReferenceAppGateError, "top-level"
            ):
                gate._assert_private_profile_empty(profile, directories, launch)

    def test_receipt_is_one_exact_typed_line(self) -> None:
        line = gate.REFERENCE_RECEIPT_PREFIX + json.dumps(
            gate.EXPECTED_REFERENCE_RECEIPT,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(
            gate._parse_reference_receipt(line + "\n"),
            gate.EXPECTED_REFERENCE_RECEIPT,
        )
        with self.assertRaises(gate.LinuxReferenceAppGateError):
            gate._parse_reference_receipt(line + "\n" + line + "\n")
        changed = dict(gate.EXPECTED_REFERENCE_RECEIPT, fullCpuAssignment=1)
        with self.assertRaises(gate.LinuxReferenceAppGateError):
            gate._parse_reference_receipt(
                gate.REFERENCE_RECEIPT_PREFIX + json.dumps(changed)
            )

    def test_process_group_launch_settles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stdout, stderr = gate._run_process_group(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('settled\\n'); sys.stdout.flush()",
                ),
                cwd=root,
                environment={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
            self.assertEqual(stdout, "settled\n")
            self.assertEqual(stderr, "")

    def test_reference_launch_uses_verified_xvfb_and_direct_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            xvfb = root / "Xvfb"
            executable = root / "fonix_reference"
            xvfb.write_text(
                f"#!{sys.executable}\n"
                "import os, sys, time\n"
                "args = sys.argv[1:]\n"
                "if len(args) != 7 or args[0] != '-displayfd' or "
                "args[2:] != ['-screen', '0', '1280x720x24', '-nolisten', 'tcp']:\n"
                "    raise SystemExit(3)\n"
                "os.write(int(args[1]), b'54321\\n')\n"
                "while True:\n"
                "    time.sleep(1)\n",
                encoding="utf-8",
            )
            receipt = json.dumps(
                gate.EXPECTED_REFERENCE_RECEIPT,
                sort_keys=True,
                separators=(",", ":"),
            )
            executable.write_text(
                f"#!{sys.executable}\n"
                "import os\n"
                "if os.environ.get('DISPLAY') != ':54321':\n"
                "    raise SystemExit(4)\n"
                f"print({(gate.REFERENCE_RECEIPT_PREFIX + receipt)!r})\n",
                encoding="utf-8",
            )
            xvfb.chmod(0o755)
            executable.chmod(0o755)
            self.assertEqual(
                gate._launch_reference(
                    executable=executable,
                    xvfb=xvfb,
                    work_root=root,
                ),
                gate.EXPECTED_REFERENCE_RECEIPT,
            )

    def test_generic_command_timeout_terminates_descendant_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "child-ready"
            terminated = root / "child-terminated"
            child_source = f"""import signal
import sys
import time
from pathlib import Path
ready = Path({str(ready)!r})
done = Path({str(terminated)!r})
def stop(*_):
    done.write_text('terminated', encoding='utf-8')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
ready.write_text('ready', encoding='utf-8')
time.sleep(60)
"""
            parent_source = f"""import subprocess
import sys
import time
from pathlib import Path
ready = Path({str(ready)!r})
subprocess.Popen([sys.executable, '-c', {child_source!r}])
deadline = time.monotonic() + 5
while not ready.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
time.sleep(60)
"""
            with self.assertRaisesRegex(
                gate.LinuxReferenceAppGateError, "timed out"
            ):
                gate._run(
                    (sys.executable, "-c", parent_source),
                    cwd=root,
                    environment={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                    timeout_seconds=0.5,
                    maximum_output=16 * 1024,
                    operation="synthetic build command",
                )
            self.assertEqual(terminated.read_text(encoding="utf-8"), "terminated")


class HookAndAuditBindingTests(unittest.TestCase):
    def _hook_fixture(self, root: Path) -> tuple[Path, Path]:
        work = root / "application-work"
        application = work / "build/linux/x64/release/bundle"
        invocation = work / ".dart_tool/hooks_runner/fonix/123456789a"
        shared = work / ".dart_tool/hooks_runner/shared/fonix/build"
        staging = shared / "staged"
        for directory in (application / "lib", invocation, shared / "shim", staging / "notices"):
            directory.mkdir(parents=True, exist_ok=True)
        paths = {
            "package:fonix/fonix_shim": shared / "shim/libfonix_shim.so",
            "package:fonix/onnxruntime": staging / gate._AUDITOR.RUNTIME_NAME,
            "package:fonix/onnxruntime_providers_shared": staging
            / gate._AUDITOR.PROVIDER_NAME,
        }
        names = {
            key: value.name for key, value in paths.items()
        }
        for identifier, source in paths.items():
            source.write_bytes(identifier.encode("utf-8"))
            installed = application / "lib" / names[identifier]
            installed.write_bytes(source.read_bytes())
        (staging / "fonix-native-artifact-manifest.json").write_bytes(b"manifest")
        (staging / "notices/ThirdPartyNotices.txt").write_bytes(b"notices")
        input_value = {
            "config": {
                "extensions": {
                    "code_assets": {
                        "target_os": "linux",
                        "target_architecture": "x64",
                    }
                }
            },
            "out_dir_shared": str(shared),
        }
        (invocation / "input.json").write_text(json.dumps(input_value), encoding="utf-8")
        assets = [
            {
                "encoding": {
                    "file": str(path),
                    "id": identifier,
                    "link_mode": {"type": "dynamic_loading_bundle"},
                },
                "type": "code_assets/code",
            }
            for identifier, path in paths.items()
        ]
        (invocation / "output.json").write_text(
            json.dumps(
                {
                    "assets": assets,
                    "assets_for_linking": {},
                    "dependencies": ["duplicate", "duplicate"],
                    "status": "success",
                    "timestamp": "2026-08-07 16:00:54.000",
                }
            ),
            encoding="utf-8",
        )
        return work, application

    def test_hook_assets_are_selected_from_exact_output_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work, application = self._hook_fixture(Path(temporary))
            provenance = gate._discover_hook_provenance(work, application)
            self.assertEqual(provenance.shim.name, gate._AUDITOR.SHIM_NAME)
            self.assertEqual(provenance.runtime.parent, provenance.provider.parent)
            output_path = provenance.input_path.parent / "output.json"
            value = json.loads(output_path.read_text(encoding="utf-8"))
            value["assets"][0]["encoding"]["file"] = str(
                Path(temporary) / gate._AUDITOR.SHIM_NAME
            )
            output_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(gate.LinuxReferenceAppGateError):
                gate._discover_hook_provenance(work, application)

    def test_unrelated_same_target_hook_invocation_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work, application = self._hook_fixture(root)
            other_invocation = (
                work / ".dart_tool/hooks_runner/fonix/abcdef1234"
            )
            other_shared = (
                work / ".dart_tool/hooks_runner/shared/fonix/old-build"
            )
            other_staging = other_shared / "staged"
            for directory in (
                other_invocation,
                other_shared / "shim",
                other_staging / "notices",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            paths = {
                "package:fonix/fonix_shim": other_shared
                / "shim"
                / gate._AUDITOR.SHIM_NAME,
                "package:fonix/onnxruntime": other_staging
                / gate._AUDITOR.RUNTIME_NAME,
                "package:fonix/onnxruntime_providers_shared": other_staging
                / gate._AUDITOR.PROVIDER_NAME,
            }
            for identifier, path in paths.items():
                path.write_bytes(("old:" + identifier).encode("utf-8"))
            (other_staging / "fonix-native-artifact-manifest.json").write_bytes(
                b"old-manifest"
            )
            (other_staging / "notices/ThirdPartyNotices.txt").write_bytes(
                b"old-notices"
            )
            (other_invocation / "input.json").write_text(
                json.dumps(
                    {
                        "config": {
                            "extensions": {
                                "code_assets": {
                                    "target_os": "linux",
                                    "target_architecture": "x64",
                                }
                            }
                        },
                        "out_dir_shared": str(other_shared),
                    }
                ),
                encoding="utf-8",
            )
            (other_invocation / "output.json").write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "encoding": {
                                    "file": str(path),
                                    "id": identifier,
                                    "link_mode": {
                                        "type": "dynamic_loading_bundle"
                                    },
                                },
                                "type": "code_assets/code",
                            }
                            for identifier, path in paths.items()
                        ],
                        "assets_for_linking": {},
                        "dependencies": ["old"],
                        "status": "success",
                        "timestamp": "2026-08-07 15:00:00.000",
                    }
                ),
                encoding="utf-8",
            )
            provenance = gate._discover_hook_provenance(work, application)
            self.assertEqual(provenance.input_path.parent.name, "123456789a")

    def test_frozen_auditor_loader_uses_exact_source_epoch_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "source-epoch"
            auditor_path = repository / "tool/ci/audit_linux_application.py"
            auditor_path.parent.mkdir(parents=True)
            auditor_path.write_text("# frozen auditor fixture\n", encoding="utf-8")
            sentinel = object()
            with mock.patch.object(
                gate, "_load_module", return_value=sentinel
            ) as load_module:
                result = gate._load_frozen_linux_auditor(repository)

        self.assertIs(result, sentinel)
        load_module.assert_called_once_with(
            "_fonix_linux_gate_frozen_auditor",
            auditor_path,
        )

    def test_auditor_runs_in_process_with_exact_closed_arguments(self) -> None:
        class FakeAuditError(RuntimeError):
            pass

        base = Path("/closed")
        provenance = gate.HookProvenance(
            base / "input.json",
            base / "shim.so",
            base / "runtime.so",
            base / "provider.so",
            base / "staging",
        )
        tree = gate.TreeIdentity(21, 12345, "a" * 64)
        report = _audit_report(tree)
        auditor = mock.Mock()
        auditor.LinuxApplicationAuditError = FakeAuditError
        bytecode_modes: list[bool] = []

        def audit_application(**_: object) -> dict[str, object]:
            bytecode_modes.append(sys.dont_write_bytecode)
            return report

        auditor.audit_application.side_effect = audit_application
        former_cli = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
        original_strict_json = gate._strict_json
        with (
            mock.patch.object(sys, "dont_write_bytecode", False),
            mock.patch.object(
                gate, "_load_frozen_linux_auditor", return_value=auditor
            ),
            mock.patch.object(gate, "_run") as outer_runner,
            mock.patch.object(
                gate, "_strict_json", wraps=original_strict_json
            ) as strict_json,
        ):
            result = gate._run_linux_application_audit(
                repository=base / "repository",
                application=base / "bundle",
                provenance=provenance,
                readelf=base / "readelf",
                clang=base / "clang",
                archiver=base / "llvm-ar",
                linker=base / "ld.lld",
                tree=tree,
            )
            restored_bytecode_mode = sys.dont_write_bytecode

        self.assertEqual(result, report)
        self.assertEqual(bytecode_modes, [True])
        self.assertFalse(restored_bytecode_mode)
        auditor.audit_application.assert_called_once_with(
            repository=base / "repository",
            application=base / "bundle",
            hook_input=provenance.input_path,
            reference_shim=provenance.shim,
            reference_runtime=provenance.runtime,
            reference_provider=provenance.provider,
            staging_directory=provenance.staging,
            readelf=base / "readelf",
            expected_cc=base / "clang",
            expected_ar=base / "llvm-ar",
            expected_ld=base / "ld.lld",
        )
        strict_json.assert_called_once_with(
            former_cli,
            "Linux application audit report",
            maximum=gate.MAX_COMMAND_OUTPUT_BYTES,
        )
        outer_runner.assert_not_called()

    def test_in_process_audit_preserves_former_output_bound(self) -> None:
        class FakeAuditError(RuntimeError):
            pass

        auditor = mock.Mock()
        auditor.LinuxApplicationAuditError = FakeAuditError
        auditor.audit_application.return_value = {
            "payload": "x" * gate.MAX_COMMAND_OUTPUT_BYTES
        }
        with (
            mock.patch.object(
                gate, "_load_frozen_linux_auditor", return_value=auditor
            ),
            mock.patch.object(gate, "_validate_audit_report") as validate,
            self.assertRaisesRegex(
                gate.LinuxReferenceAppGateError,
                "exceeds",
            ),
        ):
            gate._run_linux_application_audit(
                repository=Path("/closed/repository"),
                application=Path("/closed/bundle"),
                provenance=gate.HookProvenance(
                    Path("/closed/input.json"),
                    Path("/closed/shim.so"),
                    Path("/closed/runtime.so"),
                    Path("/closed/provider.so"),
                    Path("/closed/staging"),
                ),
                readelf=Path("/closed/readelf"),
                clang=Path("/closed/clang"),
                archiver=Path("/closed/llvm-ar"),
                linker=Path("/closed/ld.lld"),
                tree=gate.TreeIdentity(21, 12345, "a" * 64),
            )

        validate.assert_not_called()

    def test_in_process_audit_still_validates_the_closed_report(self) -> None:
        class FakeAuditError(RuntimeError):
            pass

        auditor = mock.Mock()
        auditor.LinuxApplicationAuditError = FakeAuditError
        auditor.audit_application.return_value = {}
        with (
            mock.patch.object(
                gate, "_load_frozen_linux_auditor", return_value=auditor
            ),
            self.assertRaises(gate.LinuxReferenceAppGateError),
        ):
            gate._run_linux_application_audit(
                repository=Path("/closed/repository"),
                application=Path("/closed/bundle"),
                provenance=gate.HookProvenance(
                    Path("/closed/input.json"),
                    Path("/closed/shim.so"),
                    Path("/closed/runtime.so"),
                    Path("/closed/provider.so"),
                    Path("/closed/staging"),
                ),
                readelf=Path("/closed/readelf"),
                clang=Path("/closed/clang"),
                archiver=Path("/closed/llvm-ar"),
                linker=Path("/closed/ld.lld"),
                tree=gate.TreeIdentity(21, 12345, "a" * 64),
            )

    def test_audit_report_binds_exact_prelaunch_tree(self) -> None:
        tree = gate.TreeIdentity(21, 12345, "a" * 64)
        report = _audit_report(tree)
        gate._validate_audit_report(report, tree)
        report["applicationTree"]["sha256"] = "b" * 64
        with self.assertRaises(gate.LinuxReferenceAppGateError):
            gate._validate_audit_report(report, tree)
        with self.assertRaises(gate.LinuxReferenceAppGateError):
            gate._validate_audit_report(
                {
                    "schemaVersion": 1,
                    "result": "passed",
                    "target": report["target"],
                    "applicationTree": report["applicationTree"],
                    "dortExportCount": 67,
                },
                tree,
            )


if __name__ == "__main__":
    unittest.main()
