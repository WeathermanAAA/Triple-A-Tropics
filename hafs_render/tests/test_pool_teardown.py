#!/usr/bin/env python3
"""Behavioural proof for the incident-#4 teardown fix: a pool of workers that
are DELIBERATELY unkillable by SIGTERM (signal blocked, then wedged inside a
single long C call - the production wedge's signature) must not stop the
stage deadline from salvaging, must actually DIE (SIGKILL), and must leave
nothing behind that can block the main thread later - the exact hazard that
cost run 30661097361 its finished build one second short of the manifest
write (an unbounded multiprocessing finalizer join fired from cyclic GC).
"""
import gc
import multiprocessing as mp
import os
import signal
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hafs_render import generate_hafs_plots as g  # noqa: E402


def _job(job):
    if job.startswith("wedge"):
        # The production wedge, reproduced honestly: SIGTERM delivery blocked
        # (a wedged C call never returns to the interpreter, so handlers never
        # run - masking gets the same immunity deterministically), then ONE
        # long blocking C call. Only SIGKILL can end this process.
        signal.pthread_sigmask(signal.SIG_BLOCK,
                               {signal.SIGTERM, signal.SIGALRM})
        time.sleep(3600)
    return {"ok": True, "job": job}


@unittest.skipUnless(os.name == "posix", "SIGKILL/sigmask semantics are POSIX")
class TestDeadlineKillsUnkillableWorkers(unittest.TestCase):
    def test_salvage_publish_and_clean_exit_path(self):
        recorded = []
        t0 = time.time()
        g._run_pool(["a", "b", "wedge1", "wedge2"], _job, 2,
                    lambda r: recorded.append(r),
                    lambda j: {"ok": False, "job": j},
                    stage_deadline_s=6)
        elapsed = time.time() - t0

        # 1. The stage returned promptly: deadline + kill + reap, no 3h wedge.
        self.assertLess(elapsed, 60)
        # 2. Everything is recorded: the completed tasks ok, the wedged ones
        #    as failures - the salvage set the next stage runs on.
        by_job = {r["job"]: r["ok"] for r in recorded}
        self.assertEqual(by_job.get("a"), True)
        self.assertEqual(by_job.get("b"), True)
        self.assertEqual(by_job.get("wedge1"), False)
        self.assertEqual(by_job.get("wedge2"), False)
        # 3. The wedged workers are DEAD, not orphans: SIGKILL needs no
        #    interpreter participation. (active_children also reaps.)
        deadline = time.time() + 10
        while mp.active_children() and time.time() < deadline:
            time.sleep(0.2)
        self.assertEqual(mp.active_children(), [])
        # 4. THE PRODUCTION KILL-SHOT: cyclic GC after the stage. With workers
        #    merely SIGTERM'd (and immune), the call-queue feeder thread stayed
        #    blocked and multiprocessing's unbounded Queue._finalize_join hung
        #    the main thread here. With SIGKILL + reap it must return at once.
        t1 = time.time()
        gc.collect()
        self.assertLess(time.time() - t1, 30)


if __name__ == "__main__":
    unittest.main()


class TestStallForensics(unittest.TestCase):
    """The evidence capture must run against a live process, log the kernel-
    side facts, and be incapable of hanging or raising - it fires while the
    build is wedged and the stage deadline still has salvaging to do."""

    def test_capture_on_live_child_is_bounded_and_quiet(self):
        proc = mp.Process(target=time.sleep, args=(60,))
        proc.start()
        try:
            t0 = time.time()
            with self.assertLogs("hafs-build", level="ERROR") as cm:
                g._stall_forensics([proc.pid])
            out = "\n".join(cm.output)
            self.assertLess(time.time() - t0, 120)
            self.assertIn("STALL FORENSICS", out)
            self.assertIn("State:", out)          # /proc status reached
            self.assertIn("wchan=", out)
        finally:
            proc.kill()
            proc.join(timeout=10)

    def test_capture_of_dead_pid_never_raises(self):
        g._stall_forensics([99999999])
