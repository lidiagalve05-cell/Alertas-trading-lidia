"""RA-030 regressions: consecutive health and audited ambiguous delivery."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from bot2 import Ledger

T=datetime(2026,11,22,12,tzinfo=timezone.utc)
K=("BTCUSDT","FRESHNESS","github")
ISSUE=[("BTCUSDT","FRESHNESS","HIGH","late","github")]

class RA030(unittest.TestCase):
    def test_missing_check_breaks_consecutive_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"))
            l.reconcile(ISSUE,T,[K])
            l.reconcile([],T+timedelta(minutes=1),[K])
            l.reconcile([],T+timedelta(minutes=5),[])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"OPEN")
            l.reconcile([],T+timedelta(minutes=6),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RECOVERING")
            l.reconcile([],T+timedelta(minutes=11),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RESOLVED")
            l.close()

    def test_failure_and_severity_change_restart_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"))
            l.reconcile(ISSUE,T,[K])
            l.reconcile([],T+timedelta(minutes=1),[K])
            l.reconcile([("BTCUSDT","FRESHNESS","CRITICAL","late","github")],T+timedelta(minutes=2),[K])
            l.reconcile([],T+timedelta(minutes=6),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RECOVERING")
            l.close()
            l=Ledger(os.path.join(d,"db"))
            l.reconcile([],T+timedelta(minutes=11),[K])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RESOLVED")
            l.close()

    def test_unknown_held_across_restart_and_audited(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"db")
            l=Ledger(p);l.reconcile(ISSUE,T,[K])
            eid=l.pending(T)[0][0]
            l.delivery(eid,"UNKNOWN",T);l.close()
            l=Ledger(p)
            self.assertEqual(l.pending(T+timedelta(hours=2)),[])
            with self.assertRaises(ValueError):l.delivery(eid,"ACK",T+timedelta(minutes=1))
            with self.assertRaises(ValueError):l.reconcile_unknown(eid,"CONFIRMED_DELIVERED",T,"",actor="auditor_1")
            l.reconcile_unknown(eid,"KEEP_UNKNOWN",T+timedelta(minutes=1),"provider_unavailable",actor="auditor_1")
            self.assertEqual(l.pending(T+timedelta(hours=3)),[])
            l.reconcile_unknown(eid,"CONFIRMED_DELIVERED",T+timedelta(minutes=2),"receipt_42",actor="auditor_1")
            self.assertEqual(l.db.execute("SELECT status FROM outbox WHERE event=?",(eid,)).fetchone()[0],"ACKED")
            self.assertEqual(l.db.execute("SELECT COUNT(*) FROM events WHERE kind='MANUAL_RECONCILIATION'").fetchone()[0],2)
            l.close()
