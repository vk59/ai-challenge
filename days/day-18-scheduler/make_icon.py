#!/usr/bin/env python3
"""Иконка дня 18: циферблат со стрелками — задачи по расписанию.

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

    cx, cy = px / 2, px / 2
    r = px * 0.245

    # Циферблат
    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.95).set()
    кольцо = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r, cy - r, r * 2, r * 2))
    кольцо.setLineWidth_(px * 0.042)
    кольцо.stroke()

    # Засечки на четвертях
    for угол in (0, 90, 180, 270):
        import math
        рад = math.radians(угол)
        внеш, внутр = r * 0.94, r * 0.74
        ч = NSBezierPath.bezierPath()
        ч.setLineWidth_(px * 0.026)
        ч.moveToPoint_(NSMakePoint(cx + внеш * math.cos(рад),
                                   cy + внеш * math.sin(рад)))
        ч.lineToPoint_(NSMakePoint(cx + внутр * math.cos(рад),
                                   cy + внутр * math.sin(рад)))
        ч.stroke()

    # Стрелки: часовая вверх, минутная вправо
    for длина, угол, толщина in ((r * 0.52, 90, 0.034), (r * 0.74, 18, 0.026)):
        import math
        рад = math.radians(угол)
        с = NSBezierPath.bezierPath()
        с.setLineWidth_(px * толщина)
        с.setLineCapStyle_(1)
        с.moveToPoint_(NSMakePoint(cx, cy))
        с.lineToPoint_(NSMakePoint(cx + длина * math.cos(рад),
                                   cy + длина * math.sin(рад)))
        с.stroke()

    # Центр
    т = px * 0.028
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - т, cy - т, т * 2, т * 2)).fill()

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
