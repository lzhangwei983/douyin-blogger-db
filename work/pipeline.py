"""流水线 v5：下载 -> faster-whisper (CUDA int8, 批处理) 转写 -> 删除 mp4（只留字幕 txt）
用法: python pipeline.py <TSV> <工作目录> --cookies <file> [--model small] [--batch 16]
  <工作目录>/mp4/    临时视频（转写后删除）
  <工作目录>/txt/    最终字幕 {seq:02d}_video_{id}_subtitle.txt
模型缓存: D:/DouyinBlogDB/models
断点续传：已有字幕的跳过；进度写 pipeline.log
调控:
  1. 转写参数读 D:/DouyinBlogDB/daily/config.json 的 whisper 段（batch_size/num_workers/beam_size/sleep）
  2. 队列调控: <工作目录>/todo.json  {order:[seq...], skip:[seq...]} → 按 order 处理、skip 跳过
"""
import sys, re, csv, os, time, random, subprocess, shutil, glob, json, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe"
MODELS_DIR = Path("D:/DouyinBlogDB/models")
CONFIG = Path("D:/DouyinBlogDB/daily/config.json")

def load_config():
    cfg = {"whisper": {"num_workers": 4, "beam_size": 1, "concurrency": 1,
                       "sleep_min": 1, "sleep_max": 3}}
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
def get_model(name, num_workers=4):
    if getattr(_model_local, "m", None) is None:
        from faster_whisper import WhisperModel
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        _model_local.m = WhisperModel(name, device="cuda", compute_type="int8",
                                      download_root=str(MODELS_DIR),
                                      num_workers=num_workers)
    return _model_local.m

def yt_cmd():
    exe = shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]

def download_one(url, mp4, cookie):
    cmd = yt_cmd() + ["-f", "best[height<=360]/best", "--no-playlist",
                      "--no-warnings", "--socket-timeout", "30", "--retries", "2",
                      "--max-filesize", "150M", "-o", str(mp4)]
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
        wh["batch_size"] = batch_override
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
                      "title": r.get("标题", ""), "done": (txtdir / f"{seq:02d}_video_{vid}_subtitle.txt").exists()})
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
    pending_items = [it for it in items if not it["done"] and it["seq"] not in skip_seqs]
    done = skip = fail = 0
    log = open(work / "pipeline.log", "a", encoding="utf-8")
    log.write(f"=== 流水线v5开始 剩余 {len(pending_items)} 条, model={model}, cuda int8, "
              f"workers={wh['num_workers']} beam={wh['beam_size']} 并发={wh['concurrency']} ===\n"); log.flush()

    def process_one(it):
        get_model(model, num_workers=wh["num_workers"])
        mp4 = mp4dir / f"{it['seq']:02d}_video_{it['vid']}.mp4"
        if not mp4.exists():
            rc, err = download_one(it["url"], mp4, cookie)
            if rc != 0:
                time.sleep(random.uniform(wh["sleep_min"], wh["sleep_max"]))
                return f"[FAIL] {it['seq']:02d} {it['vid']} 下载失败 {err}"
        try:
            segments, info = get_model(model, num_workers=wh["num_workers"]).transcribe(
                str(mp4), language="zh", vad_filter=True, beam_size=wh["beam_size"],
                condition_on_previous_text=True, temperature=[0.0])
            text = "".join(s.text for s in segments)
            if not text.strip():
                return f"[FAIL] {it['seq']:02d} {it['vid']} 无转写输出"
            target = txtdir / f"{it['seq']:02d}_video_{it['vid']}_subtitle.txt"
            target.write_text(text.strip(), encoding="utf-8")
            return f"[OK] {it['seq']:02d} {it['vid']} {len(text.strip())}字"
        except Exception as e:
            return f"[ERR] {it['seq']:02d} {it['vid']} {str(e)[:100]}"
        finally:
            try: mp4.unlink()
            except OSError: pass
            time.sleep(random.uniform(wh["sleep_min"], wh["sleep_max"]))

    concur = max(1, int(wh.get("concurrency", 1)))
    with ThreadPoolExecutor(max_workers=concur) as ex:
        futs = [ex.submit(process_one, it) for it in pending_items]
        for f in as_completed(futs):
            msg = f.result()
            if msg.startswith("[OK]"):
                done += 1
            else:
                fail += 1
            print(msg, flush=True); log.write(msg + "\n"); log.flush()
            if done and done % 5 == 0:
                print(f"--- 进度: 完成 {done} / 跳过 {skip} / 失败 {fail}", flush=True)
    log.write(f"=== 结束: 完成 {done} / 跳过 {skip} / 失败 {fail} ===\n"); log.close()
    print(f"流水线完成: 转写 {done} / 跳过 {skip} / 失败 {fail}")

if __name__ == "__main__":
    main()