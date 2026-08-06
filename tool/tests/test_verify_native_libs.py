from __future__ import annotations

import importlib.util
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
from pathlib import Path
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]
VERIFIER = REPOSITORY / "templates" / "android" / "verify_native_libs.py"

_SPEC = importlib.util.spec_from_file_location("verify_native_libs", VERIFIER)
assert _SPEC is not None and _SPEC.loader is not None
VERIFY_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(VERIFY_MODULE)

ELF_IDENTITIES = {
    "armeabi-v7a": (1, 40),
    "arm64-v8a": (2, 183),
    "x86": (1, 3),
    "x86_64": (2, 62),
}


def synthetic_elf(
    abi: str,
    *,
    alignment: int = 16 * 1024,
    soname: str | None = "libsynthetic.so",
    needed: tuple[str, ...] = (),
    defined_symbols: tuple[str, ...] = (),
    undefined_symbols: tuple[str, ...] = (),
    hash_style: str = "sysv",
    terminate_gnu_hash: bool = True,
    terminate_dynamic: bool = True,
    string_table_address: int | None = None,
) -> bytes:
    if hash_style not in {"sysv", "gnu"}:
        raise ValueError(f"unsupported synthetic ELF hash style: {hash_style}")
    elf_class, machine = ELF_IDENTITIES[abi]
    header_size = 52 if elf_class == 1 else 64
    program_header_size = 32 if elf_class == 1 else 56
    program_header_count = 2
    dynamic_offset = 0x200
    string_offset = 0x400
    base_virtual_address = 0x10000

    string_table = bytearray(b"\0")

    def add_string(value: str) -> int:
        offset = len(string_table)
        string_table.extend(value.encode("utf-8") + b"\0")
        return offset

    soname_offset = None if soname is None else add_string(soname)
    needed_offsets = [add_string(value) for value in needed]
    symbol_specs = [(add_string(value), 1) for value in defined_symbols]
    symbol_specs.extend((add_string(value), 0) for value in undefined_symbols)
    symbol_entry_size = 16 if elf_class == 1 else 24
    symbol_table = bytearray(symbol_entry_size * (1 + len(symbol_specs)))
    for index, (name_offset, section_index) in enumerate(symbol_specs, start=1):
        if elf_class == 1:
            struct.pack_into(
                "<IIIBBH",
                symbol_table,
                index * symbol_entry_size,
                name_offset,
                0,
                0,
                0x12,
                0,
                section_index,
            )
        else:
            struct.pack_into(
                "<IBBHQQ",
                symbol_table,
                index * symbol_entry_size,
                name_offset,
                0x12,
                0,
                section_index,
                0,
                0,
            )

    symbol_offset = (string_offset + len(string_table) + 0xFF) & ~0xFF
    hash_offset = (symbol_offset + len(symbol_table) + 0xFF) & ~0xFF
    hash_table = bytearray()
    if symbol_specs and hash_style == "sysv":
        symbol_count = 1 + len(symbol_specs)
        hash_table.extend(struct.pack("<II", 1, symbol_count))
        hash_table.extend(struct.pack("<I", 1))
        hash_table.extend(bytes(4 * symbol_count))
    elif symbol_specs:
        symbol_count = 1 + len(symbol_specs)
        word_size = 4 if elf_class == 1 else 8
        hash_table.extend(struct.pack("<IIII", 1, 1, 1, 0))
        hash_table.extend(bytes(word_size))
        hash_table.extend(struct.pack("<I", 1))
        for index in range(symbol_count - 1):
            hash_table.extend(
                struct.pack(
                    "<I",
                    1
                    if terminate_gnu_hash and index == symbol_count - 2
                    else 0,
                )
            )

    dynamic_entries = [
        (
            5,
            string_table_address
            if string_table_address is not None
            else base_virtual_address + string_offset,
        ),
        (10, len(string_table)),
    ]
    if symbol_specs:
        dynamic_entries.extend(
            (
                (6, base_virtual_address + symbol_offset),
                (11, symbol_entry_size),
                (
                    4 if hash_style == "sysv" else 0x6FFFFEF5,
                    base_virtual_address + hash_offset,
                ),
            )
        )
    if soname_offset is not None:
        dynamic_entries.append((14, soname_offset))
    dynamic_entries.extend((1, offset) for offset in needed_offsets)
    dynamic_entries.append((0 if terminate_dynamic else 1, 0))
    dynamic_entry_size = 8 if elf_class == 1 else 16
    dynamic_size = len(dynamic_entries) * dynamic_entry_size
    payload_size = string_offset + len(string_table)
    if symbol_specs:
        payload_size = hash_offset + len(hash_table)
    payload = bytearray(payload_size)
    payload[:7] = b"\x7fELF" + bytes((elf_class, 1, 1))

    if elf_class == 1:
        struct.pack_into(
            "<HHIIIIIHHHHHH",
            payload,
            16,
            3,
            machine,
            1,
            0,
            header_size,
            0,
            0,
            header_size,
            program_header_size,
            program_header_count,
            0,
            0,
            0,
        )
        struct.pack_into(
            "<IIIIIIII",
            payload,
            header_size,
            1,
            0,
            base_virtual_address,
            base_virtual_address,
            len(payload),
            len(payload),
            5,
            alignment,
        )
        struct.pack_into(
            "<IIIIIIII",
            payload,
            header_size + program_header_size,
            2,
            dynamic_offset,
            base_virtual_address + dynamic_offset,
            base_virtual_address + dynamic_offset,
            dynamic_size,
            dynamic_size,
            6,
            4,
        )
    else:
        struct.pack_into(
            "<HHIQQQIHHHHHH",
            payload,
            16,
            3,
            machine,
            1,
            0,
            header_size,
            0,
            0,
            header_size,
            program_header_size,
            program_header_count,
            0,
            0,
            0,
        )
        struct.pack_into(
            "<IIQQQQQQ",
            payload,
            header_size,
            1,
            5,
            0,
            base_virtual_address,
            base_virtual_address,
            len(payload),
            len(payload),
            alignment,
        )
        struct.pack_into(
            "<IIQQQQQQ",
            payload,
            header_size + program_header_size,
            2,
            6,
            dynamic_offset,
            base_virtual_address + dynamic_offset,
            base_virtual_address + dynamic_offset,
            dynamic_size,
            dynamic_size,
            8,
        )

    dynamic_format = "<II" if elf_class == 1 else "<QQ"
    for index, (tag, value) in enumerate(dynamic_entries):
        struct.pack_into(
            dynamic_format,
            payload,
            dynamic_offset + index * dynamic_entry_size,
            tag,
            value,
        )
    payload[string_offset : string_offset + len(string_table)] = string_table
    if symbol_specs:
        payload[symbol_offset : symbol_offset + len(symbol_table)] = symbol_table
        payload[hash_offset : hash_offset + len(hash_table)] = hash_table
    return bytes(payload)


