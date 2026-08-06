from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tool/ci/build_sherpa_aligned_android.py"
WRAPPER = REPOSITORY / "templates/android/sherpa_aligned_build.sh"
ELF_TESTS = REPOSITORY / "tool/tests/test_verify_native_libs.py"
_ELF_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_elf_test_fixture", ELF_TESTS
)
assert _ELF_SPEC is not None and _ELF_SPEC.loader is not None
ELF_FIXTURE = importlib.util.module_from_spec(_ELF_SPEC)
_ELF_SPEC.loader.exec_module(ELF_FIXTURE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(path: Path, relative: str) -> dict[str, object]:
    return {
        "path": relative,
        "sizeBytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


class SherpaAlignedBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.abi = "arm64-v8a"
        self.repository_url = "https://example.invalid/sherpa-onnx"
        self.source = self.root / "source"
        self.source.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Fonix fixture")
        self._git("config", "user.email", "fixture@fonix.invalid")
        self._git("remote", "add", "origin", self.repository_url)

        fixture_directory = self.source / "fixtures" / self.abi
        fixture_directory.mkdir(parents=True)
        self.jni_source = fixture_directory / "libsherpa-onnx-jni.so"
        self.jni_source.write_bytes(
            ELF_FIXTURE.synthetic_elf(
                self.abi,
                soname="libsherpa-onnx-jni.so",
                needed=("libonnxruntime.so", "libc.so"),
            )
        )
        self.marker = self.source / "build-mode.txt"
        self.marker.write_text("UNPATCHED\n", encoding="utf-8")
        self.build_script = self.source / "build-fixture.sh"
        self.build_script.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
test "$BUILD_SHARED_LIBS" = ON
test "$SHERPA_ONNX_ENABLE_JNI" = ON
test "$SHERPA_ONNX_ENABLE_C_API" = OFF
test "$SHERPA_ONNX_DOWNLOAD_ONNXRUNTIME" = OFF
test "$SHERPA_ONNX_USE_PRECOMPILED_ONNXRUNTIME_IF_AVAILABLE" = OFF
test "$FETCHCONTENT_FULLY_DISCONNECTED" = ON
test -f "$SHERPA_ONNXRUNTIME_INCLUDE_DIR/onnxruntime_c_api.h"
test -f "$SHERPA_ONNXRUNTIME_LIB_DIR/libonnxruntime.so"
grep -qx PATCHED build-mode.txt
mkdir -p "$(dirname "$FONIX_SHERPA_JNI_OUTPUT")"
cp "fixtures/$ANDROID_ABI/libsherpa-onnx-jni.so" "$FONIX_SHERPA_JNI_OUTPUT"
if [[ -f emit-default-ort ]]; then
  cp "$SHERPA_ONNXRUNTIME_LIB_DIR/libonnxruntime.so" libonnxruntime.so
fi
""",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-q", "-m", "fixture source")
        self.revision = self._git("rev-parse", "HEAD").stdout.strip()

        self.marker.write_text("PATCHED\n", encoding="utf-8")
        patch_bytes = self._git("diff", "--binary", binary=True).stdout
        self.patch = self.root / "aligned.patch"
        self.patch.write_bytes(patch_bytes)
        self._git("checkout", "--", "build-mode.txt")

        self.ndk = self.root / "ndk"
        self.ndk_revision = "30.0.10000000"
        self.host_tag = "darwin-x86_64"
        self.source_properties = self.ndk / "source.properties"
        self.clang_relative = (
            f"toolchains/llvm/prebuilt/{self.host_tag}/bin/clang"
        )
        self.clang = self.ndk / self.clang_relative
        self.toolchain_relative = "build/cmake/android.toolchain.cmake"
        self.toolchain = self.ndk / self.toolchain_relative
        self.clang.parent.mkdir(parents=True)
        self.toolchain.parent.mkdir(parents=True)
        self.source_properties.write_text(
            f"Pkg.Revision = {self.ndk_revision}\n", encoding="utf-8"
        )
        self.clang.write_bytes(b"synthetic pinned clang\n")
        self.toolchain.write_bytes(b"# synthetic pinned toolchain\n")

        self.ort_root = self.root / "ort"
        self.header = self.ort_root / "include/onnxruntime_c_api.h"
        self.library = self.ort_root / "lib/libonnxruntime.so"
        self.header.parent.mkdir(parents=True)
        self.library.parent.mkdir(parents=True)
        self.header.write_bytes(b"#define ORT_API_VERSION 27\n")
        self.library.write_bytes(
            ELF_FIXTURE.synthetic_elf(
                self.abi,
                soname="libonnxruntime.so",
                needed=("libdl.so", "libc.so"),
            )
        )
        self.plan_path = self.root / "plan.json"
        self.plan = self._base_plan()
        self._write_plan()

    def _git(
        self, *arguments: str, binary: bool = False
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.source), *arguments],
            check=True,
            capture_output=True,
            text=not binary,
        )

    def _base_plan(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "sherpa": {
                "repository": self.repository_url,
                "revision": self.revision,
                "buildsByAbi": {
                    self.abi: {
                        "script": "build-fixture.sh",
                        "output": "build/jni/libsherpa-onnx-jni.so",
                    }
                },
            },
            "android": {
                "minimumApi": 24,
                "ndk": {
                    "revision": self.ndk_revision,
                    "hostTag": self.host_tag,
                    "sourcePropertiesSha256": _sha256(self.source_properties),
                    "clangPath": self.clang_relative,
                    "clangSha256": _sha256(self.clang),
                    "cmakeToolchainPath": self.toolchain_relative,
                    "cmakeToolchainSha256": _sha256(self.toolchain),
                },
            },
            "onnxRuntime": {
                "version": "1.27.1",
                "requiredApi": 27,
                "byAbi": {
                    self.abi: {
                        "artifactId": "synthetic-ort-arm64-v8a",
                        "includeDir": "include",
                        "headers": [
                            _file_record(self.header, "include/onnxruntime_c_api.h")
                        ],
                        "library": _file_record(
                            self.library, "lib/libonnxruntime.so"
                        ),
                    }
                },
            },
            "patches": [
                {
                    "id": "force-external-ort",
                    "fileName": self.patch.name,
                    "sizeBytes": self.patch.stat().st_size,
                    "sha256": _sha256(self.patch),
                }
            ],
            "qnn": None,
        }

    def _write_plan(self) -> None:
        self.plan_path.write_text(
            json.dumps(self.plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _arguments(
        self,
        *,
        suffix: str = "first",
        include_qnn: bool = False,
        use_wrapper: bool = False,
    ) -> list[str]:
        executable = ["bash", str(WRAPPER)] if use_wrapper else [sys.executable, str(SCRIPT)]
        result = [
            *executable,
            "--plan",
            str(self.plan_path),
            "--sherpa-source",
            str(self.source),
            "--ndk-root",
            str(self.ndk),
            "--ort",
            f"{self.abi}={self.ort_root}",
            "--patch",
            f"force-external-ort={self.patch}",
            "--work-dir",
            str(self.root / f"work-{suffix}"),
            "--output",
            str(self.root / f"receipt-{suffix}.json"),
        ]
        if include_qnn:
            result.extend(
                (
                    "--qnn-manifest",
                    str(self.qnn_manifest),
                    "--qnn-root",
                    str(self.qnn_root),
                )
            )
        return result

    def _run(self, **arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._arguments(**arguments),
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )

    def _commit_source_change(self, message: str) -> None:
        self._git("add", ".")
        self._git("commit", "-q", "-m", message)
        self.revision = self._git("rev-parse", "HEAD").stdout.strip()
        self.plan["sherpa"]["revision"] = self.revision
        self._write_plan()

    def _configure_qnn(self) -> None:
        self.qnn_root = self.root / "qnn"
        backend = self.qnn_root / f"lib/{self.abi}/libQnnHtp.so"
        notice = self.qnn_root / "LICENSE.txt"
        backend.parent.mkdir(parents=True)
        backend.write_bytes(b"synthetic QNN backend identity only\n")
        notice.write_bytes(b"Synthetic test-only notice; no proprietary terms.\n")
        manifest = {
            "schemaVersion": 1,
            "sdkId": "synthetic-qnn-sdk",
            "sdkVersion": "0.0.1-fixture",
            "source": "https://example.invalid/qnn-sdk",
            "backendId": "synthetic-htp",
            "backendVersion": "0.0.1-fixture",
            "license": {
                "id": "synthetic-license",
                "spdxId": "LicenseRef-Synthetic",
                "redistribution": "prohibited",
                "notice": _file_record(notice, "LICENSE.txt"),
            },
            "artifactsByAbi": {
                self.abi: [
                    {
                        "role": "backend",
                        **_file_record(backend, f"lib/{self.abi}/libQnnHtp.so"),
                        "licenseId": "synthetic-license",
                    }
                ]
            },
        }
        self.qnn_manifest = self.root / "qnn-manifest.json"
        self.qnn_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.plan["qnn"] = {
            "manifestSha256": _sha256(self.qnn_manifest),
            "backendId": "synthetic-htp",
        }
        self._write_plan()

    def test_builds_exact_inputs_and_emits_reproducible_receipt(self) -> None:
        first = self._run(use_wrapper=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = (self.root / "receipt-first.json").read_bytes()
        receipt = json.loads(first_bytes)
        self.assertEqual(receipt["result"], "passed")
        self.assertEqual(receipt["onnxRuntime"]["owner"], "application")
        self.assertEqual(
            receipt["sherpa"]["buildsByAbi"][self.abi]["output"]["elf"]["needed"],
            ["libonnxruntime.so", "libc.so"],
        )
        artifact = (
            self.root
            / "work-first/artifact/jni"
            / self.abi
            / "libsherpa-onnx-jni.so"
        )
        self.assertEqual(_sha256(artifact), _sha256(self.jni_source))

        second = self._run(suffix="second")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.root / "receipt-second.json").read_bytes(), first_bytes)

    def test_rejects_patch_identity_drift(self) -> None:
        self.patch.write_bytes(self.patch.read_bytes() + b"\n")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("patch identity mismatch", result.stderr)

    def test_rejects_unavailable_exact_source_revision(self) -> None:
        self.plan["sherpa"]["revision"] = "f" * 40
        self._write_plan()
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("sherpa revision lookup failed", result.stderr)

    def test_rejects_ort_library_identity_drift(self) -> None:
        self.library.write_bytes(self.library.read_bytes() + b"drift")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("identity does not match the plan", result.stderr)

    def test_rejects_ndk_toolchain_identity_drift(self) -> None:
        self.toolchain.write_bytes(b"# drifted toolchain\n")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("NDK/toolchain identity", result.stderr)

    def test_rejects_jni_without_exact_shared_ort_dependency(self) -> None:
        self.jni_source.write_bytes(
            ELF_FIXTURE.synthetic_elf(
                self.abi,
                soname="libsherpa-onnx-jni.so",
                needed=("libc.so",),
            )
        )
        self._commit_source_change("missing ORT dependency")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly one DT_NEEDED libonnxruntime.so", result.stderr)

    def test_rejects_default_or_fallback_ort_emitted_by_build(self) -> None:
        (self.source / "emit-default-ort").write_text("yes\n", encoding="utf-8")
        self._commit_source_change("emit forbidden ORT")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("acquired or emitted a forbidden ORT", result.stderr)

    def test_qnn_is_explicit_and_hash_bound(self) -> None:
        self._configure_qnn()
        missing = self._run()
        self.assertEqual(missing.returncode, 1)
        self.assertIn("require --qnn-manifest and --qnn-root", missing.stderr)

        passed = self._run(suffix="qnn", include_qnn=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        receipt = json.loads((self.root / "receipt-qnn.json").read_bytes())
        self.assertEqual(receipt["qnn"]["backendId"], "synthetic-htp")
        self.assertEqual(
            receipt["qnn"]["license"]["redistribution"], "prohibited"
        )

        backend = self.qnn_root / f"lib/{self.abi}/libQnnHtp.so"
        backend.write_bytes(b"drifted\n")
        drifted = self._run(suffix="qnn-drift", include_qnn=True)
        self.assertEqual(drifted.returncode, 1)
        self.assertIn("identity does not match", drifted.stderr)

    def test_qnn_license_notice_is_hash_bound(self) -> None:
        self._configure_qnn()
        notice = self.qnn_root / "LICENSE.txt"
        notice.write_bytes(b"drifted notice\n")
        result = self._run(include_qnn=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("identity does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()
