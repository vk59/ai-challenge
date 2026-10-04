#!/usr/bin/env python3
"""Иконка дня 21: страница, разрезанная на куски, и лупа.

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

    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.95).set()

    # Лист, сдвинутый влево-вверх: справа-снизу встанет лупа.
    л_ш, л_в = px * 0.300, px * 0.380
    л_x, л_y = cx - px * 0.200, cy - л_в / 2 + px * 0.045
    лист = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(л_x, л_y, л_ш, л_в), px * 0.022, px * 0.022)
    лист.setLineWidth_(px * 0.026)
    лист.stroke()

    # Строки текста, сгруппированные по три — это и есть куски.
    всего, отступ = 9, px * 0.030
    шаг = (л_в - отступ * 2) / всего
    for номер in range(всего):
        y = л_y + л_в - отступ - шаг * (номер + 0.5)
        # Между группами по три строки — разрез: пропускаем линию.
        if номер % 3 == 2:
            continue
        длина = л_ш - отступ * 2 - (px * 0.045 if номер % 3 == 1 else 0)
        с = NSBezierPath.bezierPath()
        с.setLineWidth_(px * 0.020)
        с.setLineCapStyle_(1)
        с.moveToPoint_(NSMakePoint(л_x + отступ, y))
        с.lineToPoint_(NSMakePoint(л_x + отступ + длина, y))
        с.stroke()

    # Разрезы между группами — пунктиром, поперёк листа.
    for группа in (1, 2):
        y = л_y + л_в - отступ - шаг * (группа * 3 - 0.5)
        штрих = NSBezierPath.bezierPath()
        штрих.setLineWidth_(px * 0.014)
        x = л_x + отступ * 0.5
        while x < л_x + л_ш - отступ * 0.5:
            штрих.moveToPoint_(NSMakePoint(x, y))
            штрих.lineToPoint_(NSMakePoint(min(x + px * 0.022, л_x + л_ш - отступ * 0.5), y))
            x += px * 0.040
        штрих.stroke()

    # Лупа поверх правого нижнего угла листа.
    л_цx, л_цy = cx + px * 0.130, cy - px * 0.120
    r = px * 0.105
    кольцо = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(л_цx - r, л_цy - r, r * 2, r * 2))
    # Подложка, чтобы строки не просвечивали сквозь стекло.
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.26, 0.42, 0.88, 1.0).set()
    кольцо.fill()
    NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.95).set()
    кольцо.setLineWidth_(px * 0.032)
    кольцо.stroke()

    ручка = NSBezierPath.bezierPath()
    ручка.setLineWidth_(px * 0.038)
    ручка.setLineCapStyle_(1)
    угол = math.radians(-45)
    ручка.moveToPoint_(NSMakePoint(л_цx + r * math.cos(угол) * 0.96,
                                   л_цy + r * math.sin(угол) * 0.96))
    ручка.lineToPoint_(NSMakePoint(л_цx + (r + px * 0.072) * math.cos(угол),
                                   л_цy + (r + px * 0.072) * math.sin(угол)))
    ручка.stroke()

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
