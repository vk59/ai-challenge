#!/usr/bin/env python3
"""Иконка дня 12: силуэт собеседника — персонализация.

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
    # Зелёный градиент — цвет панели профиля в самом приложении.
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.16, 0.72, 0.38, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.11, 0.52, 0.72, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    NSColor.whiteColor().set()
    # Голова
    голова = px * 0.155
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(px / 2 - голова, px * 0.545, голова * 2, голова * 2)).fill()
    # Плечи — полукруг, обрезанный нижней кромкой плашки
    плечи_ш = px * 0.46
    плечи_в = px * 0.30
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(px / 2 - плечи_ш / 2, px * 0.245, плечи_ш, плечи_в),
        плечи_ш / 2, плечи_ш / 2).fill()

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
