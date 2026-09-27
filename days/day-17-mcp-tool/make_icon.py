#!/usr/bin/env python3
"""Иконка дня 17: гаечный ключ — инструмент, который агент берёт в руки.

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
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.37, 0.36, 0.90, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.18, 0.50, 0.92, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    NSColor.whiteColor().set()
    # Ручка ключа — наклонный скруглённый прямоугольник.
    ручка = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(-px * 0.055, -px * 0.23, px * 0.11, px * 0.46),
        px * 0.055, px * 0.055)
    from Foundation import NSAffineTransform
    перенос = NSAffineTransform.transform()
    перенос.translateXBy_yBy_(px * 0.52, px * 0.44)
    перенос.rotateByDegrees_(-38)
    ручка.transformUsingAffineTransform_(перенос)
    ручка.fill()

    # Головка — кольцо с вырезом, получается «рожок».
    центр_x, центр_y = px * 0.38, px * 0.62
    внеш = px * 0.115
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(центр_x - внеш, центр_y - внеш, внеш * 2, внеш * 2)).fill()
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.26, 0.42, 0.91, 1.0).set()
    внутр = внеш * 0.52
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(центр_x - внутр, центр_y - внутр, внутр * 2, внутр * 2)).fill()

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
