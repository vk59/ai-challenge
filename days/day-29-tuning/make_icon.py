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
    dim = NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.45)

    # Два столбика: было пониже, стало повыше, и стрелка роста.
    bar_w = px * 0.105
    base_y = cy - px * 0.215
    low, high = px * 0.165, px * 0.330
    left_x = cx - px * 0.195
    right_x = cx - px * 0.030

    dim.set()
    low_bar = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(left_x, base_y, bar_w, low), bar_w * 0.30, bar_w * 0.30)
    low_bar.setLineWidth_(px * 0.026)
    low_bar.stroke()

    white.set()
    high_bar = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(right_x, base_y, bar_w, high), bar_w * 0.30, bar_w * 0.30)
    high_bar.fill()

    # Основание.
    ground = NSBezierPath.bezierPath()
    ground.setLineWidth_(px * 0.026)
    ground.setLineCapStyle_(1)
    ground.moveToPoint_(NSMakePoint(left_x - px * 0.045, base_y - px * 0.026))
    ground.lineToPoint_(NSMakePoint(right_x + bar_w + px * 0.045,
                                    base_y - px * 0.026))
    ground.stroke()

    # Гаечный ключ справа: наклонная рукоять и раскрытый зев.
    angle = math.radians(48)
    handle_from = NSMakePoint(cx + px * 0.095, cy - px * 0.175)
    length = px * 0.275
    handle_to = NSMakePoint(handle_from.x + length * math.cos(angle),
                            handle_from.y + length * math.sin(angle))
    handle = NSBezierPath.bezierPath()
    handle.setLineWidth_(px * 0.052)
    handle.setLineCapStyle_(1)
    handle.moveToPoint_(handle_from)
    handle.lineToPoint_(handle_to)
    handle.stroke()

    # Зев: кольцо с вырезом, повёрнутое по рукояти.
    head_r = px * 0.072
    head = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(handle_to.x - head_r, handle_to.y - head_r,
                   head_r * 2, head_r * 2))
    head.setLineWidth_(px * 0.046)
    head.stroke()
    # Вырез — короткий отрезок цветом фона поверх кольца.
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.26, 0.42, 0.88, 1.0).set()
    notch = NSBezierPath.bezierPath()
    notch.setLineWidth_(px * 0.062)
    notch.setLineCapStyle_(0)
    notch.moveToPoint_(NSMakePoint(handle_to.x + head_r * 0.30,
                                   handle_to.y + head_r * 0.80))
    notch.lineToPoint_(NSMakePoint(handle_to.x + head_r * 1.30,
                                   handle_to.y + head_r * 1.30))
    notch.stroke()

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
