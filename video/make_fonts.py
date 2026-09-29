"""把下载的完整字体裁成"片中用到的字"的小字体（设计文档 6.3）。改了片中文字后重新运行一次。

用法：uv run python video/make_fonts.py

- 源字体放在 video/assets/fonts/src/（不提交，来自 github.com/google/fonts 的 ofl/ 目录）：
  NotoSansSC[wght].ttf、JetBrainsMono[wght].ttf 及各自的 OFL 授权文本
- 输出到 video/assets/fonts/（提交）：固定字重的 woff2 子集 + 授权文本
- 按 SIL OFL 要求，修改后的字体改用新名字（Noto Sans SC 的授权保留了 "Source" 这个名字）
"""

import shutil
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

VIDEO = Path(__file__).resolve().parent
SRC = VIDEO / "assets" / "fonts" / "src"
OUT = VIDEO / "assets" / "fonts"

TARGETS = [
    # (源文件, 字重, 新字体名, 输出文件)
    ("NotoSansSC[wght].ttf", 400, "SD Promo Sans", "sdpromo-sans-regular.woff2"),
    ("NotoSansSC[wght].ttf", 700, "SD Promo Sans", "sdpromo-sans-bold.woff2"),
    ("JetBrainsMono[wght].ttf", 400, "SD Promo Mono", "sdpromo-mono-regular.woff2"),
]


def used_text() -> str:
    """片中可能出现的所有字符：各脚本和页面里的文字，加上全部可见 ASCII 与常用中文标点。"""
    chars = set(chr(c) for c in range(0x20, 0x7F))
    chars |= set("，。、：；？！“”‘’（）《》【】…—·×→✓✗●○")
    for p in list(VIDEO.glob("*.js")) + list(VIDEO.glob("*.html")):
        chars |= set(p.read_text(encoding="utf-8"))
    return "".join(sorted(c for c in chars if c.isprintable()))


def rename(font: TTFont, family: str, weight: int) -> None:
    style = "Bold" if weight >= 700 else "Regular"
    ps = family.replace(" ", "") + "-" + style
    names = {1: family, 2: style, 4: f"{family} {style}", 6: ps, 16: family, 17: style}
    table = font["name"]
    for rec in list(table.names):
        if rec.nameID in (1, 2, 3, 4, 6, 16, 17, 21, 22, 25):
            table.removeNames(nameID=rec.nameID)
    for nid, value in names.items():
        table.setName(value, nid, 3, 1, 0x409)
    table.setName(f"{ps};subset for ServiceDeck promo video", 3, 3, 1, 0x409)


def main() -> None:
    text = used_text()
    for src, weight, family, out_name in TARGETS:
        font = TTFont(SRC / src)
        font = instancer.instantiateVariableFont(font, {"wght": weight})
        options = subset.Options()
        options.flavor = "woff2"
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        options.notdef_outline = True
        sub = subset.Subsetter(options)
        sub.populate(text=text)
        sub.subset(font)
        rename(font, family, weight)
        font.flavor = "woff2"
        font.save(OUT / out_name)
        print(f"{out_name}: {(OUT / out_name).stat().st_size // 1024} KB")
    for lic in ("NotoSansSC-OFL.txt", "JetBrainsMono-OFL.txt"):
        shutil.copy(SRC / lic, OUT / lic)
    print(f"共 {len(text)} 个字符")


if __name__ == "__main__":
    main()
