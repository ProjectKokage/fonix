#!/usr/bin/env python3
"""Run one trusted Android sherpa/Fonix load-order target gate.

The runner accepts one exact, previously statically audited Release APK and
one load order.  It owns the adb observations and launch challenge, retains a
bounded raw command/log capture, joins only the closed app-owned completion
fields with independently observed host facts, and invokes the repository's
offline receipt validator.  It deliberately does not aggregate the 4/16 KiB
matrix or make an AAB runtime claim.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence
import zipfile


sys.dont_write_bytecode = True

from android_gate_common import (
    AndroidGateCommonError,
    CommandOutput,
    regular_file,
    run_bounded,
    sha256_file,
    strict_json,
    tool_environment,
)
import run_android_sherpa_reference_app_gate as staged_build_gate


APPLICATION_ID = "dev.fonix.sherpa_reference"
APPLICATION_COMPONENT = f"{APPLICATION_ID}/.MainActivity"
LOAD_ORDER_EXTRA = "dev.fonix.sherpa_reference.LOAD_ORDER"
LAUNCH_CHALLENGE_EXTRA = (
    "dev.fonix.sherpa_reference.LAUNCH_CHALLENGE_BASE64"
)
LOG_TAG = "FonixSherpaRef"
BEGIN_MARKER = "FONIX_SHERPA_COMPLETION_BEGIN"
CHUNK_MARKER = "FONIX_SHERPA_COMPLETION_CHUNK"
END_MARKER = "FONIX_SHERPA_COMPLETION_END"

ABI = "arm64-v8a"
BUILD_TYPE = "release-minified"
LOAD_ORDERS = frozenset({"dart-first", "sherpa-first"})
SHERPA_SOURCE = "https://github.com/k2-fsa/sherpa-onnx"
SHERPA_REVISION = "142807252687d81b40d6315f23470a1512a00de3"
SHERPA_VERSION = "1.13.4"
SHERPA_LIBRARY_PROFILE = "flutter-ffi"
STATIC_SNAPSHOT_DATE = "2026-08-07"
ORT_API_REQUIRED = 27
ORT_ARCHIVE_PATH = f"lib/{ABI}/libonnxruntime.so"

MAX_APK_BYTES = 512 * 1024 * 1024
MAX_ADB_BYTES = 128 * 1024 * 1024
MAX_APKANALYZER_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SMALL_FILE_BYTES = 128 * 1024 * 1024
MAX_MODEL_BYTES = 4 * 1024 * 1024 * 1024
MAX_COMPLETION_BYTES = 512 * 1024
MAX_LOGCAT_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
MAX_COMMANDS = 1024
MAX_CHALLENGE_BYTES = 1024
CHALLENGE_BYTES = 32
MAX_LOG_CHUNK_CHARACTERS = 3000
MAX_LOG_CHUNKS = (MAX_COMPLETION_BYTES + MAX_LOG_CHUNK_CHARACTERS - 1) // (
    MAX_LOG_CHUNK_CHARACTERS
)
MAX_ZIP_COMPRESSION_RATIO = staged_build_gate.MAX_ZIP_COMPRESSION_RATIO
TARGET_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.25

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SERIAL_PATTERN = re.compile(r"^[!-~]{1,256}$")

EXPECTED_PROFILE_VALUES: dict[str, object] = {
    "provider": "cpu",
    "sampleRateHz": 16000,
    "windowSamples": 512,
    "numThreads": 1,
    "thresholdMillionths": 500000,
    "minimumSpeechMilliseconds": 250,
    "minimumSilenceMilliseconds": 800,
    "maximumSpeechMilliseconds": 30000,
    "bufferMilliseconds": 60000,
}

RUNTIME_FIXTURE_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "fonix_cancellation_input.bin": ("fonix_cancellation_input",),
    "fonix_dynamic_matmul_chain.onnx": (
        "fonix_model",
        "fonix_cancellation_model",
    ),
    "fonix_reference_input.bin": ("fonix_input",),
    "fonix_reference_output.bin": ("fonix_reference_output",),
    "sherpa_synthetic_speech.wav": ("sherpa_audio",),
    "sherpa_vad_reference.json": ("sherpa_reference",),
    staged_build_gate.SHERPA_MODEL_NAME: ("sherpa_model",),
}

FIXTURE_ARGUMENTS: tuple[tuple[str, str, int], ...] = (
    ("fonix_model", "fonixModelSha256", MAX_MODEL_BYTES),
    ("fonix_input", "fonixInputSha256", MAX_SMALL_FILE_BYTES),
    (
        "fonix_reference_output",
        "fonixReferenceOutputSha256",
        MAX_SMALL_FILE_BYTES,
    ),
    (
        "fonix_cancellation_model",
        "fonixCancellationModelSha256",
        MAX_MODEL_BYTES,
    ),
    (
        "fonix_cancellation_input",
        "fonixCancellationInputSha256",
        MAX_SMALL_FILE_BYTES,
    ),
    ("sherpa_model", "sherpaModelSha256", MAX_MODEL_BYTES),
    ("sherpa_audio", "sherpaAudioSha256", MAX_SMALL_FILE_BYTES),
    ("sherpa_reference", "sherpaReferenceSha256", MAX_SMALL_FILE_BYTES),
)


class AndroidSherpaTargetGateError(RuntimeError):
    """The trusted Android target gate failed closed."""


class CompletionPending(AndroidSherpaTargetGateError):
    """The bounded log snapshot contains no complete envelope yet."""


class FileIdentity(NamedTuple):
    size_bytes: int
    sha256: str

    def to_json(self) -> dict[str, object]:
        return {"sizeBytes": self.size_bytes, "sha256": self.sha256}


class DeviceFacts(NamedTuple):
    kind: str
    model_token: str
    android_api: int
    fingerprint_sha256: str
    page_size_bytes: int


class CompletionEvidence(NamedTuple):
    payload: dict[str, Any]
    payload_sha256: str
    raw_payload: str
    uid: int
    pid: int


CommandRunner = Callable[..., CommandOutput]


def _common(error: AndroidGateCommonError) -> AndroidSherpaTargetGateError:
    return AndroidSherpaTargetGateError(str(error))


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise AndroidSherpaTargetGateError(f"{label} has an unexpected field set")


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AndroidSherpaTargetGateError(f"{label} must be an object")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise AndroidSherpaTargetGateError(f"{label} must be an integer in range")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise AndroidSherpaTargetGateError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _identity(path: Path, label: str, maximum: int) -> FileIdentity:
    try:
        size, digest = sha256_file(path, label, maximum=maximum)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if size <= 0:
        raise AndroidSherpaTargetGateError(f"{label} must not be empty")
    return FileIdentity(size, digest)


def _read_json(path: Path, label: str, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    identity = _identity(path, label, maximum)
    try:
        value = strict_json(path.read_bytes(), label, maximum=identity.size_bytes)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    return _object(value, label)


def _resolve_input(path: Path, label: str, maximum: int) -> Path:
    try:
        regular_file(path, label, maximum=maximum)
        return path.resolve(strict=True)
    except (AndroidGateCommonError, OSError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaTargetGateError(f"could not resolve {label}") from error


def _prepare_capture_directory(path: Path) -> Path:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise AndroidSherpaTargetGateError(
            "--capture-directory must be a new absolute path"
        )
    try:
        parent = path.parent.resolve(strict=True)
        metadata = parent.lstat()
    except OSError as error:
        raise AndroidSherpaTargetGateError(
            "capture-directory parent must already exist"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise AndroidSherpaTargetGateError(
            "capture-directory parent is not a non-symlink directory"
        )
    destination = parent / path.name
    try:
        destination.mkdir(mode=0o700)
    except OSError as error:
        raise AndroidSherpaTargetGateError(
            "could not create the private capture directory"
        ) from error
    return destination


def _write_new_bytes(path: Path, data: bytes) -> FileIdentity:
    if path.exists() or path.is_symlink():
        raise AndroidSherpaTargetGateError(
            f"capture output already exists: {path.name}"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AndroidSherpaTargetGateError(
                    f"could not write capture output: {path.name}"
                )
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        linked = path.lstat()
        if (
            not stat.S_ISREG(linked.st_mode)
            or metadata.st_dev != linked.st_dev
            or metadata.st_ino != linked.st_ino
            or metadata.st_size != len(data)
        ):
            raise AndroidSherpaTargetGateError(
                f"capture output changed while writing: {path.name}"
            )
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return FileIdentity(len(data), hashlib.sha256(data).hexdigest())


def _write_new_json(path: Path, value: object) -> FileIdentity:
    return _write_new_bytes(path, _canonical_json(value))


def _text_identity(value: str) -> dict[str, object]:
    data = value.encode("utf-8")
    return {
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _redacted_argv(command: Sequence[str]) -> list[str]:
    result: list[str] = []
    redact_next_serial = False
    redact_next_challenge = False
    for index, raw_value in enumerate(command):
        value = str(raw_value)
        if redact_next_serial:
            result.append("<adb-serial>")
            redact_next_serial = False
            continue
        if redact_next_challenge:
            result.append("<launch-challenge-base64>")
            redact_next_challenge = False
            continue
        if value == "-s" and index == 1:
            result.append(value)
            redact_next_serial = True
            continue
        if value == LAUNCH_CHALLENGE_EXTRA:
            result.append(value)
            redact_next_challenge = True
            continue
        if value.startswith("/data/app/"):
            result.append("<installed-base-apk-path>")
            continue
        if os.path.isabs(value):
            result.append(Path(value).name if index == 0 else "<absolute-path>")
            continue
        result.append(value)
    if redact_next_serial or redact_next_challenge:
        raise AndroidSherpaTargetGateError(
            "command transcript encountered an incomplete redaction pair"
        )
    return result


class CapturingRunner:
    """Capture publishable command metadata while retaining one execution owner."""

    def __init__(self, delegate: CommandRunner) -> None:
        self._delegate = delegate
        self._entries: list[dict[str, object]] = []
        self._captured_bytes = 0

    @property
    def entries(self) -> list[dict[str, object]]:
        return list(self._entries)

    def __call__(
        self,
        command: Sequence[str],
        *,
        operation: str,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 30 * 60,
        maximum_output: int = MAX_LOGCAT_BYTES,
    ) -> CommandOutput:
        if len(self._entries) >= MAX_COMMANDS:
            raise AndroidSherpaTargetGateError("target gate exceeded its command bound")
        try:
            output = self._delegate(
                command,
                operation=operation,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                maximum_output=maximum_output,
            )
        except AndroidGateCommonError as error:
            raise _common(error) from error
        if not isinstance(output, CommandOutput):
            try:
                output = CommandOutput(output.stdout, output.stderr)
            except (AttributeError, TypeError) as error:
                raise AndroidSherpaTargetGateError(
                    f"{operation} returned an invalid command result"
                ) from error
        entry = {
            "ordinal": len(self._entries) + 1,
            "operation": operation,
            "argv": _redacted_argv(command),
            "timeoutSeconds": timeout_seconds,
            "maximumOutputBytes": maximum_output,
            "stdout": _text_identity(output.stdout),
            "stderr": _text_identity(output.stderr),
        }
        encoded_size = len(_canonical_json(entry))
        if encoded_size > MAX_TRANSCRIPT_BYTES - self._captured_bytes:
            raise AndroidSherpaTargetGateError(
                "target gate command transcript exceeds its byte bound"
            )
        self._captured_bytes += encoded_size
        self._entries.append(entry)
        return output


def _execute(
    runner: CapturingRunner,
    command: Sequence[str],
    *,
    operation: str,
    maximum_output: int = 64 * 1024,
    timeout_seconds: int = 120,
) -> str:
    output = runner(
        command,
        operation=operation,
        timeout_seconds=timeout_seconds,
        maximum_output=maximum_output,
        environment=tool_environment(),
    )
    if output.stderr:
        raise AndroidSherpaTargetGateError(
            f"{operation} unexpectedly wrote to stderr"
        )
    return output.stdout


def _adb(
    runner: CapturingRunner,
    adb: Path,
    serial: str,
    arguments: Sequence[str],
    operation: str,
    *,
    maximum_output: int = 64 * 1024,
) -> str:
    return _execute(
        runner,
        (str(adb), "-s", serial, *arguments),
        operation=operation,
        maximum_output=maximum_output,
    )


def _single_line(output: str, label: str, *, maximum: int = 1024) -> str:
    if "\r" in output or "\x00" in output:
        raise AndroidSherpaTargetGateError(f"{label} contains invalid controls")
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise AndroidSherpaTargetGateError(f"{label} is not one non-empty line")
    value = lines[0]
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise AndroidSherpaTargetGateError(f"{label} is not valid UTF-8") from error
    if len(encoded) > maximum or any(character < 0x20 for character in encoded):
        raise AndroidSherpaTargetGateError(f"{label} is outside its byte bound")
    return value


def _empty_output(output: str, label: str) -> None:
    if output not in {"", "\n", "\r\n"}:
        raise AndroidSherpaTargetGateError(f"{label} response is not empty")


def _positive_int(output: str, label: str, maximum: int) -> int:
    value = _single_line(output, label, maximum=32)
    if re.fullmatch(r"[1-9][0-9]{0,9}", value) is None:
        raise AndroidSherpaTargetGateError(f"{label} is not a canonical integer")
    result = int(value)
    if result > maximum:
        raise AndroidSherpaTargetGateError(f"{label} exceeds its bound")
    return result


def _device_inventory(output: str) -> dict[str, str]:
    if "\r" in output or "\x00" in output:
        raise AndroidSherpaTargetGateError("adb device inventory has invalid controls")
    lines = output.splitlines()
    if not lines or lines[0] != "List of devices attached":
        raise AndroidSherpaTargetGateError("adb device inventory header changed")
    inventory: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if (
            len(fields) != 2
            or SERIAL_PATTERN.fullmatch(fields[0]) is None
            or re.fullmatch(r"[a-z]{1,32}", fields[1]) is None
            or fields[0] in inventory
        ):
            raise AndroidSherpaTargetGateError("adb device inventory is not closed")
        inventory[fields[0]] = fields[1]
    return inventory


def _model_token(raw: str) -> str:
    if not raw or len(raw.encode("utf-8")) > 512:
        raise AndroidSherpaTargetGateError("device model is outside its byte bound")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("._-")
    if not normalized or not normalized[0].isalnum():
        normalized = f"model-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"
    if len(normalized) > 128:
        suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        normalized = f"{normalized[:111]}-{suffix}"
    if TOKEN_PATTERN.fullmatch(normalized) is None:
        raise AndroidSherpaTargetGateError("device model cannot form a bounded token")
    return normalized


def _query_device_facts(
    runner: CapturingRunner, adb: Path, serial: str
) -> DeviceFacts:
    inventory = _device_inventory(
        _execute(
            runner,
            (str(adb), "devices"),
            operation="adb device inventory",
        )
    )
    if inventory.get(serial) != "device":
        raise AndroidSherpaTargetGateError(
            "selected adb serial is not uniquely available in device state"
        )
    abi = _single_line(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "getprop", "ro.product.cpu.abi"),
            "device ABI query",
        ),
        "device ABI",
    )
    if abi != ABI:
        raise AndroidSherpaTargetGateError(
            f"target ABI is {abi!r}, expected the audited {ABI} APK"
        )
    api = _positive_int(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "getprop", "ro.build.version.sdk"),
            "device API query",
        ),
        "device API",
        100,
    )
    if api < 24:
        raise AndroidSherpaTargetGateError("device API is below the package floor")
    page_size = _positive_int(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "getconf", "PAGE_SIZE"),
            "device page-size query",
        ),
        "device page size",
        64 * 1024,
    )
    if page_size not in {4096, 16 * 1024}:
        raise AndroidSherpaTargetGateError(
            "device page size is outside the 4/16 KiB contract"
        )
    model = _single_line(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "getprop", "ro.product.model"),
            "device model query",
        ),
        "device model",
        maximum=512,
    )
    fingerprint = _single_line(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "getprop", "ro.build.fingerprint"),
            "device fingerprint query",
        ),
        "device fingerprint",
        maximum=1024,
    )
    qemu_output = _adb(
        runner,
        adb,
        serial,
        ("shell", "getprop", "ro.kernel.qemu"),
        "device-kind query",
    )
    if "\r" in qemu_output or "\x00" in qemu_output:
        raise AndroidSherpaTargetGateError(
            "device-kind property contains invalid controls"
        )
    qemu = qemu_output.rstrip("\n")
    if qemu not in {"", "0", "1"}:
        raise AndroidSherpaTargetGateError(
            "device-kind property is outside the closed set"
        )
    return DeviceFacts(
        "emulator" if qemu == "1" else "physical",
        _model_token(model),
        api,
        hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
        page_size,
    )


def _parse_package_uid(output: str) -> int:
    value = _single_line(output, "package UID response", maximum=512)
    match = re.fullmatch(
        rf"package:{re.escape(APPLICATION_ID)} uid:([1-9][0-9]{{0,9}})",
        value,
    )
    if match is None:
        raise AndroidSherpaTargetGateError(
            "installed package manager UID response is not exact"
        )
    return _integer(int(match.group(1)), "installed application UID", 10_000, 2**31 - 1)


def _parse_application_pid(output: str) -> int | None:
    value = output.rstrip("\n")
    if value == "":
        return None
    if re.fullmatch(r"[1-9][0-9]{0,9}", value) is None:
        raise AndroidSherpaTargetGateError(
            "installed application process identity is ambiguous"
        )
    return _integer(int(value), "installed application PID", 1, 2**31 - 1)


def _query_application_pid(
    runner: CapturingRunner, adb: Path, serial: str
) -> int | None:
    remote_command = f"pidof {APPLICATION_ID} 2>/dev/null || true"
    return _parse_application_pid(
        _adb(
            runner,
            adb,
            serial,
            ("shell", remote_command),
            "installed application PID query",
        )
    )


def _verify_apk_application_id(
    runner: CapturingRunner,
    apkanalyzer: Path,
    final_apk: Path,
) -> None:
    application_id = _single_line(
        _execute(
            runner,
            (
                str(apkanalyzer),
                "manifest",
                "application-id",
                str(final_apk),
            ),
            operation="audited Release APK application ID query",
            maximum_output=1024,
        ),
        "audited Release APK application ID",
        maximum=256,
    )
    if application_id != APPLICATION_ID:
        raise AndroidSherpaTargetGateError(
            "audited Release APK application ID is outside the dedicated harness"
        )


def _package_presence(output: str) -> bool:
    if output in {"", "\n"}:
        return False
    if output in {
        f"package:{APPLICATION_ID}",
        f"package:{APPLICATION_ID}\n",
    }:
        return True
    raise AndroidSherpaTargetGateError(
        "package-presence query returned an ambiguous result"
    )


def _install_succeeded(output: str) -> None:
    if "\r" in output or "\x00" in output:
        raise AndroidSherpaTargetGateError("APK install response has invalid controls")
    lines = output.splitlines()
    if lines == ["Success"]:
        return
    if (
        len(lines) == 2
        and re.fullmatch(r"Performing (?:Push|Streamed) Install", lines[0])
        and lines[1] == "Success"
    ):
        return
    raise AndroidSherpaTargetGateError(
        "APK install did not return the exact success receipt"
    )


def _parse_installed_apk_path(output: str) -> str:
    value = _single_line(output, "installed base APK path", maximum=1024)
    match = re.fullmatch(
        rf"package:(/data/app/~~[A-Za-z0-9_+=-]{{1,128}}/"
        rf"{re.escape(APPLICATION_ID)}-[A-Za-z0-9_+=-]{{1,256}}/base\.apk)",
        value,
    )
    if match is None:
        raise AndroidSherpaTargetGateError(
            "installed package does not expose one closed /data/app base APK"
        )
    return match.group(1)


def _verify_installed_apk(
    runner: CapturingRunner,
    adb: Path,
    serial: str,
    expected_sha256: str,
) -> None:
    installed_path = _parse_installed_apk_path(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "pm", "path", APPLICATION_ID),
            "installed base APK path query",
        )
    )
    output = _single_line(
        _adb(
            runner,
            adb,
            serial,
            ("shell", "sha256sum", installed_path),
            "installed base APK checksum",
        ),
        "installed base APK checksum",
        maximum=1200,
    )
    if output != f"{expected_sha256}  {installed_path}":
        raise AndroidSherpaTargetGateError(
            "installed base APK differs from the audited Release APK"
        )


def _parse_logcat_uid(value: str) -> int:
    if re.fullmatch(r"[1-9][0-9]{0,9}", value):
        uid = int(value)
    else:
        match = re.fullmatch(r"u([0-9]{1,5})_a([0-9]{1,4})", value)
        if match is None:
            raise AndroidSherpaTargetGateError(
                "completion log line has an unsupported UID identity"
            )
        uid = int(match.group(1)) * 100_000 + 10_000 + int(match.group(2))
    return _integer(uid, "completion log UID", 10_000, 2**31 - 1)


def _canonical_decimal(value: str, label: str, minimum: int, maximum: int) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise AndroidSherpaTargetGateError(f"{label} is not canonical decimal")
    return _integer(int(value), label, minimum, maximum)


def _parse_completion_log(
    output: str,
    *,
    expected_uid: int,
    expected_pid: int,
) -> CompletionEvidence:
    try:
        encoded = output.encode("ascii")
    except UnicodeEncodeError as error:
        raise AndroidSherpaTargetGateError(
            "completion logcat output is not printable ASCII"
        ) from error
    if len(encoded) > MAX_LOGCAT_BYTES:
        raise AndroidSherpaTargetGateError("completion logcat output exceeds its bound")
    if "\r" in output or "\x00" in output:
        raise AndroidSherpaTargetGateError(
            "completion logcat output contains invalid controls"
        )

    line_pattern = re.compile(
        r"^[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}"
        r"[ ]+([A-Za-z0-9_]{1,32})[ ]+([1-9][0-9]{0,9})"
        r"[ ]+([1-9][0-9]{0,9})[ ]+I[ ]+"
        + re.escape(LOG_TAG)
        + r"[ ]*: ([\x20-\x7e]+)$"
    )
    messages: list[str] = []
    observed_uid: int | None = None
    observed_pid: int | None = None
    for line in output.splitlines():
        if line == "--------- beginning of main":
            continue
        if len(line.encode("ascii")) > MAX_LOG_CHUNK_CHARACTERS + 512:
            raise AndroidSherpaTargetGateError("completion log line exceeds its bound")
        match = line_pattern.fullmatch(line)
        if match is None:
            if LOG_TAG in line or any(
                marker in line
                for marker in (BEGIN_MARKER, CHUNK_MARKER, END_MARKER)
            ):
                raise AndroidSherpaTargetGateError(
                    "completion log line lacks exact UID/PID/tag provenance"
                )
            continue
        uid = _parse_logcat_uid(match.group(1))
        pid = _integer(int(match.group(2)), "completion log PID", 1, 2**31 - 1)
        if uid != expected_uid or pid != expected_pid:
            raise AndroidSherpaTargetGateError(
                "completion log line came from a different UID or PID"
            )
        message = match.group(4)
        if not message.startswith((BEGIN_MARKER, CHUNK_MARKER, END_MARKER)):
            raise AndroidSherpaTargetGateError(
                "completion tag emitted a non-contract log line"
            )
        observed_uid = uid
        observed_pid = pid
        messages.append(message)

    if not messages:
        raise CompletionPending("completion envelope is not present yet")
    begin = messages[0].split("|")
    if len(begin) != 6 or begin[0] != BEGIN_MARKER or begin[1] != "1":
        raise AndroidSherpaTargetGateError("completion begin marker is not exact")
    status = begin[2]
    if status not in {"passed", "unavailable", "failed"}:
        raise AndroidSherpaTargetGateError(
            "completion status is outside the closed set"
        )
    if status != "passed":
        raise AndroidSherpaTargetGateError(
            f"target harness reported the non-passing status {status!r}"
        )
    payload_sha256 = _digest(begin[3], "completion payload digest")
    payload_length = _canonical_decimal(
        begin[4], "completion payload length", 1, MAX_COMPLETION_BYTES
    )
    chunk_count = _canonical_decimal(
        begin[5], "completion chunk count", 1, MAX_LOG_CHUNKS
    )
    expected_chunk_count = (
        payload_length + MAX_LOG_CHUNK_CHARACTERS - 1
    ) // MAX_LOG_CHUNK_CHARACTERS
    if chunk_count != expected_chunk_count:
        raise AndroidSherpaTargetGateError(
            "completion chunk count does not match the declared payload length"
        )

    chunks: list[str] = []
    cursor = 1
    while cursor < len(messages) and messages[cursor].startswith(
        f"{CHUNK_MARKER}|"
    ):
        parts = messages[cursor].split("|", 3)
        if len(parts) != 4 or parts[0] != CHUNK_MARKER:
            raise AndroidSherpaTargetGateError("completion chunk marker is not exact")
        if parts[1] != payload_sha256:
            raise AndroidSherpaTargetGateError("completion chunk digest changed")
        index = _canonical_decimal(
            parts[2], "completion chunk index", 0, MAX_LOG_CHUNKS - 1
        )
        if index != len(chunks):
            raise AndroidSherpaTargetGateError(
                "completion chunks are missing, duplicated, or out of order"
            )
        if not parts[3] or len(parts[3]) > MAX_LOG_CHUNK_CHARACTERS:
            raise AndroidSherpaTargetGateError(
                "completion chunk body is outside its character bound"
            )
        expected_chunk_length = min(
            MAX_LOG_CHUNK_CHARACTERS,
            payload_length - index * MAX_LOG_CHUNK_CHARACTERS,
        )
        if len(parts[3]) != expected_chunk_length:
            raise AndroidSherpaTargetGateError(
                "completion chunk body does not have its canonical length"
            )
        chunks.append(parts[3])
        cursor += 1

    if len(chunks) > chunk_count:
        raise AndroidSherpaTargetGateError("completion emitted too many chunks")
    if cursor == len(messages):
        if len(chunks) < chunk_count:
            raise CompletionPending("completion chunks are still pending")
        raise CompletionPending("completion end marker is still pending")
    if len(chunks) != chunk_count:
        raise AndroidSherpaTargetGateError(
            "completion end marker arrived before every declared chunk"
        )
    end = messages[cursor].split("|")
    if len(end) != 2 or end != [END_MARKER, payload_sha256]:
        raise AndroidSherpaTargetGateError("completion end marker is not exact")
    if cursor + 1 != len(messages):
        raise AndroidSherpaTargetGateError(
            "completion emitted more than one framed envelope"
        )

    raw_payload = "".join(chunks)
    payload_bytes = raw_payload.encode("ascii")
    if len(payload_bytes) != payload_length:
        raise AndroidSherpaTargetGateError(
            "reassembled completion payload length does not match"
        )
    if hashlib.sha256(payload_bytes).hexdigest() != payload_sha256:
        raise AndroidSherpaTargetGateError(
            "reassembled completion payload digest does not match"
        )
    try:
        payload = strict_json(
            payload_bytes,
            "target completion payload",
            maximum=MAX_COMPLETION_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    return CompletionEvidence(
        _object(payload, "target completion payload"),
        payload_sha256,
        raw_payload,
        observed_uid if observed_uid is not None else expected_uid,
        observed_pid if observed_pid is not None else expected_pid,
    )


def _validate_static_manifest(
    path: Path, apk_identity: FileIdentity
) -> tuple[dict[str, Any], FileIdentity]:
    identity = _identity(path, "Android static package manifest", MAX_JSON_BYTES)
    value = _read_json(path, "Android static package manifest")
    _exact_keys(
        value,
        {
            "schemaVersion",
            "result",
            "claimStatus",
            "snapshotDate",
            "tools",
            "sherpaOnnx",
            "android",
            "claimBoundary",
        },
        "Android static package manifest",
    )
    sherpa = _object(value["sherpaOnnx"], "static manifest sherpaOnnx")
    android = _object(value["android"], "static manifest android")
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["result"] != "passed"
        or value["claimStatus"] != "static-package-only"
        or value["snapshotDate"] != STATIC_SNAPSHOT_DATE
        or sherpa.get("source") != SHERPA_SOURCE
        or sherpa.get("revision") != SHERPA_REVISION
        or sherpa.get("provenanceBinding")
        != "caller-declared; enclosing-gate-required"
        or sherpa.get("libraryProfile") != SHERPA_LIBRARY_PROFILE
        or android.get("integrationMode") != "sherpa-owned"
        or android.get("buildType") != BUILD_TYPE
        or android.get("buildTypeBinding")
        != "caller-declared; enclosing-gate-required"
        or android.get("abis") != [ABI]
        or android.get("ortOwner") != "sherpa"
        or type(android.get("ortApiRequired")) is not int
        or android.get("ortApiRequired") != ORT_API_REQUIRED
    ):
        raise AndroidSherpaTargetGateError(
            "static package manifest does not describe the closed Release APK gate"
        )
    artifacts = _object(android.get("artifacts"), "static manifest artifacts")
    final_apk = _object(artifacts.get("finalApk"), "static manifest final APK")
    _exact_keys(
        final_apk,
        {"fileName", "kind", "sizeBytes", "sha256", "digestScope"},
        "static manifest final APK",
    )
    if (
        final_apk["kind"] != "apk"
        or type(final_apk["sizeBytes"]) is not int
        or final_apk["sizeBytes"] != apk_identity.size_bytes
        or final_apk["sha256"] != apk_identity.sha256
        or final_apk["digestScope"] != "archive-bytes-v1"
    ):
        raise AndroidSherpaTargetGateError(
            "static package manifest is not bound to the supplied exact APK"
        )
    return value, identity


def _identity_record(
    value: object,
    label: str,
    *,
    maximum: int,
) -> FileIdentity:
    record = _object(value, label)
    _exact_keys(record, {"sizeBytes", "sha256"}, label)
    size = _integer(record["sizeBytes"], f"{label}.sizeBytes", 1, maximum)
    return FileIdentity(size, _digest(record["sha256"], f"{label}.sha256"))


def _bounded_text(value: object, label: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value:
        raise AndroidSherpaTargetGateError(f"{label} must be non-empty text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise AndroidSherpaTargetGateError(f"{label} is not valid UTF-8") from error
    if len(encoded) > maximum or "\x00" in value:
        raise AndroidSherpaTargetGateError(f"{label} exceeds its text bound")
    return value


def _staged_source_record(value: object, label: str) -> dict[str, Any]:
    record = _object(value, label)
    _exact_keys(
        record,
        {"entryCount", "fileCount", "byteCount", "sha256"},
        label,
    )
    _integer(record["entryCount"], f"{label}.entryCount", 1, 1_000_000)
    _integer(record["fileCount"], f"{label}.fileCount", 1, 100_000)
    _integer(
        record["byteCount"],
        f"{label}.byteCount",
        1,
        staged_build_gate.MAX_TEMPLATE_BYTES
        + staged_build_gate.MAX_RUNTIME_FIXTURE_TOTAL_BYTES,
    )
    _digest(record["sha256"], f"{label}.sha256")
    return record


def _apk_runtime_fixture_identities(path: Path) -> dict[str, FileIdentity]:
    expected_names = set(staged_build_gate.RUNTIME_FIXTURE_NAMES)
    observed: dict[str, FileIdentity] = {}
    prefix = staged_build_gate.APK_RUNTIME_ASSET_PREFIX
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.startswith(prefix):
                    continue
                name = info.filename[len(prefix) :]
                if (
                    name not in expected_names
                    or name in observed
                    or info.is_dir()
                    or info.file_size <= 0
                    or info.file_size > MAX_MODEL_BYTES
                    or info.flag_bits & 0x1
                    or (info.compress_size == 0 and info.file_size != 0)
                    or (
                        info.compress_size > 0
                        and info.file_size
                        > info.compress_size * MAX_ZIP_COMPRESSION_RATIO
                    )
                ):
                    raise AndroidSherpaTargetGateError(
                        "audited APK runtime fixture inventory is not closed"
                    )
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > info.file_size or size > MAX_MODEL_BYTES:
                            raise AndroidSherpaTargetGateError(
                                "audited APK runtime fixture exceeds its bound"
                            )
                        digest.update(chunk)
                if size != info.file_size:
                    raise AndroidSherpaTargetGateError(
                        "audited APK runtime fixture changed while it was read"
                    )
                observed[name] = FileIdentity(size, digest.hexdigest())
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, AndroidSherpaTargetGateError):
            raise
        raise AndroidSherpaTargetGateError(
            "could not inspect audited APK runtime fixtures"
        ) from error
    if set(observed) != expected_names:
        raise AndroidSherpaTargetGateError(
            "audited APK does not contain the exact runtime fixture inventory"
        )
    return observed


def _validate_static_gate_report(
    report_path: Path,
    static_manifest_path: Path,
    *,
    apk_identity: FileIdentity,
    pubspec_identity: FileIdentity,
    fixture_identities: Mapping[str, FileIdentity],
    runtime_fixture_identities: Mapping[str, FileIdentity],
) -> tuple[FileIdentity, FileIdentity, FileIdentity]:
    report_identity = _identity(
        report_path,
        "Android staged-build gate report",
        staged_build_gate.MAX_REPORT_BYTES,
    )
    report = _read_json(
        report_path,
        "Android staged-build gate report",
        staged_build_gate.MAX_REPORT_BYTES,
    )
    _exact_keys(
        report,
        {
            "schemaVersion",
            "result",
            "mode",
            "abi",
            "buildType",
            "sourceManifestSha256",
            "sourceCopy",
            "stagedSource",
            "flutterRevision",
            "flutterVersion",
            "android",
            "java",
            "pubspecLock",
            "sherpaOnnx",
            "rawFonixShim",
            "releaseApk",
            "releaseAab",
            "rawNativeAudit",
            "staticPackageManifest",
            "targetEvidence",
            "claimBoundary",
            "runtimeFixtures",
        },
        "Android staged-build gate report",
    )
    source_manifest = (
        Path(__file__).resolve().parents[2] / staged_build_gate.MANIFEST
    )
    source_manifest_identity = _identity(
        source_manifest,
        "Fonix source checksum manifest",
        staged_build_gate.MAX_SOURCE_MANIFEST_BYTES,
    )
    if (
        type(report["schemaVersion"]) is not int
        or report["schemaVersion"] != 1
        or report["result"] != "passed"
        or report["mode"] != "runtime-provisioned"
        or report["abi"] != ABI
        or report["buildType"] != BUILD_TYPE
        or report["sourceManifestSha256"] != source_manifest_identity.sha256
        or report["flutterRevision"]
        != staged_build_gate.VALIDATED_FLUTTER_REVISION
        or report["targetEvidence"] is not None
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build gate report does not describe the exact runtime build"
        )
    _bounded_text(report["flutterVersion"], "staged-build Flutter version", 128)
    _bounded_text(report["claimBoundary"], "staged-build claim boundary", 8192)

    source_copy = _object(report["sourceCopy"], "staged-build sourceCopy")
    _exact_keys(source_copy, {"fileCount", "byteCount"}, "staged-build sourceCopy")
    _integer(source_copy["fileCount"], "sourceCopy.fileCount", 1, 100_000)
    _integer(
        source_copy["byteCount"],
        "sourceCopy.byteCount",
        1,
        staged_build_gate.MAX_TEMPLATE_BYTES,
    )

    staged_source = _object(report["stagedSource"], "staged-build stagedSource")
    _exact_keys(
        staged_source,
        {"manifestSchema", "excludedGeneratedPaths", "hostTest", "androidBuild"},
        "staged-build stagedSource",
    )
    expected_exclusions = sorted(staged_build_gate.GENERATED_EXCLUSIONS) + [
        "android/**/*.iml"
    ]
    if (
        type(staged_source["manifestSchema"]) is not int
        or staged_source["manifestSchema"] != 1
        or staged_source["excludedGeneratedPaths"] != expected_exclusions
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build source identity contract changed"
        )
    host_source = _staged_source_record(
        staged_source["hostTest"], "staged-build host source"
    )
    android_source = _staged_source_record(
        staged_source["androidBuild"], "staged-build Android source"
    )
    if host_source["sha256"] == android_source["sha256"]:
        raise AndroidSherpaTargetGateError(
            "staged-build host and Android source identities are ambiguous"
        )

    android = _object(report["android"], "staged-build Android toolchain")
    _exact_keys(
        android,
        {"compileApi", "buildToolsVersion", "ndkVersion"},
        "staged-build Android toolchain",
    )
    expected_android = {
        "compileApi": staged_build_gate.ANDROID_COMPILE_API,
        "buildToolsVersion": staged_build_gate.ANDROID_BUILD_TOOLS_VERSION,
        "ndkVersion": staged_build_gate.ANDROID_NDK_VERSION,
    }
    if not _strict_equal(android, expected_android):
        raise AndroidSherpaTargetGateError(
            "staged-build Android toolchain identity changed"
        )
    java = _object(report["java"], "staged-build Java toolchain")
    _exact_keys(java, {"version", "vendor"}, "staged-build Java toolchain")
    if java["version"] != staged_build_gate.SUPPORTED_JAVA_VERSION:
        raise AndroidSherpaTargetGateError(
            "staged-build Java version identity changed"
        )
    _bounded_text(java["vendor"], "staged-build Java vendor", 256)

    lock = _object(report["pubspecLock"], "staged-build pubspecLock")
    _exact_keys(
        lock,
        {"committedSha256", "stagedSha256"},
        "staged-build pubspecLock",
    )
    _digest(lock["committedSha256"], "committed sherpa reference lock")
    if lock["stagedSha256"] != pubspec_identity.sha256:
        raise AndroidSherpaTargetGateError(
            "staged-build gate report is not bound to the supplied pubspec.lock"
        )

    sherpa = _object(report["sherpaOnnx"], "staged-build sherpaOnnx")
    _exact_keys(
        sherpa,
        {
            "source",
            "revision",
            "version",
            "package",
            "libraryProfile",
            "hostedPackageGuard",
            "nativeInputs",
        },
        "staged-build sherpaOnnx",
    )
    if (
        sherpa["source"] != SHERPA_SOURCE
        or sherpa["revision"] != SHERPA_REVISION
        or sherpa["version"] != SHERPA_VERSION
        or sherpa["package"] != staged_build_gate.SHERPA_PACKAGE
        or sherpa["libraryProfile"] != SHERPA_LIBRARY_PROFILE
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build sherpa identity changed"
        )
    hosted = _object(
        sherpa["hostedPackageGuard"], "staged-build hosted package guard"
    )
    _exact_keys(
        hosted,
        {"threatModel", "treeSchema", "androidPluginPackages", "packages"},
        "staged-build hosted package guard",
    )
    if (
        hosted["threatModel"] != "non-hostile-local-build"
        or type(hosted["treeSchema"]) is not int
        or hosted["treeSchema"] != 1
        or hosted["androidPluginPackages"]
        != sorted(staged_build_gate.SHERPA_ANDROID_PACKAGES)
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build hosted package guard changed"
        )
    package_entries = hosted["packages"]
    if not isinstance(package_entries, list) or len(package_entries) != len(
        staged_build_gate.SHERPA_HOSTED_PACKAGE_PINS
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build hosted package inventory is incomplete"
        )
    expected_package_names = sorted(staged_build_gate.SHERPA_HOSTED_PACKAGE_PINS)
    for entry, name in zip(package_entries, expected_package_names, strict=True):
        package = _object(entry, f"staged-build hosted package {name}")
        _exact_keys(
            package,
            {
                "name",
                "version",
                "archiveSha256",
                "directoryCount",
                "fileCount",
                "byteCount",
                "treeSha256",
            },
            f"staged-build hosted package {name}",
        )
        pin = staged_build_gate.SHERPA_HOSTED_PACKAGE_PINS[name]
        expected_package = {
            "name": name,
            "version": SHERPA_VERSION,
            "archiveSha256": pin.archive_sha256,
            "directoryCount": pin.tree.directory_count,
            "fileCount": pin.tree.file_count,
            "byteCount": pin.tree.byte_count,
            "treeSha256": pin.tree.sha256,
        }
        if not _strict_equal(package, expected_package):
            raise AndroidSherpaTargetGateError(
                "staged-build hosted package identity changed"
            )

    native_inputs = sherpa["nativeInputs"]
    expected_native_names = sorted(staged_build_gate.SHERPA_NATIVE_SHA256)
    if not isinstance(native_inputs, list) or len(native_inputs) != len(
        expected_native_names
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build sherpa native inventory is incomplete"
        )
    for entry, name in zip(native_inputs, expected_native_names, strict=True):
        native = _object(entry, f"staged-build sherpa native input {name}")
        _exact_keys(
            native,
            {"abi", "fileName", "sizeBytes", "sha256"},
            f"staged-build sherpa native input {name}",
        )
        if (
            native["abi"] != ABI
            or native["fileName"] != name
            or native["sha256"] != staged_build_gate.SHERPA_NATIVE_SHA256[name]
        ):
            raise AndroidSherpaTargetGateError(
                "staged-build sherpa native input identity changed"
            )
        _integer(
            native["sizeBytes"],
            f"staged-build sherpa native input {name}.sizeBytes",
            1,
            staged_build_gate.MAX_SHERPA_LIBRARY_BYTES,
        )

    _identity_record(
        report["rawFonixShim"],
        "staged-build raw Fonix shim",
        maximum=staged_build_gate.MAX_SHIM_BYTES,
    )
    if _identity_record(
        report["releaseApk"],
        "staged-build Release APK",
        maximum=MAX_APK_BYTES,
    ) != apk_identity:
        raise AndroidSherpaTargetGateError(
            "staged-build gate report is not bound to the supplied exact APK"
        )
    _identity_record(
        report["releaseAab"],
        "staged-build Release AAB",
        maximum=MAX_APK_BYTES,
    )
    raw_audit = _object(report["rawNativeAudit"], "staged-build raw native audit")
    _exact_keys(
        raw_audit,
        {"schema", "policy", "sizeBytes", "sha256"},
        "staged-build raw native audit",
    )
    if (
        type(raw_audit["schema"]) is not int
        or raw_audit["schema"] != 4
        or raw_audit["policy"] != "sherpa-audit"
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build raw native audit identity changed"
        )
    _integer(
        raw_audit["sizeBytes"],
        "staged-build raw native audit sizeBytes",
        1,
        staged_build_gate.MAX_AUDIT_BYTES,
    )
    _digest(raw_audit["sha256"], "staged-build raw native audit sha256")

    static_manifest, static_manifest_identity = _validate_static_manifest(
        static_manifest_path, apk_identity
    )
    static_report = _object(
        report["staticPackageManifest"],
        "staged-build staticPackageManifest",
    )
    _exact_keys(
        static_report,
        {"sizeBytes", "sha256", "record"},
        "staged-build staticPackageManifest",
    )
    if (
        type(static_report["sizeBytes"]) is not int
        or static_report["sizeBytes"] != static_manifest_identity.size_bytes
        or static_report["sha256"] != static_manifest_identity.sha256
        or not _strict_equal(static_report["record"], static_manifest)
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build report is not bound to the exact static manifest bytes"
        )

    runtime_entries = report["runtimeFixtures"]
    if not isinstance(runtime_entries, list) or len(runtime_entries) != len(
        staged_build_gate.RUNTIME_FIXTURE_NAMES
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build runtime fixture inventory is incomplete"
        )
    reported_runtime: dict[str, FileIdentity] = {}
    for entry, name in zip(
        runtime_entries, staged_build_gate.RUNTIME_FIXTURE_NAMES, strict=True
    ):
        fixture = _object(entry, f"staged-build runtime fixture {name}")
        _exact_keys(
            fixture,
            {"fileName", "sizeBytes", "sha256"},
            f"staged-build runtime fixture {name}",
        )
        if fixture["fileName"] != name:
            raise AndroidSherpaTargetGateError(
                "staged-build runtime fixture ordering or name changed"
            )
        reported_runtime[name] = FileIdentity(
            _integer(
                fixture["sizeBytes"],
                f"staged-build runtime fixture {name}.sizeBytes",
                1,
                MAX_MODEL_BYTES,
            ),
            _digest(
                fixture["sha256"],
                f"staged-build runtime fixture {name}.sha256",
            ),
        )
    if reported_runtime != dict(runtime_fixture_identities):
        raise AndroidSherpaTargetGateError(
            "staged-build report differs from the exact APK runtime fixtures"
        )
    argument_identities = {
        argument_name: fixture_identities[receipt_key]
        for argument_name, receipt_key, _maximum in FIXTURE_ARGUMENTS
    }
    for name, argument_names in RUNTIME_FIXTURE_ARGUMENTS.items():
        for argument_name in argument_names:
            if argument_identities[argument_name] != reported_runtime[name]:
                raise AndroidSherpaTargetGateError(
                    "staged-build report is not bound to every supplied runtime fixture"
                )
    return (
        report_identity,
        static_manifest_identity,
        source_manifest_identity,
    )


def _apk_ort_identity(path: Path) -> FileIdentity:
    try:
        with zipfile.ZipFile(path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if info.filename == ORT_ARCHIVE_PATH
            ]
            if len(matches) != 1:
                raise AndroidSherpaTargetGateError(
                    "audited APK must contain exactly one arm64 libonnxruntime.so"
                )
            info = matches[0]
            if (
                info.is_dir()
                or info.file_size <= 0
                or info.file_size > MAX_SMALL_FILE_BYTES
                or info.flag_bits & 0x1
                or (
                    info.compress_size == 0
                    and info.file_size != 0
                )
                or (
                    info.compress_size > 0
                    and info.file_size > info.compress_size * MAX_ZIP_COMPRESSION_RATIO
                )
            ):
                raise AndroidSherpaTargetGateError(
                    "packaged libonnxruntime.so is outside its archive bounds"
                )
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > info.file_size or size > MAX_SMALL_FILE_BYTES:
                        raise AndroidSherpaTargetGateError(
                            "packaged libonnxruntime.so exceeds its declared bound"
                        )
                    digest.update(chunk)
            if size != info.file_size:
                raise AndroidSherpaTargetGateError(
                    "packaged libonnxruntime.so changed while it was read"
                )
            return FileIdentity(size, digest.hexdigest())
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, AndroidSherpaTargetGateError):
            raise
        raise AndroidSherpaTargetGateError(
            "could not inspect packaged libonnxruntime.so"
        ) from error


def _fixture_identities(arguments: argparse.Namespace) -> dict[str, FileIdentity]:
    return {
        receipt_key: _identity(
            getattr(arguments, argument_name),
            argument_name.replace("_", " "),
            maximum,
        )
        for argument_name, receipt_key, maximum in FIXTURE_ARGUMENTS
    }


def _validate_harness_contract(
    path: Path,
    *,
    apk_identity: FileIdentity,
    pubspec_identity: FileIdentity,
    ort_identity: FileIdentity,
    fixture_identities: Mapping[str, FileIdentity],
) -> tuple[dict[str, Any], FileIdentity]:
    identity = _identity(path, "harness contract", MAX_JSON_BYTES)
    contract = _read_json(path, "harness contract")
    _exact_keys(
        contract,
        {
            "schemaVersion",
            "applicationId",
            "finalApkSha256",
            "pubspecLockSha256",
            "profileId",
            "requestedCycles",
            "runtime",
            "fixtures",
            "cancellationModes",
        },
        "harness contract",
    )
    if (
        type(contract["schemaVersion"]) is not int
        or contract["schemaVersion"] != 1
        or contract["applicationId"] != APPLICATION_ID
        or contract["finalApkSha256"] != apk_identity.sha256
        or contract["pubspecLockSha256"] != pubspec_identity.sha256
        or not isinstance(contract["profileId"], str)
        or TOKEN_PATTERN.fullmatch(contract["profileId"]) is None
    ):
        raise AndroidSherpaTargetGateError(
            "harness contract is not bound to the exact APK/lock/profile"
        )
    _integer(contract["requestedCycles"], "requestedCycles", 2, 64)
    runtime = _object(contract["runtime"], "harness runtime")
    _exact_keys(
        runtime,
        {
            "runtimeOwner",
            "runtimeSource",
            "ortVersion",
            "requiredOrtApi",
            "negotiatedOrtApi",
            "ortSha256",
            "shimAbi",
            "shimBuildId",
        },
        "harness runtime",
    )
    version = runtime["ortVersion"]
    if not isinstance(version, str) or SEMVER_PATTERN.fullmatch(version) is None:
        raise AndroidSherpaTargetGateError("harness ORT version is not strict semver")
    version_parts = [int(part) for part in version.split(".")]
    expected_runtime = {
        "runtimeOwner": "sherpa",
        "runtimeSource": "process",
        "ortVersion": version,
        "requiredOrtApi": 27,
        "negotiatedOrtApi": 27,
        "ortSha256": ort_identity.sha256,
        "shimAbi": 1,
        "shimBuildId": "android-owner-sherpa-source-process",
    }
    if version_parts[0] != 1 or version_parts[1] < 27 or not _strict_equal(
        runtime, expected_runtime
    ):
        raise AndroidSherpaTargetGateError(
            "harness runtime does not match the inspected APK runtime"
        )
    fixtures = _object(contract["fixtures"], "harness fixtures")
    expected_fixtures = {
        key: identity.sha256 for key, identity in fixture_identities.items()
    }
    if not _strict_equal(fixtures, expected_fixtures):
        raise AndroidSherpaTargetGateError(
            "harness fixtures do not match the supplied exact files"
        )
    expected_cancellation = {
        "fonix": "active-native-termination",
        "sherpa": "between-bounded-frames",
    }
    if not _strict_equal(contract["cancellationModes"], expected_cancellation):
        raise AndroidSherpaTargetGateError(
            "harness cancellation modes are outside the closed contract"
        )
    return contract, identity


def _validate_app_payload(
    payload: dict[str, Any],
    *,
    load_order: str,
    challenge_sha256: str,
    harness_contract: Mapping[str, Any],
) -> dict[str, Any]:
    _exact_keys(
        payload,
        {
            "schemaVersion",
            "result",
            "launchChallengeSha256",
            "loadOrder",
            "runtime",
            "sherpa",
            "fixtures",
            "initialization",
            "workload",
            "lifecycle",
        },
        "app completion payload",
    )
    if (
        type(payload["schemaVersion"]) is not int
        or payload["schemaVersion"] != 1
        or payload["result"] != "passed"
        or payload["launchChallengeSha256"] != challenge_sha256
        or payload["loadOrder"] != load_order
    ):
        raise AndroidSherpaTargetGateError(
            "app completion is not bound to this exact launch"
        )
    runtime = _object(payload["runtime"], "app runtime")
    _exact_keys(
        runtime,
        {
            "runtimeOwner",
            "runtimeSource",
            "ortVersion",
            "requiredOrtApi",
            "negotiatedOrtApi",
            "shimAbi",
            "shimBuildId",
        },
        "app runtime",
    )
    expected_runtime = {
        key: value
        for key, value in _object(
            harness_contract["runtime"], "harness runtime"
        ).items()
        if key != "ortSha256"
    }
    if not _strict_equal(runtime, expected_runtime):
        raise AndroidSherpaTargetGateError(
            "app runtime observations differ from the closed harness contract"
        )
    sherpa = _object(payload["sherpa"], "app sherpa")
    _exact_keys(sherpa, {"getVersion", "getGitSha1", "profile"}, "app sherpa")
    native_revision = sherpa["getGitSha1"]
    if (
        sherpa["getVersion"] != SHERPA_VERSION
        or not isinstance(native_revision, str)
        or re.fullmatch(r"[0-9a-f]{7,40}", native_revision) is None
        or not SHERPA_REVISION.startswith(native_revision)
    ):
        raise AndroidSherpaTargetGateError(
            "app Sherpa version/revision does not match the selected source"
        )
    expected_profile = {
        "id": harness_contract["profileId"],
        **EXPECTED_PROFILE_VALUES,
    }
    if not _strict_equal(sherpa["profile"], expected_profile):
        raise AndroidSherpaTargetGateError(
            "app Sherpa profile is outside the closed harness contract"
        )
    if not _strict_equal(payload["fixtures"], harness_contract["fixtures"]):
        raise AndroidSherpaTargetGateError(
            "app fixture observations differ from the host-bound contract"
        )
    for key in ("initialization", "workload", "lifecycle"):
        _object(payload[key], f"app {key}")
    return payload


def _logcat_arguments(uid: int, pid: int) -> tuple[str, ...]:
    _integer(uid, "logcat UID filter", 10_000, 2**31 - 1)
    _integer(pid, "logcat PID filter", 1, 2**31 - 1)
    return (
        "logcat",
        "-d",
        "-b",
        "main",
        "-v",
        "threadtime,uid,printable",
        f"--uid={uid}",
        f"--pid={pid}",
        "-s",
        f"{LOG_TAG}:I",
        "*:S",
    )


def _cleanup_application(
    runner: CapturingRunner, adb: Path, serial: str
) -> None:
    errors: list[BaseException] = []
    try:
        _empty_output(
            _adb(
                runner,
                adb,
                serial,
                ("shell", "am", "force-stop", APPLICATION_ID),
                "target cleanup force-stop",
            ),
            "target cleanup force-stop",
        )
    except BaseException as error:  # cleanup must continue through every step
        errors.append(error)
    present: bool | None = None
    try:
        present = _package_presence(
            _adb(
                runner,
                adb,
                serial,
                ("shell", "cmd", "package", "list", "packages", APPLICATION_ID),
                "target cleanup package query",
            )
        )
    except BaseException as error:
        errors.append(error)
    if present is not False:
        try:
            uninstall = _single_line(
                _adb(
                    runner,
                    adb,
                    serial,
                    ("uninstall", APPLICATION_ID),
                    "target APK uninstall",
                ),
                "target APK uninstall",
                maximum=512,
            )
            if uninstall != "Success":
                raise AndroidSherpaTargetGateError(
                    "target APK uninstall did not return exact success"
                )
        except BaseException as error:
            errors.append(error)
    try:
        remaining = _package_presence(
            _adb(
                runner,
                adb,
                serial,
                ("shell", "cmd", "package", "list", "packages", APPLICATION_ID),
                "target APK removal verification",
            )
        )
        if remaining:
            raise AndroidSherpaTargetGateError(
                "target application remained installed after cleanup"
            )
    except BaseException as error:
        errors.append(error)
    if errors:
        raise AndroidSherpaTargetGateError(
            f"target application cleanup failed: {errors[0]}"
        ) from errors[0]


def _write_evidence_and_validate(
    *,
    arguments: argparse.Namespace,
    capture_directory: Path,
    runner: CapturingRunner,
    challenge: bytes,
    final_logcat: str,
    app_payload: Mapping[str, Any],
    apk_identity: FileIdentity,
    static_gate_report_identity: FileIdentity,
    static_manifest_identity: FileIdentity,
    source_manifest_identity: FileIdentity,
    adb_identity: FileIdentity,
    apkanalyzer_identity: FileIdentity,
    harness_contract: Mapping[str, Any],
    harness_identity: FileIdentity,
    pubspec_identity: FileIdentity,
    fixture_identities: Mapping[str, FileIdentity],
    device: DeviceFacts,
    uid: int,
    pid: int,
    completion: CompletionEvidence,
) -> dict[str, Any]:
    challenge_sha256 = hashlib.sha256(challenge).hexdigest()
    runtime = dict(_object(harness_contract["runtime"], "harness runtime"))
    app_sherpa = _object(app_payload["sherpa"], "app sherpa")
    sherpa = {
        "packageVersion": SHERPA_VERSION,
        "nativeVersion": app_sherpa["getVersion"],
        "sourceRevision": SHERPA_REVISION,
        "nativeRevision": app_sherpa["getGitSha1"],
        "profile": app_sherpa["profile"],
    }
    fixtures = {
        key: identity.sha256 for key, identity in fixture_identities.items()
    }
    matrix = {
        "abi": ABI,
        "loadOrder": arguments.load_order,
        "buildType": BUILD_TYPE,
        "pageSizeBytes": device.page_size_bytes,
    }
    device_json = {
        "kind": device.kind,
        "modelToken": device.model_token,
        "androidApi": device.android_api,
        "fingerprintSha256": device.fingerprint_sha256,
    }
    process = {
        "launchChallengeSha256": challenge_sha256,
        "uid": {
            "packageManager": uid,
            "logcatFilter": uid,
            "receiptLine": completion.uid,
        },
        "pid": {
            "beforeLaunch": None,
            "observedAfterLaunch": pid,
            "logcatFilter": pid,
            "receiptLine": completion.pid,
            "afterReceipt": pid,
            "afterForceStop": None,
        },
    }
    target = {
        "schemaVersion": 1,
        "result": "passed",
        "applicationId": APPLICATION_ID,
        "finalApkSha256": apk_identity.sha256,
        "harnessContractSha256": harness_identity.sha256,
        "pubspecLockSha256": pubspec_identity.sha256,
        "launchChallengeSha256": challenge_sha256,
        "matrix": matrix,
        "device": device_json,
        "fixtures": fixtures,
        "process": {"uid": process["uid"], "pid": process["pid"]},
        "runtime": runtime,
        "sherpa": sherpa,
        "initialization": app_payload["initialization"],
        "workload": app_payload["workload"],
        "lifecycle": app_payload["lifecycle"],
    }
    target_bytes = _canonical_json(target)
    target_sha256 = hashlib.sha256(target_bytes).hexdigest()
    logcat = {
        "schemaVersion": 1,
        "result": "passed",
        "applicationId": APPLICATION_ID,
        "deviceFingerprintSha256": device.fingerprint_sha256,
        "finalApkSha256": apk_identity.sha256,
        "harnessContractSha256": harness_identity.sha256,
        "fixtures": fixtures,
        "targetEvidenceSha256": target_sha256,
        "launchChallengeSha256": challenge_sha256,
        "uid": completion.uid,
        "pid": completion.pid,
    }
    logcat_bytes = _canonical_json(logcat)
    logcat_sha256 = hashlib.sha256(logcat_bytes).hexdigest()
    receipt = {
        "schemaVersion": 2,
        "result": "passed",
        "matrix": matrix,
        "build": {
            "applicationId": APPLICATION_ID,
            "finalApkSha256": apk_identity.sha256,
            "harnessContractSha256": harness_identity.sha256,
            "pubspecLockSha256": pubspec_identity.sha256,
            "targetEvidenceSha256": target_sha256,
            "logcatEvidenceSha256": logcat_sha256,
        },
        "device": device_json,
        "process": process,
        "runtime": runtime,
        "sherpa": sherpa,
        "fixtures": fixtures,
        "initialization": app_payload["initialization"],
        "workload": app_payload["workload"],
        "lifecycle": app_payload["lifecycle"],
    }

    output_paths = {
        "launch-challenge.bin": challenge,
        "raw-logcat.txt": final_logcat.encode("ascii"),
        "target-evidence.json": target_bytes,
        "logcat-evidence.json": logcat_bytes,
        "load-order-receipt.json": _canonical_json(receipt),
    }
    output_identities = {
        name: _write_new_bytes(capture_directory / name, data)
        for name, data in output_paths.items()
    }

    validation_output = capture_directory / "validation-record.json"
    validator = Path(__file__).resolve().with_name(
        "validate_android_load_order_receipt.py"
    )
    command = [
        sys.executable,
        "-B",
        str(validator),
        "--receipt",
        str(capture_directory / "load-order-receipt.json"),
        "--target-evidence",
        str(capture_directory / "target-evidence.json"),
        "--logcat-evidence",
        str(capture_directory / "logcat-evidence.json"),
        "--launch-challenge",
        str(capture_directory / "launch-challenge.bin"),
        "--final-apk",
        str(arguments.final_apk),
        "--harness-contract",
        str(arguments.harness_contract),
        "--pubspec-lock",
        str(arguments.pubspec_lock),
    ]
    for argument_name, cli_name, _maximum in (
        ("fonix_model", "--fonix-model", 0),
        ("fonix_input", "--fonix-input", 0),
        ("fonix_reference_output", "--fonix-reference-output", 0),
        ("fonix_cancellation_model", "--fonix-cancellation-model", 0),
        ("fonix_cancellation_input", "--fonix-cancellation-input", 0),
        ("sherpa_model", "--sherpa-model", 0),
        ("sherpa_audio", "--sherpa-audio", 0),
        ("sherpa_reference", "--sherpa-reference", 0),
    ):
        command.extend((cli_name, str(getattr(arguments, argument_name))))
    command.extend(
        (
            "--sherpa-revision",
            SHERPA_REVISION,
            "--output",
            str(validation_output),
        )
    )
    _execute(
        runner,
        command,
        operation="offline load-order receipt validation",
        maximum_output=64 * 1024,
        timeout_seconds=30 * 60,
    )
    validation_record = _read_json(
        validation_output, "load-order validation record", MAX_JSON_BYTES
    )
    if (
        validation_record.get("schemaVersion") != 1
        or validation_record.get("result") != "passed"
        or validation_record.get("claimStatus") != "offline-consistency-only"
        or not _strict_equal(validation_record.get("matrix"), matrix)
        or not _strict_equal(validation_record.get("build"), receipt["build"])
        or not _strict_equal(validation_record.get("process"), process)
    ):
        raise AndroidSherpaTargetGateError(
            "offline validator output is not bound to the assembled target receipt"
        )
    output_identities["validation-record.json"] = _identity(
        validation_output, "load-order validation record", MAX_JSON_BYTES
    )

    transcript = {
        "schemaVersion": 1,
        "result": "passed",
        "redactionPolicy": "publishable-command-metadata-v1",
        "commands": runner.entries,
    }
    transcript_bytes = _canonical_json(transcript)
    if len(transcript_bytes) > MAX_TRANSCRIPT_BYTES:
        raise AndroidSherpaTargetGateError(
            "serialized command transcript exceeds its byte bound"
        )
    output_identities["command-transcript.json"] = _write_new_bytes(
        capture_directory / "command-transcript.json", transcript_bytes
    )

    runner_identity = _identity(
        Path(__file__).resolve(), "trusted target runner", MAX_JSON_BYTES
    )
    manifest = {
        "schemaVersion": 1,
        "result": "passed",
        "claimStatus": "trusted-adb-capture",
        "applicationId": APPLICATION_ID,
        "matrix": matrix,
        "device": device_json,
        "process": process,
        "launchChallengeSha256": challenge_sha256,
        "completionPayloadSha256": completion.payload_sha256,
        "runner": runner_identity.to_json(),
        "adb": adb_identity.to_json(),
        "apkanalyzer": apkanalyzer_identity.to_json(),
        "inputBindings": {
            "finalApk": apk_identity.to_json(),
            "stagedBuildGateReport": static_gate_report_identity.to_json(),
            "staticPackageManifest": static_manifest_identity.to_json(),
            "sourceManifest": source_manifest_identity.to_json(),
            "harnessContract": harness_identity.to_json(),
            "pubspecLock": pubspec_identity.to_json(),
            **{
                key: identity.to_json()
                for key, identity in fixture_identities.items()
            },
        },
        "captureFiles": {
            name: identity.to_json()
            for name, identity in sorted(output_identities.items())
        },
        "claimBoundary": (
            "This retained trusted-runner capture applies only to one exact "
            "audited Release APK, adb target, queried ABI/API/page size/device, "
            "fresh challenge, UID/PID-bound completion, and selected load order. "
            "The validation record remains offline-consistency-only; this capture "
            "does not cover another APK, AAB, device, page size, or load order and "
            "does not aggregate the four-record matrix."
        ),
    }
    output_identities["capture-manifest.json"] = _write_new_json(
        capture_directory / "capture-manifest.json", manifest
    )
    return manifest


def run_target_gate(
    arguments: argparse.Namespace,
    *,
    command_runner: CommandRunner = run_bounded,
    challenge_factory: Callable[[int], bytes] = secrets.token_bytes,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if arguments.load_order not in LOAD_ORDERS:
        raise AndroidSherpaTargetGateError("load order is outside the closed set")
    if SERIAL_PATTERN.fullmatch(arguments.serial) is None:
        raise AndroidSherpaTargetGateError("adb serial is outside its token bound")

    arguments.adb = _resolve_input(arguments.adb, "adb executable", MAX_ADB_BYTES)
    if not os.access(arguments.adb, os.X_OK):
        raise AndroidSherpaTargetGateError("adb input is not executable")
    arguments.apkanalyzer = _resolve_input(
        arguments.apkanalyzer,
        "apkanalyzer executable",
        MAX_APKANALYZER_BYTES,
    )
    if not os.access(arguments.apkanalyzer, os.X_OK):
        raise AndroidSherpaTargetGateError("apkanalyzer input is not executable")
    arguments.final_apk = _resolve_input(
        arguments.final_apk, "audited Release APK", MAX_APK_BYTES
    )
    arguments.static_gate_report = _resolve_input(
        arguments.static_gate_report,
        "Android staged-build gate report",
        staged_build_gate.MAX_REPORT_BYTES,
    )
    arguments.static_package_manifest = _resolve_input(
        arguments.static_package_manifest,
        "Android static package manifest",
        MAX_JSON_BYTES,
    )
    arguments.harness_contract = _resolve_input(
        arguments.harness_contract, "harness contract", MAX_JSON_BYTES
    )
    arguments.pubspec_lock = _resolve_input(
        arguments.pubspec_lock, "pubspec.lock", MAX_JSON_BYTES
    )
    for argument_name, _receipt_key, maximum in FIXTURE_ARGUMENTS:
        setattr(
            arguments,
            argument_name,
            _resolve_input(
                getattr(arguments, argument_name),
                argument_name.replace("_", " "),
                maximum,
            ),
        )

    apk_identity = _identity(arguments.final_apk, "audited Release APK", MAX_APK_BYTES)
    adb_identity = _identity(arguments.adb, "adb executable", MAX_ADB_BYTES)
    apkanalyzer_identity = _identity(
        arguments.apkanalyzer,
        "apkanalyzer executable",
        MAX_APKANALYZER_BYTES,
    )
    ort_identity = _apk_ort_identity(arguments.final_apk)
    runtime_fixture_identities = _apk_runtime_fixture_identities(
        arguments.final_apk
    )
    pubspec_identity = _identity(arguments.pubspec_lock, "pubspec.lock", MAX_JSON_BYTES)
    fixtures = _fixture_identities(arguments)
    (
        static_gate_report_identity,
        static_manifest_identity,
        source_manifest_identity,
    ) = _validate_static_gate_report(
        arguments.static_gate_report,
        arguments.static_package_manifest,
        apk_identity=apk_identity,
        pubspec_identity=pubspec_identity,
        fixture_identities=fixtures,
        runtime_fixture_identities=runtime_fixture_identities,
    )
    harness_contract, harness_identity = _validate_harness_contract(
        arguments.harness_contract,
        apk_identity=apk_identity,
        pubspec_identity=pubspec_identity,
        ort_identity=ort_identity,
        fixture_identities=fixtures,
    )
    challenge = challenge_factory(CHALLENGE_BYTES)
    if not isinstance(challenge, bytes) or len(challenge) != CHALLENGE_BYTES:
        raise AndroidSherpaTargetGateError(
            "launch challenge generator did not return exact fresh bytes"
        )
    challenge_base64 = base64.b64encode(challenge).decode("ascii")
    if len(challenge) > MAX_CHALLENGE_BYTES:
        raise AndroidSherpaTargetGateError("launch challenge exceeds its bound")
    challenge_sha256 = hashlib.sha256(challenge).hexdigest()

    capture_directory = _prepare_capture_directory(arguments.capture_directory)
    runner = CapturingRunner(command_runner)
    _verify_apk_application_id(
        runner,
        arguments.apkanalyzer,
        arguments.final_apk,
    )
    device = _query_device_facts(runner, arguments.adb, arguments.serial)
    if _package_presence(
        _adb(
            runner,
            arguments.adb,
            arguments.serial,
            (
                "shell",
                "cmd",
                "package",
                "list",
                "packages",
                APPLICATION_ID,
            ),
            "pre-install package absence query",
        )
    ):
        raise AndroidSherpaTargetGateError(
            "dedicated target harness package must be absent before the run"
        )
    install_attempted = False
    primary_error: BaseException | None = None
    completion: CompletionEvidence | None = None
    final_logcat = ""
    application_uid = 0
    observed_pid: int | None = None
    app_payload: dict[str, Any] | None = None
    try:
        install_attempted = True
        _install_succeeded(
            _adb(
                runner,
                arguments.adb,
                arguments.serial,
                ("install", "--no-streaming", str(arguments.final_apk)),
                "audited Release APK install",
            )
        )
        _verify_installed_apk(
            runner,
            arguments.adb,
            arguments.serial,
            apk_identity.sha256,
        )
        application_uid = _parse_package_uid(
            _adb(
                runner,
                arguments.adb,
                arguments.serial,
                (
                    "shell",
                    "cmd",
                    "package",
                    "list",
                    "packages",
                    "-U",
                    APPLICATION_ID,
                ),
                "installed package UID query",
            )
        )
        _empty_output(
            _adb(
                runner,
                arguments.adb,
                arguments.serial,
                ("shell", "am", "force-stop", APPLICATION_ID),
                "pre-launch force-stop",
            ),
            "pre-launch force-stop",
        )
        if _query_application_pid(runner, arguments.adb, arguments.serial) is not None:
            raise AndroidSherpaTargetGateError(
                "application retained an old PID after pre-launch force-stop"
            )
        _empty_output(
            _adb(
                runner,
                arguments.adb,
                arguments.serial,
                ("logcat", "-c"),
                "pre-launch logcat clear",
            ),
            "pre-launch logcat clear",
        )
        _adb(
            runner,
            arguments.adb,
            arguments.serial,
            (
                "shell",
                "am",
                "start",
                "-n",
                APPLICATION_COMPONENT,
                "--es",
                LOAD_ORDER_EXTRA,
                arguments.load_order,
                "--es",
                LAUNCH_CHALLENGE_EXTRA,
                challenge_base64,
            ),
            "reference harness launch",
        )

        deadline = monotonic() + TARGET_TIMEOUT_SECONDS
        while monotonic() < deadline:
            current_pid = _query_application_pid(
                runner, arguments.adb, arguments.serial
            )
            if current_pid is None:
                if observed_pid is not None:
                    raise AndroidSherpaTargetGateError(
                        "application exited before its completion was accepted"
                    )
                sleeper(POLL_SECONDS)
                continue
            if observed_pid is not None and current_pid != observed_pid:
                raise AndroidSherpaTargetGateError(
                    "application PID changed during the target workload"
                )
            observed_pid = current_pid
            final_logcat = _adb(
                runner,
                arguments.adb,
                arguments.serial,
                _logcat_arguments(application_uid, observed_pid),
                "UID/PID/tag-bound completion logcat capture",
                maximum_output=MAX_LOGCAT_BYTES,
            )
            try:
                completion = _parse_completion_log(
                    final_logcat,
                    expected_uid=application_uid,
                    expected_pid=observed_pid,
                )
                break
            except CompletionPending:
                sleeper(POLL_SECONDS)
        if completion is None or observed_pid is None:
            raise AndroidSherpaTargetGateError(
                "target completion was not observed within the bounded deadline"
            )
        app_payload = _validate_app_payload(
            completion.payload,
            load_order=arguments.load_order,
            challenge_sha256=challenge_sha256,
            harness_contract=harness_contract,
        )
        after_receipt = _query_application_pid(
            runner, arguments.adb, arguments.serial
        )
        if after_receipt != observed_pid:
            raise AndroidSherpaTargetGateError(
                "application did not retain the same PID after its completion"
            )
        _empty_output(
            _adb(
                runner,
                arguments.adb,
                arguments.serial,
                ("shell", "am", "force-stop", APPLICATION_ID),
                "post-receipt force-stop",
            ),
            "post-receipt force-stop",
        )
        if _query_application_pid(runner, arguments.adb, arguments.serial) is not None:
            raise AndroidSherpaTargetGateError(
                "application PID remained after post-receipt force-stop"
            )
    except BaseException as error:
        primary_error = error
    finally:
        if install_attempted:
            continuity_error: BaseException | None = None
            try:
                cleanup_device = _query_device_facts(
                    runner, arguments.adb, arguments.serial
                )
                if cleanup_device != device:
                    raise AndroidSherpaTargetGateError(
                        "target device facts changed before cleanup; refusing "
                        "to modify the replacement target"
                    )
            except BaseException as error:
                continuity_error = error
            if continuity_error is not None:
                if primary_error is not None:
                    raise AndroidSherpaTargetGateError(
                        f"target run failed: {primary_error}; "
                        f"cleanup also failed: {continuity_error}"
                    ) from primary_error
                raise continuity_error
            try:
                _cleanup_application(runner, arguments.adb, arguments.serial)
            except BaseException as cleanup_error:
                if primary_error is not None:
                    raise AndroidSherpaTargetGateError(
                        f"target run failed: {primary_error}; "
                        f"cleanup also failed: {cleanup_error}"
                    ) from primary_error
                raise

    if primary_error is not None:
        raise primary_error

    assert completion is not None
    assert observed_pid is not None
    assert app_payload is not None
    postflight_device = _query_device_facts(
        runner, arguments.adb, arguments.serial
    )
    if postflight_device != device:
        raise AndroidSherpaTargetGateError(
            "device facts changed during the target run"
        )
    if (
        _identity(
            arguments.final_apk,
            "audited Release APK postflight",
            MAX_APK_BYTES,
        )
        != apk_identity
    ):
        raise AndroidSherpaTargetGateError(
            "audited Release APK changed during target run"
        )
    if (
        _identity(
            arguments.harness_contract,
            "harness contract postflight",
            MAX_JSON_BYTES,
        )
        != harness_identity
    ):
        raise AndroidSherpaTargetGateError("harness contract changed during target run")
    if (
        _identity(
            arguments.static_gate_report,
            "staged-build gate report postflight",
            staged_build_gate.MAX_REPORT_BYTES,
        )
        != static_gate_report_identity
    ):
        raise AndroidSherpaTargetGateError(
            "staged-build gate report changed during target run"
        )
    if (
        _identity(
            arguments.static_package_manifest,
            "static package manifest postflight",
            MAX_JSON_BYTES,
        )
        != static_manifest_identity
    ):
        raise AndroidSherpaTargetGateError(
            "static package manifest changed during target run"
        )
    if (
        _identity(arguments.adb, "adb executable postflight", MAX_ADB_BYTES)
        != adb_identity
    ):
        raise AndroidSherpaTargetGateError("adb executable changed during target run")
    if (
        _identity(
            arguments.apkanalyzer,
            "apkanalyzer executable postflight",
            MAX_APKANALYZER_BYTES,
        )
        != apkanalyzer_identity
    ):
        raise AndroidSherpaTargetGateError(
            "apkanalyzer executable changed during target run"
        )
    source_manifest_path = (
        Path(__file__).resolve().parents[2] / staged_build_gate.MANIFEST
    )
    if (
        _identity(
            source_manifest_path,
            "Fonix source checksum manifest postflight",
            staged_build_gate.MAX_SOURCE_MANIFEST_BYTES,
        )
        != source_manifest_identity
    ):
        raise AndroidSherpaTargetGateError(
            "Fonix source checksum manifest changed during target run"
        )
    if (
        _identity(
            arguments.pubspec_lock,
            "pubspec.lock postflight",
            MAX_JSON_BYTES,
        )
        != pubspec_identity
    ):
        raise AndroidSherpaTargetGateError("pubspec.lock changed during target run")
    for argument_name, receipt_key, maximum in FIXTURE_ARGUMENTS:
        if (
            _identity(
                getattr(arguments, argument_name),
                f"{argument_name.replace('_', ' ')} postflight",
                maximum,
            )
            != fixtures[receipt_key]
        ):
            raise AndroidSherpaTargetGateError(
                "a runtime fixture changed during target run"
            )
    return _write_evidence_and_validate(
        arguments=arguments,
        capture_directory=capture_directory,
        runner=runner,
        challenge=challenge,
        final_logcat=final_logcat,
        app_payload=app_payload,
        apk_identity=apk_identity,
        static_gate_report_identity=static_gate_report_identity,
        static_manifest_identity=static_manifest_identity,
        source_manifest_identity=source_manifest_identity,
        adb_identity=adb_identity,
        apkanalyzer_identity=apkanalyzer_identity,
        harness_contract=harness_contract,
        harness_identity=harness_identity,
        pubspec_identity=pubspec_identity,
        fixture_identities=fixtures,
        device=device,
        uid=application_uid,
        pid=observed_pid,
        completion=completion,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--apkanalyzer", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--final-apk", type=Path, required=True)
    parser.add_argument("--static-gate-report", type=Path, required=True)
    parser.add_argument("--static-package-manifest", type=Path, required=True)
    parser.add_argument("--load-order", choices=sorted(LOAD_ORDERS), required=True)
    parser.add_argument("--harness-contract", type=Path, required=True)
    parser.add_argument("--pubspec-lock", type=Path, required=True)
    parser.add_argument("--fonix-model", type=Path, required=True)
    parser.add_argument("--fonix-input", type=Path, required=True)
    parser.add_argument("--fonix-reference-output", type=Path, required=True)
    parser.add_argument("--fonix-cancellation-model", type=Path, required=True)
    parser.add_argument("--fonix-cancellation-input", type=Path, required=True)
    parser.add_argument("--sherpa-model", type=Path, required=True)
    parser.add_argument("--sherpa-audio", type=Path, required=True)
    parser.add_argument("--sherpa-reference", type=Path, required=True)
    parser.add_argument("--capture-directory", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = run_target_gate(arguments)
    except (
        AndroidSherpaTargetGateError,
        FileNotFoundError,
        OSError,
        UnicodeError,
        zipfile.BadZipFile,
    ) as error:
        print(
            f"run_android_sherpa_reference_app_target_gate: {error}",
            file=sys.stderr,
        )
        return 1
    print(
        "Wrote trusted Android sherpa target capture: "
        f"{arguments.capture_directory} "
        f"({manifest['matrix']['loadOrder']}, "
        f"{manifest['matrix']['pageSizeBytes']} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
