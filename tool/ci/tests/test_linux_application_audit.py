#!/usr/bin/env python3
"""Focused adversarial tests for the final Linux application auditor."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Sequence
import unittest
from unittest import mock
import zlib


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/audit_linux_application.py"
SPEC = importlib.util.spec_from_file_location("fonix_test_linux_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class LinuxApplicationTreeTests(unittest.TestCase):
    def _closed_tree(self, parent: Path) -> Path:
        root = parent / "bundle"
        root.mkdir(mode=0o755)
        for relative in sorted(audit.EXPECTED_FILES):
            target = root / relative
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            target.write_bytes(f"fixture:{relative}".encode("utf-8"))
            target.chmod(0o755 if relative == audit.APPLICATION_EXECUTABLE else 0o644)
        for current, directories, _ in os.walk(root):
            Path(current).chmod(0o755)
            for name in directories:
                (Path(current) / name).chmod(0o755)
        return root

    def test_exact_no_link_tree_and_modes_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            identity = audit._tree_identity(self._closed_tree(Path(temporary)))
            self.assertEqual(identity.file_count, len(audit.EXPECTED_FILES))
            self.assertRegex(identity.tree_sha256, r"^[0-9a-f]{64}$")

    def test_extra_file_link_and_mode_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._closed_tree(Path(temporary))
            (root / "unexpected").write_bytes(b"x")
            with self.assertRaises(audit.LinuxApplicationAuditError):
                audit._tree_identity(root)
        with tempfile.TemporaryDirectory() as temporary:
            root = self._closed_tree(Path(temporary))
            (root / "data/icudtl.dat").chmod(0o666)
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "mode changed"
            ):
                audit._tree_identity(root)
        with tempfile.TemporaryDirectory() as temporary:
            root = self._closed_tree(Path(temporary))
            root.chmod(0o777)
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "root mode changed"
            ):
                audit._tree_identity(root)
        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as temporary:
                root = self._closed_tree(Path(temporary))
                target = root / "data/icudtl.dat"
                target.unlink()
                target.symlink_to(root / "fonix_reference")
                with self.assertRaises(audit.LinuxApplicationAuditError):
                    audit._tree_identity(root)


class LinuxAssetTests(unittest.TestCase):
    def _assets(self, root: Path) -> tuple[Path, Path]:
        repository = root / "repository"
        application = root / "bundle"
        source = repository / "example/assets"
        packaged = application / "data/flutter_assets/assets"
        for relative in (
            "models/mul_1.onnx",
            "models/model.json",
            "models/xnnpack_matmul.onnx",
            "models/xnnpack_matmul.json",
        ):
            source_file = REPOSITORY / "example/assets" / relative
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
            packaged_file = packaged / relative
            packaged_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, packaged_file)
        flutter_assets = application / "data/flutter_assets"
        (flutter_assets / "version.json").write_text(
            '{"app_name":"fonix_reference","version":"0.1.0",'
            '"build_number":"1","package_name":"fonix_reference"}',
            encoding="utf-8",
        )
        (flutter_assets / "FontManifest.json").write_text(
            json.dumps(
                [
                    {
                        "family": "MaterialIcons",
                        "fonts": [{"asset": "fonts/MaterialIcons-Regular.otf"}],
                    }
                ]
            ),
            encoding="utf-8",
        )
        separator = b"\n--------------------------------------------------------------------------------\n"
        notices = b"Copyright 2013 The Flutter Authors\nLicense\n"
        notices += separator + b"fonix\nLicense\n"
        notices += separator + b"crypto\nLicense\n"
        notices += separator.join([b"package\nLicense\n"] * 12)
        (flutter_assets / "NOTICES.Z").write_bytes(gzip.compress(notices, mtime=0))
        native = {
            "format-version": [1, 0, 0],
            "native-assets": {
                "linux_x64": {
                    "package:fonix/fonix_shim": ["absolute", audit.SHIM_NAME],
                    "package:fonix/onnxruntime": ["absolute", audit.RUNTIME_NAME],
                    "package:fonix/onnxruntime_providers_shared": [
                        "absolute",
                        audit.PROVIDER_NAME,
                    ],
                }
            },
        }
        (flutter_assets / "NativeAssetsManifest.json").write_text(
            json.dumps(native), encoding="utf-8"
        )
        return repository, application

    def test_exact_assets_pass_and_global_or_extra_native_mapping_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository, application = self._assets(Path(temporary))
            original_identity = audit._require_identity

            def identity(path: Path, digest: str, size: int, label: str) -> None:
                if label != "pinned Flutter notices":
                    original_identity(path, digest, size, label)

            with mock.patch.object(audit, "_require_identity", side_effect=identity):
                result = audit._validate_repository_assets(repository, application)
            self.assertEqual(result["modelSha256"], audit.MODEL_SHA256)
            manifest_path = application / "data/flutter_assets/NativeAssetsManifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["native-assets"]["linux_x64"]["package:evil/runtime"] = [
                "absolute",
                "/usr/lib/libonnxruntime.so.1",
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                mock.patch.object(audit, "_require_identity", side_effect=identity),
                self.assertRaisesRegex(
                    audit.LinuxApplicationAuditError, "exact app-relative"
                ),
            ):
                audit._validate_repository_assets(repository, application)

    def test_flutter_notices_have_one_pinned_cross_platform_identity(self) -> None:
        self.assertEqual(audit.FLUTTER_NOTICES_SIZE_BYTES, 99_081)
        self.assertEqual(
            audit.FLUTTER_NOTICES_SHA256,
            "1de586c69a9f3847ca9aa50a26912f54a928044327989d1e17eac5e64023495e",
        )
        with tempfile.TemporaryDirectory() as temporary:
            replacement = Path(temporary) / "NOTICES.Z"
            replacement.write_bytes(b"Fonix notices")
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "identity changed"
            ):
                audit._require_identity(
                    replacement,
                    audit.FLUTTER_NOTICES_SHA256,
                    audit.FLUTTER_NOTICES_SIZE_BYTES,
                    "pinned Flutter notices",
                )

    def test_flutter_linux_engine_and_icu_identities_are_pinned(self) -> None:
        self.assertEqual(audit.FLUTTER_ENGINE_SIZE_BYTES, 17_212_792)
        self.assertEqual(
            audit.FLUTTER_ENGINE_SHA256,
            "d8d3b60530b06ba43cfc34a9e7a1688d53b743d5133702a3ca2f269e071c9afe",
        )
        self.assertEqual(audit.FLUTTER_ICU_SIZE_BYTES, 864_880)
        self.assertEqual(
            audit.FLUTTER_ICU_SHA256,
            "325a86063d26334c2eabe1743cea073b612540fbf3d8fc2ef0b5708e3763a8c7",
        )

    def test_strict_json_rejects_duplicate_and_nonfinite_values(self) -> None:
        for value in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(audit.LinuxApplicationAuditError):
                audit._strict_json(value, "fixture")


def _elf_output(
    *,
    needed: Sequence[str],
    soname: str | None,
    runpath: str | None,
    symbols: list[str] | None = None,
    pie: bool = False,
    glibc: str = "2.27",
    build_id: str = "1" * 40,
) -> str:
    dynamic = [f" 0x1 (NEEDED) Shared library: [{name}]" for name in needed]
    if soname is not None:
        dynamic.append(f" 0xe (SONAME) Library soname: [{soname}]")
    if runpath is not None:
        dynamic.append(f" 0x1d (RUNPATH) Library runpath: [{runpath}]")
    dynamic.extend(
        [
            " 0x3 (PLTGOT) 0xb0",
            " 0x1e (FLAGS) BIND_NOW",
            f" 0x6ffffffb (FLAGS_1) Flags: NOW{' PIE' if pie else ''}",
        ]
    )
    dynamic.append(" 0x0 (NULL) 0x0")
    symbol_rows = [
        *(symbols or []),
        f"  9999: 0000000000000000 0 FUNC GLOBAL DEFAULT UND fixture@GLIBC_{glibc} (2)",
    ]
    version_requirements: dict[int, str] = {}
    for row in symbol_rows:
        if " UND " not in row:
            continue
        match = re.search(r"\s(\S+@\S+)\s+\(([0-9]+)\)\s*$", row)
        if match is None:
            continue
        name, raw_index = match.groups()
        index = int(raw_index)
        version = name.rsplit("@", 1)[1]
        assert index not in version_requirements or version_requirements[index] == version
        version_requirements[index] = version
    return "\n".join(
        [
            "ELF Header:",
            "  Class:                             ELF64",
            "  Data:                              2's complement, little endian",
            "  Type:                              DYN (Shared object file)",
            "  Machine:                           Advanced Micro Devices X86-64",
            "Program Headers:",
            "  LOAD           0x0 0x0 0x0 0x100 0x100 R E 0x1000",
            "  LOAD           0x100 0x100 0x100 0x100 0x100 RW 0x1000",
            "  DYNAMIC        0x80 0x80 0x80 0x20 0x20 RW 0x8",
            *(
                [
                    "  INTERP         0x20 0x20 0x20 0x20 0x20 R 0x1",
                    "      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]",
                ]
                if pie
                else []
            ),
            "  GNU_RELRO      0x0 0x0 0x0 0x100 0x100 R   0x1",
            "  GNU_STACK      0x0 0x0 0x0 0x0 0x0 RW  0x10",
            "Section Headers:",
            "  [ 1] .dynamic DYNAMIC 0000000000000080 000080 000020 10 WA 0 0 8",
            "  [ 2] .got PROGBITS 00000000000000a0 0000a0 000010 08 WA 0 0 8",
            "  [ 3] .got.plt PROGBITS 00000000000000b0 0000b0 000010 08 WA 0 0 8",
            f"Dynamic section at offset 0x80 contains {len(dynamic)} entries:",
            *dynamic,
            f"    Build ID: {build_id}",
            "Version needs section '.gnu.version_r' contains 1 entries:",
            f"  0x0010: Version: 1  File: libc.so.6  Cnt: {len(version_requirements)}",
            *[
                f"  0x{offset + 2:04x}: Name: {name}  Flags: none  Version: {index}"
                for offset, (index, name) in enumerate(
                    sorted(version_requirements.items())
                )
            ],
            f"Symbol table '.dynsym' contains {len(symbol_rows) + 1} entries:",
            "   Num:    Value          Size Type    Bind   Vis      Ndx Name",
            "     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND",
            *symbol_rows,
            "",
        ]
    )


def _insert_dynamic(output: str, line: str) -> str:
    match = re.search(r"Dynamic section at offset 0x80 contains ([0-9]+) entries:", output)
    assert match is not None
    count = int(match.group(1))
    output = output.replace(
        match.group(0),
        f"Dynamic section at offset 0x80 contains {count + 1} entries:",
        1,
    )
    return output.replace(" 0x1e (FLAGS)", line + "\n 0x1e (FLAGS)", 1)


class LinuxElfTests(unittest.TestCase):
    def _binary(self, root: Path) -> Path:
        binary = root / "binary"
        binary.write_bytes(b"ELF fixture")
        return binary

    def test_runner_requires_structured_df_1_pie_and_parses_version_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._binary(Path(temporary))
            symbol = (
                "  3: 0000000000000000 0 FUNC GLOBAL DEFAULT UND "
                "__stack_chk_fail@GLIBC_2.4 (3)"
            )
            report = _elf_output(
                needed=audit.RUNNER_NEEDED,
                soname=None,
                runpath="$ORIGIN/lib",
                symbols=[symbol],
                pie=True,
            )
            record = audit._parse_elf(audit.APPLICATION_EXECUTABLE, binary, report)
            self.assertIn("__stack_chk_fail@GLIBC_2.4", record.undefined_symbols)
            false_positive = report.replace("Flags: NOW PIE", "Flags: NOW") + "\nPIE_symbol"
            with self.assertRaisesRegex(audit.LinuxApplicationAuditError, "DF_1_PIE"):
                audit._parse_elf(audit.APPLICATION_EXECUTABLE, binary, false_positive)

    def test_floor_rpath_and_needed_tampering_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._binary(Path(temporary))
            base = _elf_output(
                needed=audit.PROVIDER_NEEDED,
                soname=audit.PROVIDER_NAME,
                runpath=None,
            )
            path = f"lib/{audit.PROVIDER_NAME}"
            audit._parse_elf(path, binary, base)
            with self.assertRaisesRegex(audit.LinuxApplicationAuditError, "GLIBC_2.28"):
                audit._parse_elf(path, binary, base.replace("GLIBC_2.27", "GLIBC_2.28"))
            with self.assertRaisesRegex(audit.LinuxApplicationAuditError, "DT_NEEDED"):
                audit._parse_elf(
                    path,
                    binary,
                    _insert_dynamic(
                        base, " 0x1 (NEEDED) Shared library: [libevil.so]"
                    ),
                )
            with self.assertRaisesRegex(audit.LinuxApplicationAuditError, "RPATH"):
                audit._parse_elf(
                    path,
                    binary,
                    _insert_dynamic(base, " 0xf (RPATH) Library rpath: [/tmp]"),
                )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "forbidden dynamic tag"
            ):
                audit._parse_elf(
                    path,
                    binary,
                    _insert_dynamic(
                        base,
                        " 0x6ffffefc (AUDIT) Audit library: [/tmp/evil.so]",
                    ),
                )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "DF_TEXTREL"
            ):
                audit._parse_elf(
                    path,
                    binary,
                    base.replace(
                        " 0x1e (FLAGS) BIND_NOW",
                        " 0x1e (FLAGS) BIND_NOW TEXTREL",
                    ),
                )
            with self.assertRaisesRegex(audit.LinuxApplicationAuditError, r"W\+X"):
                audit._parse_elf(
                    path,
                    binary,
                    base.replace(
                        "0x100 0x100 RW 0x1000", "0x100 0x100 RWE 0x1000"
                    ),
                )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "version namespace"
            ):
                audit._parse_elf(
                    path,
                    binary,
                    base.replace("GLIBC_2.27", "GLIBC_PRIVATE"),
                )

    def test_dynamic_order_structure_flags_and_relro_tampering_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._binary(Path(temporary))
            path = f"lib/{audit.PROVIDER_NAME}"
            base = _elf_output(
                needed=audit.PROVIDER_NEEDED,
                soname=audit.PROVIDER_NAME,
                runpath=None,
            )
            mutations = (
                _elf_output(
                    needed=tuple(reversed(audit.PROVIDER_NEEDED)),
                    soname=audit.PROVIDER_NAME,
                    runpath=None,
                ),
                base.replace(
                    "  DYNAMIC        0x80 0x80 0x80 0x20 0x20 RW 0x8\n",
                    "",
                ),
                base.replace(
                    "GNU_RELRO      0x0 0x0 0x0 0x100 0x100 R",
                    "GNU_RELRO      0x0 0x0 0x0 0x0 0x0 R",
                ),
                base.replace("Flags: NOW", "Flags: NOW NODELETE"),
                base.replace("Flags: NOW", "Flags: NOW PIE"),
                base.replace(
                    " 0x6ffffffb (FLAGS_1) Flags: NOW\n 0x0 (NULL) 0x0",
                    " 0x0 (NULL) 0x0\n 0x6ffffffb (FLAGS_1) Flags: NOW",
                ),
                base.replace(" 0x0 (NULL) 0x0\n", ""),
                base.replace(".got PROGBITS 00000000000000a0 0000a0 000010 08 WA", ".got PROGBITS 00000000000000a0 0000a0 000010 08 W"),
                base.replace(" 0x3 (PLTGOT) 0xb0", " 0x3 (PLTGOT) 0x90"),
                base.replace(
                    ".dynamic DYNAMIC 0000000000000080 000080 000020",
                    ".dynamic DYNAMIC 0000000000000081 000080 000020",
                ),
            )
            for mutation in mutations:
                with self.subTest(mutation=hash(mutation)):
                    with self.assertRaises(audit.LinuxApplicationAuditError):
                        audit._parse_elf(path, binary, mutation)

    def test_version_index_and_unparsed_dynsym_rows_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._binary(Path(temporary))
            path = f"lib/{audit.PROVIDER_NAME}"
            base = _elf_output(
                needed=audit.PROVIDER_NEEDED,
                soname=audit.PROVIDER_NAME,
                runpath=None,
            )
            for mutation in (
                base.replace(
                    "fixture@GLIBC_2.27 (2)", "fixture@GLIBC_2.27 (5)"
                ),
                base.replace("File: libc.so.6", "File: libm.so.6"),
                base.replace(
                    "fixture@GLIBC_2.27 (2)", "fixture bad@GLIBC_2.27 (2)"
                ),
            ):
                with self.assertRaises(audit.LinuxApplicationAuditError):
                    audit._parse_elf(path, binary, mutation)

    def test_exact_67_versioned_global_exports_and_unique_rejection(self) -> None:
        expected = audit._expected_dort_exports(REPOSITORY)
        self.assertEqual(len(expected), 67)
        symbols = [
            "  1: 0000000000001000 0 OBJECT GLOBAL DEFAULT ABS FONIX_DORT_1.0",
            *[
                f"  {index + 2}: 0000000000001000 8 FUNC GLOBAL DEFAULT 12 "
                f"{name}@@FONIX_DORT_1.0"
                for index, name in enumerate(sorted(expected))
            ],
        ]
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._binary(Path(temporary))
            output = _elf_output(
                needed=audit.SHIM_NEEDED,
                soname=audit.SHIM_NAME,
                runpath="$ORIGIN",
                symbols=symbols,
            )
            shim = audit._parse_elf(f"lib/{audit.SHIM_NAME}", binary, output)
            runtime = replace(
                shim,
                path=f"lib/{audit.RUNTIME_NAME}",
                defined_symbols=("OrtGetApiBase",),
            )
            audit._validate_exports([shim, runtime], expected)
            unique = output.replace(
                "FUNC GLOBAL DEFAULT 12",
                "FUNC UNIQUE DEFAULT 12",
                1,
            )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "non-GLOBAL"
            ):
                audit._parse_elf(f"lib/{audit.SHIM_NAME}", binary, unique)
            for changed in (
                output.replace(
                    "FUNC GLOBAL DEFAULT 12",
                    "OBJECT GLOBAL DEFAULT 12",
                    1,
                ),
                output.replace(
                    "FUNC GLOBAL DEFAULT 12",
                    "FUNC GLOBAL HIDDEN 12",
                    1,
                ),
            ):
                with self.assertRaisesRegex(
                    audit.LinuxApplicationAuditError,
                    "FUNC GLOBAL DEFAULT",
                ):
                    audit._parse_elf(
                        f"lib/{audit.SHIM_NAME}", binary, changed
                    )
            undefined_ort = _elf_output(
                needed=audit.SHIM_NEEDED,
                soname=audit.SHIM_NAME,
                runpath="$ORIGIN",
                symbols=[
                    *symbols,
                    "  999: 0000000000000000 0 FUNC GLOBAL DEFAULT UND "
                    "OrtGetApiBase@ORT_PRIVATE (7)",
                ],
            )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "undefined ONNX Runtime"
            ):
                audit._parse_elf(
                    f"lib/{audit.SHIM_NAME}", binary, undefined_ort
                )
            duplicate_runtime = replace(
                runtime, path="lib/libflutter_linux_gtk.so"
            )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "duplicate ONNX Runtime"
            ):
                audit._validate_exports([shim, runtime, duplicate_runtime], expected)
            duplicate_export_output = _elf_output(
                needed=audit.SHIM_NEEDED,
                soname=audit.SHIM_NAME,
                runpath="$ORIGIN",
                symbols=[*symbols, symbols[1]],
            )
            duplicate_export_shim = audit._parse_elf(
                f"lib/{audit.SHIM_NAME}", binary, duplicate_export_output
            )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "exported-symbol"
            ):
                audit._validate_exports(
                    [duplicate_export_shim, runtime], expected
                )
            duplicate_ort = replace(
                runtime,
                defined_symbols=("OrtGetApiBase", "OrtGetApiBase"),
            )
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "duplicate ONNX Runtime"
            ):
                audit._validate_exports([shim, duplicate_ort], expected)

    def test_successful_readelf_stderr_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "readelf"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "sys.stdout.write('output')\n"
                "sys.stderr.write('warning')\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "unexpected stderr"
            ):
                audit._execute_bounded((str(executable),))


class LinuxHookProvenanceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, object]:
        repository = root / "repository"
        application = root / "application"
        invocation = application / ".dart_tool/hooks_runner/fonix/abcdef1234"
        shared = application / ".dart_tool/hooks_runner/shared/fonix/build"
        staging = shared / "runtime"
        cache = application / ".fonix-artifact-cache"
        for directory in (repository, invocation, shared, staging, cache):
            directory.mkdir(parents=True, exist_ok=True)
        (application / "pubspec.yaml").write_text("name: fixture\n", encoding="utf-8")
        critical = [
            repository / "native/versions.lock.yaml",
            repository / "src/CMakeLists.txt",
            repository / "src/fonix_exports.map",
            repository / "src/fonix_exports.apple",
            repository / "src/fonix_shim.def",
            repository / "src/a.c",
            repository / "src/a.h",
            repository / "third_party/onnxruntime/include/onnxruntime_c_api.h",
            repository / "third_party/onnxruntime/include/onnxruntime_ep_c_api.h",
            cache / "onnxruntime-linux-x64-1.27.1.tgz",
        ]
        for path in critical:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"dependency")
        tools = {name: root / name for name in ("cc", "ar", "ld")}
        for tool in tools.values():
            tool.write_bytes(b"tool")
            tool.chmod(0o755)
        references = {
            "package:fonix/fonix_shim": shared / "shim/libfonix_shim.so",
            "package:fonix/onnxruntime": staging / audit.RUNTIME_NAME,
            "package:fonix/onnxruntime_providers_shared": staging / audit.PROVIDER_NAME,
        }
        for path in references.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(path.name.encode("utf-8"))
        hook_input = invocation / "input.json"
        hook_input.write_text(
            json.dumps(
                {
                    "assets": {},
                    "config": {
                        "build_asset_types": ["code_assets/code"],
                        "extensions": {
                            "code_assets": {
                                "c_compiler": {
                                    "ar": str(tools["ar"]),
                                    "cc": str(tools["cc"]),
                                    "ld": str(tools["ld"]),
                                },
                                "link_mode_preference": "dynamic",
                                "target_architecture": "x64",
                                "target_os": "linux",
                            }
                        },
                        "linking_enabled": True,
                    },
                    "out_dir_shared": str(shared) + "/",
                    "out_file": str(invocation / "output.json"),
                    "package_name": "fonix",
                    "package_root": str(repository) + "/",
                    "user_defines": {
                        "workspace_pubspec": {
                            "base_path": str(application / "pubspec.yaml"),
                            "defines": {
                                "runtime_mode": "bundled",
                                "artifact_cache": ".fonix-artifact-cache",
                                "application_minimum_os": "14.0",
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        assets = [
            {
                "encoding": {
                    "file": str(path),
                    "id": identifier,
                    "link_mode": {"type": "dynamic_loading_bundle"},
                },
                "type": "code_assets/code",
            }
            for identifier, path in references.items()
        ]
        (invocation / "output.json").write_text(
            json.dumps(
                {
                    "assets": assets,
                    "assets_for_linking": {},
                    "dependencies": [
                        str(cache),
                        *[str(path) for path in critical],
                        str(repository / "src/a.c"),
                    ],
                    "status": "success",
                    "timestamp": "2026-08-07 16:00:54.000",
                }
            ),
            encoding="utf-8",
        )
        return {
            "repository": repository.resolve(),
            "hook_input": hook_input,
            "shim": references["package:fonix/fonix_shim"],
            "runtime": references["package:fonix/onnxruntime"],
            "provider": references["package:fonix/onnxruntime_providers_shared"],
            "tools": tools,
        }

    def test_exact_input_output_assets_compilers_and_duplicate_dependencies_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            tools = fixture["tools"]
            result = audit._validate_hook_input(
                fixture["hook_input"],
                fixture["repository"],
                fixture["shim"],
                fixture["runtime"],
                fixture["provider"],
                tools["cc"],
                tools["ar"],
                tools["ld"],
            )
            self.assertEqual(result["invocation"], "abcdef1234")
            self.assertEqual(set(result["compilerSha256"]), {"cc", "ar", "ld"})

    def test_extra_dependency_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            tools = fixture["tools"]
            extra = fixture["repository"] / "README.md"
            extra.write_text("unexpected dependency\n", encoding="utf-8")
            output_path = fixture["hook_input"].with_name("output.json")
            output = json.loads(output_path.read_text(encoding="utf-8"))
            output["dependencies"].append(str(extra))
            output_path.write_text(json.dumps(output), encoding="utf-8")
            with self.assertRaisesRegex(
                audit.LinuxApplicationAuditError, "dependency set changed"
            ):
                audit._validate_hook_input(
                    fixture["hook_input"],
                    fixture["repository"],
                    fixture["shim"],
                    fixture["runtime"],
                    fixture["provider"],
                    tools["cc"],
                    tools["ar"],
                    tools["ld"],
                )

    def test_malformed_or_impossible_timestamp_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            tools = fixture["tools"]
            output_path = fixture["hook_input"].with_name("output.json")
            output = json.loads(output_path.read_text(encoding="utf-8"))
            for timestamp in (
                "2026-08-07T16:00:54.000",
                "2026-02-30 16:00:54.000",
            ):
                output["timestamp"] = timestamp
                output_path.write_text(json.dumps(output), encoding="utf-8")
                with self.assertRaises(audit.LinuxApplicationAuditError):
                    audit._validate_hook_input(
                        fixture["hook_input"],
                        fixture["repository"],
                        fixture["shim"],
                        fixture["runtime"],
                        fixture["provider"],
                        tools["cc"],
                        tools["ar"],
                        tools["ld"],
                    )

    def test_malformed_nested_type_and_wrong_compiler_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            tools = fixture["tools"]
            input_path = fixture["hook_input"]
            value = json.loads(input_path.read_text(encoding="utf-8"))
            value["config"]["extensions"]["code_assets"]["c_compiler"] = []
            input_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(audit.LinuxApplicationAuditError):
                audit._validate_hook_input(
                    input_path,
                    fixture["repository"],
                    fixture["shim"],
                    fixture["runtime"],
                    fixture["provider"],
                    tools["cc"],
                    tools["ar"],
                    tools["ld"],
                )


if __name__ == "__main__":
    unittest.main()
