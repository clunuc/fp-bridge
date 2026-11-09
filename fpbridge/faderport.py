"""PreSonus FaderPort (2018 / "FaderPort V2", single fader) native protocol.

Source: FaderPort Owner's Manual, Appendix 8.2 "FaderPort Multichannel
Controller MIDI Protocol". Native (Studio One) mode is selected by holding
NEXT while powering on, then pressing SOLO.

  Buttons   90 id 7F (press) / 90 id 00 (release), channel 1
  LEDs      90 id 00 off / 7F on / 01 blinking
  RGB LEDs  91 id rr, 92 id gg, 93 id bb (7-bit each)
  Fader     E0 ll hh   (pitch bend, both directions; sending it moves the motor)
  Encoder   B0 3C xx   bit 0x40 = direction, bits 0-5 = number of steps
"""

from __future__ import annotations

BUTTONS: dict[str, int] = {
    "arm": 0x00,
    "bypass": 0x03,
    "link": 0x05,
    "solo": 0x08,
    "mute": 0x10,
    "encoder_push": 0x20,
    "pan": 0x2A,
    "prev": 0x2E,
    "next": 0x2F,
    "channel": 0x36,
    "scroll": 0x38,
    "master": 0x3A,
    "click": 0x3B,
    "section": 0x3C,
    "marker": 0x3D,
    "shift": 0x46,
    "read": 0x4A,
    "write": 0x4B,
    "touch": 0x4D,
    "loop": 0x56,
    "rewind": 0x5B,
    "ffwd": 0x5C,
    "stop": 0x5D,
    "play": 0x5E,
    "record": 0x5F,
    "footswitch": 0x66,
    "fader_touch": 0x68,
}
NOTE_TO_BUTTON = {v: k for k, v in BUTTONS.items()}

# Buttons with an RGB LED (the rest are single-colour)
RGB_BUTTONS = {"link", "pan", "channel", "scroll", "read", "write", "touch"}
NO_LED = {"encoder_push", "footswitch", "fader_touch"}

ENCODER_CC = 0x3C
# The manual documents 0x3C; FaderPort 8/16 send their pan/param encoder on 0x10.
# Accept both so the knob works whichever the firmware uses.
ENCODER_CCS = (0x3C, 0x10)

LED_OFF, LED_ON, LED_BLINK = 0x00, 0x7F, 0x01


def parse(msg: list[int], encoder_cw_sets_bit: bool = False) -> tuple | None:
    """Raw FaderPort message -> ('button', name, pressed) | ('fader', v14) | ('encoder', delta)."""
    if len(msg) < 3:
        return None
    st, d1, d2 = msg[0], msg[1], msg[2]
    kind = st & 0xF0
    if kind in (0x90, 0x80):
        name = NOTE_TO_BUTTON.get(d1, f"note{d1:02X}")
        return ("button", name, kind == 0x90 and d2 > 0)
    if kind == 0xE0:
        return ("fader", (d2 << 7) | d1)
    if kind == 0xB0 and d1 in ENCODER_CCS:
        steps = d2 & 0x3F or 1
        bit = bool(d2 & 0x40)
        cw = bit if encoder_cw_sets_bit else not bit
        return ("encoder", steps if cw else -steps)
    return None


def led(name: str, state: int) -> list[list[int]]:
    note = BUTTONS[name]
    return [[0x90, note, state]]


def led_color(name: str, rgb: tuple[int, int, int]) -> list[list[int]]:
    note = BUTTONS[name]
    r, g, b = (max(0, min(127, c)) for c in rgb)
    return [[0x91, note, r], [0x92, note, g], [0x93, note, b]]


def fader(value14: int) -> list[list[int]]:
    value14 = max(0, min(0x3FFF, value14))
    return [[0xE0, value14 & 0x7F, value14 >> 7]]
