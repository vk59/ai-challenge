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

    # Экран ноутбука.
    screen_w, screen_h = px * 0.380, px * 0.265
    screen_x, screen_y = cx - screen_w / 2, cy - px * 0.010
    white.set()
    screen = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(screen_x, screen_y, screen_w, screen_h),
        px * 0.020, px * 0.020)
    screen.setLineWidth_(px * 0.030)
    screen.stroke()

    # Основание.
    base = NSBezierPath.bezierPath()
    base.setLineWidth_(px * 0.030)
    base.setLineCapStyle_(1)
    base.moveToPoint_(NSMakePoint(screen_x - px * 0.072, screen_y - px * 0.056))
    base.lineToPoint_(NSMakePoint(screen_x + screen_w + px * 0.072,
                                  screen_y - px * 0.056))
    base.stroke()
    for side in (-1, 1):
        leg = NSBezierPath.bezierPath()
        leg.setLineWidth_(px * 0.026)
        leg.setLineCapStyle_(1)
        edge = screen_x if side < 0 else screen_x + screen_w
        leg.moveToPoint_(NSMakePoint(edge, screen_y))
        leg.lineToPoint_(NSMakePoint(edge + side * px * 0.072,
                                     screen_y - px * 0.056))
        leg.stroke()

    # Искра на экране: модель думает здесь, а не в сети.
    spark_cx, spark_cy = cx, screen_y + screen_h / 2
    for index in range(4):
        angle = math.radians(90 * index + 45)
        ray = NSBezierPath.bezierPath()
        ray.setLineWidth_(px * 0.024)
        ray.setLineCapStyle_(1)
        inner, outer = px * 0.032, px * 0.084
        ray.moveToPoint_(NSMakePoint(spark_cx + inner * math.cos(angle),
                                     spark_cy + inner * math.sin(angle)))
        ray.lineToPoint_(NSMakePoint(spark_cx + outer * math.cos(angle),
                                     spark_cy + outer * math.sin(angle)))
        ray.stroke()
    radius = px * 0.030
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(spark_cx - radius, spark_cy - radius,
                   radius * 2, radius * 2)).fill()

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
