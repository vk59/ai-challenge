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
    dim = NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.48)

    # Три столбика: локальная малая, локальная большая, облако.
    bar_w = px * 0.095
    gap = px * 0.055
    base_y = cy - px * 0.195
    heights = (px * 0.215, px * 0.280, px * 0.345)
    total = bar_w * 3 + gap * 2
    left = cx - total / 2

    for index, height in enumerate(heights):
        x = left + index * (bar_w + gap)
        rect = NSMakeRect(x, base_y, bar_w, height)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, bar_w * 0.30, bar_w * 0.30)
        # Последний столбик залит: облако пока впереди.
        if index == 2:
            white.set()
            path.fill()
        else:
            dim.set()
            path.setLineWidth_(px * 0.026)
            path.stroke()

    # Линия основания.
    white.set()
    ground = NSBezierPath.bezierPath()
    ground.setLineWidth_(px * 0.028)
    ground.setLineCapStyle_(1)
    ground.moveToPoint_(NSMakePoint(left - px * 0.040, base_y - px * 0.028))
    ground.lineToPoint_(NSMakePoint(left + total + px * 0.040,
                                    base_y - px * 0.028))
    ground.stroke()

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
