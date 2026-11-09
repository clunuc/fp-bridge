"""Draws FP Bridge's icons with AppKit (no extra dependencies).

    .venv/bin/python tools/make_icons.py

Writes:
  fpbridge/assets/menubar.png, menubar@2x.png   template image for the menu bar (a fader)
  fpbridge/assets/AppIcon.icns                   app icon
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSGradient,
    NSGraphicsContext,
    NSPNGFileType,
)
from Foundation import NSMakeRect

ASSETS = Path(__file__).resolve().parent.parent / "fpbridge" / "assets"


def _canvas(px: int):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    return rep


def _save(rep, path: Path) -> None:
    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    data.writeToFile_atomically_(str(path), True)


def _rrect(x, y, w, h, r):
    return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), r, r)


def menubar(px: int, path: Path) -> None:
    """Black fader on transparent; macOS tints template images for light/dark menu bars."""
    rep = _canvas(px)
    s = px / 18.0
    NSColor.blackColor().set()
    # slot
    _rrect(8.0 * s, 1.5 * s, 2.0 * s, 15.0 * s, 1.0 * s).fill()
    # scale ticks on the left
    for y in (3.0, 6.25, 9.5, 12.75, 16.0):
        _rrect(1.5 * s, (y - 0.5) * s, 2.5 * s, 1.0 * s, 0.5 * s).fill()
    # fader cap (with a cut-out line in the middle)
    cap = _rrect(5.5 * s, 7.0 * s, 7.0 * s, 5.0 * s, 1.3 * s)
    cap.fill()
    NSGraphicsContext.currentContext().setCompositingOperation_(0)  # NSCompositingOperationClear
    _rrect(6.5 * s, 9.1 * s, 5.0 * s, 0.9 * s, 0.45 * s).fill()
    _save(rep, path)


def app_icon(px: int, path: Path) -> None:
    rep = _canvas(px)
    s = px / 1024.0
    # background: dark rounded square
    bg = _rrect(100 * s, 100 * s, 824 * s, 824 * s, 185 * s)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.21, 0.24, 1),
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.08, 0.10, 1),
    ).drawInBezierPath_angle_(bg, -90)
    # scale ticks
    NSColor.colorWithCalibratedWhite_alpha_(0.55, 1).set()
    for i in range(9):
        y = 230 + i * 70
        w = 90 if i % 4 == 0 else 55
        _rrect((360 - w) * s, (y - 5) * s, w * s, 10 * s, 5 * s).fill()
    # slot
    NSColor.colorWithCalibratedWhite_alpha_(0.02, 1).set()
    _rrect(487 * s, 200 * s, 50 * s, 624 * s, 25 * s).fill()
    # cap: brushed silver with a white line, sitting a bit above the middle
    cap = _rrect(352 * s, 520 * s, 320 * s, 150 * s, 34 * s)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedWhite_alpha_(0.93, 1),
        NSColor.colorWithCalibratedWhite_alpha_(0.62, 1),
    ).drawInBezierPath_angle_(cap, -90)
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.42, 0.10, 1).set()  # orange line
    _rrect(372 * s, 588 * s, 280 * s, 14 * s, 7 * s).fill()
    _save(rep, path)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    menubar(18, ASSETS / "menubar.png")
    menubar(36, ASSETS / "menubar@2x.png")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            app_icon(size, iconset / f"icon_{size}x{size}.png")
            app_icon(size * 2, iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "AppIcon.icns")], check=True)
    print("iconos en", ASSETS)


if __name__ == "__main__":
    main()
