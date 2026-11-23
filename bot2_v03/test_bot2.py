import os, tempfile, unittest
from datetime import datetime,timedelta,timezone
from bot2 import ASSETS,Ledger,MockAdapter,inspect_csv,inspect_external,supervise_heartbeat
NOW=datetime(2026,11,22,12,tzinfo=timezone.utc)
HEAD="hora,activo,apertura,maximo,minimo,cierre,volumen\n"
def rows(offsets=(10,5),bad=False,duplicate=False):
    lines=[]
    for a in ASSETS:
        for offset in offsets:
            t=(NOW-timedelta(minutes=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append(f"{t},{a},100,110,90,{120 if bad else 105},1")
    if duplicate:lines.append(lines[0])
    return HEAD+"\n".join(lines)+"\n"
class Cases(unittest.TestCase):
    def test_T01_valid(self):self.assertEqual(inspect_csv(rows(),NOW),[])
    def test_T02_freshness(self):
        for age,sev in ((7,None),(8,"WARNING"),(10,"WARNING"),(11,"HIGH"),(20,"HIGH"),(21,"CRITICAL")):
            found=[x[2] for x in inspect_csv(rows((10,)),NOW+timedelta(minutes=age-5)) if x[1]=="FRESHNESS"]
            self.assertEqual(found,[] if sev is None else [sev]*3)
    def test_T03_continuity(self):
        self.assertEqual(sum(x[1]=="CONTINUITY" for x in inspect_csv(rows((20,5)),NOW)),3)
        self.assertTrue(all(x[2]=="CRITICAL" for x in inspect_csv(rows((25,5)),NOW) if x[1]=="CONTINUITY"))
    def test_T04_duplicates_integrity(self):
        self.assertTrue(any(x[1]=="DUPLICATE" for x in inspect_csv(rows(duplicate=True),NOW)))
        self.assertTrue(any(x[1]=="INTEGRITY" for x in inspect_csv(rows(bad=True),NOW)))
    def test_T05_external(self):
        self.assertEqual(inspect_external(503,b""),"HTTP_FAILURE")
        self.assertEqual(inspect_external(200,b"abc",cached=True),"CACHE_UNCERTAIN")
    def test_T06_telegram(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"db");l=Ledger(p);key=("BTCUSDT","FRESHNESS","github")
            l.reconcile([("BTCUSDT","FRESHNESS","HIGH","late","github")],NOW,[key])
            eid=l.pending()[0][0];l.delivery(eid,MockAdapter("UNKNOWN").send(eid),NOW);l.close()
            l=Ledger(p);self.assertEqual(l.db.execute("SELECT status FROM outbox").fetchone()[0],"UNKNOWN")
            l.reconcile_unknown(eid,"CONFIRMED_DELIVERED",NOW,"provider_receipt_123");self.assertEqual(l.pending(),[]);l.close()
    def test_T07_watchdog(self):
        self.assertEqual(supervise_heartbeat(NOW,NOW-timedelta(minutes=13)),"MONITOR_CRITICAL")
        self.assertEqual(supervise_heartbeat(NOW,None,supervisor_ok=False),"SUPERVISOR_UNKNOWN")
    def test_integration_recovery_episode(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"db");l=Ledger(p);key=("BTCUSDT","FRESHNESS","github")
            l.reconcile([("BTCUSDT","FRESHNESS","CRITICAL","late","github")],NOW,[key])
            l.reconcile([("BTCUSDT","FRESHNESS","HIGH","late","github")],NOW+timedelta(minutes=1),[key])
            self.assertEqual(l.db.execute("SELECT kind FROM events ORDER BY rowid").fetchall(),[("OPEN",),("DEESCALATE",)])
            l.reconcile([],NOW+timedelta(minutes=2),[key]);l.close()
            l=Ledger(p);l.reconcile([],NOW+timedelta(minutes=6),[key])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RECOVERING")
            l.reconcile([],NOW+timedelta(minutes=7),[key])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"RESOLVED")
            l.reconcile([("BTCUSDT","FRESHNESS","HIGH","late","github")],NOW+timedelta(minutes=8),[key])
            self.assertEqual(l.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],2);l.close()
    def test_integration_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"));key=("ETHUSDT","FRESHNESS","github")
            l.reconcile([("ETHUSDT","FRESHNESS","HIGH","late","github")],NOW,[key])
            l.reconcile([],NOW+timedelta(minutes=20),[])
            self.assertEqual(l.db.execute("SELECT status FROM incidents").fetchone()[0],"OPEN");l.close()
    def test_integration_source_separation(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"))
            issues=[("BTCUSDT","EXTERNAL","HIGH","fail","mirror1"),("BTCUSDT","EXTERNAL","HIGH","fail","mirror2")]
            l.reconcile(issues,NOW,[(x[0],x[1],x[4]) for x in issues])
            self.assertEqual(l.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],2);l.close()
    def test_retry_due_and_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(os.path.join(d,"db"));key=("SOLUSDT","FRESHNESS","github")
            l.reconcile([("SOLUSDT","FRESHNESS","HIGH","late","github")],NOW,[key])
            eid=l.pending(NOW)[0][0];l.delivery(eid,"RETRY",NOW)
            self.assertEqual(l.pending(NOW+timedelta(seconds=30)),[])
            self.assertEqual(l.pending(NOW+timedelta(seconds=61))[0][0],eid)
            l.delivery(eid,"UNKNOWN",NOW+timedelta(seconds=61))
            self.assertEqual(l.pending(NOW+timedelta(hours=1)),[]);l.close()
    def test_sqlite_reopen_no_duplicate_open(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"db");key=("BTCUSDT","FRESHNESS","github")
            l=Ledger(p);issue=[("BTCUSDT","FRESHNESS","HIGH","late","github")]
            l.reconcile(issue,NOW,[key]);l.close()
            l=Ledger(p);l.reconcile(issue,NOW+timedelta(minutes=1),[key])
            self.assertEqual(l.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],1)
            self.assertEqual(l.db.execute("SELECT COUNT(*) FROM events WHERE kind='OPEN'").fetchone()[0],1);l.close()
if __name__=="__main__":unittest.main(verbosity=2)
