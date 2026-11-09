"""Send keyboard shortcuts to Pro Tools (macOS, Quartz CGEvents).

Needs Accessibility permission for the app that runs the bridge
(System Settings > Privacy & Security > Accessibility > Terminal / Python).

Key names use physical key positions of a US layout. Letters and digits are
in the same place on a Spanish keyboard; for punctuation keys that differ
use `keycode:<n>` (find the code with `python -m fpbridge keylearn`).
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
    "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
    "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18,
    "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E, "o": 0x1F, "u": 0x20,
    "[": 0x21, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29,
    "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F, "`": 0x32,
    "return": 0x24, "enter": 0x24, "tab": 0x30, "space": 0x31, "delete": 0x33, "backspace": 0x33,
    "escape": 0x35, "esc": 0x35, "forwarddelete": 0x75, "home": 0x73, "end": 0x77,
    "pageup": 0x74, "pagedown": 0x79, "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62,
    "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    # numeric keypad (Pro Tools uses these a lot: transport, memory locations, nudge)
    "kp0": 0x52, "kp1": 0x53, "kp2": 0x54, "kp3": 0x55, "kp4": 0x56, "kp5": 0x57, "kp6": 0x58,
    "kp7": 0x59, "kp8": 0x5B, "kp9": 0x5C, "kpdot": 0x41, "kpmul": 0x43, "kpplus": 0x45, "kpclear": 0x47,
    "kpdiv": 0x4B, "kpenter": 0x4C, "kpminus": 0x4E, "kpequals": 0x51,
}

MODIFIERS = {
    "cmd": 1 << 20, "command": 1 << 20,
    "shift": 1 << 17,
    "opt": 1 << 19, "option": 1 << 19, "alt": 1 << 19,
    "ctrl": 1 << 18, "control": 1 << 18,
    "fn": 1 << 23,
}
_NUMPAD_FLAG = 1 << 21


def parse_combo(combo: str) -> tuple[int, int]:
    """'cmd+shift+e' -> (keycode, flags). 'keycode:41' is accepted as key."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combo")
    *mods, key = parts
    flags = 0
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r} in {combo!r}")
        flags |= MODIFIERS[m]
    if key.startswith("keycode:"):
        code = int(key.split(":", 1)[1], 0)
    elif key in KEYCODES:
        code = KEYCODES[key]
    else:
        raise ValueError(f"unknown key {key!r} in {combo!r} (use keycode:<n> for others)")
    if key.startswith("kp"):
        flags |= _NUMPAD_FLAG
    return code, flags


def check_accessibility(prompt: bool = False) -> bool:
    """Without Accessibility permission macOS silently drops synthetic key events."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    except ImportError:
        log.warning("pyobjc-framework-ApplicationServices no instalado; no puedo comprobar Accesibilidad")
        return True
    ok = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    if not ok:
        log.warning(
            "SIN PERMISO DE ACCESIBILIDAD: macOS descartará los atajos (Click, Master, Section...). "
            "Ajustes del Sistema > Privacidad y seguridad > Accesibilidad > activa la app desde la que "
            "lanzas el puente (FP Bridge, o tu app de terminal) y reinicia el puente."
        )
    return ok


class KeySender:
    """target: 'frontmost' posts like a real keyboard (Pro Tools must be in front);
    'protools' posts straight to the Pro Tools process even if it's in background."""

    def __init__(self, target: str = "frontmost", dry_run: bool = False):
        self.target = target
        self.dry_run = dry_run
        self._pid: int | None = None
        self._pid_checked = 0.0
        if not dry_run:
            import Quartz  # noqa: F401  (fail early if pyobjc missing)

            check_accessibility(prompt=True)

    def _protools_pid(self) -> int | None:
        now = time.monotonic()
        if self._pid is not None and now - self._pid_checked < 5:
            return self._pid
        from AppKit import NSWorkspace

        self._pid = None
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            bid = app.bundleIdentifier() or ""
            name = app.localizedName() or ""
            if bid.startswith("com.avid.ProTools") or name.startswith("Pro Tools"):
                self._pid = app.processIdentifier()
                break
        self._pid_checked = now
        return self._pid

    def send(self, combo: str) -> None:
        code, flags = parse_combo(combo)
        log.info("key %s", combo)
        if self.dry_run:
            return
        import Quartz

        down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
        Quartz.CGEventSetFlags(down, flags)
        Quartz.CGEventSetFlags(up, flags)
        pid = self._protools_pid() if self.target == "protools" else None
        if pid:
            Quartz.CGEventPostToPid(pid, down)
            Quartz.CGEventPostToPid(pid, up)
        else:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def keylearn() -> None:
    """Print keycode + modifiers of every key you press (Ctrl+C to quit)."""
    import Quartz

    names = {v: k for k, v in KEYCODES.items()}

    def cb(proxy, etype, event, refcon):
        if etype == Quartz.kCGEventKeyDown:
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            mods = [m for m in ("cmd", "shift", "opt", "ctrl") if flags & MODIFIERS[m]]
            key = names.get(code, f"keycode:{code}")
            print("  ->", "+".join(mods + [key]), f"(keycode {code})", flush=True)
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
        cb,
        None,
    )
    if tap is None:
        raise SystemExit(
            "No puedo leer el teclado: da permiso de Accesibilidad / Monitorización de entrada "
            "a la Terminal en Ajustes del Sistema > Privacidad y seguridad."
        )
    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    print("Pulsa teclas (Ctrl+C para salir)...")
    Quartz.CFRunLoopRun()
