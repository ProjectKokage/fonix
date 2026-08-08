from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from typing import Callable
import unittest
from unittest import mock


CI_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_DIRECTORY))

import bounded_process  # noqa: E402


@unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
class BoundedProcessPosixTests(unittest.TestCase):
    def _run_python(
        self,
        source: str,
        *,
        operation: str = "bounded fixture",
        timeout_seconds: float = 5,
        maximum_stdout_bytes: int = 64 * 1024,
        maximum_stderr_bytes: int = 64 * 1024,
        on_started: Callable[[int], None] | None = None,
    ) -> bounded_process.CommandOutput:
        return bounded_process.run_bounded(
            [sys.executable, "-I", "-S", "-B", "-c", source],
            operation=operation,
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=maximum_stdout_bytes,
            maximum_stderr_bytes=maximum_stderr_bytes,
            on_started=on_started,
        )

    def _short_cleanup(self) -> mock._patch:
        return mock.patch.multiple(
            bounded_process,
            TERMINATION_GRACE_SECONDS=0.15,
            KILL_GRACE_SECONDS=0.75,
            OUTPUT_DRAIN_GRACE_SECONDS=0.5,
        )

    def test_success_uses_devnull_and_captures_strict_utf8_streams(self) -> None:
        result = self._run_python(
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "sys.stdout.write(f'stdin={len(data)} café\\n')\n"
            "sys.stderr.write('warning Δ\\n')\n"
        )

        self.assertEqual(result.stdout, "stdin=0 café\n")
        self.assertEqual(result.stderr, "warning Δ\n")

    def test_darwin_post_exit_permission_then_missing_settles_while_draining(
        self,
    ) -> None:
        permission = (True, bounded_process.PROCESS_GROUP_PERMISSION_ERROR)
        missing = (False, None)
        states = iter((permission, missing))
        drain_count = 0
        permission_drain_count: int | None = None
        real_drain = bounded_process._drain_ready

        def group_state(_process_group_id: int) -> tuple[bool, str | None]:
            nonlocal permission_drain_count
            state = next(states)
            if state == permission:
                permission_drain_count = drain_count
            else:
                self.assertIsNotNone(permission_drain_count)
                self.assertGreater(drain_count, permission_drain_count)
            return state

        def drain(*arguments: object, **options: object) -> str | None:
            nonlocal drain_count
            drain_count += 1
            return real_drain(*arguments, **options)  # type: ignore[arg-type]

        with (
            mock.patch.object(bounded_process.sys, "platform", "darwin"),
            mock.patch.object(
                bounded_process,
                "_owned_group_state",
                side_effect=group_state,
            ) as inspect_group,
            mock.patch.object(
                bounded_process,
                "_drain_ready",
                side_effect=drain,
            ),
            mock.patch.object(bounded_process, "_signal_owned_group") as signal_group,
        ):
            result = self._run_python("print('settled')\n")

        self.assertEqual(result.stdout, "settled\n")
        self.assertEqual(inspect_group.call_count, 2)
        signal_group.assert_not_called()

    def test_darwin_post_exit_permission_then_signalable_fails_without_signal(
        self,
    ) -> None:
        permission = (True, bounded_process.PROCESS_GROUP_PERMISSION_ERROR)
        with (
            mock.patch.object(bounded_process.sys, "platform", "darwin"),
            mock.patch.object(
                bounded_process,
                "_owned_group_state",
                side_effect=(permission, (True, None)),
            ) as inspect_group,
            mock.patch.object(bounded_process, "_signal_owned_group") as signal_group,
            self.assertRaises(bounded_process.BoundedProcessCleanupError) as raised,
        ):
            self._run_python("print('ambiguous')\n")

        self.assertIn("became signalable", str(raised.exception))
        self.assertEqual(inspect_group.call_count, 2)
        signal_group.assert_not_called()

    def test_darwin_persistent_post_exit_permission_fails_without_signal(self) -> None:
        permission = (True, bounded_process.PROCESS_GROUP_PERMISSION_ERROR)
        real_monotonic = bounded_process.time.monotonic
        permission_observed = False

        def group_state(_process_group_id: int) -> tuple[bool, str | None]:
            nonlocal permission_observed
            permission_observed = True
            return permission

        def monotonic() -> float:
            return real_monotonic() + (1.0 if permission_observed else 0.0)

        with (
            mock.patch.object(bounded_process.sys, "platform", "darwin"),
            mock.patch.object(
                bounded_process,
                "_owned_group_state",
                side_effect=group_state,
            ) as inspect_group,
            mock.patch.object(bounded_process.time, "monotonic", side_effect=monotonic),
            mock.patch.object(bounded_process, "_signal_owned_group") as signal_group,
            self.assertRaises(bounded_process.BoundedProcessCleanupError) as raised,
        ):
            self._run_python("print('persistent')\n")

        self.assertIn(
            bounded_process.PROCESS_GROUP_PERMISSION_ERROR,
            str(raised.exception),
        )
        self.assertEqual(inspect_group.call_count, 2)
        signal_group.assert_not_called()

    def test_incremental_utf8_decoder_accepts_a_split_code_point(self) -> None:
        observed_process_ids: list[int] = []
        result = self._run_python(
            "import os, time\n"
            "os.write(1, b'\\xe2')\n"
            "time.sleep(0.05)\n"
            "os.write(1, b'\\x82\\xac\\n')\n",
            on_started=observed_process_ids.append,
        )

        self.assertEqual(result, bounded_process.CommandOutput("€\n", ""))
        self.assertEqual(len(observed_process_ids), 1)
        self.assertGreater(observed_process_ids[0], 0)

    def test_nonzero_exit_is_typed_and_diagnostics_are_small_bounded_tails(
        self,
    ) -> None:
        with self.assertRaises(bounded_process.BoundedProcessExitError) as raised:
            self._run_python(
                "import sys\n"
                "sys.stdout.write('a' * 3000)\n"
                "sys.stderr.write('b' * 3000)\n"
                "raise SystemExit(7)\n",
                operation="failing fixture",
                maximum_stdout_bytes=4096,
                maximum_stderr_bytes=4096,
            )

        error = raised.exception
        self.assertIsInstance(error, bounded_process.BoundedProcessError)
        self.assertEqual(error.return_code, 7)
        self.assertEqual(error.stdout, "a" * 3000)
        self.assertEqual(error.stderr, "b" * 3000)
        self.assertIn("failed with exit code 7", str(error))
        self.assertIn("[...truncated...]", str(error))
        self.assertLess(len(str(error)), 5000)

    def test_each_stream_has_an_online_independent_byte_cap(self) -> None:
        for stream_name, descriptor in (("stdout", 1), ("stderr", 2)):
            with self.subTest(stream=stream_name), self._short_cleanup():
                started = time.monotonic()
                with self.assertRaises(
                    bounded_process.BoundedProcessOutputLimitError
                ) as raised:
                    self._run_python(
                        "import os, time\n"
                        f"os.write({descriptor}, b'x' * 4096)\n"
                        "time.sleep(30)\n",
                        operation=f"{stream_name} overflow fixture",
                        timeout_seconds=10,
                        maximum_stdout_bytes=1024 if descriptor == 1 else 8192,
                        maximum_stderr_bytes=1024 if descriptor == 2 else 8192,
                    )
                elapsed = time.monotonic() - started

                error = raised.exception
                self.assertEqual(error.stream_name, stream_name)
                self.assertEqual(error.limit_bytes, 1024)
                self.assertGreaterEqual(error.observed_bytes, 4096)
                self.assertLess(elapsed, 3)

    def test_invalid_utf8_on_each_stream_terminates_the_command(self) -> None:
        for stream_name, descriptor in (("stdout", 1), ("stderr", 2)):
            with self.subTest(stream=stream_name), self._short_cleanup():
                started = time.monotonic()
                with self.assertRaises(
                    bounded_process.BoundedProcessEncodingError
                ) as raised:
                    self._run_python(
                        "import os, time\n"
                        f"os.write({descriptor}, b'\\xff')\n"
                        "time.sleep(30)\n",
                        operation=f"{stream_name} encoding fixture",
                        timeout_seconds=10,
                    )
                elapsed = time.monotonic() - started

                self.assertEqual(raised.exception.stream_name, stream_name)
                self.assertIn("strict UTF-8", str(raised.exception))
                self.assertLess(elapsed, 3)

    def test_timeout_sends_term_then_kill_and_reaps_the_direct_child(self) -> None:
        source = (
            "import os, signal, time\n"
            "def on_term(signum, frame):\n"
            "    os.write(1, b'term-received\\n')\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            "while True:\n"
            "    time.sleep(0.05)\n"
        )
        with self._short_cleanup():
            started = time.monotonic()
            with self.assertRaises(
                bounded_process.BoundedProcessTimeoutError
            ) as raised:
                self._run_python(
                    source,
                    operation="timeout fixture",
                    timeout_seconds=0.2,
                )
            elapsed = time.monotonic() - started

        error = raised.exception
        self.assertEqual(error.timeout_seconds, 0.2)
        self.assertIsNotNone(error.stdout)
        self.assertIn("term-received", error.stdout)
        self.assertFalse(error.cleanup_failures)
        self.assertLess(elapsed, 3)

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_successful_parent_with_residual_group_member_fails_and_cleans_group(
        self,
    ) -> None:
        child = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "while True:\n"
            "    time.sleep(1)\n"
        )
        source = (
            "import subprocess, sys\n"
            f"child = {child!r}\n"
            "process = subprocess.Popen([sys.executable, '-I', '-S', '-B', "
            "'-c', child], stdout=subprocess.PIPE)\n"
            "assert process.stdout.readline() == b'ready\\n'\n"
            "print(process.pid, flush=True)\n"
        )
        with self._short_cleanup():
            started = time.monotonic()
            with self.assertRaises(
                bounded_process.BoundedProcessResidualProcessError
            ) as raised:
                self._run_python(source, operation="residual group fixture")
            elapsed = time.monotonic() - started

        error = raised.exception
        self.assertRegex(error.stdout or "", r"^[0-9]+\n$")
        self.assertFalse(error.cleanup_failures)
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLess(elapsed, 3)
        with self.assertRaises(ProcessLookupError):
            os.killpg(error.process_group_id, 0)

    def test_missing_executable_is_a_typed_launch_failure(self) -> None:
        with self.assertRaises(bounded_process.BoundedProcessLaunchError) as raised:
            bounded_process.run_bounded(
                ["/fonix/does/not/exist"],
                operation="missing executable fixture",
                timeout_seconds=1,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
            )

        self.assertIsInstance(raised.exception, bounded_process.BoundedProcessError)
        self.assertIn("could not start", str(raised.exception))

    def test_malformed_environment_is_a_typed_launch_failure(self) -> None:
        with self.assertRaises(bounded_process.BoundedProcessLaunchError) as raised:
            bounded_process.run_bounded(
                [sys.executable, "-c", "pass"],
                operation="malformed environment fixture",
                environment={"INVALID": object()},  # type: ignore[dict-item]
                timeout_seconds=1,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
            )

        self.assertIsInstance(raised.exception, bounded_process.BoundedProcessError)
        self.assertIn("TypeError", str(raised.exception))

    def test_base_exception_during_launch_closes_prelaunch_selector(self) -> None:
        selector = mock.Mock()
        with (
            mock.patch.object(
                bounded_process.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(
                bounded_process.subprocess,
                "Popen",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            bounded_process.run_bounded(
                [sys.executable, "-c", "pass"],
                operation="interrupted launch fixture",
                timeout_seconds=1,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
            )

        selector.close.assert_called_once_with()

    def test_base_exception_after_launch_retires_group_and_closes_pipes(
        self,
    ) -> None:
        launched: list[object] = []
        real_popen = bounded_process.subprocess.Popen

        def launch(*args: object, **kwargs: object) -> object:
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        with (
            mock.patch.object(
                bounded_process.subprocess,
                "Popen",
                side_effect=launch,
            ),
            mock.patch.object(
                bounded_process,
                "_StreamCapture",
                side_effect=MemoryError,
            ),
            self.assertRaises(MemoryError),
        ):
            bounded_process.run_bounded(
                [sys.executable, "-I", "-S", "-B", "-c", "import time; time.sleep(30)"],
                operation="post-launch interruption fixture",
                timeout_seconds=5,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
            )

        self.assertEqual(len(launched), 1)
        process = launched[0]
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)

    def test_failed_start_observer_retires_the_owned_process_group(self) -> None:
        observed_process_ids: list[int] = []

        def fail_observer(process_id: int) -> None:
            observed_process_ids.append(process_id)
            raise RuntimeError("observer fixture")

        with self._short_cleanup(), self.assertRaises(
            bounded_process.BoundedProcessLaunchError
        ) as raised:
            self._run_python(
                "import time; time.sleep(30)",
                on_started=fail_observer,
            )

        self.assertIn("start observer failed", str(raised.exception))
        self.assertEqual(len(observed_process_ids), 1)
        with self.assertRaises(ProcessLookupError):
            os.killpg(observed_process_ids[0], 0)

    def test_start_observer_control_flow_exception_propagates_after_cleanup(
        self,
    ) -> None:
        observed_process_ids: list[int] = []

        def interrupt_observer(process_id: int) -> None:
            observed_process_ids.append(process_id)
            raise KeyboardInterrupt

        with self._short_cleanup(), self.assertRaises(KeyboardInterrupt):
            self._run_python(
                "import time; time.sleep(30)",
                on_started=interrupt_observer,
            )

        self.assertEqual(len(observed_process_ids), 1)
        with self.assertRaises(ProcessLookupError):
            os.killpg(observed_process_ids[0], 0)

    def test_bounds_accept_required_integration_range_and_reject_invalid_values(
        self,
    ) -> None:
        result = self._run_python(
            "pass\n",
            timeout_seconds=30 * 60,
            maximum_stdout_bytes=16 * 1024 * 1024,
            maximum_stderr_bytes=16 * 1024 * 1024,
        )
        self.assertEqual(result, bounded_process.CommandOutput("", ""))

        invalid_cases = (
            {"timeout_seconds": 0},
            {"timeout_seconds": bounded_process.MAX_TIMEOUT_SECONDS + 1},
            {"maximum_stdout_bytes": 0},
            {
                "maximum_stderr_bytes": (
                    bounded_process.MAX_STREAM_CAPTURE_BYTES + 1
                )
            },
            {"on_started": object()},
        )
        for replacement in invalid_cases:
            with self.subTest(replacement=replacement), self.assertRaises(
                bounded_process.BoundedProcessConfigurationError
            ):
                arguments = {
                    "timeout_seconds": 1,
                    "maximum_stdout_bytes": 1024,
                    "maximum_stderr_bytes": 1024,
                    **replacement,
                }
                bounded_process.run_bounded(
                    [sys.executable, "-I", "-S", "-B", "-c", "pass"],
                    operation="invalid bound fixture",
                    **arguments,
                )


class BoundedProcessPlatformContractTests(unittest.TestCase):
    def test_non_posix_fails_without_claiming_windows_process_ownership(
        self,
    ) -> None:
        with mock.patch.object(bounded_process.os, "name", "nt"), self.assertRaises(
            bounded_process.BoundedProcessUnsupportedPlatformError
        ) as raised:
            bounded_process.run_bounded(
                ["unused"],
                operation="Windows fixture",
                timeout_seconds=1,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
            )

        self.assertIn(
            "Windows Job Object ownership is not implemented",
            str(raised.exception),
        )

    def test_invalid_configuration_fails_before_platform_dispatch(self) -> None:
        with mock.patch.object(bounded_process.os, "name", "nt"), self.assertRaises(
            bounded_process.BoundedProcessConfigurationError
        ):
            bounded_process.run_bounded(
                ["unused"],
                operation="invalid cross-platform bound fixture",
                timeout_seconds=0,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
            )

        self.assertGreaterEqual(
            bounded_process.MAX_TIMEOUT_SECONDS,
            30 * 60,
        )
        self.assertGreaterEqual(
            bounded_process.MAX_STREAM_CAPTURE_BYTES,
            16 * 1024 * 1024,
        )


if __name__ == "__main__":
    unittest.main()
