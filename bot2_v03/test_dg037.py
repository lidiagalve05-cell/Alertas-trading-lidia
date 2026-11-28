"""DG-037 regression checks: missed cycles and UNKNOWN isolation."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from bot2 import Ledger

T=datetime(2026,11,22,12,tzinfo=timezone.utc)
K=("BTCUSDT","FRESHNESS","github")
ISSUE=[("BTCUSDT","FRESHNESS","HIGH","late","github")]

class DG037(unittest.TestCase):
    def test_skipped_cycle_cannot_resolve_even_after_long_delay(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"db"); l=Ledger(p)
            l.reconcile(ISSUE,T,[K])
            l.reconcile([],T+timedelta(minutes=5),[K]);l.close()
            l=Ledger(p)
            l.reconcile([],T+timedelta(minutes=15),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RECOVERING")
            self.assertEqual(l.db.execute("SELECT COUNT(*) FROM events WHERE kind='RESOLVE'").fetchone()[0],0)
            l.reconcile([],T+timedelta(minutes=20),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RESOLVED")
            l.close()

    def test_unknown_never_pending_and_actor_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"db");l=Ledger(p);l.reconcile(ISSUE,T,[K])
            eid=l.pending(T)[0][0];l.delivery(eid,"UNKNOWN",T);l.close()
            l=Ledger(p)
            self.assertEqual(l.pending(),[])
            self.assertEqual(l.pending(T+timedelta(hours=2)),[])
            with self.assertRaises(ValueError):
                l.reconcile_unknown(eid,"CONFIRMED_DELIVERED",T,"receipt")
            l.reconcile_unknown(eid,"KEEP_UNKNOWN",T+timedelta(minutes=1),"No provider response",actor="reviewer_7")
            l.close()
            l=Ledger(p)
            self.assertEqual(l.pending(),[])
            self.assertEqual(l.pending(T+timedelta(hours=3)),[])
            row=l.db.execute("SELECT actor,at,decision,justification FROM manual_reconciliations WHERE event=?",(eid,)).fetchone()
            self.assertEqual(row,("reviewer_7",(T+timedelta(minutes=1)).isoformat().replace("+00:00","Z"),"KEEP_UNKNOWN","No provider response"))
            l.close()

if __name__=="__main__":unittest.main(verbosity=2)
