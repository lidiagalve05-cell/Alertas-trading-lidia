"""Bot 2 v0.3: núcleo aislado, sin red ni servicios activos."""
import csv, hashlib, io, json, math, sqlite3, time
from datetime import datetime, timedelta, timezone

ASSETS=("BTCUSDT","ETHUSDT","SOLUSDT")
RANK={"WARNING":1,"HIGH":2,"CRITICAL":3}
FIVE=timedelta(minutes=5)

def utc(t):
    if t.tzinfo is None or t.utcoffset()!=timedelta(0): raise ValueError("UTC required")
    return t.isoformat().replace("+00:00","Z")

def inspect_csv(raw,now,source="github-published"):
    utc(now); issues=[]; per={a:[] for a in ASSETS}; seen={}
    reader=csv.DictReader(io.StringIO(raw))
    fields={"hora","activo","apertura","maximo","minimo","cierre","volumen"}
    if not reader.fieldnames or not fields.issubset(reader.fieldnames):
        return [("ALL","INTEGRITY","CRITICAL","schema",source)]
    for n,row in enumerate(reader,2):
        try:
            a=row["activo"]
            if a not in per: raise ValueError("unknown_asset")
            t=datetime.fromisoformat(row["hora"].replace("Z","+00:00"));utc(t)
            if t.minute%5 or t.second or t.microsecond: raise ValueError("unaligned")
            o,h,l,c,v=(float(row[k]) for k in ("apertura","maximo","minimo","cierre","volumen"))
            if not all(map(math.isfinite,(o,h,l,c,v))) or min(o,h,l,c)<=0 or v<0 or l>min(o,c) or h<max(o,c): raise ValueError("ohlcv")
            key=(a,t); value=(o,h,l,c,v)
            if key in seen: issues.append((a,"DUPLICATE","HIGH" if seen[key]==value else "CRITICAL",utc(t),source))
            seen[key]=value;per[a].append(t)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            issues.append((row.get("activo") or "ALL","INTEGRITY","CRITICAL",f"row_{n}:{str(exc)[:40]}",source))
    for a,times in per.items():
        if not times:
            issues.append((a,"FRESHNESS","CRITICAL","no_candles",source));continue
        ordered=sorted(set(times))
        for x,y in zip(ordered,ordered[1:]):
            missing=int((y-x)/FIVE)-1
            if missing>0: issues.append((a,"CONTINUITY","CRITICAL" if missing>=3 else "HIGH",f"{utc(x)}:{missing}",source))
        age=(now-ordered[-1]-FIVE).total_seconds()/60
        if ordered[-1] > now or age < -5: issues.append((a,"INTEGRITY","CRITICAL","future",source))
        elif age>7: issues.append((a,"FRESHNESS","CRITICAL" if age>20 else "HIGH" if age>10 else "WARNING",f"{age:.3f}m",source))
    return issues

