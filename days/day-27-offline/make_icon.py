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
    white.set()

    # Облако: три круга и полка под ними, обведённые одним контуром.
    cloud = NSBezierPath.bezierPath()
    for dx, dy, r in ((-px * 0.105, 0.0, px * 0.088),
                      (0.0, px * 0.052, px * 0.112),
                      (px * 0.115, 0.0, px * 0.082)):
        cloud.appendBezierPathWithOvalInRect_(
            NSMakeRect(cx + dx - r, cy + dy - r, r * 2, r * 2))
    cloud.appendBezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - px * 0.190, cy - px * 0.090, px * 0.385, px * 0.092),
        px * 0.046, px * 0.046)
    cloud.setLineWidth_(px * 0.030)
    cloud.stroke()

    # Перечёркивание — наискосок через всё облако.
    slash = NSBezierPath.bezierPath()
    slash.setLineWidth_(px * 0.046)
    slash.setLineCapStyle_(1)
    slash.moveToPoint_(NSMakePoint(cx - px * 0.215, cy - px * 0.170))
    slash.lineToPoint_(NSMakePoint(cx + px * 0.215, cy + px * 0.200))
    slash.stroke()

    # Ноль под облаком: столько обращений наружу.
    zero_r = px * 0.072
    zero_y = cy - px * 0.215
    zero = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - zero_r, zero_y - zero_r * 0.86,
                   zero_r * 2, zero_r * 1.72))
    zero.setLineWidth_(px * 0.034)
    zero.stroke()

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
