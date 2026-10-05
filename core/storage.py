"""Atomic local writes, per-resource locks, and recoverable backups."""
from pathlib import Path
from contextlib import contextmanager
import json, os, time, shutil, sqlite3, uuid
from _paths import BASE_DIR, APP_STATE_DIR

def atomic_text(path, text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('w',encoding='utf-8',newline='') as f:
            f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)

def write_json(path, value):
    atomic_text(path,json.dumps(value,ensure_ascii=False,indent=2))

def read_json(path, default=None):
    p=Path(path)
    if not p.exists():return default
    try:
        return json.loads(p.read_text(encoding='utf-8-sig'))
    except (json.JSONDecodeError,UnicodeDecodeError) as exc:
        stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        quarantined=p.with_name(p.name+'.corrupt-'+stamp)
        try:
            os.replace(p,quarantined)
        except OSError as move_error:
            raise OSError(f'JSON 文件损坏且无法隔离：{p.name}') from move_error
        return default

@contextmanager
def resource_lock(name, timeout=15):
    import re
    lock_dir=APP_STATE_DIR/'.locks';lock_dir.mkdir(parents=True,exist_ok=True)
    p=lock_dir/(re.sub(r'[^A-Za-z0-9_.-]','_',name)+'.lock')
    f=p.open('a+b');f.seek(0,2)
    if f.tell()==0:f.write(b'0');f.flush()
    deadline=time.monotonic()+timeout
    while True:
        try:
            f.seek(0)
            if os.name=='nt':
                import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic()>=deadline:f.close();raise TimeoutError('操作正在进行，请稍后重试')
            time.sleep(.05)
    try:yield
    finally:
        f.seek(0)
        if os.name=='nt':
            import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl;fcntl.flock(f,fcntl.LOCK_UN)
        f.close()

def archive_files(paths, label):
    target=BASE_DIR/'backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])/label
    copied=0
    for p in map(Path,paths):
        if not p.exists():continue
        rel=p.resolve().relative_to(BASE_DIR.resolve())
        out=target/rel;out.parent.mkdir(parents=True,exist_ok=True)
        if p.is_dir():shutil.copytree(p,out)
        else:shutil.copy2(p,out)
        copied+=1
    return str(target) if copied else None

def backup_database(path, label='database'):
    p=Path(path)
    if not p.exists():return None
    target=BASE_DIR/'backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])/label
    target.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(p) as src,sqlite3.connect(target/p.name) as dst:src.backup(dst)
    return str(target/p.name)
