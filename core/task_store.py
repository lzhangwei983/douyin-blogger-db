"""Small durable job ledger with per-date duplicate suppression."""
import sqlite3,time,uuid,json
from _paths import APP_STATE_DIR

def db():
    APP_STATE_DIR.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(APP_STATE_DIR/'tasks.db',timeout=20);con.row_factory=sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,kind TEXT,date TEXT,status TEXT,pid INTEGER DEFAULT 0,created REAL,updated REAL,message TEXT,log TEXT)')
    return con

def get(tid):
    with db() as con:
        r=con.execute('SELECT * FROM jobs WHERE id=?',(tid,)).fetchone()
        return dict(r) if r else None

def update(tid,**fields):
    allowed={'status','pid','message','log'};fields={k:v for k,v in fields.items() if k in allowed};fields['updated']=time.time()
    with db() as con:con.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[*fields.values(),tid])

def active(kind=None,date=None):
    where=["status IN ('queued','running')"];args=[]
    if kind:where.append('kind=?');args.append(kind)
    if date:where.append('date=?');args.append(date)
    with db() as con:rows=con.execute('SELECT * FROM jobs WHERE '+' AND '.join(where)+' ORDER BY created DESC LIMIT 8',args).fetchall()
    return [dict(x) for x in rows]

def create(kind,date):
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        old=con.execute("SELECT * FROM jobs WHERE kind=? AND date=? AND status IN ('queued','running') ORDER BY created DESC LIMIT 1",(kind,date)).fetchone()
        if old:
            alive=True
            if old['pid']:
                try:
                    import psutil
                    p=psutil.Process(old['pid']);alive=p.is_running() and any('job_runner.py' in x for x in p.cmdline())
                except Exception:alive=False
            elif time.time()-old['updated']>30:alive=False
            if alive:return dict(old),False
            con.execute("UPDATE jobs SET status='failed',message='上次任务已中断',updated=? WHERE id=?",(time.time(),old['id']))
        tid=uuid.uuid4().hex;now=time.time()
        con.execute('INSERT INTO jobs(id,kind,date,status,created,updated,message,log) VALUES(?,?,?,?,?,?,?,?)',(tid,kind,date,'queued',now,now,'等待开始',''))
    return get(tid),True
