"""DG-014: fault-injection checks; local only, no network."""
import os,tempfile,threading,unittest
from datetime import datetime,timedelta,timezone
from bot2 import Ledger,inspect_csv
T=datetime(2026,11,22,12,tzinfo=timezone.utc)
K=("BTCUSDT","FRESHNESS","github")
ISSUE=[("BTCUSDT","FRESHNESS","HIGH","late","github")]
class DG014(unittest.TestCase):
    def test_missing_observation_never_closes(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"));l.reconcile(ISSUE,T,[K])
            l.reconcile([],T+timedelta(minutes=30),[])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"OPEN");l.close()
    def test_failed_observation_resets_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"));l.reconcile(ISSUE,T,[K])
            l.reconcile([],T+timedelta(minutes=1),[K])
            l.reconcile(ISSUE,T+timedelta(minutes=2),[K])
            l.reconcile([],T+timedelta(minutes=6),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RECOVERING");l.close()
    def test_regressive_clock_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"));l.reconcile(ISSUE,T,[K])
            with self.assertRaises(ValueError):l.reconcile([],T-timedelta(seconds=1),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"OPEN");l.close()
    def test_future_timestamp_rejected(self):
        opening=(T+timedelta(minutes=5)).isoformat().replace("+00:00","Z")
        csv="hora,activo,apertura,maximo,minimo,cierre,volumen\n"+f"{opening},BTCUSDT,100,110,90,105,1\n"
        self.assertTrue(any(x[1]=="INTEGRITY" for x in inspect_csv(csv,T)))
    def test_unknown_delivery_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"));l.reconcile(ISSUE,T,[K])
            eid=l.pending(T)[0][0];l.delivery(eid,"UNKNOWN",T)
            self.assertEqual(l.pending(T+timedelta(hours=1)),[])
            with self.assertRaises(ValueError):l.delivery(eid,"RETRY",T+timedelta(minutes=1))
            l.reconcile_unknown(eid,"CONFIRMED_DELIVERED",T+timedelta(minutes=2),"provider_receipt",actor="auditor_1")
            self.assertEqual(l.pending(),[]);l.close()
    def test_reopen_preserves_outbox(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"db");l=Ledger(path);l.reconcile(ISSUE,T,[K]);eid=l.pending(T)[0][0];l.close()
            l=Ledger(path);self.assertEqual(l.pending(T)[0][0],eid);l.close()
    def test_concurrent_writers_no_duplicate_episode(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"db");errors=[]
            def worker():
                try:
                    l=Ledger(path);l.reconcile(ISSUE,T,[K]);l.close()
                except Exception as exc:errors.append(str(exc))
            threads=[threading.Thread(target=worker) for _ in range(2)]
            for t in threads:t.start()
            for t in threads:t.join()
            self.assertEqual(errors,[])
            l=Ledger(path);self.assertEqual(l.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],1);l.close()
    def test_parallel_initialization_stress(self):
        for repetition in range(5):
            with tempfile.TemporaryDirectory() as d:
                path=os.path.join(d,"db")
                barrier=threading.Barrier(6)
                errors=[]
                def worker():
                    try:
                        barrier.wait(timeout=10)
                        ledger=Ledger(path)
                        ledger.reconcile(ISSUE,T,[K])
                        ledger.close()
                    except Exception as exc:
                        errors.append(repr(exc))
                workers=[threading.Thread(target=worker) for _ in range(6)]
                for thread in workers:thread.start()
                for thread in workers:thread.join(timeout=40)
                self.assertFalse(any(thread.is_alive() for thread in workers),"worker did not finish")
                self.assertEqual(errors,[])
                ledger=Ledger(path)
                self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],1)
                self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM events WHERE kind='OPEN'").fetchone()[0],1)
                ledger.close()
if __name__=="__main__":unittest.main(verbosity=2)
