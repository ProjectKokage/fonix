#!/usr/bin/env python3
"""Run a trusted command with bounded POSIX process-group/output ownership.

The helper owns the new POSIX process group created for the direct child.
Ordinary subprocesses inherit that group and are retired with it.  This is not
a sandbox: a program that deliberately calls ``setsid`` or ``setpgid`` can
leave the group and is outside this helper's contract.  A Windows
implementation needs a Job Object (or an equivalent proven owner) before it
can provide a comparable inherited-subprocess boundary.
"""

from __future__ import annotations

import codecs
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Any, Mapping, NamedTuple, Sequence


MAX_OPERATION_BYTES = 256
MAX_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_STREAM_CAPTURE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
POLL_INTERVAL_SECONDS = 0.05
TERMINATION_GRACE_SECONDS = 1.0
KILL_GRACE_SECONDS = 1.0
OUTPUT_DRAIN_GRACE_SECONDS = 1.0
DIAGNOSTIC_TAIL_CHARACTERS = 2048


class CommandOutput(NamedTuple):
    """Strict UTF-8 output from one successfully completed command."""

    stdout: str
    stderr: str


def _diagnostic_tail(label: str, value: str | None) -> str:
    if not value:
        return ""
    if len(value) > DIAGNOSTIC_TAIL_CHARACTERS:
        value = "[...truncated...]\n" + value[-DIAGNOSTIC_TAIL_CHARACTERS:]
    return f"\n{label} (bounded tail):\n{value}"


class BoundedProcessError(RuntimeError):
    """Base class for every bounded-process configuration or run failure."""

    def __init__(
        self,
        operation: str,
        reason: str,
        *,
        stdout: str | None = None,
        stderr: str | None = None,
        cleanup_failures: Sequence[str] = (),
    ) -> None:
        self.operation = operation
        self.reason = reason
        self.stdout = stdout
        self.stderr = stderr
        self.cleanup_failures = tuple(cleanup_failures)
        message = f"{operation}: {reason}"
        if self.cleanup_failures:
            message += "; cleanup incomplete: " + "; ".join(
                self.cleanup_failures
            )
        message += _diagnostic_tail("stdout", stdout)
        message += _diagnostic_tail("stderr", stderr)
        super().__init__(message)


class BoundedProcessConfigurationError(BoundedProcessError):
    """The requested bounds or command are invalid."""


class BoundedProcessUnsupportedPlatformError(BoundedProcessError):
    """The host lacks the spawned-process ownership contract this helper needs."""


class BoundedProcessLaunchError(BoundedProcessError):
    """The direct child could not be created."""


class BoundedProcessTimeoutError(BoundedProcessError):
    """The command exceeded its monotonic wall-clock deadline."""

    def __init__(
        self,
        operation: str,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            operation,
            f"timed out after {timeout_seconds:g} seconds",
            **kwargs,
        )


class BoundedProcessOutputLimitError(BoundedProcessError):
    """One captured stream exceeded its configured byte limit."""

    def __init__(
        self,
        operation: str,
        *,
        stream_name: str,
        limit_bytes: int,
        observed_bytes: int,
        **kwargs: Any,
    ) -> None:
        self.stream_name = stream_name
        self.limit_bytes = limit_bytes
        self.observed_bytes = observed_bytes
        super().__init__(
            operation,
            f"{stream_name} exceeded {limit_bytes} bytes "
            f"(observed at least {observed_bytes})",
            **kwargs,
        )


class BoundedProcessEncodingError(BoundedProcessError):
    """One command stream was not strict UTF-8."""

    def __init__(
        self,
        operation: str,
        *,
        stream_name: str,
        **kwargs: Any,
    ) -> None:
        self.stream_name = stream_name
        super().__init__(
            operation,
            f"{stream_name} is not valid UTF-8; configure the command for "
            "strict UTF-8 output",
            **kwargs,
        )


class BoundedProcessExitError(BoundedProcessError):
    """The direct child returned a nonzero status."""

    def __init__(
        self,
        operation: str,
        *,
        return_code: int,
        residual_group_members: bool,
        **kwargs: Any,
    ) -> None:
        self.return_code = return_code
        self.residual_group_members = residual_group_members
        suffix = ""
        if residual_group_members:
            suffix = " and left members in its owned process group"
        super().__init__(
            operation,
            f"failed with exit code {return_code}{suffix}",
            **kwargs,
        )


