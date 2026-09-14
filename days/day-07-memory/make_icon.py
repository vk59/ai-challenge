#!/usr/bin/env python3
"""Рисует иконку приложения средствами Cocoa и складывает PNG в .iconset.

Отдельных картинок в репозитории не держим — иконка целиком описана кодом.
Вызывается из build_app.sh, дальше системный iconutil собирает из папки .icns.

В дне 6 на плашке была звёздочка — «агент». Здесь она лежит на стопке дисков:
тот же агент, но с памятью, которая переживает выключение.

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


def sparkle(px: int, center_y: float, reach: float) -> NSBezierPath:
    """Четырёхлучевая звёздочка — та же ✦, что на пустом экране интерфейса."""
    center_x = px / 2
    pull = 0.62          # насколько лучи втянуты к центру: 0 — ромб, 1 — крест

    tips = [
        (center_x, center_y + reach),
        (center_x + reach, center_y),
        (center_x, center_y - reach),
        (center_x - reach, center_y),
    ]

    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(*tips[0]))
    for i in range(4):
        a, b = tips[i], tips[(i + 1) % 4]
        c1 = (a[0] + (center_x - a[0]) * pull, a[1] + (center_y - a[1]) * pull)
        c2 = (b[0] + (center_x - b[0]) * pull, b[1] + (center_y - b[1]) * pull)
        path.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(*b), NSMakePoint(*c1), NSMakePoint(*c2)
        )
    path.closePath()
    return path


def platter(px: int, center_y: float) -> NSBezierPath:
    """Один «диск» стопки: сплюснутый эллипс, как верх цилиндра базы."""
    width = px * 0.46
    height = px * 0.125
    return NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect((px - width) / 2, center_y - height / 2, width, height)
    )


def render(px: int) -> bytes:
    """Скруглённый квадрат с градиентом, стопка дисков и звёздочка поверх."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(px, px))
    image.lockFocus()

    inset = px * 0.085                   # поля по гайдлайнам macOS
    side = px - inset * 2
    radius = side * 0.2237               # фирменная «сквиркл»-скруглённость

    plate = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(inset, inset, side, side), radius, radius
    )
    # Градиент дня 7: холоднее, чем фиолетовый дня 6 — дни должны различаться
    # в доке с одного взгляда.
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.20, 0.55, 0.97, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.40, 0.98, 1.0),
    ).drawInBezierPath_angle_(plate, -90.0)

    # Три диска снизу — архив, который остаётся на месте после выключения.
    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.85).set()
    for level in range(3):
        platter(px, px * (0.295 + level * 0.105)).fill()

    # Звёздочка-агент сверху, с небольшим тёмным ореолом, чтобы не сливалась
    # с верхним диском.
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.13, 0.20, 0.45, 0.30).set()
    sparkle(px, px * 0.635, px * 0.245).fill()
    NSColor.whiteColor().set()
    sparkle(px, px * 0.645, px * 0.225).fill()

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
