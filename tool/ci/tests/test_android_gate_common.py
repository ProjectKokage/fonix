from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


CI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_ROOT))

import android_gate_common as common  # noqa: E402


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _read_pids(path: Path) -> tuple[int, int]:
    parent, child = path.read_text(encoding="utf-8").split()
    return int(parent), int(child)


class RunBoundedTest(unittest.TestCase):
    def test_captures_stdout_and_stderr(self) -> None:
        result = common.run_bounded(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            operation="capture test",
            timeout_seconds=5,
            maximum_output=1024,
        )

        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_timeout_kills_descendant_that_ignores_termination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-gate-timeout-") as temporary:
            root = Path(temporary)
            pid_file = root / "pids"
            marker = root / "descendant-finished"
            script = textwrap.dedent(
                """
                import os
                from pathlib import Path
                import subprocess
                import sys
                import time

                descendant = subprocess.Popen([
                    sys.executable,
                    "-c",
                    (
                        "import signal,sys,time; "
                        "from pathlib import Path; "
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                        "time.sleep(1.5); "
                        "Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
                    ),
                    sys.argv[2],
                ])
                Path(sys.argv[1]).write_text(
                    f"{os.getpid()} {descendant.pid}", encoding="utf-8"
                )
                print("ready", flush=True)
                time.sleep(60)
                """
            )

            with self.assertRaisesRegex(
                common.AndroidGateCommonError, "timed out after 1 seconds"
            ):
                common.run_bounded(
                    [sys.executable, "-c", script, str(pid_file), str(marker)],
                    operation="timeout tree test",
                    timeout_seconds=1,
                    maximum_output=1024,
                )

            _, descendant_pid = _read_pids(pid_file)
            self.addCleanup(_kill_pid, descendant_pid)
            time.sleep(0.75)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_output_overflow_kills_the_owned_process_group(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-gate-overflow-") as temporary:
            root = Path(temporary)
            pid_file = root / "pids"
            marker = root / "descendant-finished"
            script = textwrap.dedent(
                """
                import os
                from pathlib import Path
                import subprocess
                import sys
                import time

                descendant = subprocess.Popen([
                    sys.executable,
                    "-c",
                    (
                        "import signal,sys,time; "
                        "from pathlib import Path; "
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                        "time.sleep(0.5); "
                        "Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
                    ),
                    sys.argv[2],
                ])
                Path(sys.argv[1]).write_text(
                    f"{os.getpid()} {descendant.pid}", encoding="utf-8"
                )
                sys.stdout.write("x" * 131072)
                sys.stdout.flush()
                time.sleep(60)
                """
            )

            with self.assertRaisesRegex(
                common.AndroidGateCommonError, "stdout exceeds 1024 bytes"
            ):
                common.run_bounded(
                    [sys.executable, "-c", script, str(pid_file), str(marker)],
                    operation="overflow tree test",
                    timeout_seconds=5,
                    maximum_output=1024,
                )

            _, descendant_pid = _read_pids(pid_file)
            self.addCleanup(_kill_pid, descendant_pid)
            time.sleep(0.75)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_fails_when_an_escaped_descendant_keeps_pipes_open(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fonix-gate-drain-") as temporary:
            pid_file = Path(temporary) / "pids"
            script = textwrap.dedent(
                """
                import os
                from pathlib import Path
                import subprocess
                import sys

                descendant = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    start_new_session=True,
                )
                Path(sys.argv[1]).write_text(
                    f"{os.getpid()} {descendant.pid}", encoding="utf-8"
                )
                """
            )
            started = time.monotonic()
            try:
                with (
                    mock.patch.object(
                        common, "PROCESS_TERMINATION_GRACE_SECONDS", 0.1
                    ),
                    mock.patch.object(common, "OUTPUT_DRAIN_GRACE_SECONDS", 0.1),
                ):
                    with self.assertRaisesRegex(
                        common.AndroidGateCommonError,
                        "stdout did not reach EOF after process termination",
                    ):
                        common.run_bounded(
                            [sys.executable, "-c", script, str(pid_file)],
                            operation="pipe drain test",
                            timeout_seconds=5,
                            maximum_output=1024,
                        )
            finally:
                if pid_file.exists():
                    _, descendant_pid = _read_pids(pid_file)
                    _kill_pid(descendant_pid)

            self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
