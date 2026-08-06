from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest


CI_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY = CI_DIRECTORY.parents[1]
sys.path.insert(0, str(CI_DIRECTORY))

import check_c_source_quality  # noqa: E402


class CSourceQualityTest(unittest.TestCase):
    def _repository(self, root: Path) -> None:
        for relative in check_c_source_quality.SOURCE_ROOTS:
            (root / relative).mkdir(parents=True, exist_ok=True)

    def test_active_repository_passes_the_offline_gate(self) -> None:
        file_count, total_bytes = check_c_source_quality.audit_repository(REPOSITORY)

        self.assertGreater(file_count, 0)
        self.assertGreater(total_bytes, 0)
        self.assertLessEqual(file_count, check_c_source_quality.MAX_SOURCE_FILES)
        self.assertLessEqual(
            total_bytes, check_c_source_quality.MAX_TOTAL_SOURCE_BYTES
        )

    def test_inventory_is_closed_sorted_and_ignores_non_c_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary).resolve()
            self._repository(repository)
            (repository / "src/z.h").write_bytes(b"#pragma once\n")
            (repository / "src/a.c").write_bytes(b"int a(void) { return 1; }\n")
            (repository / "test/native/b.c").write_bytes(
                b"int b(void) { return 2; }\n"
            )
            (repository / "tool/ci/ignored.py").write_text(
                "ignored\n", encoding="utf-8"
            )

            paths = check_c_source_quality.discover_sources(repository)

            self.assertEqual(
                [path.relative_to(repository).as_posix() for path in paths],
                ["src/a.c", "src/z.h", "test/native/b.c"],
            )

    def test_byte_contract_rejects_every_format_and_lint_escape(self) -> None:
        cases = {
            "empty": (b"", "empty"),
            "bom": (b"\xef\xbb\xbfint x;\n", "BOM"),
            "invalid_utf8": (b"int \xff;\n", "strict UTF-8"),
            "missing_lf": (b"int x;", "end in one LF"),
            "blank_eof": (b"int x;\n\n", "blank line at EOF"),
            "crlf": (b"int x;\r\n", "CRLF/CR"),
            "tab": (b"\tint x;\n", "tabs"),
            "trailing": (b"int x; \n", "trailing whitespace"),
            "preprocessor_indent": (b"  #define X 1\n", "column one"),
            "format_bypass": (b"/* clang-format off */\n", "suppression"),
            "lint_bypass": (b"/* NOLINT */\n", "suppression"),
            "pragma_bypass": (
                b"#pragma GCC diagnostic ignored \"-Wall\"\n",
                "suppression",
            ),
            "conflict": (b"<<<<<<< ours\n", "merge conflict"),
            "control": (b"int \x01x;\n", "control bytes"),
            "long_line": (
                b"x" * (check_c_source_quality.MAX_LINE_BYTES + 1) + b"\n",
                "line exceeds",
            ),
            "large_file": (
                b"x" * (check_c_source_quality.MAX_SOURCE_FILE_BYTES + 1),
                "source file exceeds",
            ),
        }
        for name, (payload, expected) in cases.items():
            with self.subTest(name=name):
                problems = check_c_source_quality.audit_source_bytes(
                    "src/example.c", payload
                )
                self.assertTrue(
                    any(expected in problem for problem in problems), problems
                )

    def test_missing_roots_and_symlink_sources_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            with self.assertRaisesRegex(
                check_c_source_quality.CSourceQualityError,
                "required C source root is missing",
            ):
                check_c_source_quality.discover_sources(repository)

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._repository(repository)
            target = repository / "outside.c"
            target.write_bytes(b"int outside;\n")
            (repository / "src/linked.c").symlink_to(target)
            with self.assertRaisesRegex(
                check_c_source_quality.CSourceQualityError,
                "regular non-symlink file",
            ):
                check_c_source_quality.discover_sources(repository)

    def test_cli_is_nonzero_and_bounded_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._repository(repository)
            (repository / "src/bad.c").write_bytes(b"int bad; \n")
            stdout = StringIO()
            stderr = StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = check_c_source_quality.main(
                    ["--repository", str(repository)]
                )

            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("C source quality gate failed", stderr.getvalue())
            self.assertIn("src/bad.c:1", stderr.getvalue())

    def test_workflow_runs_the_gate_exactly_once_without_masking(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        command = "python -B tool/ci/check_c_source_quality.py --repository ."

        self.assertEqual(workflow.count(command), 1)
        self.assertNotIn(f"{command} || true", workflow)


if __name__ == "__main__":
    unittest.main()
