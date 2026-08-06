from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


CI_ROOT = Path(__file__).resolve().parents[1]
if str(CI_ROOT) not in sys.path:
    sys.path.insert(0, str(CI_ROOT))

import generate_android_sherpa_fonix_fixtures as fixtures  # noqa: E402


class AndroidSherpaFonixFixtureTests(unittest.TestCase):
    def test_exact_generated_identities_and_contract(self) -> None:
        generated = fixtures.generated_files()
        expected = {
            fixtures.MODEL_FILENAME: (
                4437,
                "da7dc57b74c05d57109100bccd1e745c234eb9f015be37284cf519f977a76076",
            ),
            fixtures.REFERENCE_INPUT_FILENAME: (
                56,
                "0bd93e8768ce38931d43c96a5fab06feb2b2ee6724f94adcb03893d60c864bab",
            ),
            fixtures.REFERENCE_OUTPUT_FILENAME: (
                16,
                "ad73b9acd6e4a74b2f5bb5386658ce3bb146cd040a1867646ab3b973fb6632b1",
            ),
            fixtures.CANCELLATION_INPUT_FILENAME: (
                2_097_176,
                "45da4d7ebbe3f6414ffd60e140fb86d273d8d0c2f1a30cc8f6d97eb6f993a0fb",
            ),
            fixtures.MANIFEST_FILENAME: (
                1771,
                "66218ac6c9e98bf15ae250472229ee517e3475511f4d0e50aaf03007e648bafc",
            ),
            fixtures.SHERPA_AUDIO_FILENAME: (
                256_044,
                "2956ffe337260f54408e90f03f705e2c354074bb97b8935cdcd0d1760c313c46",
            ),
            fixtures.SHERPA_REFERENCE_FILENAME: (
                1159,
                "fb49299f25a4287c4c0b726e62ebafb099c6984f304bc0d3c09d4c722c8306cd",
            ),
        }
        self.assertEqual(set(generated), set(expected))
        for name, contents in generated.items():
            size, digest = expected[name]
            self.assertEqual(len(contents), size, name)
            self.assertEqual(hashlib.sha256(contents).hexdigest(), digest, name)

        model = generated[fixtures.MODEL_FILENAME]
        self.assertEqual(model.count(b"\x22\x06MatMul"), fixtures.MATMUL_NODE_COUNT)
        self.assertIn(fixtures.MODEL_INPUT_NAME.encode("ascii"), model)
        self.assertIn(fixtures.MODEL_WEIGHT_NAME.encode("ascii"), model)
        self.assertIn(fixtures.MODEL_OUTPUT_NAME.encode("ascii"), model)

        manifest = json.loads(generated[fixtures.MANIFEST_FILENAME])
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["model"]["matMulNodeCount"], 64)
        self.assertEqual(manifest["reference"]["dimension"], 2)
        self.assertEqual(manifest["cancellation"]["dimension"], 512)
        self.assertEqual(
            manifest["sherpa"]["audio"]["generatorId"],
            fixtures.SHERPA_AUDIO_GENERATOR_ID,
        )
        self.assertEqual(
            manifest["sherpa"]["model"]["sha256"],
            fixtures.SHERPA_MODEL_SHA256,
        )

    def test_matrix_envelope_is_closed_little_endian_float32(self) -> None:
        raw = fixtures.reference_input_bytes()
        magic, schema, rows, inner, columns = (
            fixtures.MATRIX_INPUT_HEADER.unpack_from(raw)
        )
        self.assertEqual(magic, fixtures.MATRIX_INPUT_MAGIC)
        self.assertEqual((schema, rows, inner, columns), (1, 2, 2, 2))
        values = struct.unpack_from("<8f", raw, fixtures.MATRIX_INPUT_HEADER.size)
        self.assertEqual(values[:4], (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(values[4:], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(
            struct.unpack("<4f", fixtures.reference_output_bytes()),
            (1.0, 2.0, 3.0, 4.0),
        )

    def test_synthetic_vad_audio_and_reference_are_closed(self) -> None:
        audio = fixtures.sherpa_audio_bytes()
        self.assertEqual(audio[:4], b"RIFF")
        self.assertEqual(audio[8:12], b"WAVE")
        self.assertEqual(struct.unpack_from("<I", audio, 4)[0], len(audio) - 8)
        self.assertEqual(audio[12:16], b"fmt ")
        self.assertEqual(
            struct.unpack_from("<IHHIIHH", audio, 16),
            (16, 1, 1, 16_000, 32_000, 2, 16),
        )
        self.assertEqual(audio[36:40], b"data")
        self.assertEqual(
            struct.unpack_from("<I", audio, 40)[0],
            fixtures.SHERPA_AUDIO_SAMPLES * 2,
        )

        reference = json.loads(fixtures.sherpa_reference_bytes(audio))
        self.assertEqual(reference["schemaVersion"], 1)
        self.assertEqual(
            reference["sherpa"],
            {
                "packageVersion": "1.13.4",
                "nativeRevision": fixtures.SHERPA_NATIVE_REVISION,
            },
        )
        self.assertEqual(
            reference["model"]["sha256"],
            fixtures.SHERPA_MODEL_SHA256,
        )
        self.assertEqual(reference["audio"]["sampleCount"], 128_000)
        self.assertEqual(
            reference["observedSegments"],
            [{"startSample": 11_872, "sampleCount": 96_160}],
        )
        self.assertEqual(reference["invariant"]["minimumSegments"], 1)
        self.assertEqual(reference["invariant"]["maximumSegments"], 1)

    def test_write_is_repeatable_and_rejects_unexpected_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = fixtures.write_fixtures(root)
            second = fixtures.write_fixtures(root)
            self.assertEqual(first, second)
            for name, identity in first.items():
                raw = (root / name).read_bytes()
                self.assertEqual(len(raw), identity["sizeBytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), identity["sha256"])

            (root / "unexpected.bin").write_bytes(b"unexpected")
            with self.assertRaisesRegex(
                fixtures.FixtureGenerationError,
                "unexpected entries",
            ):
                fixtures.write_fixtures(root)

    def test_rejects_symlink_output_directory_and_nonregular_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real = parent / "real"
            real.mkdir()
            link = parent / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                fixtures.FixtureGenerationError,
                "regular directory",
            ):
                fixtures.write_fixtures(link)

            nested = real / fixtures.MODEL_FILENAME
            nested.mkdir()
            with self.assertRaisesRegex(
                fixtures.FixtureGenerationError,
                "non-regular entry",
            ):
                fixtures.write_fixtures(real)


if __name__ == "__main__":
    unittest.main()
