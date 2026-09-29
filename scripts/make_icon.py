"""生成 App 图标源文件 assets/icon.png（1024×1024）：深色圆角方块 + 白色 "SD" + 绿色圆点。

用法：uv run python scripts/make_icon.py（只在想重画图标时运行；安装脚本会把它转换成 .icns）
"""

from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSPNGFileType,
    NSString,
)
from Foundation import NSMakePoint, NSMakeRect

SIZE = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def main():
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, SIZE, SIZE, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))

    # macOS 图标的标准留白：内容区约 824×824，圆角约 185
    inset = 100
    body = NSMakeRect(inset, inset, SIZE - 2 * inset, SIZE - 2 * inset)
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, 185, 185)
    top = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.25, 0.30, 1)
    bottom = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.11, 0.14, 1)
    NSGradient.alloc().initWithStartingColor_endingColor_(bottom, top).drawInBezierPath_angle_(path, 90)

    # 文字 SD
    font = NSFont.systemFontOfSize_weight_(360, NSFontWeightBold)
    attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.whiteColor()}
    text = NSString.stringWithString_("SD")
    size = text.sizeWithAttributes_(attrs)
    text.drawAtPoint_withAttributes_(NSMakePoint((SIZE - size.width) / 2, (SIZE - size.height) / 2 - 30), attrs)

    # 状态圆点（与菜单栏图标一致）
    dot = 150
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.84, 0.29, 1).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(SIZE - inset - dot - 70, SIZE - inset - dot - 70, dot, dot)).fill()

    NSGraphicsContext.restoreGraphicsState()
    OUT.parent.mkdir(exist_ok=True)
    rep.representationUsingType_properties_(NSPNGFileType, None).writeToFile_atomically_(str(OUT), True)
    print(f"已生成 {OUT}")


if __name__ == "__main__":
    main()