class BoundedProcessResidualProcessError(BoundedProcessError):
    """A successful direct child left an owned group member or output pipe."""

    def __init__(
        self,
        operation: str,
        *,
        process_group_id: int,
        **kwargs: Any,
    ) -> None:
        self.process_group_id = process_group_id
        super().__init__(
            operation,
            "the direct child exited successfully but left a process-group "
            "member or output pipe alive; the owned POSIX process group was "
            "retired",
            **kwargs,
        )


class BoundedProcessCaptureError(BoundedProcessError):
    """The helper could not establish or complete bounded stream capture."""


class BoundedProcessCleanupError(BoundedProcessError):
    """The helper could not prove that its owned process group settled."""


class _StreamCapture:
    def __init__(self, name: str, stream: Any, limit: int) -> None:
        self.name = name
        self.stream = stream
        self.limit = limit
        self.buffer = bytearray()
        self.total_bytes = 0
        self.overflow = False
        self.invalid_utf8 = False
        self.read_error: str | None = None
        self.eof = False
        self.closed = False
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        remaining = self.limit - len(self.buffer)
        if remaining > 0:
            self.buffer.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.overflow = True
        if not self.invalid_utf8:
            try:
                self.decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                self.invalid_utf8 = True

    def finish(self) -> None:
        if not self.invalid_utf8:
            try:
                self.decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                self.invalid_utf8 = True
        self.eof = True

    def text(self) -> str | None:
        if self.invalid_utf8:
            return None
        try:
            return bytes(self.buffer).decode("utf-8")
        except UnicodeDecodeError:
            # A byte-limited prefix can end midway through a valid code point.
            return None


def _configuration_error(operation: str, reason: str) -> BoundedProcessError:
    return BoundedProcessConfigurationError(operation, reason)


def _validate_operation(operation: str) -> None:
    if not isinstance(operation, str):
        raise _configuration_error("bounded process", "operation must be a string")
    try:
        encoded = operation.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _configuration_error(
            "bounded process", "operation must be valid UTF-8"
        ) from error
    if (
        not encoded
        or len(encoded) > MAX_OPERATION_BYTES
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
    ):
        raise _configuration_error(
            "bounded process",
            f"operation must contain 1..{MAX_OPERATION_BYTES} non-control "
            "UTF-8 bytes",
        )


def _validate_seconds(value: float, label: str, operation: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > MAX_TIMEOUT_SECONDS
    ):
        raise _configuration_error(
            operation,
            f"{label} must be greater than zero and no more than "
            f"{MAX_TIMEOUT_SECONDS} seconds",
        )
    return float(value)


def _validate_stream_limit(value: int, label: str, operation: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_STREAM_CAPTURE_BYTES
    ):
        raise _configuration_error(
            operation,
            f"{label} must be an integer in "
            f"1..{MAX_STREAM_CAPTURE_BYTES} bytes",
        )
    return value


def _validate_command(command: Sequence[str], operation: str) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        raise _configuration_error(operation, "command must be a sequence of strings")
    try:
        arguments = tuple(command)
    except TypeError as error:
        raise _configuration_error(
            operation, "command must be a sequence of strings"
        ) from error
    if not arguments:
        raise _configuration_error(operation, "command must not be empty")
    for index, argument in enumerate(arguments):
        if not isinstance(argument, str) or not argument or "\x00" in argument:
            raise _configuration_error(
                operation,
                f"command argument {index} must be a nonempty string without NUL",
            )
    return arguments


def _close_capture(
    selector: selectors.BaseSelector,
    capture: _StreamCapture,
) -> None:
    if capture.closed:
        return
    try:
        selector.unregister(capture.stream)
    except (KeyError, OSError, ValueError):
        pass
    try:
        capture.stream.close()
    except OSError:
        pass
    capture.closed = True


def _drain_ready(
    selector: selectors.BaseSelector,
    captures: Sequence[_StreamCapture],
    timeout_seconds: float,
) -> str | None:
    if not selector.get_map():
        if timeout_seconds > 0:
            time.sleep(timeout_seconds)
        return None
    try:
        events = selector.select(timeout_seconds)
    except OSError as error:
        return f"output selector failed with errno {error.errno}"
    for key, _ in events:
        capture = key.data
        try:
            chunk = os.read(key.fd, READ_CHUNK_BYTES)
        except BlockingIOError:
            continue
        except OSError as error:
            capture.read_error = (
                f"{capture.name} read failed with errno {error.errno}"
            )
            _close_capture(selector, capture)
            continue
        if chunk:
            capture.feed(chunk)
        else:
            capture.finish()
            _close_capture(selector, capture)
    return None


