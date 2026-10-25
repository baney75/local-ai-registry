#!/usr/bin/env python3
"""groupwatch.py fails closed: a teardown that is not clean never exits 0, from `run` or from `stop`.

  python3 -m unittest test_groupwatch      (from this directory; needs ps with lstart, as on macOS)

In-process cases run groupwatch.run() on a real short command with Supervisor.teardown replaced (the AST-tail case of the
review: the command exits 0 and teardown reports False). Program cases run groupwatch.py itself and signal it, and run
`stop` against a stand-in supervisor whose log the test writes.
"""
import os, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import groupwatch  # noqa: E402

GW = os.path.join(HERE, "groupwatch.py")


class InProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "gw")

    def run_gw(self, cmd, teardown, *opts, record=None):
        made, init = [], groupwatch.Supervisor.__init__
        def track(sv, state):
            init(sv, state)
            made.append(sv)
        self.made = made
        with patch.object(groupwatch.Supervisor, "teardown", teardown), patch.object(groupwatch.Supervisor, "__init__", track), \
                patch.object(groupwatch.Supervisor, "record", record or groupwatch.Supervisor.record), \
                patch.object(groupwatch.signal, "signal"):
            rc = groupwatch.run(["--state", self.state, *opts, "--", *cmd])
        for sv in made:  # the stand-in teardown killed nothing: end the child here, and close the log
            if sv.proc.poll() is None:
                sv.proc.kill()
            sv.proc.wait()
            sv.log.close()
        log = Path(self.state, "groupwatch.log").read_text()
        return rc, log

    def test_successful_command_with_unclean_teardown_is_not_success(self):
        rc, log = self.run_gw(["true"], lambda *a: False)
        self.assertEqual(rc, groupwatch.UNCLEAN)
        self.assertIn("EXIT: command exited 0; clean=False", log)
        self.assertIn("FAIL: teardown not clean; exit 4 (would have been 0)", log)

    def test_failing_command_keeps_its_own_code_when_unclean(self):
        rc, _ = self.run_gw(["sh", "-c", "exit 7"], lambda *a: False)
        self.assertEqual(rc, 7)

    def test_a_teardown_that_raises_counts_as_unclean(self):
        def boom(*a):
            raise OSError("ps failed")
        rc, log = self.run_gw(["true"], boom)
        self.assertEqual(rc, groupwatch.UNCLEAN)
        self.assertIn("teardown failed: OSError: ps failed", log)
        self.assertIn("clean=False", log)

    def test_clean_runs_keep_their_codes(self):
        self.assertEqual(self.run_gw(["true"], lambda *a: True)[0], 0)
        self.assertEqual(self.run_gw(["sh", "-c", "exit 5"], lambda *a: True)[0], 5)

    def test_a_telemetry_failure_after_launch_still_tears_down_and_fails(self):
        # ps_table() runs ps with timeout=10: a TimeoutExpired from record() must not escape with the child left running
        calls = []
        def timeout(sv, root):
            raise subprocess.TimeoutExpired("ps", 10)
        def teardown(sv, root, pgid, reason):
            calls.append(reason)
            sv.proc.kill(); sv.proc.wait()  # what a real teardown does to the child
            return True
        rc, log = self.run_gw(["sleep", "30"], teardown, record=timeout)
        self.assertEqual(rc, groupwatch.MONITOR_FAILED)
        self.assertEqual(calls, ["monitor failed (TimeoutExpired)"])
        self.assertIn("monitor failed: TimeoutExpired", log)
        self.assertIn("ABORT: monitor failed (TimeoutExpired); clean=True", log)
        self.assertIn("FAIL: supervision failed; exit 5", log)

    def test_a_telemetry_failure_with_a_failing_teardown_kills_the_child_and_exits_4(self):
        def timeout(sv, root):
            raise subprocess.TimeoutExpired("ps", 10)
        def broken(*a):
            raise subprocess.TimeoutExpired("ps", 10)
        rc, log = self.run_gw(["sleep", "30"], broken, record=timeout)
        self.assertEqual(rc, groupwatch.UNCLEAN)
        self.assertIsNotNone(self.made[0].proc.returncode)  # the direct child was killed by the fallback, not left running
        self.assertIn("killed direct child", log)
        self.assertIn("ABORT: monitor failed (TimeoutExpired); clean=False", log)

    def test_an_abort_exits_3_when_clean_and_4_when_not(self):
        trigger = os.path.join(self.tmp.name, "stop-now")
        Path(trigger).write_text("")
        rc, log = self.run_gw(["sleep", "30"], lambda *a: True, "--trigger-file", trigger)
        self.assertEqual(rc, 3)
        self.assertIn("ABORT: trigger file; clean=True", log)
        rc, log = self.run_gw(["sleep", "30"], lambda *a: False, "--trigger-file", trigger)
        self.assertEqual(rc, groupwatch.UNCLEAN)
        self.assertIn("ABORT: trigger file; clean=False", log)


