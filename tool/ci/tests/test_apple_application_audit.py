from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "audit_apple_application.py"
SPEC = importlib.util.spec_from_file_location("audit_apple_application", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit_apple_application = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_apple_application)


def _macho_bytes(
    *,
    signed: bool = False,
    dependencies: tuple[str | tuple[int, str, int, int], ...] = (),
    rpaths: tuple[str, ...] = (),
    dylib_id: str | None = None,
    dylib_id_timestamp: int = 1,
    dylib_id_current_version: int = 0,
    dylib_id_compatibility_version: int = 0,
    cpu_subtype: int = 0,
    file_type: int = audit_apple_application._MACHO_EXECUTE_FILE_TYPE,
    header_flags: int = 0,
    extra_command_kinds: tuple[int, ...] = (),
) -> bytes:
    commands: list[bytes] = []
    if dylib_id is not None:
        encoded = dylib_id.encode("utf-8") + b"\0"
        command_size = 24 + len(encoded)
        command_size += (-command_size) % 8
        commands.append(
            struct.pack(
                "<IIIIII",
                audit_apple_application._MACHO_DYLIB_ID_COMMAND,
                command_size,
                24,
                dylib_id_timestamp,
                dylib_id_current_version,
                dylib_id_compatibility_version,
            )
            + encoded
            + b"\0" * (command_size - 24 - len(encoded))
        )
    for dependency in dependencies:
        if isinstance(dependency, str):
            command = 0xC
            path = dependency
            current_version = 0
            compatibility_version = 0
        else:
            command, path, current_version, compatibility_version = dependency
        encoded = path.encode("utf-8") + b"\0"
        command_size = 24 + len(encoded)
        command_size += (-command_size) % 8
        commands.append(
            struct.pack(
                "<IIIIII",
                command,
                command_size,
                24,
                0,
                current_version,
                compatibility_version,
            )
            + encoded
            + b"\0" * (command_size - 24 - len(encoded))
        )
    for rpath in rpaths:
        encoded = rpath.encode("utf-8") + b"\0"
        command_size = 12 + len(encoded)
        command_size += (-command_size) % 8
        commands.append(
            struct.pack(
                "<III",
                audit_apple_application._MACHO_RPATH_COMMAND,
                command_size,
                12,
            )
            + encoded
            + b"\0" * (command_size - 12 - len(encoded))
        )
    commands.extend(struct.pack("<II", command, 8) for command in extra_command_kinds)
    if signed:
        signature_offset = 32 + sum(len(command) for command in commands) + 16
        commands.append(
            struct.pack(
                "<IIII",
                audit_apple_application._MACHO_CODE_SIGNATURE_COMMAND,
                16,
                signature_offset,
                4,
            )
        )
    command_bytes = b"".join(commands)
    data = struct.pack(
        "<IiiIIIII",
        0xFEEDFACF,
        0x0100000C,
        cpu_subtype,
        file_type,
        len(commands),
        len(command_bytes),
        header_flags,
        0,
    ) + command_bytes
    if signed:
        data += b"SIG!"
    return data


_SYNTHETIC_APP_DEPENDENCY = (
    0xC,
    "/usr/lib/libSystem.B.dylib",
    0x10000,
    0x10000,
)
_SYNTHETIC_IOS_BINARY_OPTIONS: dict[str, dict[str, object]] = {
    "Runner": {
        "file_type": audit_apple_application._MACHO_EXECUTE_FILE_TYPE,
        "header_flags": 0x210085,
    },
    "Frameworks/App.framework/App": {
        "signed": True,
        "dependencies": (_SYNTHETIC_APP_DEPENDENCY,),
        "rpaths": (
            "@executable_path/Frameworks",
            "@loader_path/Frameworks",
        ),
        "dylib_id": "@rpath/App.framework/App",
        "file_type": audit_apple_application._MACHO_DYLIB_FILE_TYPE,
        "header_flags": 0x100085,
    },
    "Frameworks/Flutter.framework/Flutter": {
        "signed": True,
        "dylib_id": "@rpath/Flutter.framework/Flutter",
        "file_type": audit_apple_application._MACHO_DYLIB_FILE_TYPE,
        "header_flags": 0x2910085,
    },
    "Frameworks/fonix_shim.framework/fonix_shim": {
        "signed": True,
        "dylib_id": "@rpath/fonix_shim.framework/fonix_shim",
        "file_type": audit_apple_application._MACHO_DYLIB_FILE_TYPE,
        "header_flags": 0x910085,
    },
}
_SYNTHETIC_IOS_MINIMUM_OS = {
    "Runner": (15, 1, 0),
    "Frameworks/App.framework/App": (15, 0, 0),
    "Frameworks/Flutter.framework/Flutter": (15, 0, 0),
    "Frameworks/fonix_shim.framework/fonix_shim": (15, 1, 0),
}


def _synthetic_ios_macho(relative: str, **overrides: object) -> bytes:
    options = dict(_SYNTHETIC_IOS_BINARY_OPTIONS[relative])
    options.update(overrides)
    return _macho_bytes(**options)