def _capture_problem(
    captures: Sequence[_StreamCapture],
) -> tuple[str, _StreamCapture] | None:
    for capture in captures:
        if capture.overflow:
            return "overflow", capture
    for capture in captures:
        if capture.invalid_utf8:
            return "encoding", capture
    for capture in captures:
        if capture.read_error is not None:
            return "capture", capture
    return None


def _owned_group_state(process_group_id: int) -> tuple[bool, str | None]:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, "permission was denied while inspecting the process group"
    except OSError as error:
        return True, f"process-group inspection failed with errno {error.errno}"
    return True, None


def _signal_owned_group(process_group_id: int, selected_signal: int) -> str | None:
    try:
        os.killpg(process_group_id, selected_signal)
    except ProcessLookupError:
        return None
    except OSError as error:
        signal_name = signal.Signals(selected_signal).name
        return f"{signal_name} failed with errno {error.errno}"
    return None


def _wait_for_process_group(
    process: subprocess.Popen[bytes],
    process_group_id: int,
    selector: selectors.BaseSelector,
    captures: Sequence[_StreamCapture],
    deadline: float,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    last_group_error: str | None = None
    while True:
        process.poll()
        group_exists, group_error = _owned_group_state(process_group_id)
        last_group_error = group_error
        if process.returncode is not None and not group_exists:
            return True, failures
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_group_error is not None:
                failures.append(last_group_error)
            return False, failures
        drain_error = _drain_ready(
            selector,
            captures,
            min(POLL_INTERVAL_SECONDS, remaining),
        )
        if drain_error is not None and drain_error not in failures:
            failures.append(drain_error)


def _terminate_and_reap(
    process: subprocess.Popen[bytes],
    process_group_id: int,
    selector: selectors.BaseSelector,
    captures: Sequence[_StreamCapture],
) -> tuple[str, ...]:
    failures: list[str] = []
    process.poll()
    group_exists, group_error = _owned_group_state(process_group_id)
    if process.returncode is None or group_exists:
        signal_error = _signal_owned_group(process_group_id, signal.SIGTERM)
        if signal_error is not None:
            failures.append(signal_error)
        settled, wait_failures = _wait_for_process_group(
            process,
            process_group_id,
            selector,
            captures,
            time.monotonic() + TERMINATION_GRACE_SECONDS,
        )
        failures.extend(
            failure for failure in wait_failures if failure not in failures
        )
        if not settled:
            signal_error = _signal_owned_group(process_group_id, signal.SIGKILL)
            if signal_error is not None:
                failures.append(signal_error)
            settled, wait_failures = _wait_for_process_group(
                process,
                process_group_id,
                selector,
                captures,
                time.monotonic() + KILL_GRACE_SECONDS,
            )
            failures.extend(
                failure for failure in wait_failures if failure not in failures
            )
            if not settled:
                process.poll()
                group_exists, group_error = _owned_group_state(process_group_id)
                if process.returncode is None:
                    failures.append("direct child was not reaped after SIGKILL")
                if group_exists:
                    failures.append("owned process group remained after SIGKILL")
                if group_error is not None and group_error not in failures:
                    failures.append(group_error)

    drain_deadline = time.monotonic() + OUTPUT_DRAIN_GRACE_SECONDS
    while any(not capture.closed for capture in captures):
        remaining = drain_deadline - time.monotonic()
        if remaining <= 0:
            break
        drain_error = _drain_ready(
            selector,
            captures,
            min(POLL_INTERVAL_SECONDS, remaining),
        )
        if drain_error is not None and drain_error not in failures:
            failures.append(drain_error)
    for capture in captures:
        if not capture.closed:
            failures.append(f"{capture.name} did not reach EOF after cleanup")
            _close_capture(selector, capture)
    return tuple(failures)


def _secondary_capture_failures(
    captures: Sequence[_StreamCapture],
    primary_kind: str,
    primary_capture: _StreamCapture | None,
) -> tuple[str, ...]:
    failures: list[str] = []
    for capture in captures:
        if capture is primary_capture:
            continue
        if capture.overflow:
            failures.append(f"{capture.name} also exceeded its output bound")
        elif capture.invalid_utf8:
            failures.append(f"{capture.name} also emitted invalid UTF-8")
        elif capture.read_error is not None:
            failures.append(capture.read_error)
    if primary_kind not in {"overflow", "encoding", "capture"}:
        for capture in captures:
            if capture.overflow:
                failures.append(f"{capture.name} exceeded its output bound")
            elif capture.invalid_utf8:
                failures.append(f"{capture.name} emitted invalid UTF-8")
            elif capture.read_error is not None:
                failures.append(capture.read_error)
    return tuple(dict.fromkeys(failures))


def run_bounded(
    command: Sequence[str],
    *,
    operation: str,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float,
    maximum_stdout_bytes: int,
    maximum_stderr_bytes: int,
) -> CommandOutput:
    """Run one command under strict bounded POSIX ownership.

    Output is captured independently for stdout and stderr, validated as
    strict UTF-8 while it arrives, and retained only through each caller-owned
    byte cap.  On every terminal protocol failure, the owned process group
    receives SIGTERM, then SIGKILL when needed, and the direct child is reaped
    within fixed cleanup deadlines.  Callers must run trusted tools: deliberate
    ``setsid`` or ``setpgid`` escape is outside this process-group contract.
    """

    _validate_operation(operation)
    arguments = _validate_command(command, operation)
    timeout = _validate_seconds(timeout_seconds, "timeout_seconds", operation)
    stdout_limit = _validate_stream_limit(
        maximum_stdout_bytes, "maximum_stdout_bytes", operation
    )
    stderr_limit = _validate_stream_limit(
        maximum_stderr_bytes, "maximum_stderr_bytes", operation
    )
    if os.name != "posix":
        raise BoundedProcessUnsupportedPlatformError(
            operation,
            "spawned-process ownership is currently implemented only with a "
            "POSIX process group; Windows Job Object ownership is not "
            "implemented",
        )

    try:
        selector = selectors.DefaultSelector()
    except OSError as error:
        raise BoundedProcessCaptureError(
            operation,
            f"could not create the output selector (errno {error.errno})",
        ) from error

    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    process_group_id: int | None = None
    try:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                env=None if environment is None else dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                bufsize=0,
            )
        except (OSError, TypeError, ValueError) as error:
            detail = (
                f"errno {error.errno}"
                if isinstance(error, OSError)
                else type(error).__name__
            )
            raise BoundedProcessLaunchError(
                operation,
                f"could not start the direct child ({detail})",
            ) from error

        # Establish the owned group and capture objects before leaving this
        # cleanup guard.  This closes the post-Popen/pre-capture failure window.
        process_group_id = process.pid
        assert process.stdout is not None and process.stderr is not None
        captures = (
            _StreamCapture("stdout", process.stdout, stdout_limit),
            _StreamCapture("stderr", process.stderr, stderr_limit),
        )
    except BaseException:
        if process is not None:
            owned_group = process.pid if process_group_id is None else process_group_id
            _terminate_and_reap(
                process,
                owned_group,
                selector,
                (),
            )
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        selector.close()
        raise

    assert process_group_id is not None
    cleanup_performed = False
    try:
        try:
            for capture in captures:
                os.set_blocking(capture.stream.fileno(), False)
                selector.register(
                    capture.stream,
                    selectors.EVENT_READ,
                    capture,
                )
        except (OSError, ValueError) as error:
            cleanup_failures = _terminate_and_reap(
                process,
                process_group_id,
                selector,
                captures,
            )
            cleanup_performed = True
            raise BoundedProcessCaptureError(
                operation,
                f"could not establish bounded output capture ({type(error).__name__})",
                cleanup_failures=cleanup_failures,
            ) from error

        deadline = started + timeout
        failure_kind: str | None = None
        failure_capture: _StreamCapture | None = None
        failure_reason: str | None = None
        return_code: int | None = None
        residual_group_members = False
        exit_observed_at: float | None = None

        while failure_kind is None:
            now = time.monotonic()
            return_code = process.poll()
            if return_code is not None:
                if exit_observed_at is None:
                    exit_observed_at = now
                group_exists, group_error = _owned_group_state(process_group_id)
                if group_error is not None:
                    failure_kind = "cleanup"
                    failure_reason = group_error
                    break
                if group_exists:
                    residual_group_members = True
                    failure_kind = "residual" if return_code == 0 else "exit"
                    break
                if all(capture.closed for capture in captures):
                    if return_code != 0:
                        failure_kind = "exit"
                    break
                if now >= exit_observed_at + OUTPUT_DRAIN_GRACE_SECONDS:
                    failure_kind = "residual" if return_code == 0 else "exit"
                    failure_reason = "an output pipe remained open after direct exit"
                    break
                wait_deadline = exit_observed_at + OUTPUT_DRAIN_GRACE_SECONDS
            else:
                if now >= deadline:
                    failure_kind = "timeout"
                    break
                wait_deadline = deadline

            drain_error = _drain_ready(
                selector,
                captures,
                min(POLL_INTERVAL_SECONDS, max(0.0, wait_deadline - now)),
            )
            if drain_error is not None:
                failure_kind = "capture"
                failure_reason = drain_error
                break
            problem = _capture_problem(captures)
            if problem is not None:
                failure_kind, failure_capture = problem
                break

        if failure_kind is None:
            stdout = captures[0].text()
            stderr = captures[1].text()
            if stdout is None or stderr is None:
                invalid = captures[0] if stdout is None else captures[1]
                raise BoundedProcessEncodingError(
                    operation,
                    stream_name=invalid.name,
                    stdout=stdout,
                    stderr=stderr,
                )
            return CommandOutput(stdout=stdout, stderr=stderr)

        cleanup_failures = list(
            _terminate_and_reap(
                process,
                process_group_id,
                selector,
                captures,
            )
        )
        cleanup_performed = True
        cleanup_failures.extend(
            failure
            for failure in _secondary_capture_failures(
                captures, failure_kind, failure_capture
            )
            if failure not in cleanup_failures
        )
        stdout = captures[0].text()
        stderr = captures[1].text()

        if failure_kind == "timeout":
            raise BoundedProcessTimeoutError(
                operation,
                timeout,
                stdout=stdout,
                stderr=stderr,
                cleanup_failures=cleanup_failures,
            )
        if failure_kind == "overflow":
            assert failure_capture is not None
            raise BoundedProcessOutputLimitError(
                operation,
                stream_name=failure_capture.name,
                limit_bytes=failure_capture.limit,
                observed_bytes=failure_capture.total_bytes,
                stdout=stdout,
                stderr=stderr,
                cleanup_failures=cleanup_failures,
            )
        if failure_kind == "encoding":
            assert failure_capture is not None
            raise BoundedProcessEncodingError(
                operation,
                stream_name=failure_capture.name,
                stdout=stdout,
                stderr=stderr,
                cleanup_failures=cleanup_failures,
            )
        if failure_kind == "exit":
            assert return_code is not None
            if failure_reason is not None:
                cleanup_failures.append(failure_reason)
            raise BoundedProcessExitError(
                operation,
                return_code=return_code,
                residual_group_members=residual_group_members,
                stdout=stdout,
                stderr=stderr,
                cleanup_failures=cleanup_failures,
            )
        if failure_kind == "residual":
            if failure_reason is not None:
                cleanup_failures.append(failure_reason)
            raise BoundedProcessResidualProcessError(
                operation,
                process_group_id=process_group_id,
                stdout=stdout,
                stderr=stderr,
                cleanup_failures=cleanup_failures,
            )
        if failure_kind == "capture":
            reason = failure_reason
            if reason is None and failure_capture is not None:
                reason = failure_capture.read_error
            raise BoundedProcessCaptureError(
                operation,
                reason or "could not complete bounded output capture",
                stdout=stdout,
                stderr=stderr,
                cleanup_failures=cleanup_failures,
            )
        raise BoundedProcessCleanupError(
            operation,
            failure_reason or "could not inspect the owned process group",
            stdout=stdout,
            stderr=stderr,
            cleanup_failures=cleanup_failures,
        )
    except BaseException:
        if not cleanup_performed:
            _terminate_and_reap(
                process,
                process_group_id,
                selector,
                captures,
            )
        raise
    finally:
        for capture in captures:
            _close_capture(selector, capture)
        selector.close()
