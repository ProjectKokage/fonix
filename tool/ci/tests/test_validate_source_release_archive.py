from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile
import zlib


CI_DIRECTORY = Path(__file__).resolve().parents[1]
REPOSITORY = CI_DIRECTORY.parents[1]
sys.path.insert(0, str(CI_DIRECTORY))

import source_checksum_manifest  # noqa: E402
import validate_source_release_archive as validator  # noqa: E402


GIT = Path("/usr/bin/git")
FIXED_DATE = "2026-08-08T00:00:00Z"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *arguments: str, environment: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [str(GIT), "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=30,
    )
    return result.stdout.decode("ascii").strip()


class SourceReleaseArchiveFixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self.repository = self.root / "repository"
        self.external = self.root / "external"
        (self.repository / "tool/ci").mkdir(parents=True)
        (self.repository / "templates/ci").mkdir(parents=True)
        self.external.mkdir()
        shutil.copyfile(
            REPOSITORY / validator.SOURCE_HELPER_PATH,
            self.repository / validator.SOURCE_HELPER_PATH,
        )
        shutil.copyfile(
            REPOSITORY / validator.VALIDATOR_PATH,
            self.repository / validator.VALIDATOR_PATH,
        )
        shutil.copyfile(
            REPOSITORY / validator.VALIDATION_SCHEMA_PATH,
            self.repository / validator.VALIDATION_SCHEMA_PATH,
        )
        executable = self.repository / "tool/sample.sh"
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        (self.repository / "tool/empty.txt").write_bytes(b"")
        long_directory = self.repository / "templates" / ("a" * 80)
        long_directory.mkdir()
        (long_directory / ("b" * 40 + ".txt")).write_bytes(b"long ustar path\n")
        source_checksum_manifest.generate_manifest(
            self.repository, self.repository / source_checksum_manifest.MANIFEST_NAME
        )
        _git(self.repository, "init", "-q")
        _git(self.repository, "config", "user.name", "Fonix tests")
        _git(
            self.repository,
            "config",
            "user.email",
            "fonix-tests@example.invalid",
        )
        _git(self.repository, "add", ".")
        environment = {
            **os.environ,
            "GIT_AUTHOR_DATE": FIXED_DATE,
            "GIT_COMMITTER_DATE": FIXED_DATE,
        }
        _git(self.repository, "commit", "-qm", "source fixture", environment=environment)
        self.revision = _git(self.repository, "rev-parse", "HEAD")
        self.zip = self.external / "source.zip"
        self.tar_gzip = self.external / "source.tar.gz"
        _git(
            self.repository,
            "archive",
            "--format=zip",
            f"--output={self.zip}",
            "HEAD",
        )
        _git(
            self.repository,
            "-c",
            "tar.umask=0022",
            "archive",
            "--format=tar.gz",
            f"--output={self.tar_gzip}",
            "HEAD",
        )

    def validate_descriptor(self, path: Path, media_type: str) -> dict[str, object]:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.lseek(descriptor, min(7, path.stat().st_size), os.SEEK_SET)
            record = validator.validate_archive_descriptor(
                self.repository,
                descriptor,
                media_type=media_type,
                expected_sha256=_sha256(path),
                expected_size_bytes=path.stat().st_size,
                expected_source_revision=self.revision,
            )
            self.descriptor_was_left_open = os.fstat(descriptor)
            self.descriptor_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
            return record
        finally:
            os.close(descriptor)

    def expect_rejected(self, path: Path, media_type: str) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            with self._assert_raises():
                validator.validate_archive_descriptor(
                    self.repository,
                    descriptor,
                    media_type=media_type,
                    expected_sha256=_sha256(path),
                    expected_size_bytes=path.stat().st_size,
                    expected_source_revision=self.revision,
                )
            os.fstat(descriptor)
            self.rejected_descriptor_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
        finally:
            os.close(descriptor)

    @staticmethod
    def _assert_raises() -> unittest.case._AssertRaisesContext:
        return unittest.TestCase().assertRaises(validator.SourceReleaseArchiveError)

    def rewritten_zip(self, name: str, mutation: object) -> Path:
        output = self.external / name
        with zipfile.ZipFile(self.zip, "r") as source:
            entries = [(copy.copy(info), source.read(info)) for info in source.infolist()]
            comment = source.comment
        if callable(mutation):
            entries, comment = mutation(entries, comment)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, "w") as destination:
                destination.comment = comment
                for info, contents in entries:
                    destination.writestr(
                        info, contents, compress_type=info.compress_type
                    )
        return output

    def zip_with_unused_deflate_data(self, name: str) -> Path:
        raw = bytearray(self.zip.read_bytes())
        eocd_offset = len(raw) - validator._ZIP_EOCD.size - len(self.revision)
        eocd = list(validator._ZIP_EOCD.unpack_from(raw, eocd_offset))
        central_offset = eocd[6]
        entry_count = eocd[4]
        central_entries: list[tuple[int, list[int]]] = []
        offset = central_offset
        target: tuple[int, list[int]] | None = None
        for _ in range(entry_count):
            fields = list(validator._ZIP_CENTRAL.unpack_from(raw, offset))
            central_entries.append((offset, fields))
            if target is None and fields[4] == zipfile.ZIP_DEFLATED:
                target = (offset, fields)
            offset += validator._ZIP_CENTRAL.size + fields[10] + fields[11] + fields[12]
        if target is None:
            raise AssertionError("fixture has no DEFLATE member")
        _, target_fields = target
        local_offset = target_fields[16]
        local = list(validator._ZIP_LOCAL.unpack_from(raw, local_offset))
        data_offset = local_offset + validator._ZIP_LOCAL.size + local[9] + local[10]
        insertion = data_offset + local[7]
        raw[insertion:insertion] = b"\x00"
        struct_pack = validator.struct.pack_into
        struct_pack("<I", raw, local_offset + 18, local[7] + 1)
        for old_central_offset, fields in central_entries:
            current_offset = old_central_offset + 1
            if fields[16] == local_offset:
                struct_pack("<I", raw, current_offset + 20, fields[8] + 1)
            elif fields[16] > local_offset:
                struct_pack("<I", raw, current_offset + 42, fields[16] + 1)
        new_eocd_offset = eocd_offset + 1
        struct_pack("<I", raw, new_eocd_offset + 16, central_offset + 1)
        output = self.external / name
        output.write_bytes(raw)
        return output

    def mutated_tar(self, name: str, mutate: object) -> Path:
        compressed = self.tar_gzip.read_bytes()
        raw = bytearray(zlib.decompress(compressed, 16 + zlib.MAX_WBITS))
        mutate(raw)
        compressor = zlib.compressobj(level=6, wbits=16 + zlib.MAX_WBITS)
        encoded = bytearray(compressor.compress(bytes(raw)) + compressor.flush())
        encoded[9] = 3
        if encoded[:10] != b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03":
            raise AssertionError("test gzip encoder is not canonical")
        output = self.external / name
        output.write_bytes(bytes(encoded))
        return output

    @staticmethod
    def tar_header_offset(raw: bytearray, name: bytes) -> int:
        for offset in range(0, len(raw), 512):
            if raw[offset : offset + len(name) + 1] == name + b"\x00":
                return offset
        raise AssertionError(f"missing tar header {name!r}")

    @staticmethod
    def fix_tar_checksum(raw: bytearray, offset: int) -> None:
        header = bytearray(raw[offset : offset + 512])
        header[148:156] = b"        "
        checksum = sum(header)
        header[148:156] = f"{checksum:07o}\0".encode("ascii")
        raw[offset : offset + 512] = header