def _ios_application(
    *,
    app_dependencies: tuple[str, ...] = (),
    app_rpaths: tuple[str, ...] | None = None,
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-audit-")
    application = Path(temporary.name) / "Runner.app"
    app_options = dict(
        _SYNTHETIC_IOS_BINARY_OPTIONS["Frameworks/App.framework/App"]
    )
    app_options["dependencies"] = (
        _SYNTHETIC_APP_DEPENDENCY,
        *app_dependencies,
    )
    if app_rpaths is not None:
        app_options["rpaths"] = app_rpaths
    files = {
        relative: (
            _macho_bytes(**app_options)
            if relative == "Frameworks/App.framework/App"
            else _synthetic_ios_macho(relative)
        )
        for relative in _SYNTHETIC_IOS_BINARY_OPTIONS
    }
    for relative, data in files.items():
        path = application / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return temporary, application


def _synthetic_ios_profile(
    application: Path, platform: str, executable: str
) -> tuple[str, dict[str, dict[str, object]], dict[str, str]]:
    del application, platform
    binaries: dict[str, dict[str, object]] = {}
    for relative in _SYNTHETIC_IOS_BINARY_OPTIONS:
        expected_relative = executable if relative == "Runner" else relative
        facts = audit_apple_application._macho_slice_facts(
            _synthetic_ios_macho(relative), Path(expected_relative)
        )
        binaries[expected_relative] = {
            "dependencies": frozenset(facts["dynamicDependencies"]),
            "minimumOs": _SYNTHETIC_IOS_MINIMUM_OS[relative],
            "cpuSubtype": facts["cpuSubtype"],
            "fileType": facts["fileType"],
            "headerFlags": facts["headerFlags"],
            "dylibId": facts["dylibId"],
            "dependencyMetadataSha256": facts["dependencyMetadataSha256"],
            "loadCommandKindsSha256": facts["loadCommandKindsSha256"],
            "rpaths": tuple(facts["rpaths"]),
        }
    return (
        "synthetic-ios",
        binaries,
        {},
    )


_IOS_OTOOL_OUTPUT = """
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 2
    minos 15.1
      sdk 26.0
   ntools 1
"""


def _ios_otool_output(version: str) -> str:
    return _IOS_OTOOL_OUTPUT.replace("15.1", version)


def _normalized_macho_bytes(
    *,
    cpu_subtype: int = 0,
    file_type: int = audit_apple_application._MACHO_DYLIB_FILE_TYPE,
    header_flags: int = 0x910085,
    text_address: int = 0x100000300,
    text_flags: int = 0x80000400,
    text_maximum_protection: int = 5,
    text_initial_protection: int = 5,
    text_bytes: bytes = b"T" * 0x20,
    header_padding_byte: int = 0,
    padding_byte: int = 0xA5,
    data_byte: int = 0xD0,
    export_payload: bytes = b"EXPT",
    fixups_payload: bytes = b"FIXU",
    load_command_uuid: bytes = b"U" * 16,
) -> bytes:
    if len(text_bytes) != 0x20:
        raise ValueError("synthetic text payload must be exactly 32 bytes")
    if len(load_command_uuid) != 16:
        raise ValueError("synthetic LC_UUID must be exactly 16 bytes")

    def name(value: str) -> bytes:
        return value.encode("ascii").ljust(16, b"\0")

    text_section = struct.pack(
        "<16s16sQQIIIIIIII",
        name("__text"),
        name("__TEXT"),
        text_address,
        len(text_bytes),
        0x300,
        2,
        0,
        0,
        text_flags,
        0,
        0,
        0,
    )
    text_segment = struct.pack(
        "<II16sQQQQiiII",
        audit_apple_application._MACHO_SEGMENT_64_COMMAND,
        72 + len(text_section),
        name("__TEXT"),
        0x100000000,
        0x400,
        0,
        0x400,
        text_maximum_protection,
        text_initial_protection,
        1,
        0,
    ) + text_section
    data_segment = struct.pack(
        "<II16sQQQQiiII",
        audit_apple_application._MACHO_SEGMENT_64_COMMAND,
        72,
        name("__DATA"),
        0x100001000,
        0x100,
        0x400,
        0x80,
        3,
        3,
        0,
        0,
    )
    linkedit_segment = struct.pack(
        "<II16sQQQQiiII",
        audit_apple_application._MACHO_SEGMENT_64_COMMAND,
        72,
        name("__LINKEDIT"),
        0x100002000,
        0x100,
        0x480,
        0x80,
        1,
        1,
        0,
        0,
    )
    export_command = struct.pack(
        "<IIII", 0x80000033, 16, 0x490, len(export_payload)
    )
    fixups_command = struct.pack(
        "<IIII", 0x80000034, 16, 0x4A0, len(fixups_payload)
    )
    uuid_command = struct.pack(
        "<II16s",
        audit_apple_application._MACHO_UUID_COMMAND,
        24,
        load_command_uuid,
    )
    commands = (
        text_segment
        + data_segment
        + linkedit_segment
        + uuid_command
        + export_command
        + fixups_command
    )
    data = bytearray(0x500)
    data[:32] = struct.pack(
        "<IiiIIIII",
        0xFEEDFACF,
        0x0100000C,
        cpu_subtype,
        file_type,
        6,
        len(commands),
        header_flags,
        0,
    )
    data[32 : 32 + len(commands)] = commands
    data[0x250] = header_padding_byte
    data[0x300:0x320] = text_bytes
    data[0x350] = padding_byte
    data[0x400:0x480] = bytes([data_byte]) * 0x80
    data[0x480:0x500] = b"L" * 0x80
    data[0x490 : 0x490 + len(export_payload)] = export_payload
    data[0x4A0 : 0x4A0 + len(fixups_payload)] = fixups_payload
    return bytes(data)


def _hook_reference_fixture() -> tuple[
    tempfile.TemporaryDirectory[str],
    Path,
    Path,
    Path,
    Path,
    Path,
    Path,
    dict[str, object],
    dict[str, object],
]:
    temporary = tempfile.TemporaryDirectory(prefix="fonix-hook-reference-")
    root = Path(temporary.name)
    application = root / "build/ios/iphoneos/Runner.app"
    packaged = application / audit_apple_application._IOS_SHIM_PATH
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"packaged-shim")
    invocation_id = "0123456789"
    reference = (
        root
        / ".dart_tool/hooks_runner/shared/fonix/build"
        / invocation_id
        / "libfonix_shim.dylib"
    )
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"prepackage-reference-shim")
    runner_root = root / ".dart_tool/hooks_runner/fonix" / invocation_id
    runner_root.mkdir(parents=True)
    input_path = runner_root / "input.json"
    output_path = runner_root / "output.json"
    repository = root / "fonix-package"
    repository.mkdir()
    hook_input: dict[str, object] = {
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
                        "target_sdk": "iphoneos",
                        "target_version": 13,
                    },
                    "link_mode_preference": "dynamic",
                    "target_architecture": "arm64",
                    "target_os": "ios",
                }
            },
            "linking_enabled": True,
        },
        "out_dir_shared": str(reference.parents[1]),
        "out_file": str(output_path),
        "package_name": "fonix",
        "package_root": str(repository),
        "user_defines": {
            "workspace_pubspec": {
                "base_path": str(root / "pubspec.yaml"),
                "defines": {
                    "application_minimum_os": "15.1",
                    "artifact_cache": ".fonix-artifact-cache",
                    "runtime_mode": "linked",
                },
            }
        },
    }
    hook_output: dict[str, object] = {
        "assets": [
            {
                "encoding": {
                    "file": str(reference),
                    "id": "package:fonix/fonix_shim",
                    "link_mode": {"type": "dynamic_loading_bundle"},
                },
                "type": "code_assets/code",
            }
        ],
        "assets_for_linking": {},
        "dependencies": [str(root / "pubspec.yaml")],
        "status": "success",
        "timestamp": "2026-08-07T00:00:00Z",
    }
    input_path.write_text(json.dumps(hook_input), encoding="utf-8")
    output_path.write_text(json.dumps(hook_output), encoding="utf-8")
    return (
        temporary,
        application,
        repository,
        packaged,
        reference,
        input_path,
        output_path,
        hook_input,
        hook_output,
    )


def _synthetic_linkedit_images(
    *, local_strip: bool, signed_reference: bool
) -> tuple[dict[str, object], dict[str, object], int, int]:
    file_offset = 0x1000
    symbol_offset = 0x1100
    reference_local_count = 3 if local_strip else 1
    packaged_local_count = 1
    external_count = 2
    undefined_count = 1

    def image(
        *, local_count: int, string_size: int, signature_size: int | None
    ) -> tuple[dict[str, object], int]:
        symbol_count = local_count + external_count + undefined_count
        symbol_end = symbol_offset + symbol_count * 16
        indirect_offset = symbol_end
        string_offset = indirect_offset + 16
        content_end = string_offset + string_size
        if signature_size is None:
            code_signature = None
            file_end = content_end
        else:
            signature_offset = (content_end + 0xF) & ~0xF
            code_signature = {
                "offset": signature_offset,
                "size": signature_size,
            }
            file_end = signature_offset + signature_size
        file_size = file_end - file_offset
        linkedit = {
            "name": "__LINKEDIT",
            "vmAddress": 0x2000,
            "vmSize": (file_size + 0x3FFF) & ~0x3FFF,
            "fileOffset": file_offset,
            "fileSize": file_size,
            "maximumProtection": 1,
            "initialProtection": 1,
            "flags": 0,
            "sections": [],
        }
        symtab = {
            "symbolOffset": symbol_offset,
            "symbolCount": symbol_count,
            "stringOffset": string_offset,
            "stringSize": string_size,
        }
        dysymtab = {
            "localIndex": 0,
            "localCount": local_count,
            "externalIndex": local_count,
            "externalCount": external_count,
            "undefinedIndex": local_count + external_count,
            "undefinedCount": undefined_count,
            "tocOffset": 0,
            "tocCount": 0,
            "moduleTableOffset": 0,
            "moduleTableCount": 0,
            "externalReferenceOffset": 0,
            "externalReferenceCount": 0,
            "indirectSymbolOffset": indirect_offset,
            "indirectSymbolCount": 4,
            "externalRelocationOffset": 0,
            "externalRelocationCount": 0,
            "localRelocationOffset": 0,
            "localRelocationCount": 0,
        }
        return {
            "linkedit": linkedit,
            "symtab": symtab,
            "dysymtab": dysymtab,
            "codeSignature": code_signature,
        }, file_end

    reference, reference_size = image(
        local_count=reference_local_count,
        string_size=100 if local_strip else 60,
        signature_size=64 if signed_reference else None,
    )
    packaged, packaged_size = image(
        local_count=packaged_local_count,
        string_size=60,
        signature_size=80,
    )
    return packaged, reference, packaged_size, reference_size


def _synthetic_dysymtab_payload(
    *, local_count: int = 1
) -> tuple[bytes, dict[str, int], dict[str, int], dict[str, int]]:
    data = bytearray(0x500)
    symbol_offset = 0x40
    names = [
        *(f"_local{index}" for index in range(local_count)),
        "_export",
        "_import",
        "module",
    ]
    strings = bytearray(b"\0")
    string_indexes: dict[str, int] = {}
    for name in names:
        string_indexes[name] = len(strings)
        strings.extend(name.encode("ascii") + b"\0")

    external_index = local_count
    undefined_index = external_index + 1
    symbol_names = [
        *(f"_local{index}" for index in range(local_count)),
        "_export",
        "_import",
    ]
    for index, name in enumerate(symbol_names):
        symbol_type = 0x0E if index < local_count + 1 else 0x01
        section = 1 if symbol_type == 0x0E else 0
        value = (
            0x1000 + index
            if index < local_count
            else (0x2000 if name == "_export" else 0)
        )
        struct.pack_into(
            "<IBBHQ",
            data,
            symbol_offset + index * 16,
            string_indexes[name],
            symbol_type,
            section,
            0,
            value,
        )

    cursor = symbol_offset + len(symbol_names) * 16
    cursor = (cursor + 7) & ~7
    toc_offset = cursor
    struct.pack_into("<II", data, toc_offset, external_index, 0)
    cursor += 8
    module_offset = cursor
    struct.pack_into(
        "<12IQ",
        data,
        module_offset,
        string_indexes["module"],
        external_index,
        1,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
    )
    cursor += 56
    external_reference_offset = cursor
    struct.pack_into("<I", data, external_reference_offset, (1 << 24) | external_index)
    cursor += 4
    indirect_offset = cursor
    struct.pack_into(
        "<4I",
        data,
        indirect_offset,
        external_index,
        undefined_index,
        0x80000000,
        0xC0000000,
    )
    cursor += 16
    external_relocation_offset = cursor
    struct.pack_into(
        "<II",
        data,
        external_relocation_offset,
        0x10,
        external_index | (3 << 25) | (1 << 27),
    )
    cursor += 8
    local_relocation_offset = cursor
    struct.pack_into(
        "<II",
        data,
        local_relocation_offset,
        0x20,
        1 | (3 << 25),
    )
    cursor += 8
    string_offset = cursor
    data[string_offset : string_offset + len(strings)] = strings
    string_size = len(strings)
    symtab = {
        "symbolOffset": symbol_offset,
        "symbolCount": len(symbol_names),
        "stringOffset": string_offset,
        "stringSize": string_size,
    }
    dysymtab = {
        "localIndex": 0,
        "localCount": local_count,
        "externalIndex": external_index,
        "externalCount": 1,
        "undefinedIndex": undefined_index,
        "undefinedCount": 1,
        "tocOffset": toc_offset,
        "tocCount": 1,
        "moduleTableOffset": module_offset,
        "moduleTableCount": 1,
        "externalReferenceOffset": external_reference_offset,
        "externalReferenceCount": 1,
        "indirectSymbolOffset": indirect_offset,
        "indirectSymbolCount": 4,
        "externalRelocationOffset": external_relocation_offset,
        "externalRelocationCount": 1,
        "localRelocationOffset": local_relocation_offset,
        "localRelocationCount": 1,
    }
    offsets = {
        "toc": toc_offset,
        "module": module_offset,
        "externalReference": external_reference_offset,
        "indirect": indirect_offset,
        "externalRelocation": external_relocation_offset,
        "localRelocation": local_relocation_offset,
        "string": string_offset,
        "exportString": string_offset + string_indexes["_export"],
    }
    return bytes(data), symtab, dysymtab, offsets


