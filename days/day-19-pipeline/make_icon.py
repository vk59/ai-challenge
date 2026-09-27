#!/usr/bin/env python3
"""Иконка дня 19: три звена цепи — инструменты, сцепленные в пайплайн.

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

    # Три звена в ряд: найти → сжать → сохранить.
    шаг = px * 0.215
    r = px * 0.083
    центры = [(cx - шаг, cy), (cx, cy), (cx + шаг, cy)]

    # Сначала связи, чтобы кружки легли поверх стыков.
    for (x1, _), (x2, _) in zip(центры, центры[1:]):
        л = NSBezierPath.bezierPath()
        л.setLineWidth_(px * 0.030)
        л.setLineCapStyle_(1)
        л.moveToPoint_(NSMakePoint(x1 + r, cy))
        л.lineToPoint_(NSMakePoint(x2 - r, cy))
        л.stroke()

    # Звенья: крайние — контуром, среднее залито (данные внутри, не снаружи).
    for индекс, (x, y) in enumerate(центры):
        круг = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(x - r, y - r, r * 2, r * 2))
        if индекс == 1:
            круг.fill()
        else:
            круг.setLineWidth_(px * 0.034)
            круг.stroke()

    # Стрелка вправо у последнего звена — результат уходит в файл.
    нос = cx + шаг + r + px * 0.052
    for знак in (1, -1):
        у = NSBezierPath.bezierPath()
        у.setLineWidth_(px * 0.030)
        у.setLineCapStyle_(1)
        у.moveToPoint_(NSMakePoint(нос - px * 0.048, cy + знак * px * 0.048))
        у.lineToPoint_(NSMakePoint(нос, cy))
        у.stroke()
    хвост = NSBezierPath.bezierPath()
    хвост.setLineWidth_(px * 0.030)
    хвост.setLineCapStyle_(1)
    хвост.moveToPoint_(NSMakePoint(cx + шаг + r, cy))
    хвост.lineToPoint_(NSMakePoint(нос, cy))
    хвост.stroke()

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
