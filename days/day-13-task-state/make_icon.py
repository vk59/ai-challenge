#!/usr/bin/env python3
"""Иконка дня 13: четыре узла автомата, третий — текущий.

    python make_icon.py путь/к/icon.iconset
"""

import sys
from pathlib import Path

from AppKit import NSBezierPath, NSBitmapImageRep, NSColor, NSGradient, NSImage
from Foundation import NSMakeRect, NSMakeSize

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
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.37, 0.36, 0.90, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.20, 0.55, 0.88, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    # Четыре узла в ряд, соединённые линией. Текущий (третий) — крупнее
    # и непрозрачный, пройденные — поменьше, будущий — полупрозрачный.
    узлов = 4
    шаг = px * 0.175
    старт = px / 2 - шаг * (узлов - 1) / 2
    y = px / 2

    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.55).set()
    линия = NSBezierPath.bezierPath()
    линия.setLineWidth_(px * 0.022)
    линия.moveToPoint_((старт, y))
    линия.lineToPoint_((старт + шаг * (узлов - 1), y))
    линия.stroke()

    for i in range(узлов):
        x = старт + шаг * i
        если_текущий = i == 2
        r = px * (0.062 if если_текущий else 0.042)
        прозрачность = 1.0 if i <= 2 else 0.45
        NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, прозрачность).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(x - r, y - r, r * 2, r * 2)).fill()
        if если_текущий:
            # Кольцо вокруг текущего узла
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.20, 0.30, 0.75, 1.0).set()
            внутр = r * 0.42
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - внутр, y - внутр, внутр * 2, внутр * 2)).fill()

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
