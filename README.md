# 抖音博主数据库 / Douyin Blogger Database

> ⚠️ **免责声明**：本项目仅供**个人学习 / 技术研究 / 个人内容备份**使用，禁止商业用途与再分发他人版权内容。使用即表示你同意遵守各平台服务条款与当地法律，并**自负一切风险与责任**。详见 [DISCLAIMER.md](./DISCLAIMER.md)。

一个用于**采集、转写、检索抖音（及多平台）博主视频**的桌面应用。

核心能力：

- **视频采集**：通过 `yt-dlp` 下载博主主页下的视频（支持抖音、快手、B站、YouTube 等）。
- **语音转写**：用 `faster-whisper` 把视频转成字幕文本（默认 CUDA GPU 加速，断点续传）。
- **结构化存储**：博主 / 视频 / 字幕统一存进 SQLite，前端可按博主、标题、字幕全文检索。
- **桌面前端**：FastAPI 提供后端，pywebview 套壳成桌面窗口，可视化浏览、搜索、查看转写结果。
- **每日信息差**：内置「每日 AI 信息差」报告的浏览与导出（报告由独立的每日脚本生成，见下方说明）。

> 本仓库为**公开版**：已移除飞书推送等私有集成，保留可复用的采集 / 转写 / 检索主流程。

---

## 项目结构

```
.
├── app.py                 # FastAPI 后端 + REST API + 前端静态服务
├── main.py                # 桌面启动器（pywebview 套壳，自带 exe 打包）
├── DouyinBlogDB.spec      # PyInstaller 打包配置
├── static/                # 前端页面（index.html 等）
├── work/                  # 采集 / 转写流水线
│   ├── pipeline.py          # 下载 + whisper 转写主流程
│   ├── fetch_user_videos.py # 抓取博主视频列表 -> TSV
│   ├── fetch_user_info.py   # 抓取博主资料
│   └── import_to_db.py      # 把 TSV / 字幕导入 SQLite
└── tests/                 # 接口冒烟测试
```

---

## 环境要求

- Python 3.10+
- **ffmpeg**：系统需安装 `ffmpeg` 并加入 PATH（Windows 也可放到 `C:\ffmpeg\bin\ffmpeg.exe`）。
- **GPU（推荐）**：转写默认使用 CUDA。无 NVIDIA GPU 时，把 `work/pipeline.py` 里
  `WhisperModel(..., device="cuda", ...)` 改为 `device="cpu"` 即可（速度较慢）。
- 首次运行会自动下载 whisper 模型到 `D:/DouyinBlogDB/models`。

---

## 安装

```bash
git clone <your-fork-or-this-repo>
cd douyin_blog_db

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

---

## 使用

### 1. 桌面应用（推荐）

```bash
python main.py
```

会自动起一个本地 FastAPI 服务并弹出桌面窗口（`http://127.0.0.1:<随机端口>`）。

### 2. 仅启动 Web 后端

```bash
uvicorn app:app --host 127.0.0.1 --port 8321
# 浏览器打开 http://127.0.0.1:8321
```

### 3. 采集 + 转写一个博主

```bash
# 抓取博主视频列表 -> work/<name>_videos.tsv
python work/fetch_user_videos.py <博主主页URL> [--cookies cookies.txt]

# 下载 + 转写（把 TSV 交给流水线）
python work/pipeline.py work/<name>_videos.tsv work/outputs/<name> --cookies cookies.txt [--model small] [--batch 16]

# 导入数据库
python work/import_to_db.py work/outputs/<name>
```

`--cookies` 可选：遇到「需要登录才能访问」时，从浏览器导出 `cookies.txt`（Netscape 格式）后传入。

### 4. 前端功能

- **概览 / 采集进度**：查看博主、视频总量与转写进度。
- **转写设置**：实时调整 `batch_size / num_workers / beam_size / 间隔`，下次转写生效。
- **每日信息差**：浏览与导出每日报告（报告由独立的每日脚本生成，数据目录 `D:/DouyinBlogDB/daily/report`）。

---

## 数据目录

本应用默认把所有数据放在 **`D:\DouyinBlogDB`**（Windows）：

```
D:/DouyinBlogDB/
├── work/            # 采集中间产物（TSV、mp4 临时文件）
├── outputs/         # 转写结果（字幕 txt）
├── models/          # whisper 模型缓存
└── daily/report/    # 每日信息差报告
```

想换路径时，修改 `app.py` 与 `work/pipeline.py` 里的 `D:/DouyinBlogDB` 相关常量即可。

---

## 打包成 exe

已提供 `DouyinBlogDB.spec`，用 PyInstaller 打包：

```bash
pip install pyinstaller
pyinstaller DouyinBlogDB.spec
# 产物在 dist/DouyinBlogDB.exe
```

---

## 许可证

MIT —— 随意使用、修改、再分发。
