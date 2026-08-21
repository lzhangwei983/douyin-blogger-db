# 抖音博主数据库 / Douyin Blogger Database

> ⚠️ **免责声明**：本项目仅供**个人学习 / 技术研究 / 个人内容备份**使用，禁止商业用途与再分发他人版权内容。使用即表示你同意遵守各平台服务条款与当地法律，并**自负一切风险与责任**。详见 [DISCLAIMER.md](./DISCLAIMER.md)。

这是我平时用来备份关注博主视频的小工具——把抖音（也支持 B站/YouTube 等）的视频批量抓下来，用 `faster-whisper` 转成文字丢进 SQLite，慢慢检索。顺手用 `FastAPI + pywebview` 套了个桌面界面，还加了个「每日 AI 信息差」的浏览页，开源出来给需要的朋友参考。

我为什么做这个：关注的博主多了，想有个本地库能按关键词搜字幕、看转写进度，不用每次去翻手机。能跑就行，代码不算漂亮，欢迎提 issue 一起改。

> 本仓库为**公开版**：已移除飞书推送等私有集成，保留可复用的采集 / 转写 / 检索主流程。

## ⚖️ 说在前面 / 法律与合规

> **先说好**：你克隆/下载/运行，就当是看过并同意 `LICENSE` / `DISCLAIMER.md` / `SECURITY.md` 了。

- **我做这个只是自己备份学习用**：别商用、别做有偿代下/代运营，也别把下下来的东西二次分发或公开传播。
- **规矩得你自己守**：抖音/B站/YouTube 各家的服务条款，还有《著作权法》《网络安全法》这些，得你自己遵守；我没做也不鼓励绕过付费墙、DRM 或平台风控的路子。
- **风险自负**：软件就按原样给你用，真因为用了被封号、丢数据或惹上版权麻烦，别找我；真有人来找我索赔也得你来担。
- **侵权我认删**：你是权利人觉得哪里不对，直接提 Issue 标题写 `Takedown` 或按 `SECURITY.md` 来，核实后 48 小时内删或改。

**说人话版**：只下你有权下的，别商用、别外传、别猛扒。

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

会在**你自己的电脑**上起一个本地 FastAPI 服务，并弹出桌面窗口（地址是 `http://127.0.0.1:<随机端口>`，`127.0.0.1` 就是“本机自己”，外人访问不到，随机端口只是为了避免和你电脑上已有程序冲突）。

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
