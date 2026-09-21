#!/usr/bin/env python3
"""Иконка дня 15: два узла и замок на переходе между ними.

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
    # Оранжевый замок на фиолетовом автомате: цвет запертого перехода в окне.
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.37, 0.36, 0.90, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.55, 0.28, 0.82, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    y = px * 0.42
    r = px * 0.058
    левый, правый = px * 0.30, px * 0.70

    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.92).set()
    for x in (левый, правый):
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(x - r, y - r, r * 2, r * 2)).fill()

    # Линия перехода, разорванная посередине — там, где замок.
    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.6).set()
    for от, до in ((левый + r, px * 0.44), (px * 0.56, правый - r)):
        отрезок = NSBezierPath.bezierPath()
        отрезок.setLineWidth_(px * 0.022)
        отрезок.moveToPoint_(NSMakePoint(от, y))
        отрезок.lineToPoint_(NSMakePoint(до, y))
        отрезок.stroke()

    # Замок: дужка и корпус.
    цвет = NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.62, 0.04, 1.0)
    цвет.set()
    кш, кв = px * 0.145, px * 0.115
    кx, кy = px / 2 - кш / 2, y + px * 0.035
    дужка = NSBezierPath.bezierPath()
    дужка.setLineWidth_(px * 0.030)
    дужка.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        NSMakePoint(px / 2, кy + кв), кш * 0.30, 0, 180)
    дужка.stroke()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(кx, кy, кш, кв), px * 0.018, px * 0.018).fill()

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
