#!/usr/bin/env python3
"""Иконка дня 14: щит с перечёркнутой линией — то, чего нельзя.

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


def щит(px: int) -> NSBezierPath:
    """Простой щит: прямоугольник со скруглённым верхом и остриём снизу."""
    ш = px * 0.40
    в = px * 0.46
    x = px / 2
    верх = px * 0.72
    низ = верх - в

    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(x, верх))
    path.lineToPoint_(NSMakePoint(x + ш / 2, верх - в * 0.22))
    path.lineToPoint_(NSMakePoint(x + ш / 2, низ + в * 0.34))
    path.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(x, низ),
        NSMakePoint(x + ш / 2, низ + в * 0.12),
        NSMakePoint(x + ш * 0.28, низ + в * 0.03))
    path.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(x - ш / 2, низ + в * 0.34),
        NSMakePoint(x - ш * 0.28, низ + в * 0.03),
        NSMakePoint(x - ш / 2, низ + в * 0.12))
    path.lineToPoint_(NSMakePoint(x - ш / 2, верх - в * 0.22))
    path.closePath()
    return path


def render(px: int) -> bytes:
    image = NSImage.alloc().initWithSize_(NSMakeSize(px, px))
    image.lockFocus()

    inset = px * 0.085
    side = px - inset * 2
    radius = side * 0.2237
    plate = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(inset, inset, side, side), radius, radius)
    # Красно-розовый: цвет панели ограничений в приложении.
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.92, 0.18, 0.33, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.62, 0.13, 0.45, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    NSColor.whiteColor().set()
    щ = щит(px)
    щ.fill()

    # Перечёркивание — то, что запрещено.
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.80, 0.14, 0.30, 1.0).set()
    черта = NSBezierPath.bezierPath()
    черта.setLineWidth_(px * 0.055)
    черта.setLineCapStyle_(1)
    черта.moveToPoint_(NSMakePoint(px * 0.38, px * 0.58))
    черта.lineToPoint_(NSMakePoint(px * 0.62, px * 0.38))
    черта.stroke()

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
