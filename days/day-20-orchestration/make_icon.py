#!/usr/bin/env python3
"""Иконка дня 20: три узла сходятся в один — диспетчер серверов.

    python make_icon.py путь/к/icon.iconset
"""

import sys
from pathlib import Path

from AppKit import NSBezierPath, NSBitmapImageRep, NSColor, NSGradient, NSImage
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize

try:
    from AppKit import NSBitmapImageFileTypePNG as PNG_TYPE
except ImportError:
    PNG_TYPE = 4

SIZES = {
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}


def render(px: int) -> bytes:
    image = NSImage.alloc().initWithSize_(NSMakeSize(px, px))
    image.lockFocus()

    inset = px * 0.085
    side = px - inset * 2
    radius = side * 0.2237
    plate = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(inset, inset, side, side), radius, radius)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.18, 0.60, 0.95, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.37, 0.36, 0.90, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    import math
    cx, cy = px / 2, px / 2

    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.95).set()

    # Три источника слева — три MCP-сервера, один хаб справа.
    хаб_x = cx + px * 0.155
    r_хаб = px * 0.072
    r_узла = px * 0.050
    источники = [(cx - px * 0.175, cy + px * 0.150),
                 (cx - px * 0.175, cy),
                 (cx - px * 0.175, cy - px * 0.150)]

    # Линии первыми, чтобы узлы легли поверх стыков.
    for x, y in источники:
        л = NSBezierPath.bezierPath()
        л.setLineWidth_(px * 0.026)
        л.setLineCapStyle_(1)
        л.moveToPoint_(NSMakePoint(x + r_узла, y))
        # Небольшой изгиб к хабу: прямые в три точки смотрятся как вилка.
        серединаx = (x + хаб_x) / 2
        л.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(хаб_x - r_хаб, cy),
            NSMakePoint(серединаx, y),
            NSMakePoint(серединаx, cy))
        л.stroke()

    for x, y in источники:
        круг = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(x - r_узла, y - r_узла, r_узла * 2, r_узла * 2))
        круг.setLineWidth_(px * 0.030)
        круг.stroke()

    # Хаб залит: решение принимается здесь.
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(хаб_x - r_хаб, cy - r_хаб, r_хаб * 2, r_хаб * 2)).fill()

    image.unlockFocus()
    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    return rep.representationUsingType_properties_(PNG_TYPE, {})


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    iconset = Path(sys.argv[1])
    iconset.mkdir(parents=True, exist_ok=True)
    for name, px in SIZES.items():
        render(px).writeToFile_atomically_(str(iconset / name), True)
    print(f"  нарисовано {len(SIZES)} размеров в {iconset.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
