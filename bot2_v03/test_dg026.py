"""DG-026: isolated multiprocess SQLite tests; no network or real notifications."""
import multiprocessing as mp
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from bot2 import Ledger

T = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
K = ("BTCUSDT", "FRESHNESS", "github")
ISSUE = [("BTCUSDT", "FRESHNESS", "HIGH", "late", "github")]


def writer(path, start, errors):
    try:
        if not start.wait(timeout=15):
            raise TimeoutError("start signal missing")
        ledger = Ledger(path)
        try:
            ledger.reconcile(ISSUE, T, [K])
        finally:
            ledger.close()
    except BaseException as exc:
        errors.put(repr(exc))


def crash_in_transaction(path, ready):
    ledger = Ledger(path)
    ledger.db.execute("BEGIN IMMEDIATE")
    ledger.db.execute(
        "INSERT INTO incidents VALUES (?,?,?,?,?,?,?)",
        ("interrupted", '["BTCUSDT","FRESHNESS","github"]', 1,
         "OPEN", "HIGH", None, T.isoformat().replace("+00:00", "Z")),
    )
    ready.set()
    os._exit(37)


class MultiprocessDG026(unittest.TestCase):
    def test_independent_processes_single_episode(self):
        ctx = mp.get_context("spawn")
        for repetition in range(5):
            with self.subTest(repetition=repetition), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "ledger.sqlite")
                start = ctx.Event()
                errors = ctx.Queue()
                workers = [ctx.Process(target=writer, args=(path, start, errors)) for _ in range(6)]
                for worker in workers:
                    worker.start()
                start.set()
                for worker in workers:
                    worker.join(timeout=45)
                for worker in workers:
                    if worker.is_alive():
                        worker.terminate()
                        worker.join(timeout=5)
                self.assertTrue(all(worker.exitcode == 0 for worker in workers),
                                [worker.exitcode for worker in workers])
                failures = []
                while not errors.empty():
                    failures.append(errors.get())
                self.assertEqual(failures, [])
                ledger = Ledger(path)
                try:
                    self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)
                    self.assertEqual(ledger.db.execute(
                        "SELECT COUNT(*) FROM events WHERE kind='OPEN'").fetchone()[0], 1)
                    self.assertEqual(ledger.db.execute(
                        "SELECT COUNT(*) FROM outbox").fetchone()[0], 1)
                finally:
                    ledger.close()

    def test_crash_rolls_back_and_recovery_persists(self):
        ctx = mp.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ledger.sqlite")
            ready = ctx.Event()
            worker = ctx.Process(target=crash_in_transaction, args=(path, ready))
            worker.start()
            self.assertTrue(ready.wait(timeout=15), "child never reached transaction")
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            self.assertEqual(worker.exitcode, 37)
            ledger = Ledger(path)
            try:
                self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 0)
                ledger.reconcile(ISSUE, T, [K])
                eid = ledger.pending(T)[0][0]
            finally:
                ledger.close()
            ledger = Ledger(path)
            try:
                self.assertEqual(ledger.pending(T)[0][0], eid)
                self.assertEqual(ledger.db.execute(
                    "SELECT COUNT(*) FROM incidents").fetchone()[0], 1)
                self.assertEqual(ledger.db.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='OPEN'").fetchone()[0], 1)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
