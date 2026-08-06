#!/usr/bin/env python3
"""Fail-closed native-library inventory for Android archives/directories.

Only loadable native-library locations count. Every counted ``.so`` must be a
structurally valid ELF shared object whose class and machine match its Android
ABI. The script inventories hashes, load-segment alignment, SONAME, and
DT_NEEDED metadata, detects defined ``OrtGetApiBase`` dynamic symbols, and
enforces explicit ownership/final-package policies without extracting
untrusted archives.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import tempfile
from typing import BinaryIO, Iterable
import zipfile


ORT_NAME = "libonnxruntime.so"
SHIM_NAME = "libfonix_shim.so"
SHERPA_JNI_NAME = "libsherpa-onnx-jni.so"
SHERPA_C_API_NAME = "libsherpa-onnx-c-api.so"
SHERPA_CXX_API_NAME = "libsherpa-onnx-cxx-api.so"
LIBCXX_NAME = "libc++_shared.so"
LIBRARY_NAME_RE = re.compile(r"^lib[A-Za-z0-9_.+-]+\.so$")
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ELF_PROGRAM_HEADERS = 1_024
MAX_ELF_HEADER_TABLE_BYTES = 1024 * 1024
MAX_ELF_DYNAMIC_TABLE_BYTES = 1024 * 1024
MAX_ELF_DYNAMIC_ENTRIES = 4_096
MAX_ELF_STRING_TABLE_BYTES = 1024 * 1024
MAX_ELF_DYNAMIC_STRING_BYTES = 4_096
MAX_ELF_DYNAMIC_SYMBOLS = 1_000_000
MAX_ELF_SYMBOL_TABLE_BYTES = 64 * 1024 * 1024
MAX_ELF_HASH_TABLE_BYTES = 16 * 1024 * 1024
MAX_ELF_GNU_HASH_CHAIN_STEPS = 2_000_000
MAX_NATIVE_LIBRARY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_NATIVE_LIBRARY_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200
ANDROID_16K_PAGE_SIZE = 16 * 1024
ANDROID_16K_ABIS = frozenset({"arm64-v8a", "x86_64"})
ANDROID_SYSTEM_LIBRARIES = frozenset(
    {
        "libEGL.so",
        "libGLESv2.so",
        "libGLESv3.so",
        "libOpenSLES.so",
        "libaaudio.so",
        "libandroid.so",
        "libatomic.so",
        "libbinder_ndk.so",
        "libc.so",
        "libcamera2ndk.so",
        "libdl.so",
        "libjnigraphics.so",
        "liblog.so",
        "libm.so",
        "libmediandk.so",
        "libnativewindow.so",
        "libneuralnetworks.so",
        "libsync.so",
        "libvulkan.so",
        "libz.so",
    }
)

POLICY_MODES = ("generic", "sherpa-audit", "fonix-standalone-final")
SHERPA_LIBRARY_PROFILES = ("auto", "jni", "flutter-ffi")
FLUTTER_AOT_NAME = "libapp.so"

# These are deliberately closed expectations for the exact standalone Fonix
# package design. A changed NDK/runtime graph must update the policy and its
# evidence instead of silently widening the dependency set.
STANDALONE_DEPENDENCIES = {
    ORT_NAME: frozenset(
        {"libandroid.so", "libc.so", "libdl.so", "liblog.so", "libm.so"}
    ),
    SHIM_NAME: frozenset({"libc.so", "libdl.so"}),
}

PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_HASH = 4
DT_STRTAB = 5
DT_SYMTAB = 6
DT_STRSZ = 10
DT_SYMENT = 11
DT_SONAME = 14
DT_GNU_HASH = 0x6FFFFEF5
SHN_UNDEF = 0
ORT_API_SYMBOL = b"OrtGetApiBase"

# ABI -> (ELF class, e_machine)
ANDROID_ABIS: dict[str, tuple[int, int]] = {
    "armeabi-v7a": (1, 40),  # ELFCLASS32, EM_ARM
    "arm64-v8a": (2, 183),  # ELFCLASS64, EM_AARCH64
    "x86": (1, 3),  # ELFCLASS32, EM_386
    "x86_64": (2, 62),  # ELFCLASS64, EM_X86_64
}


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _canonical_archive_path(raw_path: str) -> str | None:
    if not raw_path or "\\" in raw_path or raw_path.startswith("/"):
        return None
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _artifact_kind(path: Path) -> str:
    if path.is_dir():
        if (path / "BundleConfig.pb").is_file():
            return "aab"
        if (path / "jni").is_dir():
            return "aar"
        if (path / "lib").is_dir():
            return "apk"
        return "directory"
    suffix = path.suffix.lower()
    if suffix in {".aar", ".apk", ".aab"}:
        return suffix[1:]
    return "zip"


def _aab_modules(paths: Iterable[str]) -> set[str]:
    modules: set[str] = set()
    for value in paths:
        parts = PurePosixPath(value).parts
        if len(parts) == 3 and parts[1:] == ("manifest", "AndroidManifest.xml"):
            modules.add(parts[0])
    return modules


def classify_path(
    path: str,
    kind: str = "zip",
    aab_modules: Iterable[str] = (),
) -> tuple[str, str] | None:
    """Return ``(ABI, name)`` only for a canonical loadable library path."""

    canonical = _canonical_archive_path(path)
    if canonical is None:
        return None
    parts = PurePosixPath(canonical).parts
    modules = set(aab_modules)

    abi: str
    name: str
    if kind == "aar" and len(parts) == 3 and parts[0] == "jni":
        _, abi, name = parts
    elif kind == "apk" and len(parts) == 3 and parts[0] == "lib":
        _, abi, name = parts
    elif (
        kind == "aab"
        and len(parts) == 4
        and parts[0] in modules
        and parts[1] == "lib"
    ):
        _, _, abi, name = parts
    elif kind == "zip" and len(parts) == 3 and parts[0] in {"jni", "lib"}:
        _, abi, name = parts
    elif kind == "directory":
        if len(parts) == 2:
            abi, name = parts
        elif len(parts) == 3 and parts[0] in {"jni", "lib"}:
            _, abi, name = parts
        else:
            return None
    else:
        return None

    if not LIBRARY_NAME_RE.fullmatch(name):
        return None
    return abi, name


def _read_prefix(stream: BinaryIO, byte_count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class _ElfFormatError(ValueError):
    pass


def _read_at(stream: BinaryIO, file_size: int, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0 or offset > file_size or size > file_size - offset:
        raise _ElfFormatError("ELF byte range is out of bounds")
    try:
        stream.seek(offset)
    except (OSError, ValueError) as error:
        raise _ElfFormatError(f"ELF stream is not seekable: {error}") from error
    data = _read_prefix(stream, size)
    if len(data) != size:
        raise _ElfFormatError("ELF byte range is truncated")
    return data


def _is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def _parse_program_headers(
    stream: BinaryIO,
    file_size: int,
    *,
    elf_class: int,
    e_phoff: int,
    e_phentsize: int,
    e_phnum: int,
) -> list[dict]:
    table_size = e_phentsize * e_phnum
    if table_size > MAX_ELF_HEADER_TABLE_BYTES:
        raise _ElfFormatError("ELF program-header table exceeds the size bound")
    table = _read_at(stream, file_size, e_phoff, table_size)
    headers: list[dict] = []
    for index in range(e_phnum):
        offset = index * e_phentsize
        if elf_class == 1:
            (
                p_type,
                p_offset,
                p_vaddr,
                _p_paddr,
                p_filesz,
                p_memsz,
                p_flags,
                p_align,
            ) = struct.unpack_from("<IIIIIIII", table, offset)
        else:
            (
                p_type,
                p_flags,
                p_offset,
                p_vaddr,
                _p_paddr,
                p_filesz,
                p_memsz,
                p_align,
            ) = struct.unpack_from("<IIQQQQQQ", table, offset)
        if p_offset > file_size or p_filesz > file_size - p_offset:
            raise _ElfFormatError(f"ELF program segment {index} is out of bounds")
        if p_filesz > p_memsz:
            raise _ElfFormatError(
                f"ELF program segment {index} has file size greater than memory size"
            )
        headers.append(
            {
                "index": index,
                "type": p_type,
                "flags": p_flags,
                "offset": p_offset,
                "virtualAddress": p_vaddr,
                "fileSize": p_filesz,
                "memorySize": p_memsz,
                "alignment": p_align,
            }
        )
    return headers


def _virtual_address_to_file_offset(
    virtual_address: int,
    byte_count: int,
    load_segments: list[dict],
) -> int:
    candidates: list[int] = []
    for segment in load_segments:
        start = segment["virtualAddress"]
        file_size = segment["fileSize"]
        if virtual_address < start:
            continue
        delta = virtual_address - start
        if delta > file_size or byte_count > file_size - delta:
            continue
        candidates.append(segment["offset"] + delta)
    if len(candidates) != 1:
        raise _ElfFormatError(
            "ELF DT_STRTAB does not map uniquely into one file-backed PT_LOAD segment"
        )
    return candidates[0]


def _virtual_address_file_range(
    virtual_address: int,
    load_segments: list[dict],
) -> tuple[int, int]:
    candidates: list[tuple[int, int]] = []
    for segment in load_segments:
        start = segment["virtualAddress"]
        file_size = segment["fileSize"]
        if virtual_address < start:
            continue
        delta = virtual_address - start
        if delta >= file_size:
            continue
        candidates.append((segment["offset"] + delta, file_size - delta))
    if len(candidates) != 1:
        raise _ElfFormatError(
            "ELF virtual address does not map uniquely into one file-backed "
            "PT_LOAD segment"
        )
    return candidates[0]


def _dynamic_string(string_table: bytes, offset: int, field: str) -> str:
    if offset < 0 or offset >= len(string_table):
        raise _ElfFormatError(f"ELF {field} string offset is out of bounds")
    end = string_table.find(b"\0", offset)
    if end < 0:
        raise _ElfFormatError(f"ELF {field} string is not NUL-terminated")
    if end - offset > MAX_ELF_DYNAMIC_STRING_BYTES:
        raise _ElfFormatError(f"ELF {field} string exceeds the size bound")
    try:
        value = string_table[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise _ElfFormatError(f"ELF {field} string is not valid UTF-8") from error
    if not value or "\0" in value:
        raise _ElfFormatError(f"ELF {field} string is empty or invalid")
    return value


def _parse_sysv_hash_symbol_count(
    stream: BinaryIO,
    file_size: int,
    hash_table_address: int,
    load_segments: list[dict],
) -> int:
    header_offset = _virtual_address_to_file_offset(
        hash_table_address,
        8,
        load_segments,
    )
    bucket_count, symbol_count = struct.unpack(
        "<II",
        _read_at(stream, file_size, header_offset, 8),
    )
    if bucket_count < 1 or bucket_count > MAX_ELF_DYNAMIC_SYMBOLS:
        raise _ElfFormatError("ELF DT_HASH bucket count is outside the bound")
    if symbol_count < 1 or symbol_count > MAX_ELF_DYNAMIC_SYMBOLS:
        raise _ElfFormatError("ELF DT_HASH symbol count is outside the bound")
    table_size = 8 + 4 * (bucket_count + symbol_count)
    if table_size > MAX_ELF_HASH_TABLE_BYTES:
        raise _ElfFormatError("ELF DT_HASH table exceeds the size bound")
    _virtual_address_to_file_offset(
        hash_table_address,
        table_size,
        load_segments,
    )
    return symbol_count


def _parse_gnu_hash_symbol_count(
    stream: BinaryIO,
    file_size: int,
    *,
    elf_class: int,
    hash_table_address: int,
    load_segments: list[dict],
) -> int:
    header_offset = _virtual_address_to_file_offset(
        hash_table_address,
        16,
        load_segments,
    )
    bucket_count, symbol_offset, bloom_size, _bloom_shift = struct.unpack(
        "<IIII",
        _read_at(stream, file_size, header_offset, 16),
    )
    if bucket_count < 1 or bucket_count > MAX_ELF_DYNAMIC_SYMBOLS:
        raise _ElfFormatError("ELF DT_GNU_HASH bucket count is outside the bound")
    if symbol_offset < 1 or symbol_offset > MAX_ELF_DYNAMIC_SYMBOLS:
        raise _ElfFormatError("ELF DT_GNU_HASH symbol offset is outside the bound")
    if bloom_size < 1 or bloom_size > MAX_ELF_DYNAMIC_SYMBOLS:
        raise _ElfFormatError("ELF DT_GNU_HASH bloom size is outside the bound")

    word_size = 4 if elf_class == 1 else 8
    bucket_offset = 16 + bloom_size * word_size
    prefix_size = bucket_offset + bucket_count * 4
    if prefix_size > MAX_ELF_HASH_TABLE_BYTES:
        raise _ElfFormatError("ELF DT_GNU_HASH table prefix exceeds the size bound")
    prefix_file_offset = _virtual_address_to_file_offset(
        hash_table_address,
        prefix_size,
        load_segments,
    )
    prefix = _read_at(stream, file_size, prefix_file_offset, prefix_size)
    buckets = struct.unpack_from(f"<{bucket_count}I", prefix, bucket_offset)
    nonzero_buckets = [value for value in buckets if value != 0]
    if not nonzero_buckets:
        return symbol_offset
    if any(value < symbol_offset for value in nonzero_buckets):
        raise _ElfFormatError(
            "ELF DT_GNU_HASH bucket precedes the hashed symbol offset"
        )

    chain_address = hash_table_address + prefix_size
    chain_file_offset, available_bytes = _virtual_address_file_range(
        chain_address,
        load_segments,
    )
    maximum_words = min(
        available_bytes // 4,
        MAX_ELF_DYNAMIC_SYMBOLS - symbol_offset + 1,
        MAX_ELF_HASH_TABLE_BYTES // 4,
    )
    if maximum_words < 1:
        raise _ElfFormatError("ELF DT_GNU_HASH chain table is truncated")
    chain_table = _read_at(
        stream,
        file_size,
        chain_file_offset,
        maximum_words * 4,
    )

    maximum_symbol = symbol_offset - 1
    traversal_steps = 0
    for bucket in nonzero_buckets:
        chain_index = bucket - symbol_offset
        while True:
            traversal_steps += 1
            if traversal_steps > MAX_ELF_GNU_HASH_CHAIN_STEPS:
                raise _ElfFormatError(
                    "ELF DT_GNU_HASH chain traversal exceeds the bound"
                )
            if chain_index >= maximum_words:
                raise _ElfFormatError(
                    "ELF DT_GNU_HASH chain is unterminated or out of bounds"
                )
            symbol_index = symbol_offset + chain_index
            maximum_symbol = max(maximum_symbol, symbol_index)
            chain_value = struct.unpack_from(
                "<I",
                chain_table,
                chain_index * 4,
            )[0]
            if chain_value & 1:
                break
            chain_index += 1

    symbol_count = maximum_symbol + 1
    if symbol_count > MAX_ELF_DYNAMIC_SYMBOLS:
        raise _ElfFormatError("ELF DT_GNU_HASH symbol count exceeds the bound")
    return symbol_count


def _defines_dynamic_symbol(
    stream: BinaryIO,
    file_size: int,
    *,
    elf_class: int,
    symbol_table_address: int,
    symbol_entry_size: int,
    symbol_count: int,
    string_table: bytes,
    load_segments: list[dict],
    target: bytes,
) -> bool:
    expected_entry_size = 16 if elf_class == 1 else 24
    if symbol_entry_size != expected_entry_size:
        raise _ElfFormatError(
            f"ELF DT_SYMENT is {symbol_entry_size}, expected {expected_entry_size}"
        )
    table_size = symbol_entry_size * symbol_count
    if table_size <= 0 or table_size > MAX_ELF_SYMBOL_TABLE_BYTES:
        raise _ElfFormatError("ELF dynamic symbol table size is outside the bound")
    table_offset = _virtual_address_to_file_offset(
        symbol_table_address,
        table_size,
        load_segments,
    )
    table = _read_at(stream, file_size, table_offset, table_size)

    target_with_terminator = target + b"\0"
    defines_target = False
    for index in range(symbol_count):
        offset = index * symbol_entry_size
        if elf_class == 1:
            name_offset = struct.unpack_from("<I", table, offset)[0]
            section_index = struct.unpack_from("<H", table, offset + 14)[0]
        else:
            name_offset = struct.unpack_from("<I", table, offset)[0]
            section_index = struct.unpack_from("<H", table, offset + 6)[0]
        if name_offset >= len(string_table):
            raise _ElfFormatError(
                f"ELF dynamic symbol {index} name offset is out of bounds"
            )
        name_end = string_table.find(
            b"\0",
            name_offset,
            min(len(string_table), name_offset + MAX_ELF_DYNAMIC_STRING_BYTES + 1),
        )
        if name_end < 0:
            raise _ElfFormatError(
                f"ELF dynamic symbol {index} name is not NUL-terminated within "
                "the size bound"
            )
        if (
            string_table.startswith(target_with_terminator, name_offset)
            and section_index != SHN_UNDEF
        ):
            defines_target = True
    return defines_target


def _parse_dynamic_metadata(
    stream: BinaryIO,
    file_size: int,
    *,
    elf_class: int,
    program_headers: list[dict],
) -> tuple[str | None, list[str], bool]:
    dynamic_segments = [
        segment for segment in program_headers if segment["type"] == PT_DYNAMIC
    ]
    if not dynamic_segments:
        return None, [], False
    if len(dynamic_segments) != 1:
        raise _ElfFormatError("ELF must not contain multiple PT_DYNAMIC segments")
    dynamic = dynamic_segments[0]
    dynamic_size = dynamic["fileSize"]
    entry_size = 8 if elf_class == 1 else 16
    if (
        dynamic_size <= 0
        or dynamic_size > MAX_ELF_DYNAMIC_TABLE_BYTES
        or dynamic_size % entry_size != 0
        or dynamic_size // entry_size > MAX_ELF_DYNAMIC_ENTRIES
    ):
        raise _ElfFormatError("ELF PT_DYNAMIC table size is outside the accepted range")
    load_segments = [
        segment for segment in program_headers if segment["type"] == PT_LOAD
    ]
    mapped_dynamic_offset = _virtual_address_to_file_offset(
        dynamic["virtualAddress"],
        dynamic_size,
        load_segments,
    )
    if mapped_dynamic_offset != dynamic["offset"]:
        raise _ElfFormatError(
            "ELF PT_DYNAMIC offset does not match its file-backed virtual address"
        )
    table = _read_at(stream, file_size, dynamic["offset"], dynamic_size)

    string_table_address: int | None = None
    string_table_size: int | None = None
    symbol_table_address: int | None = None
    symbol_entry_size: int | None = None
    sysv_hash_address: int | None = None
    gnu_hash_address: int | None = None
    soname_offset: int | None = None
    needed_offsets: list[int] = []
    found_null = False
    for offset in range(0, len(table), entry_size):
        if elf_class == 1:
            tag, value = struct.unpack_from("<II", table, offset)
        else:
            tag, value = struct.unpack_from("<QQ", table, offset)
        if tag == DT_NULL:
            found_null = True
            break
        if tag == DT_STRTAB:
            if string_table_address is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_STRTAB")
            string_table_address = value
        elif tag == DT_STRSZ:
            if string_table_size is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_STRSZ")
            string_table_size = value
        elif tag == DT_SYMTAB:
            if symbol_table_address is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_SYMTAB")
            symbol_table_address = value
        elif tag == DT_SYMENT:
            if symbol_entry_size is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_SYMENT")
            symbol_entry_size = value
        elif tag == DT_HASH:
            if sysv_hash_address is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_HASH")
            sysv_hash_address = value
        elif tag == DT_GNU_HASH:
            if gnu_hash_address is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_GNU_HASH")
            gnu_hash_address = value
        elif tag == DT_SONAME:
            if soname_offset is not None:
                raise _ElfFormatError("ELF PT_DYNAMIC duplicates DT_SONAME")
            soname_offset = value
        elif tag == DT_NEEDED:
            needed_offsets.append(value)
    if not found_null:
        raise _ElfFormatError("ELF PT_DYNAMIC table has no DT_NULL terminator")

    symbol_metadata = (
        symbol_table_address,
        symbol_entry_size,
        sysv_hash_address,
        gnu_hash_address,
    )
    has_symbol_metadata = any(value is not None for value in symbol_metadata)
    if has_symbol_metadata and (
        symbol_table_address is None
        or symbol_entry_size is None
        or (sysv_hash_address is None and gnu_hash_address is None)
    ):
        raise _ElfFormatError(
            "ELF dynamic symbols require DT_SYMTAB, DT_SYMENT, and a "
            "DT_HASH or DT_GNU_HASH table"
        )

    symbol_counts: list[int] = []
    if sysv_hash_address is not None:
        symbol_counts.append(
            _parse_sysv_hash_symbol_count(
                stream,
                file_size,
                sysv_hash_address,
                load_segments,
            )
        )
    if gnu_hash_address is not None:
        symbol_counts.append(
            _parse_gnu_hash_symbol_count(
                stream,
                file_size,
                elf_class=elf_class,
                hash_table_address=gnu_hash_address,
                load_segments=load_segments,
            )
        )
    if len(set(symbol_counts)) > 1:
        raise _ElfFormatError(
            "ELF DT_HASH and DT_GNU_HASH disagree on the dynamic symbol count"
        )
    symbol_count = symbol_counts[0] if symbol_counts else 0

    has_string_references = (
        soname_offset is not None or bool(needed_offsets) or symbol_count > 0
    )
    if not has_string_references:
        return None, [], False
    if string_table_address is None or string_table_size is None:
        raise _ElfFormatError(
            "ELF dynamic strings require both DT_STRTAB and DT_STRSZ"
        )
    if string_table_size <= 0 or string_table_size > MAX_ELF_STRING_TABLE_BYTES:
        raise _ElfFormatError("ELF dynamic string table size is outside the bound")

    string_table_offset = _virtual_address_to_file_offset(
        string_table_address,
        string_table_size,
        load_segments,
    )
    string_table = _read_at(
        stream,
        file_size,
        string_table_offset,
        string_table_size,
    )
    soname = (
        None
        if soname_offset is None
        else _dynamic_string(string_table, soname_offset, "DT_SONAME")
    )
    needed = [
        _dynamic_string(string_table, offset, "DT_NEEDED")
        for offset in needed_offsets
    ]
    if len(needed) != len(set(needed)):
        raise _ElfFormatError("ELF PT_DYNAMIC duplicates a DT_NEEDED entry")
    defines_ort_api = False
    if symbol_count:
        assert symbol_table_address is not None
        assert symbol_entry_size is not None
        defines_ort_api = _defines_dynamic_symbol(
            stream,
            file_size,
            elf_class=elf_class,
            symbol_table_address=symbol_table_address,
            symbol_entry_size=symbol_entry_size,
            symbol_count=symbol_count,
            string_table=string_table,
            load_segments=load_segments,
            target=ORT_API_SYMBOL,
        )
    return soname, needed, defines_ort_api


def inspect_elf(
    stream: BinaryIO,
    file_size: int,
    abi: str,
) -> tuple[dict | None, str | None]:
    """Return bounded ELF metadata plus an error for an Android shared object."""

    expected = ANDROID_ABIS.get(abi)
    if expected is None:
        return None, f"unsupported Android ABI {abi!r}"
    if file_size <= 0 or file_size > MAX_NATIVE_LIBRARY_BYTES:
        return None, f"native library size {file_size} is outside the accepted range"

    try:
        first = _read_at(stream, file_size, 0, min(file_size, 64))
    except _ElfFormatError as error:
        return None, str(error)
    if len(first) < 20 or first[:4] != b"\x7fELF":
        return None, "not an ELF file"

    elf_class = first[4]
    data_encoding = first[5]
    ident_version = first[6]
    if elf_class not in {1, 2}:
        return None, f"unsupported ELF class {elf_class}"
    if data_encoding != 1:
        return None, (
            f"ELF data encoding is {data_encoding}, expected little-endian "
            f"for Android ABI {abi}"
        )
    if ident_version != 1:
        return None, f"unsupported ELF identification version {ident_version}"

    endian = "<"
    if len(first) < (52 if elf_class == 1 else 64):
        return None, "truncated ELF header"
    e_type, e_machine, e_version = struct.unpack_from(f"{endian}HHI", first, 16)
    if e_type != 3:  # ET_DYN
        return None, f"ELF type is {e_type}, expected ET_DYN"
    if e_version != 1:
        return None, f"unsupported ELF version {e_version}"

    expected_class, expected_machine = expected
    if elf_class != expected_class or e_machine != expected_machine:
        return None, (
            f"ELF class/machine ({elf_class}, {e_machine}) does not match "
            f"{abi} ({expected_class}, {expected_machine})"
        )

    if elf_class == 1:
        e_phoff = struct.unpack_from(f"{endian}I", first, 28)[0]
        e_ehsize, e_phentsize, e_phnum = struct.unpack_from(
            f"{endian}HHH", first, 40
        )
        expected_ehsize = 52
        minimum_phentsize = 32
    else:
        e_phoff = struct.unpack_from(f"{endian}Q", first, 32)[0]
        e_ehsize, e_phentsize, e_phnum = struct.unpack_from(
            f"{endian}HHH", first, 52
        )
        expected_ehsize = 64
        minimum_phentsize = 56

    if e_ehsize != expected_ehsize:
        return None, f"ELF header size is {e_ehsize}, expected {expected_ehsize}"
    if e_phnum < 1 or e_phnum > MAX_ELF_PROGRAM_HEADERS:
        return None, (
            f"ELF program-header count {e_phnum} is outside the accepted range"
        )
    if e_phentsize < minimum_phentsize:
        return None, f"ELF program-header size {e_phentsize} is too small"

    table_size = e_phentsize * e_phnum
    table_end = e_phoff + table_size
    if table_end > file_size or table_end > MAX_ELF_HEADER_TABLE_BYTES:
        return None, "ELF program-header table is out of bounds"

    try:
        program_headers = _parse_program_headers(
            stream,
            file_size,
            elf_class=elf_class,
            e_phoff=e_phoff,
            e_phentsize=e_phentsize,
            e_phnum=e_phnum,
        )
        load_segments = [
            segment for segment in program_headers if segment["type"] == PT_LOAD
        ]
        if not load_segments:
            raise _ElfFormatError("ELF has no PT_LOAD segment")
        for segment in load_segments:
            alignment = segment["alignment"]
            if alignment not in {0, 1} and not _is_power_of_two(alignment):
                raise _ElfFormatError(
                    f"ELF PT_LOAD segment {segment['index']} has non-power-of-two "
                    f"alignment {alignment}"
                )
            if alignment > 1 and (
                segment["offset"] - segment["virtualAddress"]
            ) % alignment != 0:
                raise _ElfFormatError(
                    f"ELF PT_LOAD segment {segment['index']} has incongruent "
                    "file offset and virtual address"
                )
        soname, needed, defines_ort_api = _parse_dynamic_metadata(
            stream,
            file_size,
            elf_class=elf_class,
            program_headers=program_headers,
        )
    except (_ElfFormatError, struct.error) as error:
        return None, str(error)

    load_report = [
        {
            "index": segment["index"],
            "flags": segment["flags"],
            "offset": segment["offset"],
            "virtualAddress": segment["virtualAddress"],
            "fileSize": segment["fileSize"],
            "memorySize": segment["memorySize"],
            "alignment": segment["alignment"],
            "sha256": hashlib.sha256(
                _read_at(
                    stream,
                    file_size,
                    segment["offset"],
                    segment["fileSize"],
                )
            ).hexdigest(),
            "offsetVaddrCongruent": (
                segment["alignment"] in {0, 1}
                or (segment["offset"] - segment["virtualAddress"])
                % segment["alignment"]
                == 0
            ),
        }
        for segment in load_segments
    ]
    compatible_16k = all(
        segment["alignment"] >= ANDROID_16K_PAGE_SIZE
        and (segment["offset"] - segment["virtualAddress"])
        % ANDROID_16K_PAGE_SIZE
        == 0
        for segment in load_segments
    )
    return (
        {
            "class": 32 if elf_class == 1 else 64,
            "machine": e_machine,
            "programHeaderCount": e_phnum,
            "loadSegments": load_report,
            "pageSize16KiBCompatible": compatible_16k,
            "soname": soname,
            "needed": needed,
            "definesOrtApi": defines_ort_api,
        },
        None,
    )


def validate_elf(stream: BinaryIO, file_size: int, abi: str) -> str | None:
    """Compatibility wrapper returning only a structural ELF error."""

    _metadata, error = inspect_elf(stream, file_size, abi)
    return error


def _library_entry(
    *,
    path: str,
    abi: str,
    name: str,
    size: int,
    digest: str,
) -> dict:
    return {
        "path": path,
        "abi": abi,
        "name": name,
        "size": size,
        "sha256": digest,
    }


def inspect_zip(path: Path) -> dict:
    entries: list[dict] = []
    invalid_libraries: list[dict] = []
    duplicate_paths: list[str] = []
    invalid_archive_paths: list[str] = []
    ort_candidates: list[str] = []
    kind = _artifact_kind(path)

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(
                f"{path}: archive has {len(infos)} entries; maximum is "
                f"{MAX_ARCHIVE_ENTRIES}"
            )

        canonical_names: list[str] = []
        seen: set[str] = set()
        total_native_bytes = 0
        for info in infos:
            canonical = _canonical_archive_path(info.filename.rstrip("/"))
            if canonical is None:
                invalid_archive_paths.append(info.filename)
                continue
            canonical_names.append(canonical)
            if canonical in seen:
                duplicate_paths.append(canonical)
            seen.add(canonical)
            if PurePosixPath(canonical).name == ORT_NAME:
                ort_candidates.append(canonical)

        modules = _aab_modules(canonical_names) if kind == "aab" else set()
        for info in infos:
            if info.is_dir():
                continue
            canonical = _canonical_archive_path(info.filename)
            if canonical is None:
                continue
            classified = classify_path(canonical, kind, modules)
            if classified is None:
                continue
            abi, name = classified
            entry = _library_entry(
                path=canonical,
                abi=abi,
                name=name,
                size=info.file_size,
                digest="",
            )
            if info.flag_bits & 0x1:
                invalid_libraries.append(
                    {**entry, "error": "encrypted native library is not allowed"}
                )
                continue
            if info.file_size <= 0 or info.file_size > MAX_NATIVE_LIBRARY_BYTES:
                invalid_libraries.append(
                    {
                        **entry,
                        "error": (
                            f"native library size {info.file_size} is outside "
                            "the accepted range"
                        ),
                    }
                )
                continue
            if (
                info.compress_size <= 0
                or info.file_size > info.compress_size * MAX_ZIP_COMPRESSION_RATIO
            ):
                invalid_libraries.append(
                    {**entry, "error": "suspicious native-library compression ratio"}
                )
                continue
            if info.file_size > MAX_TOTAL_NATIVE_LIBRARY_BYTES - total_native_bytes:
                invalid_libraries.append(
                    {
                        **entry,
                        "error": "cumulative native-library size budget exceeded",
                    }
                )
                continue
            total_native_bytes += info.file_size
            try:
                with archive.open(info, "r") as stream:
                    digest = sha256_stream(stream)
                with archive.open(info, "r") as stream:
                    elf_metadata, elf_error = inspect_elf(
                        stream,
                        info.file_size,
                        abi,
                    )
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                elf_error = f"could not read archive entry: {error}"
                elf_metadata = None
                digest = ""

            entry["sha256"] = digest
            if elf_error is None:
                entry["elf"] = elf_metadata
                entries.append(entry)
                if elf_metadata["definesOrtApi"]:
                    ort_candidates.append(canonical)
            else:
                invalid_libraries.append({**entry, "error": elf_error})

    sort_key = lambda item: (item["abi"], item["name"], item["path"])
    return {
        "artifact": str(path),
        "kind": kind,
        "duplicate_paths": sorted(set(duplicate_paths)),
        "invalid_archive_paths": sorted(set(invalid_archive_paths)),
        "ort_candidates": sorted(set(ort_candidates)),
        "invalid_libraries": sorted(invalid_libraries, key=sort_key),
        "libraries": sorted(entries, key=sort_key),
    }


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_directory_elf(
    file_path: Path,
    abi: str,
    expected_metadata: os.stat_result,
) -> tuple[str, dict | None, str | None]:
    expected_identity = _stable_file_identity(expected_metadata)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(file_path, flags)
    try:
        source = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        with source, tempfile.TemporaryFile(mode="w+b") as snapshot:
            opened_metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(opened_metadata.st_mode):
                raise _ElfFormatError("native library must be a regular file")
            if _stable_file_identity(opened_metadata) != expected_identity:
                raise _ElfFormatError(
                    "native library changed while being opened for inspection"
                )

            copied_bytes = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                copied_bytes += len(chunk)
                if copied_bytes > expected_metadata.st_size:
                    raise _ElfFormatError(
                        "native library changed while being snapshotted"
                    )
                snapshot.write(chunk)
            if copied_bytes != expected_metadata.st_size:
                raise _ElfFormatError(
                    "native library changed while being snapshotted"
                )

            after_copy_metadata = os.fstat(source.fileno())
            after_copy_path_metadata = os.lstat(file_path)
            if (
                _stable_file_identity(after_copy_metadata) != expected_identity
                or _stable_file_identity(after_copy_path_metadata)
                != expected_identity
            ):
                raise _ElfFormatError(
                    "native library changed while being snapshotted"
                )

            snapshot.seek(0)
            digest = sha256_stream(snapshot)
            snapshot.seek(0)
            elf_metadata, elf_error = inspect_elf(
                snapshot,
                expected_metadata.st_size,
                abi,
            )

            after_inspection_metadata = os.fstat(source.fileno())
            after_inspection_path_metadata = os.lstat(file_path)
            if (
                _stable_file_identity(after_inspection_metadata) != expected_identity
                or _stable_file_identity(after_inspection_path_metadata)
                != expected_identity
            ):
                raise _ElfFormatError(
                    "native library changed while being inspected"
                )
            return digest, elf_metadata, elf_error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def inspect_directory(path: Path) -> dict:
    entries: list[dict] = []
    invalid_libraries: list[dict] = []
    invalid_archive_paths: list[str] = []
    ort_candidates: list[str] = []
    kind = _artifact_kind(path)
    all_files: list[Path] = []
    for current_root, directory_names, file_names in os.walk(path, followlinks=False):
        current_path = Path(current_root)
        for directory_name in list(directory_names):
            directory_path = current_path / directory_name
            if directory_path.is_symlink():
                invalid_archive_paths.append(
                    directory_path.relative_to(path).as_posix()
                )
                directory_names.remove(directory_name)
        all_files.extend(current_path / file_name for file_name in file_names)
    all_files.sort()
    canonical_names = [candidate.relative_to(path).as_posix() for candidate in all_files]
    modules = _aab_modules(canonical_names) if kind == "aab" else set()

    total_native_bytes = 0
    for file_path in all_files:
        relative = file_path.relative_to(path).as_posix()
        if file_path.name == ORT_NAME:
            ort_candidates.append(relative)
        classified = classify_path(relative, kind, modules)
        if classified is None:
            continue
        abi, name = classified
        try:
            path_metadata = os.lstat(file_path)
        except OSError as error:
            invalid_libraries.append(
                {
                    **_library_entry(
                        path=relative,
                        abi=abi,
                        name=name,
                        size=0,
                        digest="",
                    ),
                    "error": f"could not stat native library: {error}",
                }
            )
            continue
        if stat.S_ISLNK(path_metadata.st_mode):
            invalid_libraries.append(
                {
                    **_library_entry(
                        path=relative,
                        abi=abi,
                        name=name,
                        size=0,
                        digest="",
                    ),
                    "error": "native library must not be a symbolic link",
                }
            )
            continue

        size = path_metadata.st_size
        entry = _library_entry(
            path=relative,
            abi=abi,
            name=name,
            size=size,
            digest="",
        )
        if size <= 0 or size > MAX_NATIVE_LIBRARY_BYTES:
            invalid_libraries.append(
                {
                    **entry,
                    "error": (
                        f"native library size {size} is outside the accepted range"
                    ),
                }
            )
            continue
        if size > MAX_TOTAL_NATIVE_LIBRARY_BYTES - total_native_bytes:
            invalid_libraries.append(
                {**entry, "error": "cumulative native-library size budget exceeded"}
            )
            continue
        total_native_bytes += size
        try:
            digest, elf_metadata, elf_error = _snapshot_directory_elf(
                file_path,
                abi,
                path_metadata,
            )
        except (OSError, _ElfFormatError) as error:
            invalid_libraries.append(
                {
                    **entry,
                    "error": f"could not securely inspect native library: {error}",
                }
            )
            continue
        entry["sha256"] = digest
        if elf_error is None:
            entry["elf"] = elf_metadata
            entries.append(entry)
            if elf_metadata["definesOrtApi"]:
                ort_candidates.append(relative)
        else:
            invalid_libraries.append({**entry, "error": elf_error})

    sort_key = lambda item: (item["abi"], item["name"], item["path"])
    return {
        "artifact": str(path),
        "kind": kind,
        "duplicate_paths": [],
        "invalid_archive_paths": sorted(set(invalid_archive_paths)),
        "ort_candidates": sorted(set(ort_candidates)),
        "invalid_libraries": sorted(invalid_libraries, key=sort_key),
        "libraries": sorted(entries, key=sort_key),
    }


def inspect(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_dir():
        return inspect_directory(path)
    if zipfile.is_zipfile(path):
        return inspect_zip(path)
    raise ValueError(
        f"Unsupported artifact (expected directory or ZIP/AAR/APK/AAB): {path}"
    )


def ort_entries(report: dict) -> list[dict]:
    return [
        entry
        for entry in report["libraries"]
        if entry["name"] == ORT_NAME or entry["elf"]["definesOrtApi"]
    ]


def owner_matches(artifact: str, patterns: Iterable[str]) -> bool:
    absolute = os.path.abspath(artifact)
    basename = os.path.basename(artifact)
    return any(pattern in absolute or pattern in basename for pattern in patterns)


def _entries_named(report: dict, name: str, abi: str | None = None) -> list[dict]:
    return [
        entry
        for entry in report["libraries"]
        if entry["name"] == name and (abi is None or entry["abi"] == abi)
    ]


def _needed(entry: dict) -> list[str]:
    return entry["elf"]["needed"]


def _validate_16k_alignment(report: dict) -> list[str]:
    errors: list[str] = []
    for entry in report["libraries"]:
        if entry["abi"] not in ANDROID_16K_ABIS:
            continue
        if not entry["elf"]["pageSize16KiBCompatible"]:
            segments = ", ".join(
                (
                    f"#{segment['index']} align={segment['alignment']} "
                    f"offset={segment['offset']} "
                    f"vaddr={segment['virtualAddress']}"
                )
                for segment in entry["elf"]["loadSegments"]
            )
            errors.append(
                f"{report['artifact']}: {entry['path']} is not 16 KiB "
                f"PT_LOAD compatible ({segments})"
            )
    return errors


def _validate_closed_dependency_set(
    report: dict,
    entry: dict,
    expected: frozenset[str],
) -> list[str]:
    errors: list[str] = []
    actual = set(_needed(entry))
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        errors.append(
            f"{report['artifact']}: {entry['path']} is missing required "
            f"DT_NEEDED entries: {', '.join(missing)}"
        )
    if unexpected:
        errors.append(
            f"{report['artifact']}: {entry['path']} has unexpected DT_NEEDED "
            f"entries: {', '.join(unexpected)}"
        )
    return errors


def _validate_standalone_fonix(report: dict, required_abis: list[str]) -> list[str]:
    errors: list[str] = []
    if report["kind"] not in {"apk", "aab"}:
        errors.append(
            f"{report['artifact']}: fonix-standalone-final policy requires a "
            "final APK or AAB, not an intermediate/native directory"
        )

    present_abis = {entry["abi"] for entry in report["libraries"]}
    policy_abis = sorted(present_abis | set(required_abis))
    for abi in policy_abis:
        abi_entries = [
            entry for entry in report["libraries"] if entry["abi"] == abi
        ]
        packaged_names = {entry["name"] for entry in abi_entries}
        for entry in abi_entries:
            soname = entry["elf"]["soname"]
            # Flutter's final AOT image is filename-addressed and does not carry
            # DT_SONAME. Keep this exception exact and local to the standalone
            # final-package policy; every other library remains basename-bound.
            flutter_aot_without_soname = (
                entry["name"] == FLUTTER_AOT_NAME and soname is None
            )
            if soname != entry["name"] and not flutter_aot_without_soname:
                errors.append(
                    f"{report['artifact']}: {entry['path']} has SONAME "
                    f"{soname!r}; expected its final basename {entry['name']!r}"
                )
            unresolved = sorted(
                dependency
                for dependency in _needed(entry)
                if dependency not in ANDROID_SYSTEM_LIBRARIES
                and dependency not in packaged_names
            )
            if unresolved:
                errors.append(
                    f"{report['artifact']}: {entry['path']} has unresolved "
                    f"DT_NEEDED entries: {', '.join(unresolved)}"
                )

        for name, expected_dependencies in STANDALONE_DEPENDENCIES.items():
            entries = _entries_named(report, name, abi)
            if len(entries) != 1:
                errors.append(
                    f"{report['artifact']}: standalone Fonix expects exactly "
                    f"one {name} for {abi}, found {len(entries)}"
                )
                continue
            entry = entries[0]
            errors.extend(
                _validate_closed_dependency_set(
                    report,
                    entry,
                    expected_dependencies,
                )
            )

        for entry in report["libraries"]:
            if entry["abi"] != abi or entry["name"] in STANDALONE_DEPENDENCIES:
                continue
            if ORT_NAME in _needed(entry):
                errors.append(
                    f"{report['artifact']}: {entry['path']} has unexpected "
                    f"DT_NEEDED dependency on {ORT_NAME} in standalone mode"
                )

        libcxx_owners = _entries_named(report, LIBCXX_NAME, abi)
        libcxx_users = [
            entry
            for entry in report["libraries"]
            if entry["abi"] == abi
            and entry["name"] != LIBCXX_NAME
            and LIBCXX_NAME in _needed(entry)
        ]
        if len(libcxx_owners) > 1:
            errors.append(
                f"{report['artifact']}: ambiguous {LIBCXX_NAME} ownership for "
                f"{abi}; found {len(libcxx_owners)} final-package paths"
            )
        elif libcxx_users and len(libcxx_owners) != 1:
            errors.append(
                f"{report['artifact']}: {abi} libraries require {LIBCXX_NAME} "
                "but the final package does not contain exactly one owner"
            )
        elif libcxx_owners and not libcxx_users:
            errors.append(
                f"{report['artifact']}: {LIBCXX_NAME} is packaged for {abi} "
                "without any recorded DT_NEEDED consumer; ownership is ambiguous"
            )
    return errors


def _validate_sherpa_consumer(
    report: dict,
    entry: dict,
    *,
    required_dependencies: frozenset[str],
) -> list[str]:
    errors: list[str] = []
    expected_name = entry["name"]
    if entry["elf"]["soname"] != expected_name:
        errors.append(
            f"{report['artifact']}: {entry['path']} has SONAME "
            f"{entry['elf']['soname']!r}; expected {expected_name!r}"
        )
    missing = sorted(required_dependencies - set(_needed(entry)))
    if missing:
        errors.append(
            f"{report['artifact']}: sherpa shared-runtime library "
            f"{entry['path']} is missing required DT_NEEDED entries: "
            + ", ".join(missing)
        )
    return errors


def _validate_sherpa_audit(report: dict) -> list[str]:
    errors: list[str] = []
    for entry in _entries_named(report, SHIM_NAME):
        if entry["elf"]["soname"] != SHIM_NAME:
            errors.append(
                f"{report['artifact']}: {entry['path']} has SONAME "
                f"{entry['elf']['soname']!r}; expected {SHIM_NAME!r}"
            )
        if ORT_NAME in _needed(entry):
            errors.append(
                f"{report['artifact']}: sherpa-audit external shim "
                f"{entry['path']} must not DT_NEEDED {ORT_NAME}"
            )
    sherpa_dependencies = {
        SHERPA_JNI_NAME: frozenset({ORT_NAME}),
        SHERPA_C_API_NAME: frozenset({ORT_NAME}),
        SHERPA_CXX_API_NAME: frozenset({SHERPA_C_API_NAME, ORT_NAME}),
    }
    for name, required_dependencies in sherpa_dependencies.items():
        for entry in _entries_named(report, name):
            errors.extend(
                _validate_sherpa_consumer(
                    report,
                    entry,
                    required_dependencies=required_dependencies,
                )
            )
    return errors


def _validate_sherpa_library_profile(
    reports: list[dict],
    required_abis: list[str],
    selected_profile: str,
) -> list[str]:
    consumer_names = frozenset(
        {SHERPA_JNI_NAME, SHERPA_C_API_NAME, SHERPA_CXX_API_NAME}
    )
    entries_by_abi: dict[str, dict[str, list[tuple[str, dict]]]] = {}
    all_library_abis: set[str] = set()
    for report in reports:
        for entry in report["libraries"]:
            abi = entry["abi"]
            all_library_abis.add(abi)
            if entry["name"] not in consumer_names:
                continue
            entries_by_abi.setdefault(abi, {}).setdefault(entry["name"], []).append(
                (report["artifact"], entry)
            )

    if selected_profile == "auto" and not entries_by_abi:
        # Preserve inventory-only sherpa audits of wrapper/provider inputs.
        # Once any sherpa consumer is observed, auto still infers and enforces
        # one complete, unmixed topology for every selected ABI.
        return []

    if required_abis:
        profile_abis = sorted(set(required_abis))
    elif selected_profile == "auto":
        profile_abis = sorted(entries_by_abi)
    else:
        # An explicit profile is a closed audit even if no sherpa library was
        # observed. Infer the ABI set from the other named inputs so a missing
        # consumer cannot silently pass.
        profile_abis = sorted(all_library_abis)

    if not profile_abis:
        return [
            "sherpa-audit found no ABI on which to validate a sherpa consumer "
            f"for library profile {selected_profile!r}"
        ]

    errors: list[str] = []
    for abi in profile_abis:
        by_name = entries_by_abi.get(abi, {})
        jni_count = len(by_name.get(SHERPA_JNI_NAME, []))
        c_api_count = len(by_name.get(SHERPA_C_API_NAME, []))
        cxx_api_count = len(by_name.get(SHERPA_CXX_API_NAME, []))

        effective_profile = selected_profile
        if selected_profile == "auto":
            has_jni = jni_count > 0
            has_flutter_ffi = c_api_count > 0 or cxx_api_count > 0
            if has_jni and has_flutter_ffi:
                errors.append(
                    f"sherpa-audit found mixed JNI and Flutter FFI sherpa "
                    f"library profiles for {abi}"
                )
                continue
            if has_jni:
                effective_profile = "jni"
            elif has_flutter_ffi:
                effective_profile = "flutter-ffi"
            else:
                errors.append(
                    f"sherpa-audit could not determine a sherpa library "
                    f"profile for required ABI {abi}"
                )
                continue

        if effective_profile == "jni":
            if jni_count != 1:
                errors.append(
                    f"sherpa-audit JNI profile expects exactly one "
                    f"{SHERPA_JNI_NAME} for {abi}, found {jni_count}"
                )
            for forbidden_name, count in (
                (SHERPA_C_API_NAME, c_api_count),
                (SHERPA_CXX_API_NAME, cxx_api_count),
            ):
                if count:
                    errors.append(
                        f"sherpa-audit JNI profile rejects {forbidden_name} "
                        f"for {abi}; found {count}"
                    )
        elif effective_profile == "flutter-ffi":
            for required_name, count in (
                (SHERPA_C_API_NAME, c_api_count),
                (SHERPA_CXX_API_NAME, cxx_api_count),
            ):
                if count != 1:
                    errors.append(
                        f"sherpa-audit Flutter FFI profile expects exactly one "
                        f"{required_name} for {abi}, found {count}"
                    )
            if jni_count:
                errors.append(
                    f"sherpa-audit Flutter FFI profile rejects "
                    f"{SHERPA_JNI_NAME} for {abi}; found {jni_count}"
                )
        else:  # pragma: no cover - argparse constrains the CLI value.
            raise ValueError(f"unknown sherpa library profile: {effective_profile}")
    return errors


def _multiple_owner_errors(
    reports: list[dict],
    library_name: str,
) -> list[str]:
    owners_by_abi: dict[str, list[tuple[str, dict]]] = {}
    for report in reports:
        entries = (
            ort_entries(report)
            if library_name == ORT_NAME
            else _entries_named(report, library_name)
        )
        for entry in entries:
            owners_by_abi.setdefault(entry["abi"], []).append(
                (report["artifact"], entry)
            )
    errors: list[str] = []
    for abi, owners in sorted(owners_by_abi.items()):
        if library_name == ORT_NAME:
            entries_by_artifact: dict[str, list[dict]] = {}
            for artifact, entry in owners:
                entries_by_artifact.setdefault(artifact, []).append(entry)
            for artifact, entries in sorted(entries_by_artifact.items()):
                if len(entries) > 1:
                    errors.append(
                        f"{artifact}: multiple ORT runtime candidates for {abi}: "
                        + ", ".join(sorted(entry["path"] for entry in entries))
                    )
        distinct_artifacts = sorted({artifact for artifact, _ in owners})
        if len(distinct_artifacts) > 1:
            errors.append(
                f"multiple input artifacts own {library_name} for {abi}: "
                + ", ".join(distinct_artifacts)
            )
    return errors


def validate(reports: list[dict], args: argparse.Namespace) -> list[str]:
    errors: list[str] = []

    final_policy = args.policy == "fonix-standalone-final"
    sherpa_library_profile = getattr(args, "sherpa_library_profile", "auto")
    if args.policy != "sherpa-audit" and sherpa_library_profile != "auto":
        errors.append(
            "--sherpa-library-profile is only valid with "
            "--policy sherpa-audit"
        )
    final_gate = args.require_final_single_ort or final_policy
    if final_gate and not args.require_16k_page_alignment:
        errors.append(
            "final native-library validation requires "
            "--require-16k-page-alignment"
        )

    for report in reports:
        if report["duplicate_paths"]:
            errors.append(
                f"{report['artifact']}: duplicate archive paths: "
                + ", ".join(report["duplicate_paths"])
            )
        if report["invalid_archive_paths"]:
            errors.append(
                f"{report['artifact']}: non-canonical archive paths: "
                + ", ".join(report["invalid_archive_paths"])
            )
        for entry in report["invalid_libraries"]:
            errors.append(
                f"{report['artifact']}: invalid native library {entry['path']}: "
                f"{entry['error']}"
            )

        if owner_matches(report["artifact"], args.forbid_ort_in):
            found = report["ort_candidates"]
            if found:
                errors.append(
                    f"{report['artifact']}: ORT is forbidden but found at "
                    + ", ".join(found)
                )

        if final_gate:
            by_abi: dict[str, list[dict]] = {}
            for entry in ort_entries(report):
                by_abi.setdefault(entry["abi"], []).append(entry)
            canonical_by_abi: dict[str, list[dict]] = {}
            for entry in _entries_named(report, ORT_NAME):
                canonical_by_abi.setdefault(entry["abi"], []).append(entry)
            present_abis = {entry["abi"] for entry in report["libraries"]}
            required_abis = sorted(
                set(args.require_abi) | present_abis
            )
            if not required_abis:
                errors.append(
                    f"{report['artifact']}: no valid native ABIs were found; "
                    f"cannot verify final {ORT_NAME} ownership"
                )
            for abi in required_abis:
                count = len(by_abi.get(abi, []))
                if count != 1:
                    errors.append(
                        f"{report['artifact']}: expected exactly one valid ORT "
                        f"runtime candidate for {abi}, found {count}"
                    )
                canonical_count = len(canonical_by_abi.get(abi, []))
                if canonical_count != 1:
                    errors.append(
                        f"{report['artifact']}: expected exactly one valid "
                        f"{ORT_NAME} for {abi}, found {canonical_count}"
                    )

        if args.require_16k_page_alignment:
            errors.extend(_validate_16k_alignment(report))

        if final_policy:
            errors.extend(_validate_standalone_fonix(report, args.require_abi))
        elif args.policy == "sherpa-audit":
            errors.extend(_validate_sherpa_audit(report))

    if args.policy == "sherpa-audit":
        errors.extend(
            _validate_sherpa_library_profile(
                reports,
                args.require_abi,
                sherpa_library_profile,
            )
        )

    if args.reject_multiple_ort_owners:
        errors.extend(_multiple_owner_errors(reports, ORT_NAME))

    if args.reject_multiple_libcxx_owners:
        errors.extend(_multiple_owner_errors(reports, LIBCXX_NAME))

    if args.require_abi:
        for report in reports:
            present = {entry["abi"] for entry in report["libraries"]}
            missing = sorted(set(args.require_abi) - present)
            if missing:
                errors.append(
                    f"{report['artifact']}: required ABIs with no valid native "
                    "libraries: " + ", ".join(missing)
                )

    return errors


def print_human(reports: list[dict]) -> None:
    for report in reports:
        print(f"\nArtifact: {report['artifact']} ({report['kind']})")
        if report["duplicate_paths"]:
            print("  Duplicate paths:")
            for path_value in report["duplicate_paths"]:
                print(f"    - {path_value}")
        if report["invalid_archive_paths"]:
            print("  Non-canonical archive paths:")
            for path_value in report["invalid_archive_paths"]:
                print(f"    - {path_value}")
        if report["invalid_libraries"]:
            print("  Invalid native libraries:")
            for entry in report["invalid_libraries"]:
                print(f"    - {entry['path']}: {entry['error']}")
        if not report["libraries"]:
            print("  No valid native libraries found.")
            continue
        for entry in report["libraries"]:
            owner = (
                " [ORT]"
                if entry["name"] == ORT_NAME or entry["elf"]["definesOrtApi"]
                else ""
            )
            elf = entry["elf"]
            alignments = ",".join(
                str(segment["alignment"]) for segment in elf["loadSegments"]
            )
            needed = ",".join(elf["needed"]) or "-"
            print(
                f"  {entry['abi']:12} {entry['name']:36} "
                f"{entry['size']:10} bytes  {entry['sha256']}{owner}"
            )
            print(
                f"    SONAME={elf['soname'] or '-'} "
                f"DT_NEEDED={needed} PT_LOAD_ALIGN={alignments} "
                f"16KiB={elf['pageSize16KiBCompatible']}"
            )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        help="AAR/APK/AAB/ZIP file or extracted directory; repeatable.",
    )
    parser.add_argument(
        "--policy",
        choices=POLICY_MODES,
        default="generic",
        help=(
            "Validation policy: generic inventory, sherpa coexistence audit, "
            "or the closed standalone Fonix final-package contract."
        ),
    )
    parser.add_argument(
        "--sherpa-library-profile",
        choices=SHERPA_LIBRARY_PROFILES,
        default="auto",
        help=(
            "Expected sherpa native consumer topology for sherpa-audit: infer "
            "JNI versus Flutter FFI per ABI, or require one exact profile."
        ),
    )
    parser.add_argument(
        "--require-final-single-ort",
        action="store_true",
        help=(
            "For each artifact, require exactly one valid libonnxruntime.so "
            "per required/present ABI."
        ),
    )
    parser.add_argument(
        "--reject-multiple-ort-owners",
        action="store_true",
        help="Across input artifacts, reject more than one ORT-owning artifact per ABI.",
    )
    parser.add_argument(
        "--reject-multiple-libcxx-owners",
        action="store_true",
        help=(
            "Across input artifacts, reject more than one artifact containing "
            "libc++_shared.so for an ABI."
        ),
    )
    parser.add_argument(
        "--forbid-ort-in",
        action="append",
        default=[],
        metavar="PATH_SUBSTRING",
        help=(
            "Reject every libonnxruntime.so candidate in matching artifacts, "
            "including non-canonical paths; repeatable."
        ),
    )
    parser.add_argument(
        "--require-abi",
        action="append",
        default=[],
        choices=sorted(ANDROID_ABIS),
        help="Require this ABI to contain a valid native library; repeatable.",
    )
    parser.add_argument(
        "--require-16k-page-alignment",
        action="store_true",
        help=(
            "Require every arm64-v8a/x86_64 native library to have 16 KiB "
            "compatible, offset/vaddr-congruent PT_LOAD segments. Mandatory "
            "for final-package validation."
        ),
    )
    parser.add_argument("--json-out", help="Write the full report as JSON.")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        reports = [inspect(Path(value).resolve()) for value in args.artifact]
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    errors = validate(reports, args)
    payload = {
        "schema": 4,
        "policy": args.policy,
        "sherpaLibraryProfile": args.sherpa_library_profile,
        "require16KiBPageAlignment": args.require_16k_page_alignment,
        "reports": reports,
        "errors": errors,
    }

    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    if not args.quiet:
        print_human(reports)

    if errors:
        print("Validation errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    elif not args.quiet:
        print("\nValidation passed.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
