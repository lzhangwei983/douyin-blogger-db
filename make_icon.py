#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成应用图标：深色圆角方块 + 青色数据库圆柱（lucide Database 风格）"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(__file__).resolve().parent
BG = (11, 14, 17, 255)        # #0b0e11
ACCENT = (55, 182, 230)       # #37b6e6
ACCENT_SOFT = (55, 182, 230, 55)
BORDER = (55, 182, 230, 90)

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角方块背景
r = int(S * 0.22)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=BG)
d.rounded_rectangle([2, 2, S - 3, S - 3], radius=r - 2, outline=BORDER, width=3)

def scale_pts(pts, k=S / 24):
    return [(x * k, y * k) for x, y in pts]

# 数据库圆柱（lucide Database 24x24 坐标）
body = scale_pts([(3, 5), (21, 5), (21, 19), (3, 19)])
top_ell = scale_pts([(3, 5), (21, 11)])
mid_ell = scale_pts([(3, 12), (21, 18)])
bot_ell = scale_pts([(3, 19), (21, 25)])
# 圆柱侧面
d.line([body[0], body[3]], fill=ACCENT, width=8)
d.line([body[1], body[2]], fill=ACCENT, width=8)
# 中段椭圆（隔行感）
d.ellipse([mid_ell[0][0], mid_ell[0][1], mid_ell[1][0], mid_ell[1][1]], outline=ACCENT, width=8)
# 顶盖椭圆（填充）
d.ellipse([top_ell[0][0], top_ell[0][1], top_ell[1][0], top_ell[1][1]], fill=BG, outline=ACCENT, width=8)
# 底盖椭圆
d.ellipse([bot_ell[0][0], bot_ell[0][1], bot_ell[1][0], bot_ell[1][1]], outline=ACCENT, width=8)

# 心跳线（左上到右下，accent 亮色）
heart = scale_pts([(8, 15), (10, 15), (11.4, 11.2), (13, 17.4), (14.6, 12.6), (16, 15), (19, 15)])
for i in range(len(heart) - 1):
    d.line([heart[i], heart[i + 1]], fill=(94, 203, 242), width=5, joint="curve")

img.save(OUT / "icon.png")

# 多尺寸 ico（16~256）
ico_sizes = [16, 24, 32, 48, 64, 128, 256]
img.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(s, s) for s in ico_sizes])
print("icon.png + icon.ico generated")
