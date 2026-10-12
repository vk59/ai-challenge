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

    # Слева книга: две страницы с корешком посередине.
    book_w, book_h = px * 0.300, px * 0.255
    book_x, book_y = cx - px * 0.300, cy - px * 0.040
    white.set()
    book = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(book_x, book_y, book_w, book_h), px * 0.018, px * 0.018)
    book.setLineWidth_(px * 0.028)
    book.stroke()
    spine = NSBezierPath.bezierPath()
    spine.setLineWidth_(px * 0.024)
    spine.moveToPoint_(NSMakePoint(book_x + book_w / 2, book_y))
    spine.lineToPoint_(NSMakePoint(book_x + book_w / 2, book_y + book_h))
    spine.stroke()
    for side in (0, 1):
        for row in range(2):
            y = book_y + book_h - px * (0.072 + row * 0.058)
            left = book_x + px * 0.030 + side * (book_w / 2)
            line = NSBezierPath.bezierPath()
            line.setLineWidth_(px * 0.018)
            line.setLineCapStyle_(1)
            line.moveToPoint_(NSMakePoint(left, y))
            line.lineToPoint_(NSMakePoint(left + book_w / 2 - px * 0.060, y))
            line.stroke()

    # Справа ветка git: ствол и отвод с узлами.
    trunk_x = cx + px * 0.130
    low, high = cy - px * 0.175, cy + px * 0.215
    trunk = NSBezierPath.bezierPath()
    trunk.setLineWidth_(px * 0.028)
    trunk.setLineCapStyle_(1)
    trunk.moveToPoint_(NSMakePoint(trunk_x, low))
    trunk.lineToPoint_(NSMakePoint(trunk_x, high))
    trunk.stroke()

    branch_x = trunk_x + px * 0.145
    fork_y = cy - px * 0.010
    branch = NSBezierPath.bezierPath()
    branch.setLineWidth_(px * 0.026)
    branch.setLineCapStyle_(1)
    branch.moveToPoint_(NSMakePoint(trunk_x, fork_y))
    branch.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(branch_x, fork_y + px * 0.145),
        NSMakePoint(trunk_x + px * 0.090, fork_y),
        NSMakePoint(branch_x, fork_y + px * 0.055))
    branch.stroke()

    node = px * 0.042
    for x, y in ((trunk_x, high), (trunk_x, low),
                 (branch_x, fork_y + px * 0.145)):
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(x - node, y - node, node * 2, node * 2)).fill()

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
