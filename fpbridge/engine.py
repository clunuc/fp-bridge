"""Translation engine: FaderPort (native) <-> virtual HUI <-> keyboard shortcuts.

The engine is pure logic: it never touches MIDI ports itself. It gets raw
messages through on_faderport()/on_hui() and emits through the callables it
was given, so it can be tested without hardware.

Action mini-language (strings in the YAML config, a list = run in order):

  hui <switch>              momentary: press on press, release on release
  hui_tap <switch>          press + release when the button is pressed
  hui_combo <hold> <tap>    hold <hold>, tap <tap>, release <hold>
  hui_tap_spaced <sw> <ms>  tap, but never two taps closer than <ms> (avoids PT double-clicks)
  key <combo>               keyboard shortcut, e.g. "key cmd+e", "key c"
  wait <ms>                 pause (inside a list), e.g. to let Pro Tools catch up
  mode <name>               switch the encoder to mode <name>
  encoder +1 | encoder -1   one encoder step in the current mode
  vpot [strip]              (encoder only) HUI V-pot of the strip
  jog                       (encoder only) HUI jog wheel
  none                      do nothing

LED sources:  hui <switch> | mode <name> | toggle | held | shift | on | off
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from . import faderport as fp
from . import hui

log = logging.getLogger(__name__)

Send = Callable[[list[int]], None]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@dataclass
class Action:
    kind: str
    args: list[str] = field(default_factory=list)
    target: tuple[int, int] | None = None
    target2: tuple[int, int] | None = None

    def __str__(self) -> str:
        return " ".join([self.kind, *self.args])


REL = -1  # zone placeholder: "the strip the fader is on right now"


def _res(name: str, strip: int) -> tuple[int, int]:
    """'strip.x' stays relative to the live fader strip; 'strip3.x' and others are fixed."""
    if name.startswith("strip."):
        return REL, hui.resolve(name, 0)[1]
    return hui.resolve(name, strip)


def parse_action(spec, strip: int) -> list[Action]:
    if spec is None:
        return []
    if isinstance(spec, list):
        out: list[Action] = []
        for s in spec:
            out += parse_action(s, strip)
        return out
    words = str(spec).split()
    if not words:
        return []
    kind, args = words[0].lower(), words[1:]
    a = Action(kind, args)
    if kind in ("hui", "hui_tap"):
        _need(args, 1, spec)
        a.target = _res(args[0], strip)
    elif kind == "hui_combo":
        _need(args, 2, spec)
        a.target, a.target2 = _res(args[0], strip), _res(args[1], strip)
    elif kind == "key":
        _need(args, 1, spec)
        from .keys import parse_combo

        parse_combo(args[0])  # validate now, fail at load time
    elif kind == "mode":
        _need(args, 1, spec)
    elif kind == "encoder":
        _need(args, 1, spec)
        int(args[0])
    elif kind == "vpot":
        a.args = [args[0] if args else "rel"]
    elif kind == "hui_tap_spaced":
        _need(args, 2, spec)
        a.target = _res(args[0], strip)
        int(args[1])
    elif kind == "wait":
        _need(args, 1, spec)
        if not 0 <= int(args[0]) <= 1000:
            raise ValueError(f"wait must be 0-1000 ms in {spec!r}")
    elif kind == "auto_mode":
        _need(args, 1, spec)
        if args[0] not in ("read", "touch", "latch", "write", "off", "trim"):
            raise ValueError(f"unknown automation mode in {spec!r}")
    elif kind == "send_step":
        _need(args, 1, spec)
        int(args[0])
    elif kind in ("jog", "none", "auto_trim_toggle", "send_toggle", "send_off"):
        pass
    else:
        raise ValueError(f"unknown action {spec!r}")
    return [] if kind == "none" else [a]


def _need(args, n, spec):
    if len(args) < n:
        raise ValueError(f"action {spec!r} needs {n} argument(s)")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class ButtonCfg:
    press: list[Action]
    shift: list[Action] | None
    led: str | None
    color_on: tuple | None = None  # RGB buttons: colour when the LED is "on"
    color_off: tuple | None = None  # ... and when "off" (None = LED dark)
    flash: dict | None = None  # {color, times, ms}: blink on press
    hold: list[Action] | None = None  # long press: runs instead of `press` when held


def _rgb(v):
    return tuple(int(c) for c in v) if v is not None else None


# Automation mode texts Pro Tools flashes on the HUI scribble strip after a change
# ("Read", "Tch", "Wrt", ...; a leading T means Trim is on: "TRd" = Trim + Read).
_MODE_TEXT = {"read": "read", "rd": "read", "tch": "touch", "touch": "touch", "tc": "touch",
              "wrt": "write", "write": "write", "wr": "write", "ltch": "latch", "latch": "latch",
              "lch": "latch", "ltc": "latch", "lt": "latch", "off": "off"}


def parse_mode_text(text: str) -> tuple[str, bool] | None:
    t = text.strip().lower()
    if t in _MODE_TEXT:
        return _MODE_TEXT[t], False
    if len(t) >= 2 and t[0] == "t" and t[1:] in _MODE_TEXT:
        return _MODE_TEXT[t[1:]], True
    return None


class Engine:
    AUTO_TOUCH_RELEASE = 0.35  # s; for fader moves that arrive without a touch message

    def __init__(self, config: dict, fp_send: Send, hui_send: Send, key_send: Callable[[str], None]):
        self.cfg = config
        self.fp_send = fp_send
        self.hui_send = hui_send
        self.key_send = key_send
        self.sleep = time.sleep  # replaced in tests
        self.now = time.monotonic  # replaced in tests
        self._last_tap: dict[tuple[int, int], float] = {}
        self._pending_taps: dict[tuple[int, int], float] = {}
        self.trim_tracks: dict[str, str] = {}  # track names with Trim on
        # sends on the fader (HUI flip): which send, and whether the fader is on it
        scfg = config.get("sends") or {}
        self.send_on = False
        self.send_idx = 0
        self.send_led = scfg.get("led_button", "channel")
        self.send_mode = scfg.get("encoder_mode", "send")
        self.send_colors = [tuple(c) for c in scfg.get("colors", [
            [127, 0, 0], [127, 50, 0], [127, 110, 0], [0, 127, 0], [0, 40, 127]])]
        self._mode_before_send: str | None = None
        # follow Pro Tools selection (PTSL): index of the track on strip 0, None = unknown
        self.follow = bool(config.get("follow_selection", True))
        self.bank_pos: int | None = None
        self.track_count = 0
        self._verify_at: float | None = None
        self._verified_once = False
        self._search: list[int] = []
        self._seen_select_led = False
        self._auto_log: dict = {}
        self.auto_modes: dict[str, str] = {}  # track name -> automation mode
        # our last automation press: (time, "mode"|"trim"); Pro Tools then shows the
        # mode the track had BEFORE the change, which must not be taken as the new one
        self._auto_press: tuple[float, str] | None = None
        self.probe_auto = bool((config.get("automation") or {}).get("probe_on_track_change", False))
        self._probe_at: float | None = None
        # long press: button -> (due time, short actions); fires `hold` when due
        self.long_press = int(config.get("long_press_ms", 500)) / 1000
        self._holds: dict[str, tuple[float, list[Action]]] = {}
        # a key on the computer keyboard that, held, selects the second layer (`shift:`)
        self.kbd_modifier = (config.get("keyboard_modifier") or "").lower() or None
        self.kbd_mod_down: Callable[[], bool] = self._read_kbd_modifier  # replaced in tests
        # mute the armed track while Pro Tools records (no doubled, delayed monitoring)
        rcfg = config.get("record_mute") or {}
        self.record_mute = bool(rcfg.get("enabled", False))
        self.recording = False
        self._rec_muted: tuple[int, str] | None = None  # (strip, track) we muted
        self._rec_stop_at: float | None = None  # Play went out: stop if it stays out
        self._track_name = ""  # last real track name on the fader strip
        self._flashes: dict[str, tuple[float, float, int]] = {}  # button -> (start, ms, phases)
        self._rgb_sent: dict[str, tuple] = {}

        hcfg = config.get("hui", {})
        self.strip = self.base_strip = int(hcfg.get("strip", 0))
        self.vpot_cw_bit = bool(hcfg.get("vpot_cw_sets_bit", True))
        self.jog_cw_bit = bool(hcfg.get("jog_cw_sets_bit", False))
        self.enc_cw_bit = bool(config.get("faderport", {}).get("encoder_cw_sets_bit", False))

        self.buttons: dict[str, ButtonCfg] = {}
        for name, b in (config.get("buttons") or {}).items():
            if name not in fp.BUTTONS:
                raise ValueError(f"unknown FaderPort button {name!r} (valid: {', '.join(fp.BUTTONS)})")
            b = b or {}
            self.buttons[name] = ButtonCfg(
                press=parse_action(b.get("press"), self.strip),
                shift=parse_action(b["shift"], self.strip) if "shift" in b else None,
                led=b.get("led"),
                color_on=_rgb(b.get("color_on")),
                color_off=_rgb(b.get("color_off")),
                flash=({"color": _rgb(b["flash"].get("color", [127, 127, 127])),
                        "times": int(b["flash"].get("times", 1)),
                        "ms": int(b["flash"].get("ms", 120))} if b.get("flash") else None),
                hold=parse_action(b["hold"], self.strip) if b.get("hold") else None,
            )

        ecfg = config.get("encoder") or {}
        self.modes: dict[str, dict[str, list[Action]]] = {}
        self.mode_colors: dict[str, tuple[int, int, int]] = {}
        for mname, m in (ecfg.get("modes") or {}).items():
            m = m or {}
            self.modes[mname] = {
                k: parse_action(m.get(k), self.strip) for k in ("turn", "cw", "ccw", "push", "push_release", "shift_push", "on_enter")
            }
        self.mode = ecfg.get("default_mode") or next(iter(self.modes), None)
        for b in self.buttons.values():
            for a in b.press + (b.shift or []):
                if a.kind == "mode" and a.args[0] not in self.modes:
                    raise ValueError(f"mode {a.args[0]!r} is not defined under encoder.modes")

        self.colors = {k: tuple(v) for k, v in (config.get("colors") or {}).items()}

        # runtime state
        self.host = hui.HostState()
        self.shift = False
        self.toggles: dict[str, bool] = {}
        self.held: set[str] = set()
        self.active: dict[str, list[Action]] = {}  # button -> actions fired on press
        self.fader_touched = False
        self.auto_touch_until: float | None = None
        self.last_host_fader: int | None = None
        self.hui_online = False
        self.last_ping = 0.0
        self._leds: dict[str, int] = {}
        self._warned_hui_mode = False
        self._unknown_seen: set[tuple] = set()

    # ------------------------------------------------------------------ init
    def start(self) -> None:
        self._leds.clear()
        self._rgb_sent.clear()
        self.refresh_leds()
        self._send_fp(fp.fader(self.last_host_fader or 0))

    def stop(self) -> None:
        for name in fp.BUTTONS:
            if name not in fp.NO_LED:
                self._send_fp(fp.led(name, fp.LED_OFF))

    # ------------------------------------------------------- FaderPort input
    def on_faderport(self, msg: list[int]) -> None:
        if len(msg) >= 3 and msg[0] == 0xB0 and msg[1] in (0x0F, 0x2F) and not self._warned_hui_mode:
            self._warned_hui_mode = True
            log.warning("El FaderPort está enviando mensajes HUI: está en modo Pro Tools. "
                        "Apágalo y enciéndelo manteniendo NEXT, y pulsa SOLO (modo Studio One).")
        ev = fp.parse(msg, self.enc_cw_bit)
        if ev is None:
            key = tuple(msg[:2])
            if key not in self._unknown_seen:
                self._unknown_seen.add(key)
                log.info("mensaje del FaderPort no reconocido: %s", " ".join(f"{b:02X}" for b in msg))
            return
        log.debug("FP %s", ev)
        if ev[0] == "button":
            self._button(ev[1], ev[2])
        elif ev[0] == "fader":
            self._fader_move(ev[1])
        elif ev[0] == "encoder":
            self._encoder(ev[1])

    def _button(self, name: str, pressed: bool) -> None:
        log.debug("FP %s %s", name, "down" if pressed else "up")
        if name == "fader_touch":
            self._fader_touch(pressed)
            return
        if pressed:
            self.held.add(name)
        else:
            self.held.discard(name)

        if name == "shift" and name not in self.buttons:
            self.shift = pressed
            self.refresh_leds()
            return

        cfg = self.buttons.get(name)
        if name == "encoder_push" and cfg is None:
            if pressed:
                m = self.modes.get(self.mode, {})
                acts = m.get("shift_push") if self.shift and m.get("shift_push") else m.get("push", [])
                self.active[name] = acts
                self._run(acts, True)
            else:
                self._run(self.active.pop(name, []), False)
                self._run(self.modes.get(self.mode, {}).get("push_release", []), True)
            return
        if cfg is None:
            if pressed:
                log.info("FaderPort button %r has no mapping", name)
            return

        if pressed:
            second = self.shift or (cfg.shift is not None and self.kbd_modifier and self.kbd_mod_down())
            acts = cfg.shift if (second and cfg.shift is not None) else cfg.press
            if cfg.flash:
                self._flashes[name] = (self.now(), cfg.flash["ms"] / 1000, cfg.flash["times"] * 2)
            if cfg.led == "toggle":
                self.toggles[name] = not self.toggles.get(name, False)
            if cfg.hold and not second:
                # short or long press? decided on release / when the time is up
                self._holds[name] = (self.now() + self.long_press, acts)
            else:
                self.active[name] = acts
                self._run(acts, True)
        elif name in self._holds:  # released before the long-press time: short press
            _, acts = self._holds.pop(name)
            self._run(acts, True)
            self._run(acts, False)
        else:
            self._run(self.active.pop(name, []), False)
        self.refresh_leds()

    def _read_kbd_modifier(self) -> bool:
        """Is the configured modifier key held on the computer keyboard right now?"""
        try:
            import Quartz
            from .keys import MODIFIERS

            flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
            return bool(flags & MODIFIERS[self.kbd_modifier])
        except Exception:
            return False

    def _run(self, actions: list[Action], pressed: bool) -> None:
        for a in actions:
            k = a.kind
            if k == "hui":
                self._hui_switch(a.target, pressed)
            elif not pressed:
                continue
            elif k == "hui_tap":
                self._tap(a.target)
                self._note_bank_move(a.target)
                if a.target[0] == 0x18:  # automation mode chosen from the FaderPort
                    mode = hui.describe(*a.target).split(".")[1]
                    if mode != "trim":
                        self.auto_modes[self._track_key()] = mode
                        self.refresh_leds()
            elif k == "hui_tap_spaced":
                self._tap_spaced(a.target, int(a.args[1]) / 1000)
            elif k == "hui_combo":
                if a.target[0] == 0x18 and a.target2[1] == hui.STRIP_PORTS["auto"]:
                    mode = hui.describe(*a.target).split(".")[1]
                    if mode != "trim":
                        self.auto_modes[self._track_key()] = mode
                self._hui_switch(a.target, True)
                self._hui_switch(a.target2, True)
                self._hui_switch(a.target2, False)
                self._hui_switch(a.target, False)
            elif k == "key":
                self.key_send(a.args[0])
            elif k == "wait":
                self.sleep(int(a.args[0]) / 1000)
            elif k == "auto_trim_toggle":
                self._auto_trim_toggle()
            elif k == "auto_mode":
                self._auto_mode(a.args[0])
            elif k == "send_toggle":
                self._send_toggle()
            elif k == "send_off":
                self._set_send(False)
            elif k == "send_step":
                self._send_step(int(a.args[0]))
            elif k == "mode":
                self.set_mode(a.args[0])
            elif k == "encoder":
                self._encoder(int(a.args[0]))

    def _tap_spaced(self, target, gap: float) -> None:
        """Tap now if the last tap of this switch was >= gap ago; otherwise schedule a
        single tap for when the gap is over (repeated requests collapse into one)."""
        now = self.now()
        last = self._last_tap.get(target, -1e9)
        if now - last >= gap and target not in self._pending_taps:
            self._last_tap[target] = now
            self._hui_switch(target, True)
            self._hui_switch(target, False)
        else:
            self._pending_taps[target] = last + gap

    AUTO_MODES = ("read", "touch", "latch", "write", "off")
    SENDS = "abcde"

    # ------------------------------------------------- sends on the fader
    FLIP = hui.SWITCHES["assign2.shift"]
    SEND_PORTS = {hui.SWITCHES[f"assign.send_{c}"]: i for i, c in enumerate("abcde")}

    def _is_send_on(self) -> bool:
        """Pro Tools' FLIP LED is the truth when it has told us; our own flag otherwise."""
        return self.host.leds.get(self.FLIP, self.send_on) if self.FLIP in self.host.leds else self.send_on

    def _send_toggle(self) -> None:
        self._set_send(not self._is_send_on())

    def _set_send(self, on: bool) -> None:
        """Fader on a send (HUI: assign the V-pots to the send, then FLIP swaps V-pot and
        fader) or back on volume (un-FLIP, V-pots back to pan). FLIP is a toggle in Pro
        Tools, so it is only pressed when the current state needs it."""
        flipped = self._is_send_on()
        if on:
            self._tap(hui.SWITCHES[f"assign.send_{self.SENDS[self.send_idx]}"])
            if not flipped:
                self.sleep(0.05)
                self._tap(self.FLIP)
            log.info("fader -> envío %s", self.SENDS[self.send_idx].upper())
        elif flipped:
            # (only when flipped: tapping PAN while already on pan swaps pan L/R)
            self._tap(self.FLIP)
            self.sleep(0.05)
            self._tap(hui.SWITCHES["assign.pan"])
            log.info("fader -> volumen")
        self._apply_send_state(on)

    def _apply_send_state(self, on: bool) -> None:
        """Mode, LEDs and colour for 'fader on send' (on) or 'fader on volume'."""
        self.send_on = on
        if on and self.send_mode in self.modes and self.mode != self.send_mode:
            self._mode_before_send = self.mode
            self.set_mode(self.send_mode)
        elif not on and self.mode == self.send_mode:
            self.set_mode(self._mode_before_send or "pan")
        self._send_color()
        self.refresh_leds()

    def _send_step(self, delta: int) -> None:
        """Choose send A..E (wraps). With the fader on a send, it follows at once."""
        self.send_idx = (self.send_idx + delta) % len(self.SENDS)
        if self._is_send_on():
            self._tap(hui.SWITCHES[f"assign.send_{self.SENDS[self.send_idx]}"])
        log.info("envío %s", self.SENDS[self.send_idx].upper())
        self._send_color()

    def _send_color(self) -> None:
        self.refresh_leds()  # the send colour is drawn by refresh_leds()

    # ----------------------------------------------- follow PT selection
    _BANK_MOVES = {
        hui.SWITCHES["bank.chan_left"]: -1,
        hui.SWITCHES["bank.chan_right"]: 1,
        hui.SWITCHES["bank.bank_left"]: -8,
        hui.SWITCHES["bank.bank_right"]: 8,
    }

    def _note_bank_move(self, target) -> None:
        step = self._BANK_MOVES.get(target)
        if step:
            self.set_strip(self.base_strip)
        if step and self.bank_pos is not None:
            hi = max(self.track_count - 1, 0)
            self.bank_pos = max(0, min(hi, self.bank_pos + step))

    def on_ptsl(self, ev: dict) -> None:
        """Pro Tools says which track is selected; bring it to the fader."""
        self.track_count = ev.get("count", 0)
        idx = ev.get("index")
        if not self.follow or idx is None or idx == self.bank_pos:
            return
        log.info("Pro Tools seleccionó '%s' -> fader", ev.get("name"))
        self.set_strip(self.base_strip)
        if self.bank_pos is None:  # unknown position: go to the start of the session first
            for _ in range(self.track_count // 8 + 2):
                self._tap(hui.SWITCHES["bank.bank_left"])
            self.bank_pos = 0
        delta = idx - self.bank_pos
        banks, chans = divmod(abs(delta), 8)
        right = delta > 0
        for _ in range(banks):
            self._tap(hui.SWITCHES["bank.bank_right" if right else "bank.bank_left"])
        for _ in range(chans):
            self._tap(hui.SWITCHES["bank.chan_right" if right else "bank.chan_left"])
        self.bank_pos = idx
        self._verify_at = self.now() + self.VERIFY_DELAY
        self._verified_once = False
        # if the track isn't in the bank we land on, look in the neighbouring banks:
        # -8, +8, -16, +16 tracks from here (moves in banks of 8, relative to the last one)
        self._search = [-1, 2, -3, 4]

    VERIFY_DELAY = 0.3  # s for Pro Tools to update the SELECT LEDs after a bank move

    def _verify_bank(self) -> None:
        """After a move, the selected track should be on strip 0 (its SELECT LED lit)."""
        if not self._seen_select_led:
            return  # Pro Tools never sent SELECT LEDs: nothing to check against
        lit = [k for k in range(8) if self.host.leds.get((k, hui.STRIP_PORTS["select"]))]
        if 0 in lit:
            if len(self._search) < 4:
                log.info("pista encontrada en un banco vecino; posición corregida")
            self._search = []
            return
        if lit and not self._verified_once:
            k = min(lit)
            log.info("ajuste de banco: la pista seleccionada está %d canales a la derecha", k)
            for _ in range(k):
                self._tap(hui.SWITCHES["bank.chan_right"])
            self._verified_once = True
            self._search = []
            self._verify_at = self.now() + self.VERIFY_DELAY
        elif lit:
            # Pro Tools won't bank further (end of the session): move the fader instead
            self.set_strip(min(lit))
        elif self._search:
            step = self._search.pop(0)
            log.debug("pista no visible en el banco, pruebo %+d bancos", step)
            for _ in range(abs(step)):
                self._tap(hui.SWITCHES["bank.bank_right" if step > 0 else "bank.bank_left"])
            self._verify_at = self.now() + self.VERIFY_DELAY
        else:
            log.warning("no encuentro la pista seleccionada en el banco HUI")
            self.bank_pos = None

    def set_strip(self, k: int) -> None:
        """Point the fader (and solo/mute/arm/pan...) at another strip of the HUI bank."""
        if k == self.strip:
            return
        log.info("el fader pasa al canal %d del banco", k + 1)
        self.strip = k
        self.last_host_fader = self.host.faders.get(k)
        if not self.fader_touched:
            self._send_fp(fp.fader(self.last_host_fader or 0))
        self.refresh_leds()

    def _tap(self, target) -> None:
        self._hui_switch(target, True)
        self._hui_switch(target, False)

    def _track_key(self) -> str:
        return self._track_name.strip() or "?"

    def _auto_trim_toggle(self) -> None:
        """Toggle Trim on the current track. In Pro Tools Trim is an on/off switch on top of
        the automation mode, so the same HUI combo turns it on and off; the LED is tracked
        per track name."""
        track = self._track_key()
        if track in self.trim_tracks:
            del self.trim_tracks[track]
            log.info("pista %s: trim OFF", track)
        else:
            self.trim_tracks[track] = "on"
            log.info("pista %s: trim ON", track)
        self._auto_mode("trim")

    def _check_recording(self) -> None:
        """Recording starts when Play and Rec are lit together and ends when Play goes out
        (Rec alone blinking = only armed). While recording, the armed track on the fader
        is muted so its delayed input doesn't double the direct monitoring; it is unmuted
        again when the transport stops, unless the user muted it themselves."""
        leds = self.host.leds
        play = leds.get(hui.SWITCHES["transport.play"], False)
        rec = leds.get(hui.SWITCHES["transport.record"], False)
        if play:
            self._rec_stop_at = None  # (Pro Tools blinks Play off for a moment as recording starts)
        if not self.recording and play and rec:
            self.recording = True
            log.info("grabando")
            if self.record_mute:
                armed = leds.get((self.strip, hui.STRIP_PORTS["rec"]), False)
                muted = leds.get((self.strip, hui.STRIP_PORTS["mute"]), False)
                if armed and not muted:
                    self._tap((self.strip, hui.STRIP_PORTS["mute"]))
                    self._rec_muted = (self.strip, self._track_key())
                    log.info("pista %s muteada mientras graba", self._track_key())
        elif self.recording and not play and self._rec_stop_at is None:
            self._rec_stop_at = self.now() + 0.4

    def _recording_stopped(self) -> None:
        leds = self.host.leds
        self.recording = False
        log.info("grabación parada")
        if self._rec_muted:
            strip, track = self._rec_muted
            self._rec_muted = None
            if strip == self.strip and track == self._track_key():
                if leds.get((strip, hui.STRIP_PORTS["mute"]), False):
                    self._tap((strip, hui.STRIP_PORTS["mute"]))
                    log.info("pista %s desmuteada", track)
            else:
                log.warning("la pista %s ya no está en el fader: no la puedo desmutear", track)

    def _probe_auto_mode(self) -> None:
        """Ask Pro Tools the automation mode of the track on the fader without changing it:
        AUTO opens 'choose mode' and Pro Tools shows the current mode; a second AUTO closes
        it (if it was already open, the first closes it and the second shows the mode)."""
        if not self.hui_online:
            return
        self._auto_press = (self.now(), "probe")
        auto = (self.strip, hui.STRIP_PORTS["auto"])
        self._tap(auto)
        self._tap(auto)

    def _on_mode_text(self, m: str, trim: bool) -> None:
        """Pro Tools showed an automation mode on the strip. Right after one of our own
        automation presses it is the mode from BEFORE the change: then the mode we set
        stands, and for a Trim toggle the new Trim state is the opposite of the old one."""
        track = self._track_key()
        press = self._auto_press
        recent = press is not None and self.now() - press[0] < 1.0
        if recent and press[1] == "probe":
            recent = False  # an answer to our question: the current mode, take it as is
        if recent and press[1] == "trim":
            self.auto_modes[track] = m  # (Trim doesn't change the mode)
            trim = not trim
            self._auto_press = (press[0], "trim-done")  # only the first text counts
        elif recent and press[1] == "trim-done":
            return
        elif not recent:
            self.auto_modes[track] = m
        # after a mode press the Trim part is still right (a mode change keeps Trim)
        if trim:
            self.trim_tracks[track] = "on"
        else:
            self.trim_tracks.pop(track, None)
        log.info("pista %s: automatización %s%s", track, "trim + " if trim else "",
                 self.auto_modes.get(track, m))

    def _auto_mode(self, mode: str) -> None:
        """Set the track's automation mode the way that works in Pro Tools: hold the mode
        switch and tap the strip's AUTO switch (a HUI switch, not a FaderPort button).
        For 'trim' this toggles Trim on and off."""
        if mode != "trim":
            self.auto_modes[self._track_key()] = mode
            self.refresh_leds()
        self._auto_press = (self.now(), "trim" if mode == "trim" else "mode")
        target = hui.SWITCHES[f"auto_mode.{mode}"]
        auto = (self.strip, hui.STRIP_PORTS["auto"])
        # If Pro Tools isn't already waiting for a mode, the first combo only makes it
        # wait; the second one applies the mode. Choosing the same mode twice is harmless,
        # but Trim is a toggle, so it gets a single combo.
        for _ in range(1 if mode == "trim" else 2):
            self._hui_switch(target, True)
            self._tap(auto)
            self._hui_switch(target, False)

    def set_mode(self, mode: str) -> None:
        if mode not in self.modes:
            log.warning("unknown encoder mode %r", mode)
            return
        self.mode = mode
        log.info("encoder mode -> %s", mode)
        self._run(self.modes[mode].get("on_enter", []), True)
        self.refresh_leds()

    def _encoder(self, delta: int) -> None:
        m = self.modes.get(self.mode)
        if not m or delta == 0:
            return
        turn = m.get("turn") or []
        if turn:
            for a in turn:
                if a.kind == "vpot":
                    st = self.strip if a.args[0] == "rel" else int(a.args[0])
                    self._send_hui(hui.vpot(st, delta, self.vpot_cw_bit))
                elif a.kind == "jog":
                    self._send_hui(hui.jog(delta, self.jog_cw_bit))
            return
        acts = m.get("cw" if delta > 0 else "ccw") or []
        for _ in range(min(abs(delta), 16)):
            self._run(acts, True)
            self._run(acts, False)

    # ------------------------------------------------------------- fader
    def _fader_touch(self, touched: bool) -> None:
        self.fader_touched = touched
        self.auto_touch_until = None
        self._hui_switch((self.strip, hui.STRIP_PORTS["fader"]), touched)
        if not touched and self.last_host_fader is not None:
            self._send_fp(fp.fader(self.last_host_fader))

    def _fader_move(self, v14: int) -> None:
        if not self.fader_touched:
            # fader moved without touch (e.g. touch sensing off): fake a touch
            if self.auto_touch_until is None:
                self._hui_switch((self.strip, hui.STRIP_PORTS["fader"]), True)
            self.auto_touch_until = time.monotonic() + self.AUTO_TOUCH_RELEASE
        self._send_hui(hui.fader(self.strip, v14))

    def tick(self, now: float | None = None) -> None:
        now = self.now() if now is None else now
        if self._verify_at is not None and now >= self._verify_at:
            self._verify_at = None
            self._verify_bank()
        for target, due in list(self._pending_taps.items()):
            if now >= due:
                del self._pending_taps[target]
                self._last_tap[target] = now
                self._hui_switch(target, True)
                self._hui_switch(target, False)
        for name, (due, _acts) in list(self._holds.items()):
            if now >= due:  # still held: long press
                del self._holds[name]
                cfg = self.buttons.get(name)
                if cfg and cfg.hold:
                    log.info("pulsación larga: %s", name)
                    self._run(cfg.hold, True)
                    self._run(cfg.hold, False)
        if self._rec_stop_at is not None and now >= self._rec_stop_at:
            self._rec_stop_at = None
            if not self.host.leds.get(hui.SWITCHES["transport.play"], False):
                self._recording_stopped()
        if self._probe_at is not None and now >= self._probe_at:
            self._probe_at = None
            self._probe_auto_mode()
        if self._flashes:
            for name, (start, step, phases) in list(self._flashes.items()):
                if now - start >= step * phases:
                    del self._flashes[name]
            self.refresh_leds(now)
        if self.auto_touch_until is not None and now >= self.auto_touch_until:
            self.auto_touch_until = None
            self._hui_switch((self.strip, hui.STRIP_PORTS["fader"]), False)
        if self.hui_online and now - self.last_ping > 3.0:
            self.hui_online = False
            log.warning("Pro Tools dejó de enviar ping (¿cerrado o HUI desconfigurado?)")

    # ---------------------------------------------------------- HUI input
    def on_hui(self, msg: list[int]) -> None:
        ev = self.host.feed(msg)
        if ev is None:
            return
        kind = ev[0]
        if kind == "ping":
            self._send_hui([hui.PING_REPLY])
            self.last_ping = time.monotonic()
            if not self.hui_online:
                self.hui_online = True
                log.info("Pro Tools conectado (ping HUI recibido)")
        elif kind == "led":
            _, zone, port, on = ev
            log.debug("PT LED %s = %s", hui.describe(zone, port), on)
            if zone <= 7 and port == hui.STRIP_PORTS["select"]:
                self._seen_select_led = True
            if zone == 0x18 or (zone == self.strip and port == hui.STRIP_PORTS["auto"]):
                if self._auto_log.get((zone, port)) != on:  # automation LEDs: log changes only
                    self._auto_log[(zone, port)] = on
                    log.info("Pro Tools LED %s = %s", hui.describe(zone, port), "on" if on else "off")
            if zone == 0x0E and port in (hui.SWITCHES["transport.play"][1], hui.SWITCHES["transport.record"][1]):
                self._check_recording()
            if (zone, port) == self.FLIP and on != self.send_on:  # (the PAN LED blinks: not logged)
                log.info("Pro Tools: flip %s", "on" if on else "off")
            if (zone, port) == self.FLIP:
                self._apply_send_state(on)  # Pro Tools says whether the fader is flipped
            elif on and (zone, port) in self.SEND_PORTS:
                self.send_idx = self.SEND_PORTS[(zone, port)]
                self._send_color()
            self.refresh_leds()
        elif kind == "fader":
            _, strip, v = ev
            if strip == self.strip:
                self.last_host_fader = v
                if not self.fader_touched and self.auto_touch_until is None:
                    self._send_fp(fp.fader(v))
        elif kind == "display":
            if ev[1] == "lcd":
                l1, l2 = self.host.lcd_text()
                log.debug("PT LCD |%s|%s|", l1, l2)
            elif ev[1] == "scribble" and ev[2] == self.strip:
                text = self.host.scribble.get(self.strip, "")
                mode = parse_mode_text(text)
                if mode:  # Pro Tools flashing the automation mode of this track
                    self._on_mode_text(*mode)
                elif (text.strip() and any(c.isalpha() for c in text)
                      and text.strip().lower() != "auto" and not self._is_send_on()):
                    # (not a track name: "Auto", send values like " -  " while flipped)
                    if text != self._track_name and self.probe_auto:
                        self._probe_at = self.now() + 0.35  # (debounced: fast Prev/Next)
                    self._track_name = text
                    log.info("pista: %s", text)
                self.refresh_leds()

    # ---------------------------------------------------------------- LEDs
    def led_state(self, name: str) -> int:
        cfg = self.buttons.get(name)
        src = cfg.led if cfg else None
        if name == "shift" and (cfg is None or src is None):
            src = "shift"
        if not src or src == "off":
            return fp.LED_OFF
        words = src.split()
        kind = words[0]
        on = False
        if kind == "hui":
            on = self.host.leds.get(hui.resolve(words[1], self.strip), False)
        elif kind == "mode":
            on = self.mode in words[1].split(",")
        elif kind == "toggle":
            on = self.toggles.get(name, False)
        elif kind == "held":
            on = name in self.held
        elif kind == "shift":
            on = self.shift
        elif kind == "trim":
            on = self._track_key() in self.trim_tracks
        elif kind == "send":
            on = self._is_send_on()
        elif kind == "auto":
            on = self.auto_modes.get(self._track_key()) in words[1].split(",")
        elif kind == "on":
            on = True
        elif kind == "blink":
            return fp.LED_BLINK
        return fp.LED_ON if on else fp.LED_OFF

    def _flash_phase(self, name: str, now: float) -> bool:
        f = self._flashes.get(name)
        if not f:
            return False
        start, step, phases = f
        i = int((now - start) / step) if step > 0 else phases
        return i < phases and i % 2 == 0

    def _rgb_for(self, name: str, state: int, flashing: bool):
        cfg = self.buttons.get(name)
        if flashing and cfg and cfg.flash:
            return cfg.flash["color"]
        if name == self.send_led and self._is_send_on():
            return self.send_colors[self.send_idx % len(self.send_colors)]
        base = self.colors.get(name, (127, 127, 127))
        if state != fp.LED_OFF:
            return (cfg.color_on if cfg and cfg.color_on else None) or base
        return cfg.color_off if cfg else None

    def refresh_leds(self, now: float | None = None) -> None:
        now = self.now() if now is None else now
        for name in fp.BUTTONS:
            if name in fp.NO_LED:
                continue
            state = self.led_state(name)
            flashing = self._flash_phase(name, now)
            if name in fp.RGB_BUTTONS:
                color = self._rgb_for(name, state, flashing)
                note = (fp.LED_BLINK if state == fp.LED_BLINK else fp.LED_ON) if color else fp.LED_OFF
                if color and self._rgb_sent.get(name) != color:
                    self._rgb_sent[name] = color
                    self._send_fp(fp.led_color(name, color))
            else:
                note = (fp.LED_OFF if state == fp.LED_ON else fp.LED_ON) if flashing else state
            if self._leds.get(name) != note:
                self._leds[name] = note
                self._send_fp(fp.led(name, note))

    # ------------------------------------------------------------- output
    def _hui_switch(self, target, pressed: bool) -> None:
        zone, port = target
        if zone == REL:
            zone = self.strip
        log.debug("-> PT %s %s", hui.describe(zone, port), "down" if pressed else "up")
        self._send_hui(hui.switch(zone, port, pressed))

    def _send_hui(self, msgs: list[list[int]]) -> None:
        for m in msgs:
            self.hui_send(m)

    def _send_fp(self, msgs: list[list[int]]) -> None:
        for m in msgs:
            self.fp_send(m)
