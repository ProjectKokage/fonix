from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
GENERATOR = REPOSITORY / "test/fixtures/generate_phase3_fixtures.py"

_SPEC = importlib.util.spec_from_file_location("phase3_fixture_generator", GENERATOR)
assert _SPEC is not None and _SPEC.loader is not None
GENERATOR_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(GENERATOR_MODULE)


class Phase3FixtureInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.files = {"generated.onnx": b"generated model bytes"}
        self.manifest = b"{}\n"
        (self.root / "generated.onnx").write_bytes(self.files["generated.onnx"])
        (self.root / "phase3_manifest.json").write_bytes(self.manifest)
        shutil.copyfile(
            REPOSITORY / "test/fixtures/mul_1.onnx",
            self.root / "mul_1.onnx",
        )

    def check(self) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = GENERATOR_MODULE._check(
                self.files,
                self.manifest,
                root=self.root,
            )
        return result, stdout.getvalue(), stderr.getvalue()

    def test_exact_generated_and_sourced_inventory_passes(self) -> None:
        result, stdout, stderr = self.check()

        self.assertEqual(result, 0)
        self.assertIn("closed model/data paths", stdout)
        self.assertEqual(stderr, "")

    def test_unknown_onnx_and_bin_files_fail(self) -> None:
        stale_directory = self.root / "renamed"
        stale_directory.mkdir()
        (stale_directory / "old-name.onnx").write_bytes(b"stale")
        (self.root / "orphan.bin").write_bytes(b"stale")

        result, _, stderr = self.check()

        self.assertEqual(result, 1)
        self.assertIn("unexpected model/data file: orphan.bin", stderr)
        self.assertIn("unexpected model/data file: renamed/old-name.onnx", stderr)

    def test_symlinked_expected_model_fails_without_following_it(self) -> None:
        (self.root / "generated.onnx").unlink()
        try:
            os.symlink("mul_1.onnx", self.root / "generated.onnx")
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlinks are unavailable: {error}")

        result, _, stderr = self.check()

        self.assertEqual(result, 1)
        self.assertIn("fixture symlink is forbidden: generated.onnx", stderr)

    def test_model_suffix_directory_is_rejected_as_nonregular(self) -> None:
        (self.root / "stale.bin").mkdir()

        result, _, stderr = self.check()

        self.assertEqual(result, 1)
        self.assertIn("model/data path must be a regular file: stale.bin", stderr)

    def test_separately_sourced_model_identity_is_pinned(self) -> None:
        (self.root / "mul_1.onnx").write_bytes(b"x" * 130)

        result, _, stderr = self.check()

        self.assertEqual(result, 1)
        self.assertIn("SHA-256 mismatch: mul_1.onnx", stderr)

    def test_inventory_entry_budget_fails_closed(self) -> None:
        extra_count = GENERATOR_MODULE.MAX_FIXTURE_INVENTORY_ENTRIES - 2
        for index in range(extra_count):
            (self.root / f"benign-{index:03d}.txt").write_bytes(b"")

        result, _, stderr = self.check()

        self.assertEqual(result, 1)
        self.assertIn("fixture inventory exceeds", stderr)


if __name__ == "__main__":
    unittest.main()
