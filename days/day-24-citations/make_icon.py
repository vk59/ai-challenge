#!/usr/bin/env python3
"""Иконка дня 23: два столбца и стрелка — куски меняют порядок.

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

    white = NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.95)
    dim = NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.40)

    # Открывающая кавычка: две запятые, развёрнутые вверх. Рисуем кружком
    # с хвостом — так читается на всех размерах, от 16 до 512.
    radius = px * 0.058
    for index in (0, 1):
        qx = cx - px * 0.165 + index * px * 0.150
        qy = cy + px * 0.120
        white.set()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(qx - radius, qy - radius, radius * 2, radius * 2)).fill()
        tail = NSBezierPath.bezierPath()
        tail.setLineWidth_(px * 0.030)
        tail.setLineCapStyle_(1)
        tail.moveToPoint_(NSMakePoint(qx + radius * 0.25, qy - radius * 0.55))
        tail.lineToPoint_(NSMakePoint(qx - radius * 0.30, qy - radius * 2.3))
        tail.stroke()

    # Три строки текста под кавычкой — сама цитата.
    for row in range(3):
        y = cy - px * 0.035 - row * px * 0.072
        width = px * 0.195 - row * px * 0.030
        line = NSBezierPath.bezierPath()
        line.setLineWidth_(px * 0.026)
        line.setLineCapStyle_(1)
        (white if row == 0 else dim).set()
        line.moveToPoint_(NSMakePoint(cx - px * 0.195, y))
        line.lineToPoint_(NSMakePoint(cx - px * 0.195 + width * 2, y))
        line.stroke()

    # Галочка в правом нижнем углу: цитата подтверждена.
    white.set()
    check = NSBezierPath.bezierPath()
    check.setLineWidth_(px * 0.046)
    check.setLineCapStyle_(1)
    check.setLineJoinStyle_(1)
    base_x, base_y = cx + px * 0.095, cy - px * 0.135
    check.moveToPoint_(NSMakePoint(base_x - px * 0.070, base_y + px * 0.010))
    check.lineToPoint_(NSMakePoint(base_x - px * 0.022, base_y - px * 0.042))
    check.lineToPoint_(NSMakePoint(base_x + px * 0.080, base_y + px * 0.085))
    check.stroke()

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
