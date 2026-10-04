#!/usr/bin/env python3
"""Иконка дня 22: весы — пустая чаша против чаши с документами.

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

    # Стойка и коромысло.
    верх = cy + px * 0.150
    стойка = NSBezierPath.bezierPath()
    стойка.setLineWidth_(px * 0.030)
    стойка.setLineCapStyle_(1)
    стойка.moveToPoint_(NSMakePoint(cx, cy - px * 0.165))
    стойка.lineToPoint_(NSMakePoint(cx, верх))
    стойка.stroke()

    плечо = px * 0.185
    коромысло = NSBezierPath.bezierPath()
    коромысло.setLineWidth_(px * 0.026)
    коромысло.setLineCapStyle_(1)
    коромысло.moveToPoint_(NSMakePoint(cx - плечо, верх))
    коромысло.lineToPoint_(NSMakePoint(cx + плечо, верх))
    коромысло.stroke()

    # Основание.
    низ = NSBezierPath.bezierPath()
    низ.setLineWidth_(px * 0.026)
    низ.setLineCapStyle_(1)
    низ.moveToPoint_(NSMakePoint(cx - px * 0.090, cy - px * 0.165))
    низ.lineToPoint_(NSMakePoint(cx + px * 0.090, cy - px * 0.165))
    низ.stroke()

    # Две чаши. Левая выше (пустая), правая ниже (гружёная документами) —
    # перевес и есть смысл дня.
    for сторона, провис in ((-1, 0.060), (1, 0.115)):
        x = cx + сторона * плечо
        чаша_y = верх - px * провис
        нить = NSBezierPath.bezierPath()
        нить.setLineWidth_(px * 0.016)
        нить.moveToPoint_(NSMakePoint(x, верх))
        нить.lineToPoint_(NSMakePoint(x, чаша_y))
        нить.stroke()

        ш = px * 0.105
        чаша = NSBezierPath.bezierPath()
        чаша.setLineWidth_(px * 0.024)
        чаша.setLineCapStyle_(1)
        чаша.moveToPoint_(NSMakePoint(x - ш, чаша_y))
        чаша.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(x + ш, чаша_y),
            NSMakePoint(x - ш * 0.55, чаша_y - px * 0.072),
            NSMakePoint(x + ш * 0.55, чаша_y - px * 0.072))
        чаша.stroke()

        # В правую чашу кладём «документы»: три короткие строки.
        if сторона > 0:
            for номер in range(3):
                y = чаша_y + px * (0.030 + номер * 0.034)
                длина = ш * (0.95 - номер * 0.22)
                с = NSBezierPath.bezierPath()
                с.setLineWidth_(px * 0.020)
                с.setLineCapStyle_(1)
                с.moveToPoint_(NSMakePoint(x - длина, y))
                с.lineToPoint_(NSMakePoint(x + длина, y))
                с.stroke()

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
