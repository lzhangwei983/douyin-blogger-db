#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前端 JS 语法检查：从 index.html 提取 <script> 内容，用 node --check 校验"""
import re, subprocess, sys, tempfile, os
from pathlib import Path

html = Path(__file__).resolve().parent.parent / "static" / "index.html"
text = html.read_text(encoding="utf-8")
scripts = re.findall(r"<script>(.*?)</script>", text, re.S)
if not scripts:
    print("未找到 <script> 块")
    sys.exit(1)

ok = True
for i, js in enumerate(scripts, 1):
    if not js.strip():
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        tmp = f.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode != 0:
        ok = False
        print(f"[script {i}] 语法错误:")
        print(r.stderr)
    else:
        print(f"[script {i}] OK ({len(js)} chars)")
print("JS 语法检查通过" if ok else "JS 语法检查失败")
sys.exit(0 if ok else 1)