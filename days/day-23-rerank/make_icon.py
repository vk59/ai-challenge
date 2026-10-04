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
    dim = NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.42)

    # Два столбца полосок: слева порядок поиска, справа после реранкинга.
    bar_w = px * 0.165
    bar_h = px * 0.055
    gap = px * 0.028
    rows = 4
    left_x = cx - px * 0.235
    right_x = cx + px * 0.070
    top_y = cy + px * 0.150

    # Выделенная полоска: слева она последняя, справа первая.
    highlight_left = 3
    highlight_right = 0

    for column, (x, highlight) in enumerate(
            ((left_x, highlight_left), (right_x, highlight_right))):
        for row in range(rows):
            y = top_y - row * (bar_h + gap) - bar_h
            rect = NSMakeRect(x, y, bar_w, bar_h)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, bar_h * 0.34, bar_h * 0.34)
            if row == highlight:
                white.set()
                path.fill()
            else:
                dim.set()
                path.setLineWidth_(px * 0.018)
                path.stroke()

    # Стрелка от выделенной полоски слева к выделенной справа.
    white.set()
    y_from = top_y - highlight_left * (bar_h + gap) - bar_h / 2
    y_to = top_y - highlight_right * (bar_h + gap) - bar_h / 2
    curve = NSBezierPath.bezierPath()
    curve.setLineWidth_(px * 0.024)
    curve.setLineCapStyle_(1)
    curve.moveToPoint_(NSMakePoint(left_x + bar_w + px * 0.014, y_from))
    middle = (left_x + bar_w + right_x) / 2
    curve.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(right_x - px * 0.030, y_to),
        NSMakePoint(middle, y_from),
        NSMakePoint(middle, y_to))
    curve.stroke()

    # Наконечник.
    tip_x = right_x - px * 0.030
    for sign in (1, -1):
        head = NSBezierPath.bezierPath()
        head.setLineWidth_(px * 0.024)
        head.setLineCapStyle_(1)
        head.moveToPoint_(NSMakePoint(tip_x - px * 0.040,
                                      y_to + sign * px * 0.036))
        head.lineToPoint_(NSMakePoint(tip_x, y_to))
        head.stroke()

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
