from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
GENERATOR = (
    REPOSITORY / "example/assets/models/generate_cpu_benchmark_matmul.py"
)

_SPEC = importlib.util.spec_from_file_location(
    "cpu_benchmark_fixture_generator", GENERATOR
)
assert _SPEC is not None and _SPEC.loader is not None
GENERATOR_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(GENERATOR_MODULE)


class CpuBenchmarkFixtureGeneratorTest(unittest.TestCase):
    def test_committed_bytes_and_closed_metadata_match_generator(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = GENERATOR_MODULE._check()

        self.assertEqual(result, 0)
        self.assertIn(
            "verified deterministic CPU benchmark MatMul fixture",
            stdout.getvalue(),
        )

        model, input_data, output_data, metadata_bytes = (
            GENERATOR_MODULE._expected_outputs()
        )
        metadata = json.loads(metadata_bytes)
        self.assertEqual(
            set(metadata),
            {
                "schemaVersion",
                "id",
                "generator",
                "model",
                "input",
                "initializer",
                "matrixMultiplication",
                "output",
                "determinism",
                "referencePolicy",
                "claimBoundary",
            },
        )
        self.assertEqual(
            set(metadata["generator"]),
            {"path", "version", "dependencies"},
        )
        self.assertEqual(metadata["generator"]["dependencies"], "python-standard-library-only")
        self.assertEqual(metadata["model"]["sizeBytes"], len(model))
        self.assertEqual(metadata["model"]["onnxIrVersion"], 8)
        self.assertEqual(metadata["model"]["opset"], 17)
        self.assertEqual(metadata["model"]["operator"], "MatMul")
        self.assertEqual(metadata["input"]["name"], "input")
        self.assertEqual(metadata["input"]["shape"], [2048, 1024])
        self.assertEqual(metadata["input"]["data"]["sizeBytes"], len(input_data))
        self.assertEqual(metadata["initializer"]["name"], "weight")
        self.assertEqual(metadata["initializer"]["shape"], [1024, 1024])
        self.assertEqual(metadata["output"]["name"], "output")
        self.assertEqual(metadata["output"]["shape"], [2048, 1024])
        self.assertEqual(
            metadata["output"]["referenceData"]["sizeBytes"], len(output_data)
        )
        self.assertEqual(
            metadata["matrixMultiplication"]["multiplyAccumulateCount"],
            2_147_483_648,
        )
        self.assertEqual(
            metadata["matrixMultiplication"]["flopCount"], 4_294_967_296
        )
        self.assertEqual(
            metadata["determinism"]["independentVerification"],
            "periodic-ieee754-math-fsum-correlation-for-every-tile-value",
        )
        self.assertEqual(
            metadata["referencePolicy"],
            {
                "comparison": "exact-ieee754-binary32-bits",
                "absoluteTolerance": 0,
                "relativeTolerance": 0,
                "nanPolicy": "forbid",
                "infinityPolicy": "forbid",
                "reason": (
                    "All operands, products, partial sums, and outputs are exact "
                    "integers with magnitude below 2^24."
                ),
            },
        )
        for forbidden_claim in (
            "latency",
            "throughput",
            "real-time",
            "support",
            "provider-qualification",
            "regression-threshold",
            "portability",
            "release",
        ):
            self.assertIn(forbidden_claim, metadata["claimBoundary"])

    def test_binary_values_match_independently_verified_reference(self) -> None:
        model, input_data, output_data, _ = GENERATOR_MODULE._expected_outputs()
        input_count = GENERATOR_MODULE.INPUT_SHAPE[0] * GENERATOR_MODULE.INPUT_SHAPE[1]
        output_count = (
            GENERATOR_MODULE.OUTPUT_SHAPE[0] * GENERATOR_MODULE.OUTPUT_SHAPE[1]
        )
        unpacked_input = struct.unpack(f"<{input_count}f", input_data)
        unpacked_output = struct.unpack(f"<{output_count}f", output_data)
        input_values = GENERATOR_MODULE._input_values()
        weight_values = GENERATOR_MODULE._weight_values()
        expected_output = GENERATOR_MODULE._reference_output_values(
            input_values, weight_values
        )

        self.assertEqual(unpacked_input, input_values)
        self.assertEqual(unpacked_output, expected_output)
        weight_data = GENERATOR_MODULE._float32_bytes(weight_values)
        self.assertEqual(model.count(weight_data), 1)

        tile = GENERATOR_MODULE._reference_tile_from_expanded_values(
            input_values, weight_values
        )
        mutated = [list(row) for row in tile]
        mutated[-1][-1] += 1
        with self.assertRaisesRegex(
            AssertionError, "independent periodic float reference"
        ):
            GENERATOR_MODULE._verify_reference_tile_independently(mutated)

    def test_pubspec_declares_only_the_generated_runtime_assets_once(self) -> None:
        pubspec = (REPOSITORY / "example/pubspec.yaml").read_text(encoding="utf-8")
        runtime_assets = (
            "assets/models/cpu_benchmark_matmul.onnx",
            "assets/models/cpu_benchmark_matmul.input.f32le",
            "assets/models/cpu_benchmark_matmul.output.f32le",
            "assets/models/cpu_benchmark_matmul.json",
        )
        for asset in runtime_assets:
            with self.subTest(asset=asset):
                self.assertEqual(pubspec.count(asset), 1)
        self.assertNotIn("assets/models/generate_cpu_benchmark_matmul.py", pubspec)

    def test_write_then_check_round_trip_and_detects_each_byte_tamper(self) -> None:
        for filename, _ in GENERATOR_MODULE._output_files():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                GENERATOR_MODULE._write(root=root)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(GENERATOR_MODULE._check(root=root), 0)

                path = root / filename
                tampered = bytearray(path.read_bytes())
                tampered[-1] ^= 1
                path.write_bytes(tampered)
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    self.assertEqual(GENERATOR_MODULE._check(root=root), 1)
                self.assertIn("byte mismatch", stderr.getvalue())

    def test_check_rejects_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            GENERATOR_MODULE._write(root=root)
            output_path = root / GENERATOR_MODULE.OUTPUT_FILENAME
            output_path.unlink()
            try:
                os.symlink(GENERATOR_MODULE.INPUT_FILENAME, output_path)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlinks are unavailable: {error}")

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(GENERATOR_MODULE._check(root=root), 1)
            self.assertIn("not a regular non-link file", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