@unittest.skipUnless(GIT.is_file(), "requires the trusted /usr/bin/git")
class SourceReleaseArchiveValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name).resolve(strict=True)
        cls.fixture = SourceReleaseArchiveFixture(root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_git_zip_and_tar_gzip_have_one_inventory(self) -> None:
        zip_record = self.fixture.validate_descriptor(
            self.fixture.zip, validator.MEDIA_TYPE_ZIP
        )
        tar_record = self.fixture.validate_descriptor(
            self.fixture.tar_gzip, validator.MEDIA_TYPE_GZIP
        )
        self.assertEqual(
            zip_record["source"]["inventorySha256"],
            tar_record["source"]["inventorySha256"],
        )
        self.assertEqual(
            zip_record["source"]["memberCount"],
            zip_record["source"]["regularFileCount"]
            + zip_record["source"]["directoryCount"],
        )
        self.assertEqual(zip_record["source"]["executableFileCount"], 1)
        self.assertEqual(zip_record["source"]["revisionBinding"], "zip-comment")
        self.assertEqual(
            tar_record["source"]["revisionBinding"], "pax-global-comment"
        )
        self.assertEqual(self.fixture.descriptor_offset, 0)
        self.assertGreater(self.fixture.descriptor_was_left_open.st_size, 0)

    def test_cli_publishes_canonical_path_free_record_once(self) -> None:
        output = self.fixture.external / "cli-record.json"
        arguments = [
            "--repository",
            str(self.fixture.repository),
            "--archive",
            str(self.fixture.zip),
            "--media-type",
            validator.MEDIA_TYPE_ZIP,
            "--archive-sha256",
            _sha256(self.fixture.zip),
            "--output",
            str(output),
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(validator.main(arguments), 0)
        self.assertEqual(stderr.getvalue(), "")
        raw = output.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        record = json.loads(raw)
        self.assertEqual(record["result"], "validated")
        self.assertEqual(record["claimStatus"], "source-closure-only")
        self.assertNotIn(str(self.fixture.root), raw.decode("ascii"))
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(validator.main(arguments), 1)

    def test_plain_text_labeled_as_gzip_is_rejected(self) -> None:
        fake = self.fixture.external / "fake-source.tar.gz"
        fake.write_bytes(b"synthetic closed source archive\n")
        self.fixture.expect_rejected(fake, validator.MEDIA_TYPE_GZIP)
        self.assertEqual(self.fixture.rejected_descriptor_offset, 0)

    def test_media_type_magic_mismatch_is_rejected(self) -> None:
        self.fixture.expect_rejected(self.fixture.zip, validator.MEDIA_TYPE_GZIP)
        self.fixture.expect_rejected(
            self.fixture.tar_gzip, validator.MEDIA_TYPE_ZIP
        )

    def test_descriptor_is_rewound_when_public_validation_fails_early(self) -> None:
        descriptor = os.open(self.fixture.zip, os.O_RDONLY)
        try:
            os.lseek(descriptor, 11, os.SEEK_SET)
            with self.assertRaises(validator.SourceReleaseArchiveError):
                validator.validate_archive_descriptor(
                    self.fixture.repository,
                    descriptor,
                    media_type="application/octet-stream",
                    expected_sha256=_sha256(self.fixture.zip),
                    expected_size_bytes=self.fixture.zip.stat().st_size,
                )
            self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 0)
        finally:
            os.close(descriptor)

    def test_wrong_archive_identity_is_rejected(self) -> None:
        descriptor = os.open(self.fixture.zip, os.O_RDONLY)
        try:
            with self.assertRaises(validator.SourceReleaseArchiveError):
                validator.validate_archive_descriptor(
                    self.fixture.repository,
                    descriptor,
                    media_type=validator.MEDIA_TYPE_ZIP,
                    expected_sha256="0" * 64,
                    expected_size_bytes=self.fixture.zip.stat().st_size,
                )
            with self.assertRaises(validator.SourceReleaseArchiveError):
                validator.validate_archive_descriptor(
                    self.fixture.repository,
                    descriptor,
                    media_type=validator.MEDIA_TYPE_ZIP,
                    expected_sha256=_sha256(self.fixture.zip),
                    expected_size_bytes=self.fixture.zip.stat().st_size + 1,
                )
        finally:
            os.close(descriptor)

    def test_zip_wrong_revision_is_rejected(self) -> None:
        def mutate(entries: object, _comment: bytes) -> tuple[object, bytes]:
            return entries, b"0" * 40

        archive = self.fixture.rewritten_zip("wrong-revision.zip", mutate)
        self.fixture.expect_rejected(archive, validator.MEDIA_TYPE_ZIP)

    def test_zip_unsafe_name_is_rejected(self) -> None:
        def mutate(entries: list[tuple[zipfile.ZipInfo, bytes]], comment: bytes):
            for info, _ in entries:
                if not info.is_dir():
                    info.filename = "../escape"
                    break
            return entries, comment

        archive = self.fixture.rewritten_zip("unsafe-name.zip", mutate)
        self.fixture.expect_rejected(archive, validator.MEDIA_TYPE_ZIP)

    def test_zip_duplicate_and_reordered_members_are_rejected(self) -> None:
        def duplicate(entries: list[tuple[zipfile.ZipInfo, bytes]], comment: bytes):
            entries[1][0].filename = entries[0][0].filename
            return entries, comment

        duplicate_archive = self.fixture.rewritten_zip("duplicate.zip", duplicate)
        self.fixture.expect_rejected(duplicate_archive, validator.MEDIA_TYPE_ZIP)

        def reorder(entries: list[tuple[zipfile.ZipInfo, bytes]], comment: bytes):
            entries[0], entries[1] = entries[1], entries[0]
            return entries, comment

        reordered = self.fixture.rewritten_zip("reordered.zip", reorder)
        self.fixture.expect_rejected(reordered, validator.MEDIA_TYPE_ZIP)

    def test_zip_content_and_executable_mode_tampering_are_rejected(self) -> None:
        def content(entries: list[tuple[zipfile.ZipInfo, bytes]], comment: bytes):
            for index, (info, contents) in enumerate(entries):
                if info.filename.endswith("source_checksum_manifest.py"):
                    entries[index] = (info, contents + b"tamper")
                    break
            return entries, comment

        changed_content = self.fixture.rewritten_zip("changed-content.zip", content)
        self.fixture.expect_rejected(changed_content, validator.MEDIA_TYPE_ZIP)

        def mode(entries: list[tuple[zipfile.ZipInfo, bytes]], comment: bytes):
            for info, _ in entries:
                if info.filename == "tool/sample.sh":
                    info.create_system = 0
                    info.external_attr = 0
                    break
            return entries, comment

        changed_mode = self.fixture.rewritten_zip("changed-mode.zip", mode)
        self.fixture.expect_rejected(changed_mode, validator.MEDIA_TYPE_ZIP)

    def test_zip_prepended_and_trailing_bytes_are_rejected(self) -> None:
        original = self.fixture.zip.read_bytes()
        prefixed = self.fixture.external / "prefixed.zip"
        prefixed.write_bytes(b"x" + original)
        self.fixture.expect_rejected(prefixed, validator.MEDIA_TYPE_ZIP)
        trailing = self.fixture.external / "trailing.zip"
        trailing.write_bytes(original + b"x")
        self.fixture.expect_rejected(trailing, validator.MEDIA_TYPE_ZIP)

    def test_zip_deflate_member_must_have_no_unused_compressed_data(self) -> None:
        archive = self.fixture.zip_with_unused_deflate_data("unused-deflate.zip")
        self.fixture.expect_rejected(archive, validator.MEDIA_TYPE_ZIP)

    def test_gzip_trailing_and_concatenated_members_are_rejected(self) -> None:
        original = self.fixture.tar_gzip.read_bytes()
        trailing = self.fixture.external / "trailing.tar.gz"
        trailing.write_bytes(original + b"x")
        self.fixture.expect_rejected(trailing, validator.MEDIA_TYPE_GZIP)
        concatenated = self.fixture.external / "concatenated.tar.gz"
        concatenated.write_bytes(original + original)
        self.fixture.expect_rejected(concatenated, validator.MEDIA_TYPE_GZIP)

    def test_tar_wrong_pax_revision_is_rejected(self) -> None:
        def mutate(raw: bytearray) -> None:
            expected = f"52 comment={self.fixture.revision}\n".encode("ascii")
            replacement = b"52 comment=" + b"0" * 40 + b"\n"
            offset = raw.index(expected)
            raw[offset : offset + len(expected)] = replacement

        archive = self.fixture.mutated_tar("wrong-pax.tar.gz", mutate)
        self.fixture.expect_rejected(archive, validator.MEDIA_TYPE_GZIP)

    def test_tar_link_and_mode_tampering_are_rejected(self) -> None:
        def link(raw: bytearray) -> None:
            offset = self.fixture.tar_header_offset(raw, b"tool/")
            raw[offset + 156] = ord("2")
            self.fixture.fix_tar_checksum(raw, offset)

        link_archive = self.fixture.mutated_tar("link.tar.gz", link)
        self.fixture.expect_rejected(link_archive, validator.MEDIA_TYPE_GZIP)

        def mode(raw: bytearray) -> None:
            offset = self.fixture.tar_header_offset(raw, b"tool/sample.sh")
            raw[offset + 100 : offset + 108] = b"0000644\0"
            self.fixture.fix_tar_checksum(raw, offset)

        mode_archive = self.fixture.mutated_tar("tar-mode.tar.gz", mode)
        self.fixture.expect_rejected(mode_archive, validator.MEDIA_TYPE_GZIP)

    def test_tar_nonzero_end_padding_is_rejected(self) -> None:
        def mutate(raw: bytearray) -> None:
            raw[-1] = 1

        archive = self.fixture.mutated_tar("nonzero-padding.tar.gz", mutate)
        self.fixture.expect_rejected(archive, validator.MEDIA_TYPE_GZIP)

    def test_worktree_and_manifest_must_match_the_committed_archive(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.fixture.root)).resolve(strict=True)
        try:
            changed = SourceReleaseArchiveFixture(root)
            (changed.repository / "tool/sample.sh").write_bytes(
                b"#!/bin/sh\nexit 1\n"
            )
            changed.expect_rejected(changed.zip, validator.MEDIA_TYPE_ZIP)
            source_checksum_manifest.generate_manifest(
                changed.repository,
                changed.repository / source_checksum_manifest.MANIFEST_NAME,
            )
            changed.expect_rejected(changed.zip, validator.MEDIA_TYPE_ZIP)
        finally:
            shutil.rmtree(root)

    def test_archive_contents_cannot_be_rebound_to_an_older_head_comment(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.fixture.root)).resolve(strict=True)
        try:
            changed = SourceReleaseArchiveFixture(root)
            older_revision = changed.revision
            (changed.repository / "tool/sample.sh").write_bytes(
                b"#!/bin/sh\nexit 2\n"
            )
            source_checksum_manifest.generate_manifest(
                changed.repository,
                changed.repository / source_checksum_manifest.MANIFEST_NAME,
            )
            _git(changed.repository, "add", ".")
            environment = {
                **os.environ,
                "GIT_AUTHOR_DATE": "2026-08-08T00:01:00Z",
                "GIT_COMMITTER_DATE": "2026-08-08T00:01:00Z",
            }
            _git(
                changed.repository,
                "commit",
                "-qm",
                "newer source",
                environment=environment,
            )
            newer_archive = changed.external / "newer.zip"
            _git(
                changed.repository,
                "archive",
                "--format=zip",
                f"--output={newer_archive}",
                "HEAD",
            )
            raw = newer_archive.read_bytes()
            newer_revision = _git(changed.repository, "rev-parse", "HEAD")
            self.assertTrue(raw.endswith(newer_revision.encode("ascii")))
            rebound = changed.external / "rebound-to-older-head.zip"
            rebound.write_bytes(
                raw[: -len(newer_revision)] + older_revision.encode("ascii")
            )
            _git(changed.repository, "reset", "--soft", older_revision)
            changed.revision = older_revision
            changed.expect_rejected(rebound, validator.MEDIA_TYPE_ZIP)
            self.assertEqual(changed.rejected_descriptor_offset, 0)
        finally:
            shutil.rmtree(root)

    def test_schema_pin_drift_is_rejected(self) -> None:
        root = Path(tempfile.mkdtemp(dir=self.fixture.root)).resolve(strict=True)
        try:
            changed = SourceReleaseArchiveFixture(root)
            schema_path = changed.repository / validator.VALIDATION_SCHEMA_PATH
            schema_path.write_bytes(schema_path.read_bytes() + b"\n")
            source_checksum_manifest.generate_manifest(
                changed.repository,
                changed.repository / source_checksum_manifest.MANIFEST_NAME,
            )
            _git(changed.repository, "add", ".")
            environment = {
                **os.environ,
                "GIT_AUTHOR_DATE": FIXED_DATE,
                "GIT_COMMITTER_DATE": FIXED_DATE,
            }
            _git(
                changed.repository,
                "commit",
                "-qm",
                "schema drift",
                environment=environment,
            )
            archive = changed.external / "schema-drift.zip"
            _git(
                changed.repository,
                "archive",
                "--format=zip",
                f"--output={archive}",
                "HEAD",
            )
            changed.revision = _git(changed.repository, "rev-parse", "HEAD")
            changed.expect_rejected(archive, validator.MEDIA_TYPE_ZIP)
        finally:
            shutil.rmtree(root)

    def test_verified_source_helper_ignores_hostile_timestamp_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = SourceReleaseArchiveFixture(Path(temporary).resolve())
            helper_path = changed.repository / validator.SOURCE_HELPER_PATH
            trusted = helper_path.read_bytes()
            hostile_prefix = (
                b"from pathlib import Path\n"
                b"exec(compile(Path(__file__).read_bytes(), __file__, 'exec'), "
                b"globals())\n"
                b"HOSTILE_CACHE_EXECUTED = True\n#"
            )
            self.assertLess(len(hostile_prefix), len(trusted))
            hostile = hostile_prefix + b"x" * (len(trusted) - len(hostile_prefix))
            fixed_seconds = 1_700_000_000
            helper_path.write_bytes(hostile)
            os.utime(helper_path, (fixed_seconds, fixed_seconds))
            py_compile.compile(
                str(helper_path),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
            )
            helper_path.write_bytes(trusted)
            os.utime(helper_path, (fixed_seconds, fixed_seconds))

            helper = validator._load_source_helper(changed.repository, None)

            self.assertFalse(hasattr(helper, "HOSTILE_CACHE_EXECUTED"))

    def test_archive_and_output_must_be_external_absolute_paths(self) -> None:
        ignored = self.fixture.repository / "build"
        ignored.mkdir()
        inside_archive = ignored / "inside.zip"
        try:
            shutil.copyfile(self.fixture.zip, inside_archive)
            with self.assertRaises(validator.SourceReleaseArchiveError):
                validator.validate_archive_path(
                    repository=self.fixture.repository,
                    archive=inside_archive,
                    media_type=validator.MEDIA_TYPE_ZIP,
                    expected_sha256=_sha256(inside_archive),
                    output=self.fixture.external / "inside-archive-result.json",
                )
        finally:
            shutil.rmtree(ignored)
        with self.assertRaises(validator.SourceReleaseArchiveError):
            validator.validate_archive_path(
                repository=self.fixture.repository,
                archive=self.fixture.zip,
                media_type=validator.MEDIA_TYPE_ZIP,
                expected_sha256=_sha256(self.fixture.zip),
                output=self.fixture.repository / "inside-output.json",
            )
        with self.assertRaises(validator.SourceReleaseArchiveError):
            validator.validate_archive_path(
                repository=self.fixture.repository,
                archive=Path("source.zip"),
                media_type=validator.MEDIA_TYPE_ZIP,
                expected_sha256=_sha256(self.fixture.zip),
                output=self.fixture.external / "relative-archive-result.json",
            )

    def test_existing_or_symlink_output_is_rejected(self) -> None:
        existing = self.fixture.external / "existing.json"
        existing.write_text("existing", encoding="utf-8")
        with self.assertRaises(validator.SourceReleaseArchiveError):
            validator.validate_archive_path(
                repository=self.fixture.repository,
                archive=self.fixture.zip,
                media_type=validator.MEDIA_TYPE_ZIP,
                expected_sha256=_sha256(self.fixture.zip),
                output=existing,
            )
        symlink = self.fixture.external / "output-link.json"
        try:
            symlink.symlink_to(existing)
        except OSError:
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(validator.SourceReleaseArchiveError):
            validator.validate_archive_path(
                repository=self.fixture.repository,
                archive=self.fixture.zip,
                media_type=validator.MEDIA_TYPE_ZIP,
                expected_sha256=_sha256(self.fixture.zip),
                output=symlink,
            )

    def test_incomplete_publication_residue_is_preserved(self) -> None:
        output = self.fixture.external / "incomplete-publication.json"
        with mock.patch.object(validator.os, "write", return_value=0):
            with self.assertRaisesRegex(
                validator.SourceReleaseArchiveError,
                "incomplete.*preserved",
            ):
                validator.validate_archive_path(
                    repository=self.fixture.repository,
                    archive=self.fixture.zip,
                    media_type=validator.MEDIA_TYPE_ZIP,
                    expected_sha256=_sha256(self.fixture.zip),
                    output=output,
                )
        self.assertTrue(output.is_file())
        self.assertEqual(output.stat().st_size, 0)

    def test_complete_publication_residue_is_preserved_on_fsync_failure(self) -> None:
        output = self.fixture.external / "fsync-publication.json"
        with mock.patch.object(validator.os, "fsync", side_effect=OSError("fault")):
            with self.assertRaisesRegex(
                validator.SourceReleaseArchiveError,
                "complete.*durability sync failed.*preserved",
            ):
                validator.validate_archive_path(
                    repository=self.fixture.repository,
                    archive=self.fixture.zip,
                    media_type=validator.MEDIA_TYPE_ZIP,
                    expected_sha256=_sha256(self.fixture.zip),
                    output=output,
                )
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 0)

    def test_schema_and_record_contract_are_closed(self) -> None:
        schema = json.loads(
            (REPOSITORY / validator.VALIDATION_SCHEMA_PATH).read_text("utf-8")
        )
        self.assertEqual(schema["$id"], validator.VALIDATION_SCHEMA_ID)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["claimBoundary"]["const"],
            validator._CLAIM_BOUNDARY,
        )
        source_properties = schema["properties"]["source"]["properties"]
        self.assertEqual(
            source_properties["memberCount"]["maximum"],
            validator.MAXIMUM_MEMBER_COUNT,
        )
        self.assertEqual(
            source_properties["regularFileCount"]["maximum"],
            validator.MAXIMUM_REGULAR_FILE_COUNT,
        )
        self.assertEqual(
            source_properties["directoryCount"]["maximum"],
            validator.MAXIMUM_DIRECTORY_COUNT,
        )
        self.assertEqual(
            _sha256(REPOSITORY / validator.VALIDATION_SCHEMA_PATH),
            validator.EXPECTED_VALIDATION_SCHEMA_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
