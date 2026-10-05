# 抖音博主数据库 Douyin Blogger Database

一个本地运行的桌面脚本工具：输入博主主页链接或已有视频链接，把公开作品资料和互动数据采集到本机 SQLite 数据库，再按需转写字幕、搜索和导出。采集结果默认留在本机。

## 能做什么

- 在桌面界面粘贴抖音博主主页链接，自定义作品条数，以及可选的起始日期和结束日期，采集后自动入库。
- 粘贴一条或多条抖音视频链接（每批最多 100 条），逐条读取标题、作者、发布时间、时长、点赞、评论、转发和收藏数据并自动入库；无效或重复链接会单独标记。
- 查看、筛选、搜索、比较本地作品资料，并将可用数据导出。
- 对已采集的视频按需下载音视频并在本机转成字幕。字幕模型首次使用时会从模型托管服务下载到本地缓存。

本版本不包含个人日报、信息差、飞书推送或 Jev 工作流。

如果要让自己选择的 Agent 处理导出的字幕和作品资料，请先读 [Agent 数据协作指南](AGENT_GUIDE.md)。

## 快速开始

### Windows：桌面版

从 GitHub Releases 下载 `DouyinBlogDB-Windows-v1.1.0.zip`，解压后双击 `DouyinBlogDB.exe`。该版本不要求单独安装 Python。桌面窗口需要 Microsoft Edge WebView2 Runtime；Windows 11 和多数已更新的 Windows 10 已预装，缺少时软件会显示[官方安装说明](https://developer.microsoft.com/microsoft-edge/webview2/)。作品采集会优先使用已安装的 Chrome 或 Edge。

首次转写需要联网下载字幕模型。若 Windows SmartScreen 显示提示，请先核对 Release 页面中的 SHA-256，再按系统提示确认来源。

### Windows：运行源码

安装 Python 3.10 或更高版本，解压源码后双击 `start.bat`。首次运行会在项目目录创建 `.venv`，自动安装 Python 依赖和 Chromium 浏览器组件；准备过程需要网络和磁盘空间。以后再次双击会复用已安装的环境。

### macOS：运行源码

安装 Python 3.10 或更高版本，解压源码后双击 `start.command`。首次运行会准备 Python 依赖和 Chromium，然后打开桌面界面。当前 Release 只提供 Windows 可执行版；macOS 源码启动流程尚未在本项目发布环境实机验收。

也可以在终端运行 `./start.sh`。目前没有为 Linux 提供一键桌面启动包；Linux 的 pywebview 图形组件需按发行版安装。

## 登录与采集

第一次采集或登录失效时，在软件内打开抖音登录窗口并完成扫码。登录 Cookie 会保存在本机应用数据目录，不要上传或发给他人。登录完成后：

1. 采集博主：粘贴个人主页链接，填写条数和可选日期范围，然后开始采集。
2. 采集指定视频：粘贴一条或多条作品链接；每行可以放一条链接，也可以从包含链接的分享文字中粘贴。
3. 查看任务进度与结果。失败项会保留原因，可在软件提示后重试；网络拒绝、登录失效或平台风控时，软件不会把失败结果当作成功入库。

采集可用性会受平台接口、登录状态、网络和风控影响。该工具不保证平台端持续可用，也不会替你绕过验证码或访问限制。

## 字幕和硬件

字幕转写使用 `faster-whisper`。程序会先检测当前机器可用的 CUDA，再回退到 CPU；没有 NVIDIA 显卡、只有核显或使用 Apple 芯片时仍可使用 CPU 转写，无需用户手动选择 GPU。CPU 转写速度会较慢。CUDA 运行库若不可用，也会回退到 CPU。

模型文件首次使用时需要联网下载，之后从本机缓存加载。模型缓存和数据库都可以通过 `DYDB_HOME` 环境变量放到自定义可写目录。

## 本地数据位置

- Windows：`%LOCALAPPDATA%\DouyinBlogDB`
- macOS / Linux：`~/.local/share/DouyinBlogDB`
- 自定义位置：设置环境变量 `DYDB_HOME`

数据库、登录状态、任务进度、模型和字幕都留在本机。备份时请同时保护数据库与登录 Cookie；不要将 Cookie、数据库或个人采集结果提交到公开仓库。

## 源码依赖

依赖清单在 `requirements.txt`。Windows 和 macOS 启动脚本会自动创建隔离环境并安装依赖。直接运行源码时，可手动执行：

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\python -m pip install -r requirements.txt
# macOS/Linux:       .venv/bin/python -m pip install -r requirements.txt
python main.py
```

桌面采集和登录需要 Playwright 及 Chromium。启动脚本会准备 Chromium；手动安装时运行 `python -m playwright install chromium`。音视频解码使用 Python 包提供的本机能力，不依赖仓库内硬编码的 FFmpeg 路径。

## 开发与验证

```bash
python -m pytest -q
python main.py --smoke-test
```

`--smoke-test` 只检查本地应用启动、页面/API 和资源，不会验证抖音当前接口、真实 Cookie 登录、实际采集或字幕模型下载。

## 许可证

从 v1.1.0 开始，本仓库当前版本采用 [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)。个人非商业使用、学习、研究和修改按许可证文本进行；任何商业用途须先取得版权所有者的单独书面授权。此版本是“公开源码、非商业授权”，不属于 OSI 定义的开放源代码许可证。

历史版本 v1.0 至 v1.0.6 继续按各自发布时附带的许可证文本管理；这些版本的 `LICENSE` 含 MIT 正文和补充条款，具体范围请以对应 tag 的原文为准。新许可证不撤销任何人已根据旧版本许可证取得的权利。请按对应 tag 查看历史版本的 `LICENSE`。

## 使用说明

请仅采集你有权访问和处理的内容，并遵守当地法律及平台条款。作品和字幕的权利归相应权利人所有。本软件按“原样”提供，不承诺平台接口持续可用或转写结果准确；详细说明见 [DISCLAIMER.md](DISCLAIMER.md)。