ORT_DEPENDENCIES = (
    "libdl.so",
    "liblog.so",
    "libandroid.so",
    "libm.so",
    "libc.so",
)
SHIM_DEPENDENCIES = ("libdl.so", "libc.so")
SHERPA_C_API_DEPENDENCIES = (
    "libandroid.so",
    "liblog.so",
    "libonnxruntime.so",
    "libm.so",
    "libdl.so",
    "libc.so",
)
SHERPA_CXX_API_DEPENDENCIES = (
    "libsherpa-onnx-c-api.so",
    *SHERPA_C_API_DEPENDENCIES,
)


class VerifyNativeLibrariesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def run_verifier(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VERIFIER), *arguments],
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )

    def make_archive(
        self,
        name: str,
        entries: list[tuple[str, bytes]],
    ) -> Path:
        archive_path = self.root / name
        with zipfile.ZipFile(archive_path, "w") as archive:
            for member, contents in entries:
                archive.writestr(member, contents)
        return archive_path

    def final_arguments(self, artifact: Path, abi: str = "arm64-v8a") -> list[str]:
        return [
            "--artifact",
            str(artifact),
            "--require-final-single-ort",
            "--require-16k-page-alignment",
            "--require-abi",
            abi,
            "--quiet",
        ]

    def standalone_entries(
        self,
        abi: str,
        *,
        ort_soname: str = "libonnxruntime.so",
        ort_needed: tuple[str, ...] = ORT_DEPENDENCIES,
        shim_soname: str = "libfonix_shim.so",
        shim_needed: tuple[str, ...] = SHIM_DEPENDENCIES,
    ) -> list[tuple[str, bytes]]:
        return [
            (
                f"lib/{abi}/libonnxruntime.so",
                synthetic_elf(
                    abi,
                    soname=ort_soname,
                    needed=ort_needed,
                ),
            ),
            (
                f"lib/{abi}/libfonix_shim.so",
                synthetic_elf(
                    abi,
                    soname=shim_soname,
                    needed=shim_needed,
                ),
            ),
        ]

    def standalone_arguments(self, artifact: Path, *abis: str) -> list[str]:
        arguments = [
            "--artifact",
            str(artifact),
            "--policy",
            "fonix-standalone-final",
            "--require-16k-page-alignment",
            "--quiet",
        ]
        for abi in abis or ("arm64-v8a",):
            arguments.extend(("--require-abi", abi))
        return arguments

    def sherpa_flutter_ffi_entries(
        self,
        abi: str,
        *,
        c_api_soname: str = "libsherpa-onnx-c-api.so",
        c_api_needed: tuple[str, ...] = SHERPA_C_API_DEPENDENCIES,
        cxx_api_soname: str = "libsherpa-onnx-cxx-api.so",
        cxx_api_needed: tuple[str, ...] = SHERPA_CXX_API_DEPENDENCIES,
    ) -> list[tuple[str, bytes]]:
        return [
            (
                f"jni/{abi}/libsherpa-onnx-c-api.so",
                synthetic_elf(
                    abi,
                    soname=c_api_soname,
                    needed=c_api_needed,
                ),
            ),
            (
                f"jni/{abi}/libsherpa-onnx-cxx-api.so",
                synthetic_elf(
                    abi,
                    soname=cxx_api_soname,
                    needed=cxx_api_needed,
                ),
            ),
        ]

    def test_valid_apk_library_passes(self) -> None:
        artifact = self.make_archive(
            "app.apk",
            [
                ("AndroidManifest.xml", b"manifest"),
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                ),
            ],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reports_dynamic_metadata_for_32_and_64_bit_elves(self) -> None:
        for abi, expected_class in (("armeabi-v7a", 32), ("arm64-v8a", 64)):
            with self.subTest(abi=abi):
                contents = synthetic_elf(
                    abi,
                    soname="libsample.so",
                    needed=("libc.so", "libdl.so"),
                )

                metadata, error = VERIFY_MODULE.inspect_elf(
                    io.BytesIO(contents),
                    len(contents),
                    abi,
                )

                self.assertIsNone(error)
                assert metadata is not None
                self.assertEqual(metadata["class"], expected_class)
                self.assertEqual(metadata["soname"], "libsample.so")
                self.assertEqual(metadata["needed"], ["libc.so", "libdl.so"])
                self.assertTrue(metadata["pageSize16KiBCompatible"])
                self.assertTrue(
                    all(
                        segment["offsetVaddrCongruent"]
                        for segment in metadata["loadSegments"]
                    )
                )
                self.assertTrue(
                    all(
                        len(segment["sha256"]) == 64
                        and segment["flags"] == 5
                        for segment in metadata["loadSegments"]
                    )
                )

    def test_detects_defined_ort_api_with_sysv_and_gnu_hashes(self) -> None:
        for abi, hash_style in (
            ("armeabi-v7a", "sysv"),
            ("arm64-v8a", "gnu"),
        ):
            with self.subTest(abi=abi, hash_style=hash_style):
                contents = synthetic_elf(
                    abi,
                    defined_symbols=("OrtGetApiBase",),
                    hash_style=hash_style,
                )

                metadata, error = VERIFY_MODULE.inspect_elf(
                    io.BytesIO(contents),
                    len(contents),
                    abi,
                )

                self.assertIsNone(error)
                assert metadata is not None
                self.assertTrue(metadata["definesOrtApi"])

    def test_dynamic_symbol_hash_tables_fail_closed_at_bounds(self) -> None:
        unterminated_gnu = synthetic_elf(
            "arm64-v8a",
            defined_symbols=("OrtGetApiBase",),
            hash_style="gnu",
            terminate_gnu_hash=False,
        )
        metadata, error = VERIFY_MODULE.inspect_elf(
            io.BytesIO(unterminated_gnu),
            len(unterminated_gnu),
            "arm64-v8a",
        )
        self.assertIsNone(metadata)
        self.assertIn("chain is unterminated or out of bounds", error)

        oversized_sysv = bytearray(
            synthetic_elf(
                "arm64-v8a",
                defined_symbols=("OrtGetApiBase",),
                hash_style="sysv",
            )
        )
        hash_header = struct.pack("<II", 1, 2)
        hash_offset = oversized_sysv.rfind(hash_header)
        self.assertGreaterEqual(hash_offset, 0)
        struct.pack_into(
            "<I",
            oversized_sysv,
            hash_offset + 4,
            VERIFY_MODULE.MAX_ELF_DYNAMIC_SYMBOLS + 1,
        )
        metadata, error = VERIFY_MODULE.inspect_elf(
            io.BytesIO(oversized_sysv),
            len(oversized_sysv),
            "arm64-v8a",
        )
        self.assertIsNone(metadata)
        self.assertIn("DT_HASH symbol count is outside the bound", error)

    def test_undefined_ort_api_consumer_is_not_a_runtime_candidate(self) -> None:
        artifact = self.make_archive(
            "undefined-consumer.apk",
            [
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                ),
                (
                    "lib/arm64-v8a/libconsumer.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libconsumer.so",
                        undefined_symbols=("OrtGetApiBase",),
                        hash_style="gnu",
                    ),
                ),
            ],
        )

        report = VERIFY_MODULE.inspect_zip(artifact)
        result = self.run_verifier(*self.final_arguments(artifact))

        consumer = next(
            entry for entry in report["libraries"] if entry["name"] == "libconsumer.so"
        )
        self.assertFalse(consumer["elf"]["definesOrtApi"])
        self.assertEqual(
            report["ort_candidates"],
            ["lib/arm64-v8a/libonnxruntime.so"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_final_gate_rejects_renamed_second_ort_runtime(self) -> None:
        artifact = self.make_archive(
            "renamed-second-runtime.apk",
            [
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                ),
                (
                    "lib/arm64-v8a/libsecond_runtime.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libsecond_runtime.so",
                        defined_symbols=("OrtGetApiBase",),
                        hash_style="gnu",
                    ),
                ),
            ],
        )

        report = VERIFY_MODULE.inspect_zip(artifact)
        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(
            report["ort_candidates"],
            [
                "lib/arm64-v8a/libonnxruntime.so",
                "lib/arm64-v8a/libsecond_runtime.so",
            ],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "expected exactly one valid ORT runtime candidate for arm64-v8a, "
            "found 2",
            result.stderr,
        )

    def test_final_gate_rejects_only_renamed_ort_runtime(self) -> None:
        artifact = self.make_archive(
            "renamed-only-runtime.apk",
            [
                (
                    "lib/arm64-v8a/librenamed_runtime.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="librenamed_runtime.so",
                        defined_symbols=("OrtGetApiBase",),
                    ),
                )
            ],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "expected exactly one valid libonnxruntime.so for arm64-v8a, found 0",
            result.stderr,
        )

    def test_multiple_owner_gate_counts_renamed_ort_runtime(self) -> None:
        canonical = self.make_archive(
            "canonical-runtime.aar",
            [
                (
                    "jni/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                )
            ],
        )
        renamed = self.make_archive(
            "renamed-runtime.aar",
            [
                (
                    "jni/arm64-v8a/librenamed_runtime.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="librenamed_runtime.so",
                        defined_symbols=("OrtGetApiBase",),
                    ),
                )
            ],
        )

        result = self.run_verifier(
            "--artifact",
            str(canonical),
            "--artifact",
            str(renamed),
            "--reject-multiple-ort-owners",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "multiple input artifacts own libonnxruntime.so for arm64-v8a",
            result.stderr,
        )

    def test_multiple_owner_gate_rejects_renamed_ort_in_same_artifact(self) -> None:
        artifact = self.make_archive(
            "two-runtimes.aar",
            [
                (
                    "jni/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                ),
                (
                    "jni/arm64-v8a/librenamed-runtime.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="librenamed-runtime.so",
                        defined_symbols=("OrtGetApiBase",),
                    ),
                ),
            ],
        )

        result = self.run_verifier(
            "--artifact",
            str(artifact),
            "--reject-multiple-ort-owners",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "multiple ORT runtime candidates for arm64-v8a",
            result.stderr,
        )
        self.assertIn("libonnxruntime.so", result.stderr)
        self.assertIn("librenamed-runtime.so", result.stderr)

    def test_4k_alignment_fails_and_16k_passes_for_final_64_bit_abis(self) -> None:
        for abi in ("arm64-v8a", "x86_64"):
            with self.subTest(abi=abi, alignment="4KiB"):
                rejected = self.make_archive(
                    f"rejected-{abi}.apk",
                    [
                        (
                            f"lib/{abi}/libonnxruntime.so",
                            synthetic_elf(abi, alignment=4096),
                        )
                    ],
                )
                result = self.run_verifier(*self.final_arguments(rejected, abi))
                self.assertEqual(result.returncode, 1)
                self.assertIn("is not 16 KiB PT_LOAD compatible", result.stderr)

            with self.subTest(abi=abi, alignment="16KiB"):
                accepted = self.make_archive(
                    f"accepted-{abi}.apk",
                    [
                        (
                            f"lib/{abi}/libonnxruntime.so",
                            synthetic_elf(abi, alignment=16 * 1024),
                        )
                    ],
                )
                result = self.run_verifier(*self.final_arguments(accepted, abi))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_final_alignment_gate_checks_every_counted_native_library(self) -> None:
        artifact = self.make_archive(
            "transitive-4k.apk",
            [
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a", alignment=16 * 1024),
                ),
                (
                    "lib/arm64-v8a/libtransitive.so",
                    synthetic_elf("arm64-v8a", alignment=4096),
                ),
            ],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("lib/arm64-v8a/libtransitive.so", result.stderr)
        self.assertIn("is not 16 KiB PT_LOAD compatible", result.stderr)

    def test_incongruent_load_offset_and_virtual_address_fails(self) -> None:
        contents = bytearray(synthetic_elf("arm64-v8a"))
        # ELF64 first program header p_vaddr.
        struct.pack_into("<Q", contents, 64 + 16, 0x10001)
        artifact = self.make_archive(
            "incongruent.apk",
            [("lib/arm64-v8a/libonnxruntime.so", bytes(contents))],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("incongruent file offset and virtual address", result.stderr)

    def test_malformed_dynamic_tables_fail_closed_for_32_and_64_bit(self) -> None:
        for abi in ("armeabi-v7a", "arm64-v8a"):
            with self.subTest(abi=abi, failure="missing DT_NULL"):
                contents = synthetic_elf(abi, terminate_dynamic=False)
                metadata, error = VERIFY_MODULE.inspect_elf(
                    io.BytesIO(contents), len(contents), abi
                )
                self.assertIsNone(metadata)
                self.assertIn("no DT_NULL terminator", error)

            with self.subTest(abi=abi, failure="unmapped DT_STRTAB"):
                contents = synthetic_elf(abi, string_table_address=0xDEADBEEF)
                metadata, error = VERIFY_MODULE.inspect_elf(
                    io.BytesIO(contents), len(contents), abi
                )
                self.assertIsNone(metadata)
                self.assertIn("does not map uniquely", error)

    def test_standalone_policy_accepts_closed_ort_and_shim_contract(self) -> None:
        artifact = self.make_archive(
            "standalone.apk",
            [
                ("AndroidManifest.xml", b"manifest"),
                *self.standalone_entries("arm64-v8a"),
                *self.standalone_entries("x86_64"),
            ],
        )

        result = self.run_verifier(
            *self.standalone_arguments(artifact, "arm64-v8a", "x86_64")
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_policy_accepts_flutter_aot_without_soname(self) -> None:
        artifact = self.make_archive(
            "flutter-release.apk",
            [
                *self.standalone_entries("arm64-v8a"),
                (
                    "lib/arm64-v8a/libapp.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname=None,
                        needed=("libc.so",),
                    ),
                ),
            ],
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_policy_rejects_other_library_without_soname(self) -> None:
        artifact = self.make_archive(
            "missing-plugin-soname.apk",
            [
                *self.standalone_entries("arm64-v8a"),
                (
                    "lib/arm64-v8a/libplugin.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname=None,
                        needed=("libc.so",),
                    ),
                ),
            ],
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("lib/arm64-v8a/libplugin.so", result.stderr)
        self.assertIn("has SONAME None", result.stderr)
        self.assertIn("expected its final basename 'libplugin.so'", result.stderr)

    def test_standalone_policy_rejects_wrong_flutter_aot_soname(self) -> None:
        artifact = self.make_archive(
            "wrong-flutter-soname.apk",
            [
                *self.standalone_entries("arm64-v8a"),
                (
                    "lib/arm64-v8a/libapp.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libwrong.so",
                        needed=("libc.so",),
                    ),
                ),
            ],
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("lib/arm64-v8a/libapp.so", result.stderr)
        self.assertIn("has SONAME 'libwrong.so'", result.stderr)
        self.assertIn("expected its final basename 'libapp.so'", result.stderr)

    def test_standalone_policy_rejects_wrong_soname(self) -> None:
        artifact = self.make_archive(
            "wrong-soname.apk",
            self.standalone_entries(
                "arm64-v8a",
                ort_soname="libwrong.so",
            ),
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("has SONAME 'libwrong.so'", result.stderr)
        self.assertIn("expected its final basename 'libonnxruntime.so'", result.stderr)

    def test_standalone_policy_rejects_missing_and_unexpected_dependencies(
        self,
    ) -> None:
        cases = (
            (
                "missing",
                tuple(name for name in ORT_DEPENDENCIES if name != "liblog.so"),
                "missing required DT_NEEDED entries: liblog.so",
            ),
            (
                "unexpected",
                (*ORT_DEPENDENCIES, "libsurprise.so"),
                "unexpected DT_NEEDED entries: libsurprise.so",
            ),
        )
        for name, dependencies, expected_error in cases:
            with self.subTest(name=name):
                artifact = self.make_archive(
                    f"{name}-dependency.apk",
                    self.standalone_entries(
                        "arm64-v8a",
                        ort_needed=dependencies,
                    ),
                )
                result = self.run_verifier(*self.standalone_arguments(artifact))
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected_error, result.stderr)

    def test_standalone_policy_rejects_unexpected_ort_dependency(self) -> None:
        artifact = self.make_archive(
            "unexpected-ort-user.apk",
            [
                *self.standalone_entries("arm64-v8a"),
                (
                    "lib/arm64-v8a/libplugin.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libplugin.so",
                        needed=("libc.so", "libonnxruntime.so"),
                    ),
                ),
            ],
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "unexpected DT_NEEDED dependency on libonnxruntime.so",
            result.stderr,
        )

    def test_standalone_policy_validates_every_counted_library(self) -> None:
        artifact = self.make_archive(
            "invalid-transitive-metadata.apk",
            [
                *self.standalone_entries("arm64-v8a"),
                (
                    "lib/arm64-v8a/libplugin.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libwrong-plugin.so",
                        needed=("libmissing-plugin-dependency.so",),
                    ),
                ),
            ],
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("expected its final basename 'libplugin.so'", result.stderr)
        self.assertIn(
            "unresolved DT_NEEDED entries: libmissing-plugin-dependency.so",
            result.stderr,
        )

    def test_standalone_policy_rejects_libcxx_ownership_ambiguity(self) -> None:
        artifact = self.make_archive(
            "ambiguous-libcxx.aab",
            [
                ("base/manifest/AndroidManifest.xml", b"manifest"),
                ("feature/manifest/AndroidManifest.xml", b"manifest"),
                *[
                    (path.replace("lib/", "base/lib/", 1), contents)
                    for path, contents in self.standalone_entries("arm64-v8a")
                ],
                (
                    "base/lib/arm64-v8a/libc++_shared.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libc++_shared.so",
                        needed=("libc.so",),
                    ),
                ),
                (
                    "feature/lib/arm64-v8a/libc++_shared.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libc++_shared.so",
                        needed=("libc.so",),
                    ),
                ),
            ],
        )

        result = self.run_verifier(*self.standalone_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("ambiguous libc++_shared.so ownership", result.stderr)

    def test_sherpa_input_audit_rejects_multiple_libcxx_owners(self) -> None:
        artifacts = []
        for owner in ("wrapper", "sherpa"):
            artifacts.append(
                self.make_archive(
                    f"{owner}.aar",
                    [
                        (
                            "jni/arm64-v8a/libc++_shared.so",
                            synthetic_elf(
                                "arm64-v8a",
                                soname="libc++_shared.so",
                                needed=("libc.so",),
                            ),
                        )
                    ],
                )
            )
        result = self.run_verifier(
            "--artifact",
            str(artifacts[0]),
            "--artifact",
            str(artifacts[1]),
            "--policy",
            "sherpa-audit",
            "--reject-multiple-libcxx-owners",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "multiple input artifacts own libc++_shared.so for arm64-v8a",
            result.stderr,
        )

    def test_sherpa_audit_and_generic_inventory_remain_distinct(self) -> None:
        sherpa = self.make_archive(
            "sherpa.aar",
            [
                (
                    "jni/arm64-v8a/libfonix_shim.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libfonix_shim.so",
                        needed=SHIM_DEPENDENCIES,
                    ),
                ),
                (
                    "jni/arm64-v8a/libsherpa-onnx-jni.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libsherpa-onnx-jni.so",
                        needed=("libc.so", "libonnxruntime.so"),
                    ),
                ),
            ],
        )
        sherpa_result = self.run_verifier(
            "--artifact",
            str(sherpa),
            "--policy",
            "sherpa-audit",
            "--sherpa-library-profile",
            "jni",
            "--quiet",
        )
        self.assertEqual(sherpa_result.returncode, 0, sherpa_result.stderr)

        generic = self.make_archive(
            "generic.apk",
            [
                (
                    "lib/arm64-v8a/libcustom.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="intentionally-different.so",
                        needed=("libnotpackaged.so",),
                    ),
                )
            ],
        )
        json_output = self.root / "generic-report.json"
        generic_result = self.run_verifier(
            "--artifact",
            str(generic),
            "--json-out",
            str(json_output),
            "--quiet",
        )
        self.assertEqual(generic_result.returncode, 0, generic_result.stderr)
        payload = json.loads(json_output.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], 4)
        elf = payload["reports"][0]["libraries"][0]["elf"]
        self.assertEqual(elf["soname"], "intentionally-different.so")
        self.assertEqual(elf["needed"], ["libnotpackaged.so"])

    def test_sherpa_flutter_ffi_profile_accepts_real_topology_across_inputs(
        self,
    ) -> None:
        wrapper = self.make_archive(
            "wrapper.aar",
            [
                (
                    "jni/arm64-v8a/libfonix_shim.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libfonix_shim.so",
                        needed=SHIM_DEPENDENCIES,
                    ),
                )
            ],
        )
        runtime_and_c_api = self.make_archive(
            "sherpa-android-arm64.aar",
            [
                (
                    "jni/arm64-v8a/libonnxruntime.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libonnxruntime.so",
                        needed=ORT_DEPENDENCIES,
                    ),
                ),
                self.sherpa_flutter_ffi_entries("arm64-v8a")[0],
            ],
        )
        cxx_api = self.make_archive(
            "sherpa-cxx-api.aar",
            [self.sherpa_flutter_ffi_entries("arm64-v8a")[1]],
        )

        result = self.run_verifier(
            "--artifact",
            str(wrapper),
            "--artifact",
            str(runtime_and_c_api),
            "--artifact",
            str(cxx_api),
            "--policy",
            "sherpa-audit",
            "--sherpa-library-profile",
            "flutter-ffi",
            "--require-abi",
            "arm64-v8a",
            "--reject-multiple-ort-owners",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sherpa_flutter_ffi_validates_required_dependencies(self) -> None:
        cases = (
            (
                "c-api-ort",
                tuple(
                    name
                    for name in SHERPA_C_API_DEPENDENCIES
                    if name != "libonnxruntime.so"
                ),
                SHERPA_CXX_API_DEPENDENCIES,
                "libsherpa-onnx-c-api.so",
                "libonnxruntime.so",
            ),
            (
                "cxx-c-api",
                SHERPA_C_API_DEPENDENCIES,
                tuple(
                    name
                    for name in SHERPA_CXX_API_DEPENDENCIES
                    if name != "libsherpa-onnx-c-api.so"
                ),
                "libsherpa-onnx-cxx-api.so",
                "libsherpa-onnx-c-api.so",
            ),
            (
                "cxx-ort",
                SHERPA_C_API_DEPENDENCIES,
                tuple(
                    name
                    for name in SHERPA_CXX_API_DEPENDENCIES
                    if name != "libonnxruntime.so"
                ),
                "libsherpa-onnx-cxx-api.so",
                "libonnxruntime.so",
            ),
        )
        for name, c_api_needed, cxx_api_needed, library, dependency in cases:
            with self.subTest(name=name):
                artifact = self.make_archive(
                    f"missing-{name}.aar",
                    self.sherpa_flutter_ffi_entries(
                        "arm64-v8a",
                        c_api_needed=c_api_needed,
                        cxx_api_needed=cxx_api_needed,
                    ),
                )

                result = self.run_verifier(
                    "--artifact",
                    str(artifact),
                    "--policy",
                    "sherpa-audit",
                    "--sherpa-library-profile",
                    "flutter-ffi",
                    "--require-abi",
                    "arm64-v8a",
                    "--quiet",
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn(library, result.stderr)
                self.assertIn(
                    f"missing required DT_NEEDED entries: {dependency}",
                    result.stderr,
                )

    def test_sherpa_audit_does_not_silently_ignore_flutter_ffi_sonames(
        self,
    ) -> None:
        cases = (
            (
                "c-api",
                {"c_api_soname": "libwrong.so"},
                "libsherpa-onnx-c-api.so",
            ),
            (
                "cxx-api",
                {"cxx_api_soname": "libwrong.so"},
                "libsherpa-onnx-cxx-api.so",
            ),
        )
        for name, overrides, expected_soname in cases:
            with self.subTest(name=name):
                artifact = self.make_archive(
                    f"wrong-{name}-soname.aar",
                    self.sherpa_flutter_ffi_entries(
                        "arm64-v8a",
                        **overrides,
                    ),
                )

                result = self.run_verifier(
                    "--artifact",
                    str(artifact),
                    "--policy",
                    "sherpa-audit",
                    "--quiet",
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn("has SONAME 'libwrong.so'", result.stderr)
                self.assertIn(f"expected {expected_soname!r}", result.stderr)

    def test_sherpa_flutter_ffi_profile_requires_both_libraries(self) -> None:
        artifact = self.make_archive(
            "c-api-only.aar",
            [self.sherpa_flutter_ffi_entries("arm64-v8a")[0]],
        )

        result = self.run_verifier(
            "--artifact",
            str(artifact),
            "--policy",
            "sherpa-audit",
            "--sherpa-library-profile",
            "flutter-ffi",
            "--require-abi",
            "arm64-v8a",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Flutter FFI profile expects exactly one "
            "libsherpa-onnx-cxx-api.so for arm64-v8a, found 0",
            result.stderr,
        )

    def test_sherpa_profiles_reject_mixed_consumer_topologies(self) -> None:
        artifact = self.make_archive(
            "mixed-sherpa.aar",
            [
                *self.sherpa_flutter_ffi_entries("arm64-v8a"),
                (
                    "jni/arm64-v8a/libsherpa-onnx-jni.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libsherpa-onnx-jni.so",
                        needed=("libonnxruntime.so", "libc.so"),
                    ),
                ),
            ],
        )

        expectations = (
            ("auto", "mixed JNI and Flutter FFI"),
            ("jni", "JNI profile rejects libsherpa-onnx-c-api.so"),
            (
                "flutter-ffi",
                "Flutter FFI profile rejects libsherpa-onnx-jni.so",
            ),
        )
        for profile, expected_error in expectations:
            with self.subTest(profile=profile):
                result = self.run_verifier(
                    "--artifact",
                    str(artifact),
                    "--policy",
                    "sherpa-audit",
                    "--sherpa-library-profile",
                    profile,
                    "--require-abi",
                    "arm64-v8a",
                    "--quiet",
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn(expected_error, result.stderr)

    def test_sherpa_jni_profile_requires_jni_consumer(self) -> None:
        artifact = self.make_archive(
            "wrapper-only.aar",
            [
                (
                    "jni/arm64-v8a/libfonix_shim.so",
                    synthetic_elf(
                        "arm64-v8a",
                        soname="libfonix_shim.so",
                        needed=SHIM_DEPENDENCIES,
                    ),
                )
            ],
        )

        result = self.run_verifier(
            "--artifact",
            str(artifact),
            "--policy",
            "sherpa-audit",
            "--sherpa-library-profile",
            "jni",
            "--require-abi",
            "arm64-v8a",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "JNI profile expects exactly one libsherpa-onnx-jni.so "
            "for arm64-v8a, found 0",
            result.stderr,
        )

    def test_final_gate_requires_explicit_16k_alignment_switch(self) -> None:
        artifact = self.make_archive(
            "missing-alignment-gate.apk",
            [
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                )
            ],
        )

        result = self.run_verifier(
            "--artifact",
            str(artifact),
            "--require-final-single-ort",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "final native-library validation requires --require-16k-page-alignment",
            result.stderr,
        )

    def test_asset_path_cannot_satisfy_final_library_gate(self) -> None:
        artifact = self.make_archive(
            "asset-only.apk",
            [
                ("AndroidManifest.xml", b"manifest"),
                (
                    "assets/lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                ),
            ],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("expected exactly one valid", result.stderr)

    def test_non_elf_canonical_library_fails(self) -> None:
        artifact = self.make_archive(
            "not-elf.apk",
            [("lib/arm64-v8a/libonnxruntime.so", b"definitely not ELF")],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("not an ELF file", result.stderr)

    def test_wrong_elf_machine_fails(self) -> None:
        artifact = self.make_archive(
            "wrong-machine.apk",
            [
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("x86_64"),
                )
            ],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match arm64-v8a", result.stderr)

    def test_big_endian_elf_fails_android_abi_gate(self) -> None:
        contents = bytearray(synthetic_elf("arm64-v8a"))
        contents[5] = 2
        artifact = self.make_archive(
            "big-endian.apk",
            [("lib/arm64-v8a/libonnxruntime.so", bytes(contents))],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("expected little-endian", result.stderr)

    def test_missing_ort_fails_final_gate(self) -> None:
        artifact = self.make_archive(
            "missing.apk",
            [("lib/arm64-v8a/libother.so", synthetic_elf("arm64-v8a"))],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("found 0", result.stderr)

    def test_duplicate_archive_member_fails(self) -> None:
        artifact = self.root / "duplicate.apk"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(artifact, "w") as archive:
                member = "lib/arm64-v8a/libonnxruntime.so"
                archive.writestr(member, synthetic_elf("arm64-v8a"))
                archive.writestr(member, synthetic_elf("arm64-v8a"))

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate archive paths", result.stderr)

    def test_noncanonical_archive_path_fails(self) -> None:
        artifact = self.make_archive(
            "traversal.apk",
            [
                (
                    "../lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                )
            ],
        )

        result = self.run_verifier(*self.final_arguments(artifact))

        self.assertEqual(result.returncode, 1)
        self.assertIn("non-canonical archive paths", result.stderr)

    def test_aab_requires_a_real_module_manifest(self) -> None:
        invalid = self.make_archive(
            "invalid.aab",
            [
                (
                    "fake/lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                )
            ],
        )
        valid = self.make_archive(
            "valid.aab",
            [
                ("base/manifest/AndroidManifest.xml", b"manifest"),
                (
                    "base/lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                ),
            ],
        )

        invalid_result = self.run_verifier(*self.final_arguments(invalid))
        valid_result = self.run_verifier(*self.final_arguments(valid))

        self.assertEqual(invalid_result.returncode, 1)
        self.assertEqual(valid_result.returncode, 0, valid_result.stderr)

    def test_forbidden_owner_detects_ort_hidden_in_assets(self) -> None:
        artifact = self.make_archive(
            "wrapper-external.aar",
            [
                (
                    "assets/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                )
            ],
        )

        result = self.run_verifier(
            "--artifact",
            str(artifact),
            "--forbid-ort-in",
            "wrapper-external",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("ORT is forbidden", result.stderr)

    def test_oversized_entry_is_rejected_before_decompression(self) -> None:
        artifact = self.make_archive(
            "oversized.apk",
            [
                (
                    "lib/arm64-v8a/libonnxruntime.so",
                    synthetic_elf("arm64-v8a"),
                )
            ],
        )
        previous_limit = VERIFY_MODULE.MAX_NATIVE_LIBRARY_BYTES
        self.addCleanup(
            setattr,
            VERIFY_MODULE,
            "MAX_NATIVE_LIBRARY_BYTES",
            previous_limit,
        )
        VERIFY_MODULE.MAX_NATIVE_LIBRARY_BYTES = 100

        report = VERIFY_MODULE.inspect_zip(artifact)

        self.assertEqual(report["libraries"], [])
        self.assertEqual(report["invalid_libraries"][0]["sha256"], "")
        self.assertIn(
            "outside the accepted range",
            report["invalid_libraries"][0]["error"],
        )

    def test_cumulative_native_byte_budget_is_enforced(self) -> None:
        artifact = self.make_archive(
            "cumulative.apk",
            [
                ("lib/arm64-v8a/libfirst.so", synthetic_elf("arm64-v8a")),
                ("lib/arm64-v8a/libsecond.so", synthetic_elf("arm64-v8a")),
            ],
        )
        previous_limit = VERIFY_MODULE.MAX_TOTAL_NATIVE_LIBRARY_BYTES
        self.addCleanup(
            setattr,
            VERIFY_MODULE,
            "MAX_TOTAL_NATIVE_LIBRARY_BYTES",
            previous_limit,
        )
        VERIFY_MODULE.MAX_TOTAL_NATIVE_LIBRARY_BYTES = (
            len(synthetic_elf("arm64-v8a")) + 1
        )

        report = VERIFY_MODULE.inspect_zip(artifact)

        self.assertEqual(len(report["libraries"]), 1)
        self.assertEqual(len(report["invalid_libraries"]), 1)
        self.assertIn(
            "cumulative native-library size budget exceeded",
            report["invalid_libraries"][0]["error"],
        )

    def test_symlinked_directory_is_rejected_without_traversal(self) -> None:
        artifact = self.root / "merged-native-libs"
        artifact.mkdir()
        outside = self.root / "outside"
        (outside / "arm64-v8a").mkdir(parents=True)
        (outside / "arm64-v8a" / "libonnxruntime.so").write_bytes(
            synthetic_elf("arm64-v8a")
        )
        (artifact / "lib").symlink_to(outside, target_is_directory=True)

        result = self.run_verifier(
            "--artifact",
            str(artifact),
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("non-canonical archive paths: lib", result.stderr)

    def test_directory_inspection_rejects_digest_parse_path_swap(self) -> None:
        artifact = self.root / "native-input"
        library_directory = artifact / "jni" / "arm64-v8a"
        library_directory.mkdir(parents=True)
        library = library_directory / "libsample.so"
        original_contents = synthetic_elf(
            "arm64-v8a",
            soname="libsample.so",
        )
        replacement_contents = synthetic_elf(
            "arm64-v8a",
            soname="libsample.so",
            defined_symbols=("OrtGetApiBase",),
        )
        library.write_bytes(original_contents)
        original_path = self.root / "original-libsample.so"
        original_sha256_stream = VERIFY_MODULE.sha256_stream
        swapped = False

        def hash_then_swap(stream: io.BufferedIOBase) -> str:
            nonlocal swapped
            digest = original_sha256_stream(stream)
            if not swapped:
                library.replace(original_path)
                library.write_bytes(replacement_contents)
                swapped = True
            return digest

        try:
            with mock.patch.object(
                VERIFY_MODULE,
                "sha256_stream",
                side_effect=hash_then_swap,
            ):
                report = VERIFY_MODULE.inspect_directory(artifact)
        finally:
            if original_path.exists():
                if library.exists():
                    library.unlink()
                original_path.replace(library)

        self.assertTrue(swapped)
        self.assertEqual(report["libraries"], [])
        self.assertEqual(len(report["invalid_libraries"]), 1)
        self.assertEqual(report["invalid_libraries"][0]["sha256"], "")
        self.assertIn(
            "native library changed while being inspected",
            report["invalid_libraries"][0]["error"],
        )


if __name__ == "__main__":
    unittest.main()
