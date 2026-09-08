#!/usr/bin/env python3
"""Рисует иконку приложения средствами Cocoa и складывает PNG в .iconset.

Отдельных картинок в репозитории не держим — иконка целиком описана кодом.
Вызывается из build_app.sh, дальше системный iconutil собирает из папки .icns.

    python make_icon.py путь/к/icon.iconset
"""

import sys
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSGradient,
    NSImage,
)
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize

try:
    from AppKit import NSBitmapImageFileTypePNG as PNG_TYPE
except ImportError:                      # старые версии PyObjC
    PNG_TYPE = 4

# Размеры, которых ждёт iconutil: имя файла → сторона в пикселях.
SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def sparkle(px: int) -> NSBezierPath:
    """Четырёхлучевая звёздочка — та же ✦, что на пустом экране интерфейса."""
    center = px / 2
    reach = px * 0.32
    pull = 0.62          # насколько лучи втянуты к центру: 0 — ромб, 1 — крест

    tips = [
        (center, center + reach),
        (center + reach, center),
        (center, center - reach),
        (center - reach, center),
    ]

    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(*tips[0]))
    for i in range(4):
        a, b = tips[i], tips[(i + 1) % 4]
        c1 = (a[0] + (center - a[0]) * pull, a[1] + (center - a[1]) * pull)
        c2 = (b[0] + (center - b[0]) * pull, b[1] + (center - b[1]) * pull)
        path.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(*b), NSMakePoint(*c1), NSMakePoint(*c2)
        )
    path.closePath()
    return path


def render(px: int) -> bytes:
    """Скруглённый квадрат с градиентом и белой звёздочкой поверх."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(px, px))
    image.lockFocus()

    inset = px * 0.085                   # поля по гайдлайнам macOS
    side = px - inset * 2
    radius = side * 0.2237               # фирменная «сквиркл»-скруглённость

    plate = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(inset, inset, side, side), radius, radius
    )
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.42, 0.99, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.79, 0.36, 0.93, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    NSColor.whiteColor().set()
    sparkle(px).fill()

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
