from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
GENERATOR = REPOSITORY / "example/assets/models/generate_xnnpack_matmul.py"

_SPEC = importlib.util.spec_from_file_location("xnnpack_fixture_generator", GENERATOR)
assert _SPEC is not None and _SPEC.loader is not None
GENERATOR_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(GENERATOR_MODULE)


class XnnpackFixtureGeneratorTest(unittest.TestCase):
    def test_committed_bytes_and_closed_metadata_match_generator(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = GENERATOR_MODULE._check()

        self.assertEqual(result, 0)
        self.assertIn(
            "verified deterministic XNNPACK MatMul fixture", stdout.getvalue()
        )

        model, metadata_bytes = GENERATOR_MODULE._expected_outputs()
        metadata = json.loads(metadata_bytes)
        self.assertEqual(
            set(metadata),
            {
                "schemaVersion",
                "id",
                "path",
                "generator",
                "generatorVersion",
                "sha256",
                "sizeBytes",
                "onnxIrVersion",
                "opset",
                "operator",
                "input",
                "initializer",
                "matrixMultiplication",
                "output",
                "referencePolicy",
                "claimBoundary",
            },
        )
        self.assertEqual(metadata["sha256"], hashlib.sha256(model).hexdigest())
        self.assertEqual(metadata["sizeBytes"], len(model))
        self.assertEqual(
            metadata["input"],
            {
                "name": "input",
                "elementType": "float32",
                "shape": [3, 2],
                "values": [1, 2, 3, 4, 5, 6],
            },
        )
        self.assertEqual(
            metadata["initializer"],
            {
                "name": "weight",
                "elementType": "float32",
                "shape": [2, 2],
                "values": [1, 2, 3, 4],
            },
        )
        self.assertEqual(
            metadata["matrixMultiplication"],
            {
                "leftShape": [3, 2],
                "rightShape": [2, 2],
            },
        )
        self.assertEqual(metadata["output"]["shape"], [3, 2])
        self.assertEqual(metadata["output"]["values"], [7, 10, 15, 22, 23, 34])
        self.assertEqual(metadata["referencePolicy"], "exact-float32")

        pubspec = (REPOSITORY / "example/pubspec.yaml").read_text(encoding="utf-8")
        self.assertEqual(pubspec.count("assets/models/xnnpack_matmul.onnx"), 1)
        self.assertEqual(pubspec.count("assets/models/xnnpack_matmul.json"), 1)

    def test_write_then_check_round_trip_and_detects_byte_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            GENERATOR_MODULE._write(root=root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(GENERATOR_MODULE._check(root=root), 0)

            model_path = root / GENERATOR_MODULE.MODEL_FILENAME
            tampered = bytearray(model_path.read_bytes())
            tampered[-1] ^= 1
            model_path.write_bytes(tampered)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(GENERATOR_MODULE._check(root=root), 1)
            self.assertIn("byte mismatch", stderr.getvalue())

    def test_check_rejects_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            GENERATOR_MODULE._write(root=root)
            metadata_path = root / GENERATOR_MODULE.METADATA_FILENAME
            metadata_path.unlink()
            try:
                os.symlink(GENERATOR_MODULE.MODEL_FILENAME, metadata_path)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(GENERATOR_MODULE._check(root=root), 1)
            self.assertIn("not a regular non-link file", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