def _walk_failing_at_directory(
    real_walk: object, directory_name: str, private_error_path: Path
):
    def walk(*args: object, **kwargs: object):
        for directory, directories, files in real_walk(  # type: ignore[operator]
            *args, **kwargs
        ):
            if Path(directory).name == directory_name:
                onerror = kwargs.get("onerror")
                assert callable(onerror)
                onerror(
                    PermissionError(
                        13,
                        "synthetic unreadable application subtree",
                        str(private_error_path),
                    )
                )
                return
            yield directory, directories, files

    return walk


class AppleApplicationMetadataAuditTest(unittest.TestCase):
    def _application(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-apple-audit-")
        application = Path(temporary.name) / "Sample.app"
        asset_root = (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix"
        )
        asset_root.mkdir(parents=True)
        notice = b"exact upstream notice fixture\n"
        notice_path = asset_root / "ThirdPartyNotices.txt"
        notice_path.write_bytes(notice)
        manifest = {
            "schema": 2,
            "artifactId": "onnxruntime-1.27.1-macos-arm64-cpu",
            "claimBoundary": audit_apple_application._CLAIM_BOUNDARY,
            "lock": {
                "path": "native/versions.lock.yaml",
                "releaseState": "unreleased-preview",
                "sha256": "a" * 64,
                "snapshotDate": "2026-08-06",
            },
            "target": {
                "os": "macos",
                "architecture": "arm64",
                "variant": "default",
                "minimumOs": "14.0",
                "flavor": "cpu",
                "runtimeMode": "bundled",
            },
            "source": {
                "archive": "tgz",
                "sha256": "b" * 64,
                "sizeBytes": 100,
                "sourceRevision": "c" * 40,
                "url": "https://example.invalid/onnxruntime.tgz",
            },
            "containers": [],
            "payloadFiles": [
                {
                    "archivePath": "runtime/libonnxruntime.1.dylib",
                    "sha256": "d" * 64,
                    "sizeBytes": 10,
                    "stagedPath": "libonnxruntime.1.dylib",
                }
            ],
            "notices": [
                {
                    "archivePath": "runtime/ThirdPartyNotices.txt",
                    "containerDepth": 0,
                    "id": "ThirdPartyNotices",
                    "stagedPath": "notices/ThirdPartyNotices.txt",
                    "sha256": hashlib.sha256(notice).hexdigest(),
                    "sizeBytes": len(notice),
                }
            ],
            "verifiedSymlinks": [],
            "archiveInspections": [
                {
                    "compressedBytes": 100,
                    "depth": 0,
                    "directoryCount": 0,
                    "format": "tar",
                    "memberCount": 2,
                    "regularFileCount": 2,
                    "symbolicLinkCount": 0,
                    "uncompressedBytes": 20,
                }
            ],
        }
        manifest_path = asset_root / "fonix-native-artifact-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return temporary, application, notice_path

    def test_accepts_exact_single_manifest_and_notice(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)

        manifest, manifest_path, notice_path, minimum = (
            audit_apple_application.audit_packaged_metadata(application, "macos")
        )

        self.assertEqual(minimum, (14, 0, 0))
        self.assertEqual(manifest["_auditIdentity"]["sourceSha256"], "b" * 64)
        self.assertEqual(manifest_path.name, "fonix-native-artifact-manifest.json")
        self.assertEqual(notice_path.name, "ThirdPartyNotices.txt")

    def test_fails_when_notice_is_missing_or_changed(self) -> None:
        temporary, application, notice_path = self._application()
        self.addCleanup(temporary.cleanup)
        notice_path.write_bytes(b"substituted notice\n")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")

    def test_fails_on_duplicate_manifest_anywhere_in_app(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        duplicate = application / "unexpected/fonix-native-artifact-manifest.json"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text("{}", encoding="utf-8")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")

    def test_metadata_inventory_fails_closed_on_unreadable_subtree(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        hidden = application / "zz-unreadable"
        hidden.mkdir()
        for name in (
            audit_apple_application._MANIFEST_NAME,
            audit_apple_application._NOTICE_NAME,
        ):
            (hidden / name).write_text("hidden duplicate", encoding="utf-8")
        private_error_path = hidden / "private-user-input.txt"
        real_walk = audit_apple_application.os.walk

        for name in (
            audit_apple_application._MANIFEST_NAME,
            audit_apple_application._NOTICE_NAME,
        ):
            with self.subTest(name=name), mock.patch.object(
                audit_apple_application.os,
                "walk",
                new=_walk_failing_at_directory(
                    real_walk, hidden.name, private_error_path
                ),
            ), self.assertRaises(
                audit_apple_application.AppleApplicationAuditError
            ) as caught:
                audit_apple_application._all_named_files(application, name)
            self.assertIn("application metadata inventory", str(caught.exception))
            self.assertNotIn(str(private_error_path), str(caught.exception))

    def test_fails_on_extra_manifest_field(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        manifest_path = (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix/fonix-native-artifact-manifest.json"
        )
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["unexpected"] = True
        manifest_path.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")

    def test_fails_on_wrong_target_variant(self) -> None:
        temporary, application, _ = self._application()
        self.addCleanup(temporary.cleanup)
        manifest_path = (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix/fonix-native-artifact-manifest.json"
        )
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["target"]["variant"] = "simulator"
        manifest_path.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application.audit_packaged_metadata(application, "macos")


class AppleApplicationMachOAuditTest(unittest.TestCase):
    def _macho(self, cpu_type: int) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-macho-audit-")
        binary = Path(temporary.name) / "binary"
        binary.write_bytes(
            struct.pack(
                "<IiiIIIII",
                0xFEEDFACF,
                cpu_type,
                0,
                2,
                0,
                0,
                0,
                0,
            )
        )
        return temporary, binary

    def test_fails_on_wrong_architecture(self) -> None:
        temporary, binary = self._macho(0x01000007)
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_macho(
                binary,
                architecture="arm64",
                platform_number=1,
                minimum_os=(14, 0, 0),
                otool="unused",
            )

    def test_fails_on_wrong_build_platform(self) -> None:
        temporary, binary = self._macho(0x0100000C)
        self.addCleanup(temporary.cleanup)
        original_run = audit_apple_application._run
        self.addCleanup(setattr, audit_apple_application, "_run", original_run)
        audit_apple_application._run = lambda command: """
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 2
    minos 15.1
      sdk 26.0
   ntools 1
"""
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_macho(
                binary,
                architecture="arm64",
                platform_number=1,
                minimum_os=(14, 0, 0),
                otool="unused",
            )


class AppleApplicationIosPolicyTest(unittest.TestCase):
    def _inventory(
        self,
        application: Path,
        *,
        minimum_os_overrides: dict[str, str] | None = None,
    ) -> dict[str, object]:
        minimum_os = {
            path: ".".join(str(part) for part in version[:2])
            for path, version in _SYNTHETIC_IOS_MINIMUM_OS.items()
        }
        minimum_os.update(minimum_os_overrides or {})

        def otool_output(command: tuple[str, ...]) -> str:
            binary = Path(command[-1])
            relative = binary.relative_to(application).as_posix()
            return _ios_otool_output(minimum_os[relative])

        with mock.patch.object(
            audit_apple_application,
            "_ios_native_profile",
            side_effect=_synthetic_ios_profile,
        ), mock.patch.object(
            audit_apple_application,
            "_audit_shim_exports",
            return_value={"symbolCount": 67},
        ), mock.patch.object(
            audit_apple_application,
            "_run",
            side_effect=otool_output,
        ):
            return audit_apple_application._inventory_ios_application(
                application,
                "Runner",
                platform="ios-device",
                architecture="arm64",
                platform_number=2,
                maximum_os=(15, 1, 0),
                repository=Path("/repository"),
                otool="otool",
                nm="nm",
                dyld_info="dyld_info",
            )

    def _audit_report(
        self,
        *,
        signature_policy: str,
        declared_minimum_os: str = "15.1",
        plist_minimum_os: str = "15.1",
    ) -> tuple[dict[str, object], mock.Mock]:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-report-")
        self.addCleanup(temporary.cleanup)
        application = Path(temporary.name) / "Runner.app"
        application.mkdir()
        manifest = {
            "_auditIdentity": {
                "artifactId": "ios-artifact",
                "minimumOs": "15.1",
            },
            "target": {"architecture": "arm64"},
        }
        binary_paths = [
            application / "Runner",
            application / audit_apple_application._IOS_SHIM_PATH,
        ]
        inventory = {
            "profile": "ios-device-release",
            "frameworks": [],
            "machOBinaries": [{"path": "Runner"}],
            "shimExports": {"symbolCount": 67},
        }
        unsigned_details = {
            "rootBundle": "unsigned",
            "rootExecutable": "unsigned",
            "provisioningProfile": "absent",
            "nestedFrameworks": [],
        }
        strict_details = {
            "rootBundle": "verified",
            "rootExecutable": "verified",
            "verifiedCodeObjects": ["Runner"],
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "audit_packaged_metadata",
                    return_value=(
                        manifest,
                        application / "manifest.json",
                        application / "notice.txt",
                        (15, 1, 0),
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_assert_locked_manifest",
                    return_value={},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_plist",
                    return_value=(
                        {
                            "MinimumOSVersion": plist_minimum_os,
                            "CFBundleExecutable": "Runner",
                        },
                        application / "Info.plist",
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_macho_paths",
                    return_value=binary_paths,
                )
            )
            stack.enter_context(
                mock.patch.object(audit_apple_application, "_audit_macho")
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_expected_build_manifest",
                    return_value={"expected": True},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_inventory_ios_application",
                    return_value=inventory,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_audit_unsigned_ios_device_signing",
                    return_value=unsigned_details,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_audit_strict_ios_signing",
                    return_value=strict_details,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    audit_apple_application,
                    "_audit_ios_reference_shim",
                    return_value={
                        "runtimeMode": "linked",
                        "normalizedRuntimeFields": "matched",
                        "staticArchiveMultiplicity": (
                            "not-provable-from-final-bundle"
                        ),
                    },
                )
            )
            run = stack.enter_context(
                mock.patch.object(audit_apple_application, "_run", return_value="")
            )
            report = audit_apple_application.audit_application(
                application,
                "ios-device",
                declared_minimum_os,
                repository=Path(temporary.name),
                reference_shim=application / "reference-shim.dylib",
                signature_policy=signature_policy,
            )
        return report, run

    def test_unsigned_report_is_explicitly_static_only(self) -> None:
        report, run = self._audit_report(
            signature_policy="ios-device-unsigned-development"
        )

        self.assertEqual(report["signaturePolicy"], "ios-device-unsigned-development")
        self.assertEqual(report["signatureStatus"], "unsigned")
        self.assertEqual(report["claimStatus"], "static-only")
        self.assertEqual(report["signatureDetails"]["rootBundle"], "unsigned")
        self.assertEqual(
            report["nativeInventory"]["linkedRuntimeIdentity"][
                "staticArchiveMultiplicity"
            ],
            "not-provable-from-final-bundle",
        )
        run.assert_not_called()

    def test_strict_policy_also_reports_closed_native_inventory(self) -> None:
        report, run = self._audit_report(signature_policy="strict")

        self.assertEqual(report["signatureStatus"], "verified")
        self.assertEqual(
            report["nativeInventory"]["profile"], "ios-device-release"
        )
        self.assertEqual(report["signatureDetails"]["rootBundle"], "verified")
        run.assert_not_called()

    def test_ios_declared_and_plist_floors_must_be_exactly_locked(self) -> None:
        for declared_minimum_os, plist_minimum_os in (
            ("16.0", "16.0"),
            ("15.1", "16.0"),
            ("15.0", "15.1"),
        ):
            with self.subTest(
                declared=declared_minimum_os, plist=plist_minimum_os
            ), self.assertRaises(audit_apple_application.AppleApplicationAuditError):
                self._audit_report(
                    signature_policy="strict",
                    declared_minimum_os=declared_minimum_os,
                    plist_minimum_os=plist_minimum_os,
                )

    def test_unsigned_policy_is_closed_to_ios_device(self) -> None:
        self.assertEqual(
            audit_apple_application._signature_policy(
                "ios-device", "ios-device-unsigned-development"
            ),
            "ios-device-unsigned-development",
        )
        for platform in ("macos", "ios-simulator"):
            with self.subTest(platform=platform), self.assertRaises(
                audit_apple_application.AppleApplicationAuditError
            ):
                audit_apple_application._signature_policy(
                    platform, "ios-device-unsigned-development"
                )

    def test_cli_defaults_to_strict_and_accepts_reference_shim(self) -> None:
        parsed = audit_apple_application._parser().parse_args(
            [
                "--app",
                "/tmp/example.app",
                "--platform",
                "ios-device",
                "--application-minimum-os",
                "15.1",
                "--reference-shim",
                "/tmp/libfonix_shim.dylib",
            ]
        )
        self.assertEqual(parsed.signature_policy, "strict")
        self.assertEqual(parsed.reference_shim, Path("/tmp/libfonix_shim.dylib"))

    def test_profiles_require_exact_four_macho_configuration(self) -> None:
        application = Path("/tmp/build/ios/iphonesimulator/Runner.app")
        for platform, expected_name in (
            ("ios-device", "ios-device-release"),
            ("ios-simulator", "ios-simulator-debug-no-debug-dylib"),
        ):
            with self.subTest(platform=platform):
                name, binaries, _ = audit_apple_application._ios_native_profile(
                    application, platform, "Runner"
                )
                self.assertEqual(name, expected_name)
                self.assertEqual(
                    set(binaries),
                    {
                        "Runner",
                        "Frameworks/App.framework/App",
                        "Frameworks/Flutter.framework/Flutter",
                        "Frameworks/fonix_shim.framework/fonix_shim",
                    },
                )
                self.assertEqual(binaries["Runner"]["minimumOs"], (15, 1, 0))
                self.assertEqual(
                    binaries["Frameworks/App.framework/App"]["minimumOs"],
                    (15, 0, 0),
                )
                self.assertEqual(
                    binaries["Frameworks/Flutter.framework/Flutter"]["minimumOs"],
                    (15, 0, 0),
                )
                self.assertEqual(
                    binaries[audit_apple_application._IOS_SHIM_PATH]["minimumOs"],
                    (15, 1, 0),
                )
                self.assertIsNone(binaries["Runner"]["dylibId"])
                self.assertEqual(
                    binaries["Frameworks/App.framework/App"]["dylibId"]["path"],
                    "@rpath/App.framework/App",
                )
                self.assertEqual(
                    binaries["Frameworks/Flutter.framework/Flutter"]["dylibId"][
                        "path"
                    ],
                    "@rpath/Flutter.framework/Flutter",
                )
                self.assertEqual(
                    binaries[audit_apple_application._IOS_SHIM_PATH]["dylibId"][
                        "path"
                    ],
                    "@rpath/fonix_shim.framework/fonix_shim",
                )
                self.assertEqual(
                    binaries["Frameworks/App.framework/App"]["dylibId"][
                        "timestamp"
                    ],
                    0 if platform == "ios-device" else 1,
                )
                self.assertEqual(
                    binaries["Runner"]["rpaths"],
                    ("/usr/lib/swift", "@executable_path/Frameworks"),
                )

    def test_accepts_exact_closed_synthetic_inventory(self) -> None:
        temporary, application = _ios_application()
        self.addCleanup(temporary.cleanup)

        inventory = self._inventory(application)

        self.assertEqual(inventory["profile"], "synthetic-ios")
        by_path = {
            entry["path"]: entry for entry in inventory["machOBinaries"]
        }
        self.assertEqual(by_path["Runner"]["codeSignatureLoadCommands"], 0)
        self.assertEqual(
            by_path[audit_apple_application._IOS_SHIM_PATH][
                "codeSignatureLoadCommands"
            ],
            1,
        )

    def test_rejects_missing_or_extra_macho(self) -> None:
        for mutation in ("missing", "extra"):
            with self.subTest(mutation=mutation):
                temporary, application = _ios_application()
                self.addCleanup(temporary.cleanup)
                if mutation == "missing":
                    (application / "Frameworks/Flutter.framework/Flutter").unlink()
                else:
                    (application / "Runner.debug.dylib").write_bytes(_macho_bytes())
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_native_inventory_fails_closed_on_unreadable_subtree(self) -> None:
        temporary, application = _ios_application()
        self.addCleanup(temporary.cleanup)
        hidden = application / "zz-unreadable"
        hidden_macho = hidden / "onnxruntime.framework/onnxruntime"
        hidden_macho.parent.mkdir(parents=True)
        hidden_macho.write_bytes(_macho_bytes(signed=True))
        private_error_path = hidden / "private-user-input.gguf"
        real_walk = audit_apple_application.os.walk

        with mock.patch.object(
            audit_apple_application.os,
            "walk",
            new=_walk_failing_at_directory(
                real_walk, hidden.name, private_error_path
            ),
        ), self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ) as caught:
            self._inventory(application)

        self.assertIn("iOS native inventory", str(caught.exception))
        self.assertNotIn(str(private_error_path), str(caught.exception))

    def test_rejects_separately_packaged_runtime_framework(self) -> None:
        temporary, application = _ios_application()
        self.addCleanup(temporary.cleanup)
        foreign = application / "Frameworks/onnxruntime.framework/onnxruntime"
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(_macho_bytes(signed=True))

        with self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            self._inventory(application)

    def test_rejects_extra_dependency_and_build_rpath(self) -> None:
        for application_arguments in (
            {"app_dependencies": ("@rpath/evil.framework/evil",)},
            {"app_rpaths": ("/private/tmp/leaked/PackageFrameworks",)},
        ):
            with self.subTest(application_arguments=application_arguments):
                temporary, application = _ios_application(**application_arguments)
                self.addCleanup(temporary.cleanup)
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_rejects_header_subtype_filetype_and_flags_drift(self) -> None:
        mutations = (
            ("Runner", {"cpu_subtype": 1}),
            (
                "Runner",
                {"file_type": audit_apple_application._MACHO_DYLIB_FILE_TYPE},
            ),
            (
                "Frameworks/App.framework/App",
                {"file_type": audit_apple_application._MACHO_EXECUTE_FILE_TYPE},
            ),
            ("Runner", {"header_flags": 0x210084}),
        )
        for relative, overrides in mutations:
            with self.subTest(relative=relative, overrides=overrides):
                temporary, application = _ios_application()
                self.addCleanup(temporary.cleanup)
                (application / relative).write_bytes(
                    _synthetic_ios_macho(relative, **overrides)
                )
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_rejects_non_exact_dylib_ids(self) -> None:
        mutations = (
            ("Runner", "@rpath/Runner"),
            ("Frameworks/App.framework/App", "@rpath/App.framework/Wrong"),
            (
                "Frameworks/Flutter.framework/Flutter",
                "@rpath/Flutter.framework/Wrong",
            ),
            (
                audit_apple_application._IOS_SHIM_PATH,
                "@rpath/fonix_shim.framework/Wrong",
            ),
        )
        for relative, dylib_id in mutations:
            with self.subTest(relative=relative):
                temporary, application = _ios_application()
                self.addCleanup(temporary.cleanup)
                (application / relative).write_bytes(
                    _synthetic_ios_macho(relative, dylib_id=dylib_id)
                )
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_rejects_dylib_id_metadata_and_rpath_order_mutations(self) -> None:
        relative = "Frameworks/App.framework/App"
        mutations = (
            {"dylib_id_timestamp": 2},
            {"dylib_id_current_version": 0x10000},
            {"dylib_id_compatibility_version": 0x10000},
            {
                "rpaths": (
                    "@loader_path/Frameworks",
                    "@executable_path/Frameworks",
                )
            },
        )
        for overrides in mutations:
            with self.subTest(overrides=overrides):
                temporary, application = _ios_application()
                self.addCleanup(temporary.cleanup)
                (application / relative).write_bytes(
                    _synthetic_ios_macho(relative, **overrides)
                )
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_rejects_dependency_kind_and_version_substitution(self) -> None:
        dependency_path = _SYNTHETIC_APP_DEPENDENCY[1]
        mutations = (
            ((0x80000018, dependency_path, 0x10000, 0x10000),),
            ((0xC, dependency_path, 0x20000, 0x10000),),
            ((0xC, dependency_path, 0x10000, 0x20000),),
            ((0xC, "/usr/lib/libEvil.dylib", 0x10000, 0x10000),),
        )
        relative = "Frameworks/App.framework/App"
        for dependencies in mutations:
            with self.subTest(dependencies=dependencies):
                temporary, application = _ios_application()
                self.addCleanup(temporary.cleanup)
                (application / relative).write_bytes(
                    _synthetic_ios_macho(relative, dependencies=dependencies)
                )
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_rejects_loader_environment_and_legacy_minimum_commands(self) -> None:
        for command in sorted(audit_apple_application._MACHO_FORBIDDEN_IOS_COMMANDS):
            with self.subTest(command=hex(command)):
                temporary, application = _ios_application()
                self.addCleanup(temporary.cleanup)
                (application / "Runner").write_bytes(
                    _synthetic_ios_macho(
                        "Runner", extra_command_kinds=(command,)
                    )
                )
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    self._inventory(application)

    def test_rejects_non_exact_runner_and_nested_deployment_floors(self) -> None:
        temporary, application = _ios_application()
        self.addCleanup(temporary.cleanup)
        mutations = (
            ("Runner", "15.0"),
            ("Runner", "16.0"),
            ("Frameworks/App.framework/App", "15.1"),
            ("Frameworks/App.framework/App", "16.0"),
            ("Frameworks/Flutter.framework/Flutter", "15.1"),
            ("Frameworks/Flutter.framework/Flutter", "16.0"),
        )
        for relative, minimum_os in mutations:
            with self.subTest(relative=relative, minimum_os=minimum_os), self.assertRaises(
                audit_apple_application.AppleApplicationAuditError
            ):
                self._inventory(
                    application,
                    minimum_os_overrides={relative: minimum_os},
                )

    def test_shim_export_allowlist_rejects_extra_symbol(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-exports-")
        self.addCleanup(temporary.cleanup)
        export_path = Path(temporary.name) / "fonix_exports.apple"
        export_path.write_text("_dort_expected\n", encoding="utf-8")
        with mock.patch.object(
            audit_apple_application,
            "_expected_shim_exports",
            return_value=(export_path, ["_dort_expected"]),
        ), mock.patch.object(
            audit_apple_application,
            "_run",
            return_value="_dort_expected\n_dort_evil\n",
        ), self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            audit_apple_application._audit_shim_exports(
                Path("/tmp/fonix_shim"),
                Path(temporary.name),
                nm="nm",
                dyld_info="dyld_info",
            )

    def test_shim_export_allowlist_rejects_dyld_export_drift(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-ios-dyld-exports-")
        self.addCleanup(temporary.cleanup)
        export_path = Path(temporary.name) / "fonix_exports.apple"
        expected = ["_dort_expected", "_dort_second"]
        export_path.write_text("\n".join(expected) + "\n", encoding="utf-8")

        def dyld_output(exports: list[str]) -> str:
            rows = "\n".join(
                f"        0x{index + 1:x} {symbol}"
                for index, symbol in enumerate(exports)
            )
            return f"/tmp/fonix_shim [arm64]:\n  -exports:\n    offset symbol\n{rows}\n"

        for runtime_exports in (
            ["_dort_expected"],
            [*expected, "_dort_evil"],
        ):
            with self.subTest(runtime_exports=runtime_exports):
                def run(command: tuple[str, ...]) -> str:
                    if command[0] == "nm":
                        return "\n".join(expected) + "\n"
                    return dyld_output(runtime_exports)

                with mock.patch.object(
                    audit_apple_application,
                    "_expected_shim_exports",
                    return_value=(export_path, expected),
                ), mock.patch.object(
                    audit_apple_application,
                    "_run",
                    side_effect=run,
                ), self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    audit_apple_application._audit_shim_exports(
                        Path("/tmp/fonix_shim"),
                        Path(temporary.name),
                        nm="nm",
                        dyld_info="dyld_info",
                    )

    def test_reference_shim_binding_reports_truthful_boundary(self) -> None:
        packaged = Path("/tmp/packaged-shim")
        reference = Path("/tmp/reference-shim")
        normalized_identity = {
            "header": {"cpuType": 0x0100000C},
            "headerRegionEnd": 0,
        }
        packaged_image = {
            "normalizedIdentity": normalized_identity,
            "loadCommandsEnd": 0,
            "loadCommandUuid": "11" * 16,
            "dylibId": {
                "path": "@rpath/fonix_shim.framework/fonix_shim",
                "timestamp": 1,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
        }
        reference_image = {
            "normalizedIdentity": normalized_identity,
            "loadCommandsEnd": 0,
            "loadCommandUuid": "11" * 16,
            "dylibId": {
                "path": str(reference),
                "timestamp": 1,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
        }
        with mock.patch.object(
            audit_apple_application,
            "_validate_reference_shim_hook_metadata",
            return_value={"status": "validated"},
        ), mock.patch.object(
            audit_apple_application,
            "_regular_file",
            side_effect=lambda path, label: path,
        ), mock.patch.object(
            audit_apple_application, "_audit_macho"
        ), mock.patch.object(
            audit_apple_application, "_extract_embedded_build_manifest"
        ) as extract, mock.patch.object(
            audit_apple_application, "_audit_shim_exports"
        ), mock.patch.object(
            audit_apple_application,
            "_macho_slices",
            side_effect=({"arm64": b"packaged"}, {"arm64": b"reference"}),
        ), mock.patch.object(
            audit_apple_application,
            "_macho_normalized_runtime_identity",
            side_effect=(packaged_image, reference_image),
        ), mock.patch.object(
            audit_apple_application,
            "_validate_ios_shim_linkedit_transformation",
            return_value={
                "localSymbolsRemoved": 1,
                "referenceCodeSignature": "absent",
                "packagedCodeSignature": "present",
                "linkeditSizeDelta": -1,
            },
        ):
            result = audit_apple_application._audit_ios_reference_shim(
                packaged,
                reference,
                {
                    "schemaVersion": 3,
                    "buildId": "ios-artifact",
                    "artifact": {"id": "ios-artifact"},
                },
                application=Path("/tmp/Runner.app"),
                repository=Path("/repository"),
                platform="ios-device",
                architecture="arm64",
                platform_number=2,
                minimum_os=(15, 1, 0),
                maximum_os=(15, 1, 0),
                otool="otool",
                nm="nm",
                dyld_info="dyld_info",
            )
        self.assertEqual(extract.call_count, 2)
        self.assertEqual(result["normalizedRuntimeFields"], "matched")
        self.assertEqual(result["runtimeDlopenBehavior"], "not-proved")
        self.assertEqual(result["otherMachOStaticOrtCopies"], "not-proved")
        self.assertEqual(result["separatelyPackagedOrtMachOs"], [])
        self.assertEqual(result["auditedOrtLoadCommandDependencies"], [])
        self.assertEqual(
            result["accountedTransformations"],
            [
                "absolute-prepackage-id-to-framework-rpath-id",
                "local-symbol-strip",
                "adhoc-code-signature-addition",
                "linkedit-resize",
            ],
        )
        self.assertEqual(
            result["staticArchiveMultiplicity"],
            "not-provable-from-final-bundle",
        )

    def test_reference_shim_binding_rejects_normalized_runtime_drift(self) -> None:
        with mock.patch.object(
            audit_apple_application,
            "_validate_reference_shim_hook_metadata",
            return_value={"status": "validated"},
        ), mock.patch.object(
            audit_apple_application,
            "_regular_file",
            side_effect=lambda path, label: path,
        ), mock.patch.object(
            audit_apple_application, "_audit_macho"
        ), mock.patch.object(
            audit_apple_application, "_extract_embedded_build_manifest"
        ), mock.patch.object(
            audit_apple_application, "_audit_shim_exports"
        ), mock.patch.object(
            audit_apple_application,
            "_macho_slices",
            side_effect=(
                {"arm64": b"packaged"},
                {"arm64": b"reference"},
            ),
        ), mock.patch.object(
            audit_apple_application,
            "_macho_normalized_runtime_identity",
            side_effect=(
                {"normalizedIdentity": {"runtime": "packaged"}},
                {"normalizedIdentity": {"runtime": "reference"}},
            ),
        ), self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            audit_apple_application._audit_ios_reference_shim(
                Path("/tmp/packaged-shim"),
                Path("/tmp/reference-shim"),
                {
                    "schemaVersion": 3,
                    "buildId": "ios-artifact",
                    "artifact": {"id": "ios-artifact"},
                },
                application=Path("/tmp/Runner.app"),
                repository=Path("/repository"),
                platform="ios-device",
                architecture="arm64",
                platform_number=2,
                minimum_os=(15, 1, 0),
                maximum_os=(15, 1, 0),
                otool="otool",
                nm="nm",
                dyld_info="dyld_info",
            )

    def test_reference_shim_binding_rejects_non_absolute_prepackage_id(self) -> None:
        normalized_identity = {"headerRegionEnd": 0}
        packaged_image = {
            "normalizedIdentity": normalized_identity,
            "loadCommandsEnd": 0,
            "loadCommandUuid": "11" * 16,
            "dylibId": {
                "path": "@rpath/fonix_shim.framework/fonix_shim",
                "timestamp": 1,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
        }
        reference_image = {
            "normalizedIdentity": normalized_identity,
            "loadCommandsEnd": 0,
            "loadCommandUuid": "11" * 16,
            "dylibId": {
                "path": "@rpath/fonix_shim.framework/fonix_shim",
                "timestamp": 1,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
        }
        with mock.patch.object(
            audit_apple_application,
            "_validate_reference_shim_hook_metadata",
            return_value={"status": "validated"},
        ), mock.patch.object(
            audit_apple_application,
            "_regular_file",
            side_effect=lambda path, label: path,
        ), mock.patch.object(
            audit_apple_application, "_audit_macho"
        ), mock.patch.object(
            audit_apple_application, "_extract_embedded_build_manifest"
        ), mock.patch.object(
            audit_apple_application, "_audit_shim_exports"
        ), mock.patch.object(
            audit_apple_application,
            "_macho_slices",
            side_effect=({"arm64": b"packaged"}, {"arm64": b"reference"}),
        ), mock.patch.object(
            audit_apple_application,
            "_macho_normalized_runtime_identity",
            side_effect=(packaged_image, reference_image),
        ), self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            audit_apple_application._audit_ios_reference_shim(
                Path("/tmp/packaged-shim"),
                Path("/tmp/reference-shim"),
                {
                    "schemaVersion": 3,
                    "buildId": "ios-artifact",
                    "artifact": {"id": "ios-artifact"},
                },
                application=Path("/tmp/Runner.app"),
                repository=Path("/repository"),
                platform="ios-device",
                architecture="arm64",
                platform_number=2,
                minimum_os=(15, 1, 0),
                maximum_os=(15, 1, 0),
                otool="otool",
                nm="nm",
                dyld_info="dyld_info",
            )

    def test_normalized_runtime_identity_detects_loadable_image_tampering(self) -> None:
        baseline = audit_apple_application._macho_normalized_runtime_identity(
            _normalized_macho_bytes(), Path("baseline-shim")
        )["normalizedIdentity"]
        mutations = (
            ("cpu-subtype", {"cpu_subtype": 1}),
            (
                "file-type",
                {"file_type": audit_apple_application._MACHO_EXECUTE_FILE_TYPE},
            ),
            ("header-flags", {"header_flags": 0x910084}),
            ("section-address", {"text_address": 0x100000308}),
            ("section-flags", {"text_flags": 0x80000500}),
            ("segment-max-protection", {"text_maximum_protection": 7}),
            ("segment-initial-protection", {"text_initial_protection": 1}),
            ("section-bytes", {"text_bytes": b"X" * 0x20}),
            ("loaded-padding", {"padding_byte": 0xA6}),
            ("loaded-segment-bytes", {"data_byte": 0xD1}),
            ("dyld-export-payload", {"export_payload": b"EVIL"}),
            ("dyld-fixups-payload", {"fixups_payload": b"EVIL"}),
        )
        for label, overrides in mutations:
            with self.subTest(label=label):
                tampered = (
                    audit_apple_application._macho_normalized_runtime_identity(
                        _normalized_macho_bytes(**overrides), Path(label)
                    )["normalizedIdentity"]
                )
                self.assertNotEqual(tampered, baseline)

    def test_dysymtab_payload_identity_normalizes_local_symbol_strip(self) -> None:
        packaged_data, packaged_symtab, packaged_dysymtab, _ = (
            _synthetic_dysymtab_payload(local_count=1)
        )
        reference_data, reference_symtab, reference_dysymtab, _ = (
            _synthetic_dysymtab_payload(local_count=3)
        )

        packaged_payloads = (
            audit_apple_application._macho_dysymtab_payload_identity(
                packaged_data,
                Path("packaged-shim"),
                packaged_symtab,
                packaged_dysymtab,
            )
        )
        reference_payloads = (
            audit_apple_application._macho_dysymtab_payload_identity(
                reference_data,
                Path("reference-shim"),
                reference_symtab,
                reference_dysymtab,
            )
        )
        self.assertEqual(packaged_payloads, reference_payloads)
        self.assertEqual(
            packaged_payloads["indirectSymbols"]["entryCount"], 4
        )
        self.assertEqual(
            audit_apple_application._macho_retained_symbol_identity(
                packaged_data,
                Path("packaged-shim"),
                packaged_symtab,
                packaged_dysymtab,
            ),
            audit_apple_application._macho_retained_symbol_identity(
                reference_data,
                Path("reference-shim"),
                reference_symtab,
                reference_dysymtab,
            ),
        )

    def test_dysymtab_payload_identity_detects_byte_tampering(self) -> None:
        baseline_data, symtab, dysymtab, offsets = (
            _synthetic_dysymtab_payload()
        )
        baseline = audit_apple_application._macho_dysymtab_payload_identity(
            baseline_data,
            Path("baseline-shim"),
            symtab,
            dysymtab,
        )
        mutations = {
            "table-of-contents": (
                offsets["toc"],
                struct.pack("<I", dysymtab["undefinedIndex"]),
            ),
            "module-table": (offsets["module"] + 36, b"\x01"),
            "external-reference": (
                offsets["externalReference"] + 3,
                b"\x02",
            ),
            "indirect-symbol": (
                offsets["indirect"],
                struct.pack("<I", dysymtab["undefinedIndex"]),
            ),
            "external-relocation": (
                offsets["externalRelocation"],
                struct.pack("<I", 0x11),
            ),
            "local-relocation": (
                offsets["localRelocation"],
                struct.pack("<I", 0x21),
            ),
        }
        for label, (offset, replacement) in mutations.items():
            with self.subTest(label=label):
                tampered = bytearray(baseline_data)
                tampered[offset : offset + len(replacement)] = replacement
                identity = (
                    audit_apple_application._macho_dysymtab_payload_identity(
                        bytes(tampered),
                        Path(f"{label}-shim"),
                        symtab,
                        dysymtab,
                    )
                )
                self.assertNotEqual(identity, baseline)

    def test_retained_string_table_semantics_detects_byte_tampering(self) -> None:
        baseline_data, symtab, dysymtab, offsets = (
            _synthetic_dysymtab_payload()
        )
        baseline = audit_apple_application._macho_retained_symbol_identity(
            baseline_data,
            Path("baseline-shim"),
            symtab,
            dysymtab,
        )
        tampered = bytearray(baseline_data)
        tampered[offsets["exportString"] + 1] = ord("X")

        identity = audit_apple_application._macho_retained_symbol_identity(
            bytes(tampered),
            Path("tampered-shim"),
            symtab,
            dysymtab,
        )

        self.assertNotEqual(identity["stringsSha256"], baseline["stringsSha256"])

    def test_normalized_runtime_identity_hashes_padding_outside_sections(self) -> None:
        baseline = audit_apple_application._macho_normalized_runtime_identity(
            _normalized_macho_bytes(), Path("baseline-shim")
        )["normalizedIdentity"]
        tampered = audit_apple_application._macho_normalized_runtime_identity(
            _normalized_macho_bytes(padding_byte=0xA6), Path("tampered-shim")
        )["normalizedIdentity"]
        baseline_text = baseline["loadableSegments"][0]
        tampered_text = tampered["loadableSegments"][0]
        self.assertEqual(
            baseline_text["sections"][0]["bytesSha256"],
            tampered_text["sections"][0]["bytesSha256"],
        )
        self.assertNotEqual(
            baseline_text["loadedBytesOutsideMutableHeaderSha256"],
            tampered_text["loadedBytesOutsideMutableHeaderSha256"],
        )

    def test_normalized_runtime_identity_rejects_post_command_header_padding_drift(
        self,
    ) -> None:
        packaged_data = _normalized_macho_bytes()
        reference_data = _normalized_macho_bytes(header_padding_byte=1)
        packaged = audit_apple_application._macho_normalized_runtime_identity(
            packaged_data, Path("packaged-shim")
        )
        reference = audit_apple_application._macho_normalized_runtime_identity(
            reference_data, Path("reference-shim")
        )
        self.assertEqual(
            packaged["normalizedIdentity"], reference["normalizedIdentity"]
        )
        with self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            audit_apple_application._validated_ios_shim_runtime_identity(
                packaged_data,
                reference_data,
                packaged,
                reference,
            )

    def test_normalized_runtime_digest_is_independent_of_install_name_length(
        self,
    ) -> None:
        data = _normalized_macho_bytes()
        packaged = audit_apple_application._macho_normalized_runtime_identity(
            data, Path("packaged-shim")
        )
        reference = audit_apple_application._macho_normalized_runtime_identity(
            data, Path("reference-shim")
        )
        first = audit_apple_application._validated_ios_shim_runtime_identity(
            data,
            data,
            packaged,
            reference,
        )
        longer_reference_id = dict(reference)
        longer_reference_id["loadCommandsEnd"] = reference["loadCommandsEnd"] + 16
        second = audit_apple_application._validated_ios_shim_runtime_identity(
            data,
            data,
            packaged,
            longer_reference_id,
        )
        self.assertEqual(
            audit_apple_application._canonical_json_sha256(first),
            audit_apple_application._canonical_json_sha256(second),
        )

    def test_normalized_runtime_identity_rejects_mismatched_uuid_pair(self) -> None:
        packaged_data = _normalized_macho_bytes(load_command_uuid=b"A" * 16)
        reference_data = _normalized_macho_bytes(load_command_uuid=b"B" * 16)
        packaged = audit_apple_application._macho_normalized_runtime_identity(
            packaged_data, Path("packaged-shim")
        )
        reference = audit_apple_application._macho_normalized_runtime_identity(
            reference_data, Path("reference-shim")
        )
        self.assertEqual(
            packaged["normalizedIdentity"], reference["normalizedIdentity"]
        )

        with self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            audit_apple_application._validated_ios_shim_runtime_identity(
                packaged_data,
                reference_data,
                packaged,
                reference,
            )

    def test_normalized_runtime_digest_is_independent_of_matched_uuid(self) -> None:
        digests = []
        for uuid_byte in (b"A", b"B"):
            data = _normalized_macho_bytes(
                load_command_uuid=uuid_byte * 16
            )
            image = audit_apple_application._macho_normalized_runtime_identity(
                data, Path(f"{uuid_byte.decode()}-shim")
            )
            normalized = (
                audit_apple_application._validated_ios_shim_runtime_identity(
                    data,
                    data,
                    image,
                    image,
                )
            )
            self.assertEqual(normalized["loadCommandUuid"], "matched")
            digests.append(
                audit_apple_application._canonical_json_sha256(normalized)
            )

        self.assertEqual(digests[0], digests[1])

    def test_linkedit_boundary_accepts_strip_and_signature_only_packaging(self) -> None:
        cases = (
            (True, False, 2, "absent"),
            (False, True, 0, "present"),
        )
        for local_strip, signed_reference, removed, reference_signature in cases:
            with self.subTest(
                local_strip=local_strip, signed_reference=signed_reference
            ):
                packaged, reference, packaged_size, reference_size = (
                    _synthetic_linkedit_images(
                        local_strip=local_strip,
                        signed_reference=signed_reference,
                    )
                )
                result = (
                    audit_apple_application._validate_ios_shim_linkedit_transformation(
                        packaged,
                        reference,
                        packaged_size,
                        reference_size,
                    )
                )
                self.assertEqual(result["localSymbolsRemoved"], removed)
                self.assertEqual(
                    result["referenceCodeSignature"], reference_signature
                )

    def test_linkedit_boundary_rejects_unaccounted_or_malformed_changes(self) -> None:
        for mutation in ("external-count", "signature-type"):
            with self.subTest(mutation=mutation):
                packaged, reference, packaged_size, reference_size = (
                    _synthetic_linkedit_images(
                        local_strip=True,
                        signed_reference=False,
                    )
                )
                if mutation == "external-count":
                    dynamic = packaged["dysymtab"]
                    assert isinstance(dynamic, dict)
                    dynamic["externalCount"] = 3
                else:
                    signature = packaged["codeSignature"]
                    assert isinstance(signature, dict)
                    signature["size"] = "invalid"
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    audit_apple_application._validate_ios_shim_linkedit_transformation(
                        packaged,
                        reference,
                        packaged_size,
                        reference_size,
                    )

    def test_reference_hook_provenance_accepts_exact_immediate_invocation(self) -> None:
        fixture = _hook_reference_fixture()
        temporary, application, repository, packaged, reference = fixture[:5]
        self.addCleanup(temporary.cleanup)

        result = audit_apple_application._validate_reference_shim_hook_metadata(
            packaged,
            reference,
            application,
            repository,
            "ios-device",
        )

        self.assertEqual(result["invocationId"], "0123456789")
        self.assertEqual(result["status"], "validated")

    def test_reference_hook_provenance_rejects_same_inode_copy_and_app_path(
        self,
    ) -> None:
        for mutation in ("same-inode", "full-copy", "inside-app"):
            with self.subTest(mutation=mutation):
                fixture = _hook_reference_fixture()
                temporary, application, repository, packaged, reference = fixture[:5]
                self.addCleanup(temporary.cleanup)
                if mutation == "same-inode":
                    reference.unlink()
                    os.link(packaged, reference)
                elif mutation == "full-copy":
                    reference.write_bytes(packaged.read_bytes())
                else:
                    application = reference.parents[6]
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    audit_apple_application._validate_reference_shim_hook_metadata(
                        packaged,
                        reference,
                        application,
                        repository,
                        "ios-device",
                    )

    def test_reference_hook_provenance_rejects_noncanonical_path(self) -> None:
        fixture = _hook_reference_fixture()
        temporary, application, repository, packaged, reference = fixture[:5]
        self.addCleanup(temporary.cleanup)
        wrong_name = reference.with_name("fonix_shim.dylib")
        wrong_name.write_bytes(b"different-reference")
        root = reference.parents[6]
        alias = root / "aliased-hook-root"
        alias.symlink_to(root, target_is_directory=True)
        invalid_references = (
            wrong_name,
            reference.parent / ".." / reference.parent.name / reference.name,
            alias / reference.relative_to(root),
        )
        for invalid_reference in invalid_references:
            with self.subTest(reference=invalid_reference), self.assertRaises(
                audit_apple_application.AppleApplicationAuditError
            ):
                audit_apple_application._validate_reference_shim_hook_metadata(
                    packaged,
                    invalid_reference,
                    application,
                    repository,
                    "ios-device",
                )

    def test_reference_hook_provenance_rejects_metadata_contract_drift(self) -> None:
        mutations = (
            ("linking-mode", "input", ("config", "linking_enabled"), False),
            (
                "architecture",
                "input",
                ("config", "extensions", "code_assets", "target_architecture"),
                "x64",
            ),
            (
                "sdk-variant",
                "input",
                ("config", "extensions", "code_assets", "ios", "target_sdk"),
                "iphonesimulator",
            ),
            (
                "dynamic-link-mode",
                "input",
                ("config", "extensions", "code_assets", "link_mode_preference"),
                "static",
            ),
            (
                "runtime-mode",
                "input",
                (
                    "user_defines",
                    "workspace_pubspec",
                    "defines",
                    "runtime_mode",
                ),
                "bundled",
            ),
            (
                "application-minimum-os",
                "input",
                (
                    "user_defines",
                    "workspace_pubspec",
                    "defines",
                    "application_minimum_os",
                ),
                "15.0",
            ),
            ("immediate-output", "input", ("out_file",), "/tmp/output.json"),
            (
                "exact-output-asset",
                "output",
                ("assets", 0, "encoding", "file"),
                "/tmp/libfonix_shim.dylib",
            ),
        )
        for label, document_name, key_path, value in mutations:
            with self.subTest(label=label):
                fixture = _hook_reference_fixture()
                (
                    temporary,
                    application,
                    repository,
                    packaged,
                    reference,
                    input_path,
                    output_path,
                    hook_input,
                    hook_output,
                ) = fixture
                self.addCleanup(temporary.cleanup)
                document = hook_input if document_name == "input" else hook_output
                cursor: object = document
                for key in key_path[:-1]:
                    cursor = cursor[key]  # type: ignore[index]
                cursor[key_path[-1]] = value  # type: ignore[index]
                target = input_path if document_name == "input" else output_path
                target.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(
                    audit_apple_application.AppleApplicationAuditError
                ):
                    audit_apple_application._validate_reference_shim_hook_metadata(
                        packaged,
                        reference,
                        application,
                        repository,
                        "ios-device",
                    )

    def test_strict_signing_verifies_every_binary_framework_and_bundle(self) -> None:
        application = Path("/tmp/Runner.app")
        inventory = {
            "machOBinaries": [
                {"path": path, "codeSignatureLoadCommands": 1}
                for path in (
                    "Runner",
                    "Frameworks/App.framework/App",
                    "Frameworks/Flutter.framework/Flutter",
                    audit_apple_application._IOS_SHIM_PATH,
                )
            ]
        }
        with mock.patch.object(
            audit_apple_application, "_run", return_value=""
        ) as run:
            result = audit_apple_application._audit_strict_ios_signing(
                application, "Runner", inventory, codesign="codesign"
            )
        self.assertEqual(run.call_count, 8)
        self.assertEqual(result["rootBundle"], "verified")

    def test_signature_inventory_fails_closed_on_unreadable_subtree(self) -> None:
        temporary, application = _ios_application()
        self.addCleanup(temporary.cleanup)
        hidden = application / "zz-unreadable"
        hidden.mkdir()
        (hidden / "embedded.mobileprovision").write_bytes(b"hidden profile")
        (hidden / "_CodeSignature").mkdir()
        private_error_path = hidden / "private-signing-state"
        real_walk = audit_apple_application.os.walk

        for name in ("embedded.mobileprovision", "_CodeSignature"):
            with self.subTest(name=name), mock.patch.object(
                audit_apple_application.os,
                "walk",
                new=_walk_failing_at_directory(
                    real_walk, hidden.name, private_error_path
                ),
            ), self.assertRaises(
                audit_apple_application.AppleApplicationAuditError
            ) as caught:
                audit_apple_application._named_application_entries(
                    application, name
                )
            self.assertIn("iOS signature inventory", str(caught.exception))
            self.assertNotIn(str(private_error_path), str(caught.exception))

    def test_rejects_provisioning_and_root_signature_contradictions(self) -> None:
        temporary, application = _ios_application()
        self.addCleanup(temporary.cleanup)
        for framework in audit_apple_application._IOS_FRAMEWORKS:
            (application / framework / "_CodeSignature").mkdir()
        inventory = self._inventory(application)
        profile = application / "embedded.mobileprovision"
        profile.write_bytes(b"forbidden")
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_unsigned_ios_device_signing(
                application, "Runner", inventory, codesign="codesign"
            )
        profile.unlink()
        root = next(
            entry for entry in inventory["machOBinaries"] if entry["path"] == "Runner"
        )
        root["codeSignatureLoadCommands"] = 1
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_unsigned_ios_device_signing(
                application, "Runner", inventory, codesign="codesign"
            )
        root["codeSignatureLoadCommands"] = 0
        missing_signature = application / "Frameworks/App.framework/_CodeSignature"
        missing_signature.rmdir()
        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._audit_unsigned_ios_device_signing(
                application, "Runner", inventory, codesign="codesign"
            )
        missing_signature.mkdir()
        with mock.patch.object(
            audit_apple_application, "_require_unsigned_code_object"
        ) as unsigned, mock.patch.object(
            audit_apple_application, "_require_adhoc_code_object"
        ) as adhoc:
            report = audit_apple_application._audit_unsigned_ios_device_signing(
                application, "Runner", inventory, codesign="codesign"
            )
        self.assertEqual(report["rootBundle"], "unsigned")
        self.assertEqual(len(report["nestedFrameworks"]), 3)
        self.assertEqual(unsigned.call_count, 2)
        self.assertEqual(adhoc.call_count, 6)

    def test_allows_only_teamless_adhoc_nested_signatures(self) -> None:
        path = Path("/tmp/synthetic.framework")
        adhoc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr=(
                "CodeDirectory v=20400 flags=0x2(adhoc) hashes=1+0 location=embedded\n"
                "Signature=adhoc\n"
                "TeamIdentifier=not set\n"
            ),
        )
        developer = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr=(
                "CodeDirectory v=20500 flags=0x10000(runtime) hashes=1+0\n"
                "Signature size=9000\n"
                "Authority=Apple Development: Example\n"
                "TeamIdentifier=ABCDE12345\n"
            ),
        )
        with mock.patch.object(
            audit_apple_application, "_run", return_value=""
        ), mock.patch.object(
            audit_apple_application, "_run_unchecked", return_value=adhoc
        ):
            audit_apple_application._require_adhoc_code_object(
                "codesign", path, "synthetic framework"
            )
        with mock.patch.object(
            audit_apple_application, "_run", return_value=""
        ), mock.patch.object(
            audit_apple_application, "_run_unchecked", return_value=developer
        ), self.assertRaises(audit_apple_application.AppleApplicationAuditError):
            audit_apple_application._require_adhoc_code_object(
                "codesign", path, "synthetic framework"
            )


class AppleApplicationBuildIdentityTest(unittest.TestCase):
    def test_rejects_tampered_probe_identity(self) -> None:
        expected = {
            "schemaVersion": 3,
            "nativeIdentity": "fonix_shim",
            "shimAbiVersion": 1,
            "requiredOrtApiVersion": 27,
            "runtimeProfile": "bundled",
            "androidRuntimeOwner": None,
            "allowedRuntimeSources": ["bundled"],
            "buildId": "artifact",
            "artifact": {
                "id": "artifact",
                "lockSha256": "a" * 64,
                "sourceSha256": "b" * 64,
                "targetOs": "macos",
                "targetArchitecture": "arm64",
                "targetVariant": "default",
                "minimumOs": "14.0",
                "flavor": "cpu",
                "runtimeMode": "bundled",
                "thirdPartyNoticesSha256": "c" * 64,
                "providers": [
                    {
                        "wrapperId": "cpu",
                        "reportedName": "CPUExecutionProvider",
                    }
                ],
            },
        }
        tampered = json.loads(json.dumps(expected))
        tampered["artifact"]["sourceSha256"] = "d" * 64

        with self.assertRaises(
            audit_apple_application.AppleApplicationAuditError
        ):
            audit_apple_application._validate_build_manifest(
                tampered, expected, "probe identity"
            )


if __name__ == "__main__":
    unittest.main()
