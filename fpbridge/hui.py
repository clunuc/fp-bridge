"""Mackie HUI protocol, as spoken by Pro Tools.

There is no public official spec; this follows the widely used community
reverse-engineering of the protocol.

Summary of the wire format (all on MIDI channel 1):

  Host -> device (Pro Tools -> us)
    90 00 00            ping (we must answer 90 00 7F, or PT drops the device)
    B0 0C zz / B0 2C pp LED: select zone zz, then port pp (bit 0x40 = on)
    B0 0z hh / B0 2z ll fader position for strip z (14-bit, hi then lo)
    B0 1z vv            V-pot LED ring for strip z
    F0 00 00 66 05 00 ... F7   displays (scribble strips, main LCD, timecode)

  Device -> host (us -> Pro Tools)
    90 00 7F            ping reply
    B0 0F zz / B0 2F pp switch: select zone zz, then port pp (bit 0x40 = pressed)
    B0 0z hh / B0 2z ll fader move for strip z (send touch switch first!)
    B0 4z dd            V-pot delta for strip z
    B0 0D dd            jog wheel delta
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Switch / LED map:  "group.name" -> (zone, port)
# Strip switches use "strip.<name>" and are resolved against a strip index.
# ---------------------------------------------------------------------------

STRIP_PORTS = {
    "fader": 0,  # fader touch
    "select": 1,
    "mute": 2,
    "solo": 3,
    "auto": 4,
    "vsel": 5,  # v-pot push
    "insert": 6,
    "rec": 7,  # record ready / arm
}

_ZONES: dict[int, dict[str, int]] = {
    0x08: {"ctrl": 0, "shift": 1, "editmode": 2, "undo": 3, "alt": 4, "option": 5, "edittool": 6, "save": 7},
    0x09: {"mix": 0, "edit": 1, "transport": 2, "memloc": 3, "status": 4, "alt": 5},
    0x0A: {"chan_left": 0, "bank_left": 1, "chan_right": 2, "bank_right": 3},
    0x0B: {"output": 0, "input": 1, "pan": 2, "send_e": 3, "send_d": 4, "send_c": 5, "send_b": 6, "send_a": 7},
    0x0C: {"assign": 0, "default": 1, "suspend": 2, "shift": 3, "mute": 4, "bypass": 5, "recrdy_all": 6},
    0x0D: {"down": 0, "left": 1, "mode": 2, "right": 3, "up": 4, "scrub": 5, "shuttle": 6},
    0x0E: {"talkback": 0, "rewind": 1, "ffwd": 2, "stop": 3, "play": 4, "record": 5},
    0x0F: {"rtz": 0, "end": 1, "online": 2, "loop": 3, "quick_punch": 4},
    0x10: {"audition": 0, "pre": 1, "in": 2, "out": 3, "post": 4},
    0x11: {"input3": 0, "input2": 1, "input1": 2, "mute": 3, "discrete": 4},
    0x12: {"output3": 0, "output2": 1, "output1": 2, "dim": 3, "mono": 4},
    0x13: {"0": 0, "1": 1, "4": 2, "2": 3, "5": 4, ".": 5, "3": 6, "6": 7},
    0x14: {"enter": 0, "+": 1},
    0x15: {"7": 0, "8": 1, "9": 2, "-": 3, "clr": 4, "=": 5, "/": 6, "*": 7},
    0x16: {"timecode": 0, "feet": 1, "beats": 2, "rude_solo": 3},
    0x17: {"plugin": 0, "pan": 1, "fader": 2, "sendmute": 3, "send": 4, "mute": 5},
    0x18: {"trim": 0, "latch": 1, "read": 2, "off": 3, "write": 4, "touch": 5},
    0x19: {"phase": 0, "monitor": 1, "auto": 2, "suspend": 3, "create": 4, "group": 5},
    0x1A: {"paste": 0, "cut": 1, "capture": 2, "delete": 3, "copy": 4, "separate": 5},
    0x1B: {"f1": 0, "f2": 1, "f3": 2, "f4": 3, "f5": 4, "f6": 5, "f7": 6, "f8": 7},
    0x1C: {"ins_param": 0, "assign": 1, "select1": 2, "select2": 3, "select3": 4, "select4": 5, "bypass": 6, "compare": 7},
    0x1D: {"relay1": 0, "relay2": 1},
}

_GROUP_NAMES = {
    0x08: "keys",
    0x09: "window",
    0x0A: "bank",
    0x0B: "assign",
    0x0C: "assign2",
    0x0D: "cursor",
    0x0E: "transport",
    0x0F: "transport2",
    0x10: "transport3",
    0x11: "cr_input",
    0x12: "cr_output",
    0x13: "numpad",
    0x14: "numpad2",
    0x15: "numpad3",
    0x16: "timecode",
    0x17: "auto_enable",
    0x18: "auto_mode",
    0x19: "status",
    0x1A: "edit",
    0x1B: "fkeys",
    0x1C: "dsp",
    0x1D: "footswitch",
}

SWITCHES: dict[str, tuple[int, int]] = {}
for _zone, _ports in _ZONES.items():
    for _name, _port in _ports.items():
        SWITCHES[f"{_GROUP_NAMES[_zone]}.{_name}"] = (_zone, _port)

_REVERSE = {v: k for k, v in SWITCHES.items()}


def resolve(name: str, strip: int = 0) -> tuple[int, int]:
    """'transport.play' -> (0x0E, 4); 'strip.solo' -> (strip, 3); 'strip3.solo' -> (3, 3)."""
    group, _, sw = name.partition(".")
    if group.startswith("strip"):
        idx = strip if group == "strip" else int(group[5:])
        if not 0 <= idx <= 7:
            raise ValueError(f"strip index out of range in {name!r}")
        if sw not in STRIP_PORTS:
            raise ValueError(f"unknown strip switch {sw!r} (valid: {', '.join(STRIP_PORTS)})")
        return idx, STRIP_PORTS[sw]
    try:
        return SWITCHES[name]
    except KeyError:
        raise ValueError(f"unknown HUI switch {name!r}") from None


def describe(zone: int, port: int) -> str:
    if zone <= 7:
        for n, p in STRIP_PORTS.items():
            if p == port:
                return f"strip{zone}.{n}"
    return _REVERSE.get((zone, port), f"zone{zone:02X}.port{port}")


# ---------------------------------------------------------------------------
# Encoding (device -> host)
# ---------------------------------------------------------------------------

PING = [0x90, 0x00, 0x00]
PING_REPLY = [0x90, 0x00, 0x7F]


def switch(zone: int, port: int, pressed: bool) -> list[list[int]]:
    return [[0xB0, 0x0F, zone], [0xB0, 0x2F, (0x40 if pressed else 0x00) | port]]


def fader(strip: int, value14: int) -> list[list[int]]:
    value14 = max(0, min(0x3FFF, value14))
    return [[0xB0, strip, value14 >> 7], [0xB0, 0x20 + strip, value14 & 0x7F]]


def _delta_byte(delta: int, cw_flag: bool) -> int:
    """Encode a relative delta. cw_flag=True -> clockwise sets bit 0x40."""
    n = min(abs(delta), 0x3F)
    positive = delta > 0
    return (0x40 if positive == cw_flag else 0x00) | n


def vpot(strip: int, delta: int, cw_sets_bit: bool = True) -> list[list[int]]:
    return [[0xB0, 0x40 + strip, _delta_byte(delta, cw_sets_bit)]]


def jog(delta: int, cw_sets_bit: bool = False) -> list[list[int]]:
    return [[0xB0, 0x0D, _delta_byte(delta, cw_sets_bit)]]


# ---------------------------------------------------------------------------
# Decoding (host -> device)
# ---------------------------------------------------------------------------

SYSEX_HDR = [0xF0, 0x00, 0x00, 0x66, 0x05, 0x00]


class HostState:
    """Mirror of everything Pro Tools tells the 'HUI'. Feed it raw messages."""

    def __init__(self) -> None:
        self.leds: dict[tuple[int, int], bool] = {}
        self.faders: dict[int, int] = {}
        self.vpot_rings: dict[int, int] = {}
        self.scribble: dict[int, str] = {}  # 4-char strip names (0-7), 8 = select-assign
        self.lcd = [" " * 10] * 8  # main 40x2 display as 8 x 10-char segments
        self.timecode: list[int] = [0] * 8
        self._zone: int | None = None
        self._fader_hi: dict[int, int] = {}

    def lcd_text(self) -> tuple[str, str]:
        return "".join(self.lcd[:4]), "".join(self.lcd[4:])

    def feed(self, msg: list[int]) -> tuple | None:
        """Update state. Returns an event tuple or None:
        ('ping',), ('led', zone, port, on), ('fader', strip, value14),
        ('vpot_ring', strip, value), ('display', kind), ('unknown', msg)
        """
        if not msg:
            return None
        st = msg[0]
        if st == 0x90 and len(msg) >= 3 and msg[1] == 0 and msg[2] == 0:
            return ("ping",)
        if st == 0xB0 and len(msg) >= 3:
            cc, val = msg[1], msg[2]
            if cc == 0x0C:
                self._zone = val
                return None
            if cc == 0x2C:
                if self._zone is None:
                    return None
                port, on = val & 0x0F, bool(val & 0x40)
                self.leds[(self._zone, port)] = on
                return ("led", self._zone, port, on)
            if cc <= 0x07:
                self._fader_hi[cc] = val
                return None
            if 0x20 <= cc <= 0x27:
                strip = cc - 0x20
                v = (self._fader_hi.get(strip, 0) << 7) | val
                self.faders[strip] = v
                return ("fader", strip, v)
            if 0x10 <= cc <= 0x1B:
                self.vpot_rings[cc - 0x10] = val
                return ("vpot_ring", cc - 0x10, val)
        if st == 0xF0 and msg[: len(SYSEX_HDR)] == SYSEX_HDR and len(msg) > 7:
            return self._sysex(msg[len(SYSEX_HDR) :])
        return ("unknown", msg)

    def _sysex(self, body: list[int]) -> tuple:
        kind = body[0]
        data = [b for b in body[1:] if b != 0xF7]
        if kind == 0x10 and data:  # 4-char scribble strip
            self.scribble[data[0]] = "".join(_hui_char(c) for c in data[1:5])
            return ("display", "scribble", data[0])
        if kind == 0x12 and data:  # main LCD, 10-char chunks
            i = 0
            while i + 11 <= len(data):
                seg = data[i]
                if 0 <= seg < 8:
                    self.lcd[seg] = "".join(_hui_char(c) for c in data[i + 1 : i + 11])
                i += 11
            return ("display", "lcd", None)
        if kind == 0x11:  # timecode digits, least significant first
            for i, d in enumerate(data[:8]):
                self.timecode[i] = d
            return ("display", "timecode", None)
        return ("display", f"sysex{kind:02X}", None)


def _hui_char(c: int) -> str:
    return chr(c) if 0x20 <= c < 0x7F else " "