class Ledger:
    """SQLite transaccional: episodios, eventos y outbox durable."""
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=30)
        self.db.execute("PRAGMA busy_timeout=30000")
        # WAL initialization may itself race with another process opening the DB.
        # Retry only SQLITE_BUSY/LOCKED during initialization, with a hard deadline.
        deadline=time.monotonic()+30
        while True:
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or time.monotonic()>=deadline:
                    self.db.close()
                    raise
                time.sleep(0.05)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, ikey TEXT, episode INTEGER,
          status TEXT, severity TEXT, good_since TEXT, updated TEXT, UNIQUE(ikey,episode));
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, incident TEXT, kind TEXT, at TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS outbox(event TEXT PRIMARY KEY, status TEXT DEFAULT 'PENDING',
          attempts INTEGER DEFAULT 0, next_at TEXT, last_error TEXT);
        CREATE TABLE IF NOT EXISTS manual_reconciliations(id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, decision TEXT NOT NULL, justification TEXT NOT NULL);
        """)
    def event(self,iid,kind,now,detail=""):
        eid=hashlib.sha256(f"{iid}|{kind}|{utc(now)}|{detail}".encode()).hexdigest()[:32]
        self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",(eid,iid,kind,utc(now),detail))
        if kind in ("OPEN","ESCALATE","RESOLVE"):
            self.db.execute("INSERT OR IGNORE INTO outbox(event) VALUES (?)",(eid,))
    def reconcile(self,issues,now,checked_keys):
        utc(now); failures={}
        for a,k,s,d,src in issues:
            key=(a,k,src)
            if key not in failures or RANK[s]>RANK[failures[key][0]]: failures[key]=(s,d)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            checked=set(checked_keys)|set(failures)
            for iid, ikey in self.db.execute("SELECT id,ikey FROM incidents WHERE status='RECOVERING'").fetchall():
                if tuple(json.loads(ikey)) not in checked:
                    self.db.execute("UPDATE incidents SET status='OPEN',good_since=NULL,updated=? WHERE id=?",(utc(now),iid))
                    self.event(iid,"RECOVERY_RESET",now,"missing_observation")
            for a,k,src in sorted(checked):
                key=(a,k,src); ikey=json.dumps(key,separators=(",",":"))
                old=self.db.execute("SELECT id,episode,status,severity,good_since,updated FROM incidents WHERE ikey=? ORDER BY episode DESC LIMIT 1",(ikey,)).fetchone()
                if old and now < datetime.fromisoformat(old[5].replace("Z","+00:00")):
                    raise ValueError("regressive_observation_clock")
                fail=failures.get(key)
                if fail:
                    severity,detail=fail
                    if not old or old[2]=="RESOLVED":
                        episode=old[1]+1 if old else 1
                        iid=hashlib.sha256(f"{ikey}|{episode}".encode()).hexdigest()[:24]
                        self.db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?)",(iid,ikey,episode,"OPEN",severity,None,utc(now)))
                        self.event(iid,"OPEN",now,detail)
                    else:
                        iid,_,_,prev,_,_=old
                        transition="ESCALATE" if RANK[severity]>RANK[prev] else "DEESCALATE" if RANK[severity]<RANK[prev] else None
                        self.db.execute("UPDATE incidents SET status='OPEN',severity=?,good_since=NULL,updated=? WHERE id=?",(severity,utc(now),iid))
                        if transition:self.event(iid,transition,now,detail)
                elif old and old[2]!="RESOLVED":
                    iid,_,_,_,good,last_checked=old
                    # Strict cadence: a missed cycle invalidates consecutiveness.
                    if good and now-datetime.fromisoformat(last_checked.replace("Z","+00:00"))!=FIVE:
                        self.db.execute("UPDATE incidents SET status='OPEN',good_since=NULL,updated=? WHERE id=?",(utc(now),iid))
                        self.event(iid,"RECOVERY_RESET",now,"missed_or_irregular_cycle")
                        good=None
                    if not good:
                        self.db.execute("UPDATE incidents SET status='RECOVERING',good_since=?,updated=? WHERE id=?",(utc(now),utc(now),iid))
                        self.event(iid,"RECOVERING",now)
                    elif now < datetime.fromisoformat(good.replace("Z","+00:00")):
                        raise ValueError("regressive_recovery_clock")
                    elif now-datetime.fromisoformat(good.replace("Z","+00:00"))>=FIVE:
                        self.db.execute("UPDATE incidents SET status='RESOLVED',updated=? WHERE id=?",(utc(now),iid))
                        self.event(iid,"RESOLVE",now)
    def pending(self,now=None):
        if now is None:
            return self.db.execute("SELECT event,attempts FROM outbox WHERE status IN ('PENDING','RETRY') ORDER BY rowid").fetchall()
        return self.db.execute("SELECT event,attempts FROM outbox WHERE status IN ('PENDING','RETRY') AND (next_at IS NULL OR next_at<=?) ORDER BY rowid",(utc(now),)).fetchall()
    def delivery(self,eid,result,now):
        if result not in ("ACK","RETRY","UNKNOWN"):raise ValueError("result")
        utc(now)
        with self.db:
            state=self.db.execute("SELECT status FROM outbox WHERE event=?",(eid,)).fetchone()
            if state is None: raise KeyError("unknown_notification")
            if state[0]=="ACKED": return
            if state[0]=="UNKNOWN": raise ValueError("ambiguous_delivery_requires_manual_reconciliation")
            self.db.execute("UPDATE outbox SET status=?,attempts=attempts+1,next_at=?,last_error=? WHERE event=?",
              ("ACKED" if result=="ACK" else result,None if result=="ACK" else utc(now+timedelta(seconds=60)),None if result=="ACK" else result,eid))
    def reconcile_unknown(self,eid,decision,now,evidence,actor=""):
        """Manual audit only: confirm delivery or keep held; never auto resend."""
        utc(now)
        if decision not in ("CONFIRMED_DELIVERED","KEEP_UNKNOWN"): raise ValueError("unsupported_ambiguity_decision")
        if not isinstance(evidence,str) or not evidence.strip(): raise ValueError("reconciliation_evidence_required")
        if not isinstance(actor,str) or not actor.strip(): raise ValueError("reconciliation_actor_required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT status FROM outbox WHERE event=?",(eid,)).fetchone()
            if row is None: raise KeyError("unknown_notification")
            if row[0]!="UNKNOWN": raise ValueError("notification_not_ambiguous")
            status="ACKED" if decision=="CONFIRMED_DELIVERED" else "UNKNOWN"
            self.db.execute("UPDATE outbox SET status=?,next_at=NULL,last_error=? WHERE event=?",
                            (status,None if status=="ACKED" else "manual_review_required",eid))
            self.db.execute("INSERT INTO manual_reconciliations(event,actor,at,decision,justification) VALUES (?,?,?,?,?)",(eid,actor.strip(),utc(now),decision,evidence.strip()))
            self.event(eid,"MANUAL_RECONCILIATION",now,f"{decision}:operator_statement_not_provider_proof")
    def close(self):self.db.close()

class MockAdapter:
    def __init__(self,result="ACK"):self.result=result;self.calls=[]
    def send(self,event):self.calls.append(event);return self.result

def supervise_heartbeat(now,last,expected_interval=FIVE,allowed_misses=2,silence_limit=timedelta(minutes=12),publisher_ok=True,supervisor_ok=True):
    utc(now)
    if not supervisor_ok:return "SUPERVISOR_UNKNOWN"
    if not publisher_ok:return "HEARTBEAT_PUBLICATION_FAILURE"
    if last is None:return "MONITOR_UNKNOWN"
    silence=now-last
    if silence.total_seconds()<0:return "CLOCK_ANOMALY"
    missed=int(silence/expected_interval)
    return "MONITOR_CRITICAL" if missed>=allowed_misses and silence>silence_limit else "OK"

def inspect_external(status,body,expected_sha=None,cached=False):
    if status!=200:return "HTTP_FAILURE"
    if cached:return "CACHE_UNCERTAIN"
    if not body:return "EMPTY"
    if expected_sha and hashlib.sha256(body).hexdigest()!=expected_sha:return "REVISION_MISMATCH"
    return "CONTENT_RECEIVED_NOT_YET_VALIDATED"
