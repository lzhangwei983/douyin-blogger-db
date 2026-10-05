# Agent 数据协作指南

DouyinBlogDB 不绑定模型服务或特定 Agent。它负责本机采集、保存、查看、筛选和导出；用户自己选择 Agent、任务和要发送的内容。软件不会自动调用云端模型。

## 导出所选数据

在应用的本地 API 中，`GET /api/bloggers` 可查看博主及其本机编号；用编号导出一个博主的全部作品和已保存分析：

```text
GET /api/export?format=json&blogger_id=1
```

省略 `blogger_id` 会导出数据库中的全部博主。JSON 中每个博主包含 `videos`；作品对象含 `video_id`、`url`、标题、日期、互动数据、字幕、笔记和标签。已保存的分析放在 `analysis` 字段，其中 `full_md` 是完整 Markdown 正文。CSV 适合表格查看；需要字幕和完整分析时使用 JSON。

API 随桌面应用一起在本机 `127.0.0.1` 随机端口运行。保持应用打开后，在应用显示的地址中替换路径即可请求。不要把本地服务映射到公网或局域网，也不要把 Cookie、数据库文件和不需要处理的字幕发送给 Agent。

## 让 Agent 处理内容

你可以自行决定要 Agent 做什么，例如摘要、核查观点、分类、比较多个作品、提取行动项或按自己的规则写报告。导出前先筛选博主和作品；只把任务确实需要的数据交给你选择的 Agent。结果格式由你决定，Agent 不受作者个人日报或主题筛选流程约束。

## 将分析结果导回数据库

源码包中的 `work/import_to_db.py` 支持把 TSV、字幕 TXT 和 Markdown 分析增量合并到本机数据库。工具会按作品链接匹配记录，保留人工编辑；已有分析默认不覆盖。源码启动脚本准备好 `.venv` 后，可这样导入：

```powershell
.\.venv\Scripts\python.exe work\import_to_db.py `
  --name "博主名称" `
  --slug "唯一标识" `
  --platform "抖音" `
  --tsv "work\唯一标识_videos.tsv" `
  --dir "outputs\Agent结果"
```

`--dir` 中的分析文件命名为 `{序号两位}_video_{视频ID}_analysis.md`。为填充软件内七个分析字段，Markdown 使用这些二级标题：

```markdown
## 内容摘要
## 核心观点
## 可执行建议
## 适合的行业或工作方向（归纳博主建议，非事实）
## 风险和可疑之处
## 对博主可信度的判断
## 哪些建议值得执行/需谨慎
```

其他自定义章节仍会保存在完整 Markdown 正文中。只有在确认要替换旧分析时才使用 `--refresh-content 1`；导入前建议备份数据库。Windows 可执行版提供采集和 JSON/CSV 导出，批量 Markdown 回填命令位于源码包中。

脚本只读写 `DYDB_HOME` 指向的本机数据库。不要把含 Cookie 或个人采集资料的数据库提交到公开仓库。
