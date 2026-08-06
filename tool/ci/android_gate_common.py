#!/usr/bin/env python3
"""Bounded process and strict-data helpers for Fonix Android gates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
from typing import Any, Mapping, NamedTuple, Sequence


MAX_PATH_BYTES = 4096
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
PROCESS_TERMINATION_GRACE_SECONDS = 1.0
OUTPUT_DRAIN_GRACE_SECONDS = 5.0


class AndroidGateCommonError(RuntimeError):
    """A shared bounded gate operation failed closed."""


class CommandOutput(NamedTuple):
    stdout: str
    stderr: str


def _signal_owned_process_group(
    process: subprocess.Popen[bytes], *, force: bool
) -> str | None:
    """Signal only the process group/session created for ``process``."""

    if os.name == "posix":
        selected_signal = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(process.pid, selected_signal)
        except ProcessLookupError:
            return None
        except OSError as error:
            return f"could not signal owned process group: {error}"
        return None

    if process.poll() is not None:
        return None
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return None
    except OSError as error:
        return f"could not terminate child process: {error}"
    return None


def _terminate_owned_process_group(process: subprocess.Popen[bytes]) -> list[str]:
    """Terminate, then kill, the complete owned POSIX process group."""

    errors: list[str] = []
    error = _signal_owned_process_group(process, force=False)
    if error is not None:
        errors.append(error)
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

    # Always address the group again on POSIX. The direct child may have
    # exited after SIGTERM while one of its descendants retained a pipe.
    error = _signal_owned_process_group(process, force=True)
    if error is not None:
        errors.append(error)
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            errors.append("child process did not exit after forced termination")
    return errors


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AndroidGateCommonError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def reject_json_constant(value: str) -> object:
    raise AndroidGateCommonError(f"non-finite JSON number {value!r}")


def strict_json(data: bytes | str, label: str, *, maximum: int) -> Any:
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    if len(raw) > maximum:
        raise AndroidGateCommonError(f"{label} exceeds {maximum} bytes")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=reject_json_constant,
        )
    except AndroidGateCommonError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AndroidGateCommonError(
            f"{label} is not strict UTF-8 JSON"
        ) from error


def bounded_path(path: Path, label: str) -> None:
    raw = os.fsencode(str(path))
    if not raw or len(raw) > MAX_PATH_BYTES or b"\x00" in raw:
        raise AndroidGateCommonError(f"{label} is outside the path bound")
    if any(byte < 0x20 for byte in raw):
        raise AndroidGateCommonError(f"{label} contains a control character")


def regular_file(path: Path, label: str, *, maximum: int | None = None) -> Path:
    bounded_path(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise AndroidGateCommonError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise AndroidGateCommonError(f"{label} is not a regular file: {path}")
    if maximum is not None and metadata.st_size > maximum:
        raise AndroidGateCommonError(f"{label} exceeds {maximum} bytes")
    return path


def directory(path: Path, label: str) -> Path:
    bounded_path(path, label)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise AndroidGateCommonError(f"missing {label}: {path}") from error
    if not stat.S_ISDIR(mode):
        raise AndroidGateCommonError(
            f"{label} is not a non-symlink directory: {path}"
        )
    return path


def sha256_file(path: Path, label: str, *, maximum: int) -> tuple[int, str]:
    path = regular_file(path, label, maximum=maximum)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise AndroidGateCommonError(f"{label} exceeds {maximum} bytes")
            digest.update(chunk)
    return size, digest.hexdigest()


def tool_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment["DART_SUPPRESS_ANALYTICS"] = "true"
    environment["FLUTTER_SUPPRESS_ANALYTICS"] = "true"
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    return environment


def run_bounded(
    command: Sequence[str],
    *,
    operation: str,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int = 30 * 60,
    maximum_output: int = MAX_COMMAND_OUTPUT_BYTES,
) -> CommandOutput:
    if timeout_seconds <= 0 or timeout_seconds > 30 * 60:
        raise AndroidGateCommonError(f"{operation} timeout is outside its bound")
    if maximum_output <= 0 or maximum_output > MAX_COMMAND_OUTPUT_BYTES:
        raise AndroidGateCommonError(
            f"{operation} output bound is outside its allowed range"
        )
    process_options: dict[str, Any] = {}
    if os.name == "posix":
        process_options["start_new_session"] = True
    elif os.name == "nt":
        process_options["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment or tool_environment()),
            **process_options,
        )
    except OSError as error:
        raise AndroidGateCommonError(f"could not execute {operation}") from error

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow: list[str] = []
    reader_errors: list[str] = []
    termination_errors: list[str] = []
    reached_eof = {"stdout": False, "stderr": False}
    lock = threading.Lock()

    def force_termination() -> None:
        error = _signal_owned_process_group(process, force=True)
        if error is not None:
            with lock:
                termination_errors.append(error)

    def drain(label: str, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    with lock:
                        reached_eof[label] = True
                    return
                exceeded = False
                with lock:
                    remaining = maximum_output - len(buffers[label])
                    if remaining > 0:
                        buffers[label].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        if not overflow:
                            overflow.append(label)
                            exceeded = True
                if exceeded:
                    # Keep draining after the forced stop so EOF itself is
                    # observed. Bytes beyond the cap are discarded.
                    force_termination()
        except (OSError, ValueError):
            with lock:
                reader_errors.append(label)
            force_termination()
        finally:
            try:
                stream.close()
            except OSError:
                pass

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(
            target=drain,
            args=("stdout", process.stdout),
            name="fonix-android-gate-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=("stderr", process.stderr),
            name="fonix-android-gate-stderr",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        errors = _terminate_owned_process_group(process)
        with lock:
            termination_errors.extend(errors)
        return_code = process.poll()

    for thread in threads:
        thread.join(timeout=OUTPUT_DRAIN_GRACE_SECONDS)
    if any(thread.is_alive() for thread in threads):
        errors = _terminate_owned_process_group(process)
        with lock:
            termination_errors.extend(errors)
        for thread in threads:
            thread.join(timeout=OUTPUT_DRAIN_GRACE_SECONDS)

    with lock:
        incomplete_drains = [
            label
            for label, thread in zip(("stdout", "stderr"), threads)
            if thread.is_alive() or not reached_eof[label]
        ]
        first_overflow = overflow[0] if overflow else None
        has_reader_errors = bool(reader_errors)
        cleanup_errors = tuple(termination_errors)

    if incomplete_drains:
        raise AndroidGateCommonError(
            f"{operation} {incomplete_drains[0]} did not reach EOF after "
            "process termination"
        )
    if timed_out:
        raise AndroidGateCommonError(
            f"{operation} timed out after {timeout_seconds} seconds"
        )
    if first_overflow is not None:
        raise AndroidGateCommonError(
            f"{operation} {first_overflow} exceeds {maximum_output} bytes"
        )
    if has_reader_errors:
        raise AndroidGateCommonError(f"could not capture {operation} output")
    if cleanup_errors:
        raise AndroidGateCommonError(
            f"could not clean up {operation}: {cleanup_errors[0]}"
        )
    if return_code is None:
        raise AndroidGateCommonError(f"{operation} did not report an exit code")
    try:
        stdout = bytes(buffers["stdout"]).decode("utf-8")
        stderr = bytes(buffers["stderr"]).decode("utf-8")
    except UnicodeDecodeError as error:
        raise AndroidGateCommonError(
            f"{operation} output is not valid UTF-8"
        ) from error
    if return_code != 0:
        diagnostic_stdout = stdout[:4096]
        diagnostic_stderr = stderr[:4096]
        raise AndroidGateCommonError(
            f"{operation} failed with exit code {return_code}"
            f"\nstdout:\n{diagnostic_stdout}\nstderr:\n{diagnostic_stderr}"
        )
    return CommandOutput(stdout=stdout, stderr=stderr)
