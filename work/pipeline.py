"""视频流水线：下载 -> faster-whisper本机字幕转写 -> 写字幕TXT与分段JSON
用法: python pipeline.py <TSV> <工作目录> --cookies <file> [--model small] [--batch 16]
  <工作目录>/mp4/    临时视频（转写后删除）
  <工作目录>/txt/    最终字幕 {seq:02d}_video_{id}_subtitle.txt
模型缓存位于本机用户数据目录
断点续传：已有字幕的跳过；进度写 pipeline.log
调控:
  1. 转写参数读用户数据目录配置（batch_size/num_workers/beam_size/sleep）
  2. 队列调控: <工作目录>/todo.json  {order:[seq...], skip:[seq...]} → 按 order 处理、skip 跳过
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from _paths import BASE_DIR as DYDB_ROOT
from _paths import CONFIG_JSON, get_proxy, proxy_cli_args
import sys, re, csv, os, time, random, subprocess, shutil, glob, json, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

MODELS_DIR = (DYDB_ROOT / 'models')
CONFIG = CONFIG_JSON

def load_config():
    cfg = {"whisper": {"num_workers": 4, "beam_size": 1, "concurrency": 1,
                       "sleep_min": 1, "sleep_max": 3, "device":"auto"}}
    try:
        if CONFIG.exists():
            d = json.loads(CONFIG.read_text(encoding="utf-8"))
            for k, v in (d.get("whisper") or {}).items():
                if k in cfg["whisper"]:
                    cfg["whisper"][k] = v
    except Exception:
        pass
    return cfg

_model_local = threading.local()
def get_model(name,num_workers=4):
    from asr_runtime import get_model as shared_model
    return shared_model(name,num_workers=num_workers)


def yt_cmd():
    exe = shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]

def download_one(url, mp4, cookie):
    cmd = yt_cmd() + ["-f", "best[height<=360]/best", "--no-playlist",
                      "--no-warnings", "--socket-timeout", "30", "--retries", "2",
                      "--max-filesize", "150M", "-o", str(mp4)]
    cmd += proxy_cli_args(get_proxy())
    if cookie:
        cmd += ["--cookies", cookie]
    cmd.append(url)
    try:
        rp = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
        return (0 if rp.returncode == 0 and mp4.exists() else 1,
                rp.stderr.strip()[-100:] if rp.returncode != 0 else "")
    except subprocess.TimeoutExpired:
        return 1, "下载超时(600s)"
    except Exception as e:
        return 1, str(e)[:100]

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python pipeline.py <TSV> <工作目录> [--cookies 文件] [--model small] [--batch 16]", file=sys.stderr)
        return 2
    tsv, work = args[0], Path(args[1])
    cookie = None
    model = "small"
    batch_override = None
    for i, a in enumerate(args):
        if a == "--cookies" and i + 1 < len(args):
            cookie = args[i + 1]
        if a == "--model" and i + 1 < len(args):
            model = args[i + 1]
        if a == "--batch" and i + 1 < len(args):
            batch_override = int(args[i + 1])
    cfg = load_config()
    wh = cfg["whisper"]
    if batch_override:
        print("batch 参数仅保留兼容；当前采用逐条识别，以并发设置控制任务数量",flush=True)
    mp4dir = work / "mp4"; txtdir = work / "txt"
    mp4dir.mkdir(parents=True, exist_ok=True); txtdir.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(tsv, encoding="utf-8"), delimiter="\t"))
    items = []
    for r in rows:
        seq = int(r.get("序号") or 0)
        url = r.get("链接", "").strip()
        m = re.search(r"(?:/video/|/note/)(\d+)", url)
        vid = m.group(1) if m else "unknown"
        items.append({"seq": seq, "vid": vid, "url": url,
                      "title": r.get("标题", ""), "done": (txtdir / f"{seq:02d}_video_{vid}_subtitle.txt").exists() and (txtdir / f"{seq:02d}_video_{vid}_subtitle.txt").stat().st_size>0, "kind":r.get("类型","视频")})
    # 队列调控
    todo = {}
    try:
        t = work / "todo.json"
        if t.exists():
            todo = json.loads(t.read_text(encoding="utf-8"))
    except Exception:
        todo = {}
    skip_seqs = set(int(x) for x in (todo.get("skip") or []))
    order = [int(x) for x in (todo.get("order") or [])]
    if order:
        items.sort(key=lambda it: order.index(it["seq"]) if it["seq"] in order else 999 + it["seq"])
    pending_items = [it for it in items if not it["done"] and it["seq"] not in skip_seqs and it["kind"]!="图文"]
    done=fail=0;skip=sum(it["done"] or it["seq"] in skip_seqs or it["kind"]=="图文" for it in items)
    failed_items=[]
    log = open(work / "pipeline.log", "a", encoding="utf-8")
    log.write(f"=== 流水线v5开始 剩余 {len(pending_items)} 条, model={model}, 设备={wh.get('device','auto')}, "
              f"workers={wh['num_workers']} beam={wh['beam_size']} 并发={wh['concurrency']} ===\n"); log.flush()

    def process_one(it):
        mp4 = mp4dir / f"{it['seq']:02d}_video_{it['vid']}.mp4"
        if not mp4.exists():
            rc, err = download_one(it["url"], mp4, cookie)
            if rc != 0:
                time.sleep(random.uniform(wh["sleep_min"], wh["sleep_max"]))
                return f"[FAIL] {it['seq']:02d} {it['vid']} 下载失败 {err}"
        try:
            from asr_runtime import transcribe
            text,language,raw=transcribe(mp4,model,language='zh')
            if not text.strip():
                return f"[FAIL] {it['seq']:02d} {it['vid']} 无转写输出"
            target = txtdir / f"{it['seq']:02d}_video_{it['vid']}_subtitle.txt"
            from storage import atomic_text,write_json
            atomic_text(target,text)
            write_json(work/'segments'/f"{it['seq']:02d}_video_{it['vid']}.json",{'text':text,'language':language,**raw})
            mp4.unlink(missing_ok=True)
            return f"[OK] {it['seq']:02d} {it['vid']} {len(text.strip())}字 ({raw['device']})"
        except Exception as e:
            return f"[ERR] {it['seq']:02d} {it['vid']} {str(e)[:100]}"
        finally:
            # 识别失败时保留已下载素材，下一次无需重复下载。
            time.sleep(random.uniform(wh["sleep_min"], wh["sleep_max"]))

    concur = max(1, int(wh.get("concurrency", 1)))
    with ThreadPoolExecutor(max_workers=concur) as ex:
        futs = {ex.submit(process_one, it):it for it in pending_items}
        for f in as_completed(futs):
            it=futs[f]
            try:msg = f.result()
            except Exception as exc:msg=f"[ERR] {it['seq']:02d} {it['vid']} {type(exc).__name__}"
            if msg.startswith("[OK]"):
                done += 1
            else:
                fail += 1
                failed_items.append({"sequence":it["seq"],"video_id":it["vid"],"message":msg})
            print(msg, flush=True); log.write(msg + "\n"); log.flush()
            if done and done % 5 == 0:
                print(f"--- 进度: 完成 {done} / 跳过 {skip} / 失败 {fail}", flush=True)
    log.write(f"=== 结束: 完成 {done} / 跳过 {skip} / 失败 {fail} ===\n"); log.close()
    status="success" if fail==0 else "partial" if done or skip else "failed"
    result={"status":status,"total":len(items),"done":done,"skipped":skip,"failed":fail,"failed_items":failed_items}
    from storage import write_json
    write_json(work/"pipeline_result.json",result)
    print(f"流水线完成: 转写 {done} / 跳过 {skip} / 失败 {fail} / 状态 {status}")
    return {"success":0,"partial":1,"failed":2}[status]

if __name__ == "__main__":
    raise SystemExit(main())