class Program(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "gw")

    def start(self, *cmd):
        p = subprocess.Popen([sys.executable, "-I", GW, "run", "--state", self.state, "--", *cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(100):  # until the supervisor has recorded its child
            if Path(self.state, "child.pid").exists() and "started child" in Path(self.state, "groupwatch.log").read_text():
                break
            time.sleep(0.1)
        time.sleep(1.2)
        return p

    def test_sigterm_tears_down_cleanly_and_exits_3(self):
        p = self.start("sleep", "60")
        child = int(Path(self.state, "child.pid").read_text())
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=60), 3)
        self.assertTrue(Path(self.state, "groupwatch.log").read_text().rstrip().endswith("ABORT: supervisor got SIGTERM; clean=True"))
        self.assertNotIn(child, groupwatch.ps_table())

    def test_stop_reports_a_clean_teardown_with_0(self):
        self.start("sleep", "60")
        r = subprocess.run([sys.executable, "-I", GW, "stop", "--state", self.state], capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(r.stdout.strip().endswith("clean=True"))

    def stand_in(self, final_lines, earlier=()):
        """A process that `stop` accepts as the recorded supervisor (pid, start time, argv), with a log this test writes."""
        os.makedirs(self.state)
        p = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)", "groupwatch-stand-in"])
        self.addCleanup(lambda: p.poll() is None and p.kill())
        me = None
        for _ in range(50):
            me = groupwatch.ps_table().get(p.pid)
            if me and "groupwatch-stand-in" in me[3]:
                break
            time.sleep(0.1)
        assert me, "the stand-in supervisor did not start"
        Path(self.state, "supervisor.id").write_text(f"{p.pid}\n{me[2]}\n{me[3]}\n")
        lines = [*earlier, f"2026-10-25T00:00:00 supervisor pid {p.pid} started child pid 1 pgid 1: sleep 60", *final_lines]
        Path(self.state, "groupwatch.log").write_text("".join(l + "\n" for l in lines))
        return p

    def stop(self):
        return subprocess.run([sys.executable, "-I", GW, "stop", "--state", self.state], capture_output=True, text=True, timeout=90)

    def test_stop_is_not_success_when_the_supervisor_logged_clean_false(self):
        self.stand_in(["2026-10-25T00:00:01 ABORT: supervisor got SIGTERM; clean=False"])
        r = self.stop()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("without a clean teardown", r.stdout)

    def test_stop_is_not_success_when_the_supervisor_vanished_without_a_final_line(self):
        # an earlier run in the same state directory ended clean; that line must not vouch for this supervisor
        self.stand_in([], earlier=["2026-10-24T00:00:00 supervisor pid 1 started child pid 2 pgid 2: x",
                                   "2026-10-24T00:00:09 EXIT: command exited 0; clean=True"])
        r = self.stop()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("left no final line", r.stdout)

    def test_stop_is_not_success_when_a_fail_line_follows_a_clean_abort(self):
        self.stand_in(["2026-10-25T00:00:01 monitor failed: TimeoutExpired: ps",
                       "2026-10-25T00:00:02 ABORT: monitor failed (TimeoutExpired); clean=True",
                       "2026-10-25T00:00:02 FAIL: supervision failed; exit 5"])
        r = self.stop()
        self.assertNotEqual(r.returncode, 0, r.stdout)

    def test_stop_accepts_this_supervisors_clean_final_line(self):
        self.stand_in(["2026-10-25T00:00:01 ABORT: supervisor got SIGTERM; clean=True"])
        r = self.stop()
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
