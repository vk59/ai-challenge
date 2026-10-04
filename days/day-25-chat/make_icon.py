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
    dim = NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.45)

    # Два облачка реплик: верхнее слева (вопрос), нижнее справа (ответ).
    bubbles = ((cx - px * 0.195, cy + px * 0.055, px * 0.260, px * 0.150, -1, True),
               (cx - px * 0.065, cy - px * 0.165, px * 0.260, px * 0.150, 1, False))
    for bx, by, bw, bh, side, filled in bubbles:
        rect = NSMakeRect(bx, by, bw, bh)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, bh * 0.36, bh * 0.36)
        (white if filled else dim).set()
        if filled:
            path.fill()
        else:
            path.setLineWidth_(px * 0.026)
            path.stroke()

        # Хвостик облачка.
        tail = NSBezierPath.bezierPath()
        tip_x = bx + (bw * 0.22 if side < 0 else bw * 0.78)
        tail.moveToPoint_(NSMakePoint(tip_x - px * 0.026, by + px * 0.004))
        tail.lineToPoint_(NSMakePoint(tip_x + px * 0.026, by + px * 0.004))
        tail.lineToPoint_(NSMakePoint(tip_x + side * px * 0.030,
                                      by - px * 0.044))
        tail.closePath()
        if filled:
            tail.fill()
        else:
            tail.setLineWidth_(px * 0.026)
            tail.stroke()

    # Закладка справа сверху — память задачи, то, что остаётся.
    white.set()
    mark_x, mark_top = cx + px * 0.175, cy + px * 0.235
    mark_w, mark_h = px * 0.090, px * 0.165
    mark = NSBezierPath.bezierPath()
    mark.moveToPoint_(NSMakePoint(mark_x, mark_top))
    mark.lineToPoint_(NSMakePoint(mark_x + mark_w, mark_top))
    mark.lineToPoint_(NSMakePoint(mark_x + mark_w, mark_top - mark_h))
    mark.lineToPoint_(NSMakePoint(mark_x + mark_w / 2,
                                  mark_top - mark_h + px * 0.052))
    mark.lineToPoint_(NSMakePoint(mark_x, mark_top - mark_h))
    mark.closePath()
    mark.fill()

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
