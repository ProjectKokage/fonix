from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


CI_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY = CI_DIRECTORY.parents[1]
sys.path.insert(0, str(CI_DIRECTORY))

import verify_native_c_abi_baseline as abi  # noqa: E402


_FIXTURE_PATHS = (
    abi.BASELINE_PATH,
    abi.HEADER_PATH,
    abi.ELF_EXPORT_PATH,
    abi.APPLE_EXPORT_PATH,
    abi.WINDOWS_EXPORT_PATH,
    abi.ALLOWLIST_PATH,
    abi.NATIVE_CMAKE_PATH,
)


class _RepositoryFixture:
    def __init__(self, owner: unittest.TestCase) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fonix-native-abi-")
        owner.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in _FIXTURE_PATHS:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, destination)

    def replace(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        count = source.count(old)
        if count != 1:
            raise AssertionError(
                f"expected one fixture occurrence in {relative}, found {count}: {old!r}"
            )
        path.write_text(source.replace(old, new), encoding="utf-8")


class NativeCAbiBaselineTest(unittest.TestCase):
    def test_committed_baseline_covers_the_complete_current_contract(self) -> None:
        baseline = abi.verify_repository(REPOSITORY)
        contract = baseline["contract"]

        self.assertEqual(contract["abiVersion"], 1)
        self.assertEqual(contract["ortApiCompatibilityFloor"], 27)
        self.assertEqual(contract["elfSymbolVersion"], "FONIX_DORT_1.0")
        self.assertEqual(contract["windowsLibrary"], "fonix_shim")
        self.assertRegex(contract["canonicalHeaderSha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            contract["pythonAllowlistSourceSha256"], r"^[0-9a-f]{64}$"
        )
        self.assertRegex(contract["nativeCTestCMakeSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            contract["pythonAuditInvocations"],
            [
                {
                    "id": "exportAllowlist",
                    "script": "test/native/check_exports.py",
                    "pythonMode": "isolated",
                },
                {
                    "id": "externalDependency",
                    "script": "test/native/check_no_ort_dependency.py",
                    "pythonMode": "isolated",
                },
            ],
        )
        self.assertIn("# include < stddef . h >", contract["preprocessorDirectives"])
        self.assertIn("# include < stdint . h >", contract["preprocessorDirectives"])
        self.assertFalse(
            any(
                directive.startswith("# pragma")
                for directive in contract["preprocessorDirectives"]
            )
        )
        self.assertEqual(len(contract["abiMacroDefinitions"]), 6)
        self.assertEqual(
            contract["cLinkageDirectives"],
            [
                {
                    "position": "beforePublicDeclarations",
                    "directives": [
                        "# ifdef __cplusplus",
                        'extern "C" {',
                        "# endif",
                    ],
                },
                {
                    "position": "afterPublicDeclarations",
                    "directives": ["# ifdef __cplusplus", "}", "# endif"],
                },
            ],
        )
        self.assertEqual(len(contract["abiConstants"]), 9)
        self.assertEqual(len(contract["opaqueTypes"]), 9)
        self.assertEqual(len(contract["enums"]), 8)
        self.assertEqual(
            sum(len(entry["values"]) for entry in contract["enums"]), 70
        )
        self.assertEqual(len(contract["structs"]), 8)
        self.assertEqual(
            sum(len(entry["fields"]) for entry in contract["structs"]), 61
        )
        self.assertEqual(len(contract["functions"]), 67)
        self.assertEqual(contract["exports"], [
            function["name"] for function in contract["functions"]
        ])

        fields_by_struct = {
            entry["alias"]: [field["name"] for field in entry["fields"]]
            for entry in contract["structs"]
        }
        self.assertEqual(
            fields_by_struct,
            {
                "dort_string_t": [
                    "struct_size",
                    "data",
                    "length",
                    "private_owner",
                ],
                "dort_runtime_config_t": [
                    "struct_size",
                    "shim_abi_version",
                    "required_ort_api_version",
                    "source_kind",
                    "flags",
                    "log_severity",
                    "reserved0",
                    "log_id_utf8",
                    "library_path_utf8",
                    "preferred_library_names_utf8",
                    "preferred_library_name_count",
                    "allowed_root_utf8",
                ],
                "dort_string_pair_t": [
                    "struct_size",
                    "reserved0",
                    "key_utf8",
                    "value_utf8",
                ],
                "dort_provider_config_t": [
                    "struct_size",
                    "reserved0",
                    "provider_id_utf8",
                    "options",
                    "option_count",
                ],
                "dort_session_config_t": [
                    "struct_size",
                    "graph_optimization_level",
                    "execution_mode",
                    "intra_op_thread_count",
                    "inter_op_thread_count",
                    "enable_cpu_memory_arena",
                    "enable_memory_pattern",
                    "deterministic_compute",
                    "enable_profiling",
                    "log_severity",
                    "log_verbosity",
                    "optimized_model_overwrite",
                    "reserved0",
                    "log_id_utf8",
                    "profile_path_prefix_utf8",
                    "optimized_model_path_utf8",
                    "artifact_root_utf8",
                    "max_model_bytes",
                    "providers",
                    "provider_count",
                    "config_entries",
                    "config_entry_count",
                ],
                "dort_named_value_t": [
                    "struct_size",
                    "reserved0",
                    "name_utf8",
                    "value",
                ],
                "dort_utf8_span_t": [
                    "struct_size",
                    "reserved0",
                    "data",
                    "length",
                ],
                "dort_external_data_t": [
                    "struct_size",
                    "reserved0",
                    "relative_name_utf8",
                    "relative_name_length",
                    "data",
                    "data_length",
                ],
            },
        )

        session_run = next(
            function
            for function in contract["functions"]
            if function["name"] == "dort_session_run"
        )
        self.assertEqual(session_run["returnType"], "dort_status_t *")
        self.assertEqual(session_run["callingConvention"], "DORT_CALL")
        self.assertEqual(
            session_run["parameters"],
            [
                "dort_session_t * session",
                "dort_run_options_t * run_options",
                "const dort_named_value_t * inputs",
                "size_t input_count",
                "const char * const * output_names_utf8",
                "size_t output_count",
                "dort_run_result_t * * out_result",
            ],
        )

    def test_comments_and_declaration_whitespace_are_not_abi_changes(self) -> None:
        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.HEADER_PATH,
            "/* ABI and build information. */",
            "/* Wording changes are deliberately outside the structural ABI. */",
        )
        fixture.replace(
            abi.HEADER_PATH,
            "DORT_API uint32_t DORT_CALL dort_get_abi_version(void);",
            "DORT_API   uint32_t DORT_CALL\n    dort_get_abi_version( void );",
        )

        self.assertEqual(
            abi.build_baseline(fixture.root), abi.build_baseline(REPOSITORY)
        )

    def test_each_header_boundary_changes_the_canonical_baseline(self) -> None:
        mutations = (
            (
                "platform macro",
                "#define DORT_CALL __cdecl",
                "#define DORT_CALL __stdcall",
            ),
            ("ABI constant", "#define DORT_ABI_VERSION 1u", "#define DORT_ABI_VERSION 2u"),
            (
                "opaque type",
                "typedef struct dort_data_lease dort_data_lease_t;",
                "typedef struct dort_data_lease_v2 dort_data_lease_t;",
            ),
            (
                "enum value",
                "DORT_TENSOR_BFLOAT16 = 16",
                "DORT_TENSOR_BFLOAT16 = 17",
            ),
            (
                "ordered struct fields",
                "  uint32_t struct_size;\n  const uint8_t* data;",
                "  const uint8_t* data;\n  uint32_t struct_size;",
            ),
            (
                "function return type",
                "DORT_API size_t DORT_CALL dort_run_result_count(",
                "DORT_API uint64_t DORT_CALL dort_run_result_count(",
            ),
            (
                "function parameter type",
                "    size_t index,\n    dort_string_t* out_name,",
                "    uint64_t index,\n    dort_string_t* out_name,",
            ),
        )
        for label, old, new in mutations:
            with self.subTest(label=label):
                fixture = _RepositoryFixture(self)
                fixture.replace(abi.HEADER_PATH, old, new)
                with self.assertRaisesRegex(
                    abi.NativeAbiBaselineError, "differs from the committed baseline"
                ):
                    abi.verify_repository(fixture.root)

    def test_cplusplus_linkage_opening_and_closing_tamper_fails_closed(self) -> None:
        mutations = (
            ('extern "C" {', 'extern "C++" {'),
            (
                '#ifdef __cplusplus\n} /* extern "C" */\n#endif\n\n'
                "#endif /* FONIX_DORT_H_ */",
                "#endif /* FONIX_DORT_H_ */",
            ),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                fixture = _RepositoryFixture(self)
                fixture.replace(abi.HEADER_PATH, old, new)
                with self.assertRaisesRegex(
                    abi.NativeAbiBaselineError, "C\\+\\+ linkage guards are malformed"
                ):
                    abi.build_contract(fixture.root)

    def test_all_preprocessor_and_unparsed_header_tokens_are_frozen(self) -> None:
        mutations = (
            (
                "packing pragma",
                "typedef struct dort_string {",
                "#pragma pack(push, 1)\ntypedef struct dort_string {",
            ),
            (
                "inactive function",
                "DORT_API uint32_t DORT_CALL dort_get_abi_version(void);",
                "#if 0\n"
                "DORT_API uint32_t DORT_CALL dort_get_abi_version(void);\n"
                "#endif",
            ),
            (
                "function macro alias",
                "DORT_API uint32_t DORT_CALL dort_get_abi_version(void);",
                "#define dort_get_abi_version dort_unreviewed_alias\n"
                "DORT_API uint32_t DORT_CALL dort_get_abi_version(void);",
            ),
            ("included ABI types", "#include <stdint.h>", "#include <inttypes.h>"),
            (
                "non-directive pragma operator",
                "typedef struct dort_string {",
                '_Pragma("pack(push, 1)")\ntypedef struct dort_string {',
            ),
        )
        for label, old, new in mutations:
            with self.subTest(label=label):
                fixture = _RepositoryFixture(self)
                fixture.replace(abi.HEADER_PATH, old, new)
                with self.assertRaisesRegex(
                    abi.NativeAbiBaselineError, "differs from the committed baseline"
                ):
                    abi.verify_repository(fixture.root)

    def test_c_line_splicing_cannot_hide_a_public_declaration(self) -> None:
        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.HEADER_PATH,
            "/* ABI and build information. */",
            "// ABI and build information. \\",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "exactly 67 functions|unexported or unsupported",
        ):
            abi.build_contract(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.HEADER_PATH,
            "/* ABI and build information. */",
            "// ABI and build information. ??/",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "may not contain C trigraphs",
        ):
            abi.build_contract(fixture.root)

    def test_implicit_enum_values_and_unknown_typedefs_fail_closed(self) -> None:
        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.HEADER_PATH,
            "  DORT_LOG_INFO = 1,",
            "  DORT_LOG_INFO,",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "must give every value an explicit expression"
        ):
            abi.build_contract(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.HEADER_PATH,
            "typedef struct dort_status dort_status_t;",
            "typedef uint32_t dort_extra_t;\n"
            "typedef struct dort_status dort_status_t;",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "unsupported typedef form"
        ):
            abi.build_contract(fixture.root)

    def test_all_platform_export_inventories_are_required_and_ordered(self) -> None:
        mutations = (
            (
                "ELF",
                abi.ELF_EXPORT_PATH,
                "    dort_run_result_get;\n",
                "",
            ),
            (
                "Apple",
                abi.APPLE_EXPORT_PATH,
                "_dort_run_result_get\n",
                "",
            ),
            (
                "Windows",
                abi.WINDOWS_EXPORT_PATH,
                "  dort_run_result_get\n",
                "",
            ),
            (
                "Python",
                abi.ALLOWLIST_PATH,
                '    "dort_run_result_get",\n',
                "",
            ),
        )
        for label, relative, old, new in mutations:
            with self.subTest(label=label):
                fixture = _RepositoryFixture(self)
                fixture.replace(relative, old, new)
                with self.assertRaisesRegex(
                    abi.NativeAbiBaselineError,
                    f"{label}.*differs|differs.*{label}",
                ):
                    abi.build_contract(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.APPLE_EXPORT_PATH,
            "_dort_status_domain\n_dort_status_code\n",
            "_dort_status_code\n_dort_status_domain\n",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "Apple export list differs"
        ):
            abi.build_contract(fixture.root)

    def test_python_export_allowlist_rejects_mutation_and_rebinding(self) -> None:
        mutations = (
            'ALLOWED.add("dort_unreviewed_export")',
            'ALLOWED.update({"dort_unreviewed_export"})',
            'ALLOWED |= {"dort_unreviewed_export"}',
            'if True:\n    ALLOWED.add("dort_unreviewed_export")',
            'ALLOWED = {"dort_unreviewed_export"}',
            "alias = ALLOWED",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = _RepositoryFixture(self)
                fixture.replace(
                    abi.ALLOWLIST_PATH,
                    "\n\ndef exported_symbols",
                    f"\n\n{mutation}\n\ndef exported_symbols",
                )
                with self.assertRaisesRegex(
                    abi.NativeAbiBaselineError, "Python ALLOWED export set"
                ):
                    abi.build_contract(fixture.root)

    def test_python_export_allowlist_indirect_mutation_changes_the_baseline(self) -> None:
        mutations = (
            'globals()["ALLOWED"].add("dort_unreviewed_export")',
            'exec(\'ALLOWED.add("dort_unreviewed_export")\')',
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = _RepositoryFixture(self)
                fixture.replace(
                    abi.ALLOWLIST_PATH,
                    "\n\ndef exported_symbols",
                    f"\n\n{mutation}\n\ndef exported_symbols",
                )
                with self.assertRaisesRegex(
                    abi.NativeAbiBaselineError, "differs from the committed baseline"
                ):
                    abi.verify_repository(fixture.root)

    def test_native_python_audits_require_isolated_mode(self) -> None:
        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.NATIVE_CMAKE_PATH,
            '    -I\n    "${CMAKE_CURRENT_LIST_DIR}/check_exports.py"',
            '    "${CMAKE_CURRENT_LIST_DIR}/check_exports.py"',
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "check_exports.py native audit must use one isolated Python invocation",
        ):
            abi.build_contract(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.NATIVE_CMAKE_PATH,
            '    -I\n    "${CMAKE_CURRENT_LIST_DIR}/check_no_ort_dependency.py"',
            '    "${CMAKE_CURRENT_LIST_DIR}/check_no_ort_dependency.py"',
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "check_no_ort_dependency.py native audit must use one isolated Python invocation",
        ):
            abi.build_contract(fixture.root)

    def test_native_python_audits_cannot_be_disabled_without_drift(self) -> None:
        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.NATIVE_CMAKE_PATH,
            "add_test(\n  NAME exported-symbol-allowlist",
            "#[=[\nadd_test(\n  NAME exported-symbol-allowlist",
        )
        fixture.replace(
            abi.NATIVE_CMAKE_PATH,
            "    \"$<TARGET_FILE:fonix_shim>\"\n)\nadd_test(\n"
            "  NAME external-shim-has-no-ort-link",
            "    \"$<TARGET_FILE:fonix_shim>\"\n)\n]=]\nadd_test(\n"
            "  NAME external-shim-has-no-ort-link",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "differs from the committed baseline",
        ):
            abi.verify_repository(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.NATIVE_CMAKE_PATH,
            "add_test(\n  NAME external-shim-has-no-ort-link",
            "set_tests_properties(\n"
            "  exported-symbol-allowlist PROPERTIES DISABLED TRUE\n"
            ")\nadd_test(\n  NAME external-shim-has-no-ort-link",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "differs from the committed baseline",
        ):
            abi.verify_repository(fixture.root)

    def test_added_header_function_must_enter_every_export_inventory(self) -> None:
        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.HEADER_PATH,
            "/* run_result_get returns an owned string and a separately retained value. */",
            "DORT_API void DORT_CALL dort_unreviewed_addition(void);\n"
            "/* run_result_get returns an owned string and a separately retained value. */",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "exactly 67 functions"
        ):
            abi.build_contract(fixture.root)

    def test_baseline_digest_duplicate_keys_and_links_fail_closed(self) -> None:
        fixture = _RepositoryFixture(self)
        baseline_path = fixture.root / abi.BASELINE_PATH
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["contract"]["abiVersion"] = 2
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "contractSha256 is stale"
        ):
            abi.verify_repository(fixture.root)

        fixture = _RepositoryFixture(self)
        baseline_path = fixture.root / abi.BASELINE_PATH
        source = baseline_path.read_text(encoding="utf-8")
        baseline_path.write_text(
            '{"schemaVersion": 1,' + source[1:],
            encoding="utf-8",
        )
        self.assertNotEqual(source, baseline_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "duplicates key 'schemaVersion'"
        ):
            abi.verify_repository(fixture.root)

        if hasattr(Path, "symlink_to"):
            fixture = _RepositoryFixture(self)
            baseline_path = fixture.root / abi.BASELINE_PATH
            external = fixture.root / "external-baseline.json"
            baseline_path.replace(external)
            baseline_path.symlink_to(external)
            with self.assertRaisesRegex(
                abi.NativeAbiBaselineError, "regular file, not a link"
            ):
                abi.verify_repository(fixture.root)

    def test_baseline_requires_strict_utf8_and_unicode_json(self) -> None:
        fixture = _RepositoryFixture(self)
        baseline_path = fixture.root / abi.BASELINE_PATH
        source = baseline_path.read_text(encoding="utf-8")
        baseline_path.write_bytes(source.encode("utf-16"))
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "strict UTF-8 JSON"
        ):
            abi.verify_repository(fixture.root)

        fixture = _RepositoryFixture(self)
        baseline_path = fixture.root / abi.BASELINE_PATH
        source = baseline_path.read_bytes()
        baseline_path.write_bytes(b"\xef\xbb\xbf" + source)
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "without a BOM"
        ):
            abi.verify_repository(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.BASELINE_PATH,
            '"claimStatus": "native-c-abi-baseline-only"',
            '"claimStatus": "\\ud800"',
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "invalid Unicode"
        ):
            abi.verify_repository(fixture.root)

        fixture = _RepositoryFixture(self)
        fixture.replace(
            abi.BASELINE_PATH,
            '"schemaVersion": 1',
            '"schemaVersion": NaN',
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError, "non-finite value NaN"
        ):
            abi.verify_repository(fixture.root)

    def test_baseline_requires_canonical_json_bytes(self) -> None:
        fixture = _RepositoryFixture(self)
        baseline_path = fixture.root / abi.BASELINE_PATH
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_path.write_text(
            json.dumps(baseline, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "canonical JSON serialization",
        ):
            abi.verify_repository(fixture.root)

        fixture = _RepositoryFixture(self)
        baseline_path = fixture.root / abi.BASELINE_PATH
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        reordered = {key: baseline[key] for key in reversed(baseline)}
        baseline_path.write_text(
            json.dumps(reordered, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            abi.NativeAbiBaselineError,
            "canonical JSON serialization",
        ):
            abi.verify_repository(fixture.root)


if __name__ == "__main__":
    unittest.main()
