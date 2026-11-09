"""MIDI ports: the physical FaderPort and the virtual HUI port Pro Tools talks to."""

from __future__ import annotations

import logging
import queue
import time

import rtmidi

log = logging.getLogger(__name__)


def list_ports() -> tuple[list[str], list[str]]:
    i, o = rtmidi.MidiIn(), rtmidi.MidiOut()
    try:
        return i.get_ports(), o.get_ports()
    finally:
        i.delete()
        o.delete()


def _find(ports: list[str], needle: str) -> int | None:
    n = needle.lower()
    for idx, name in enumerate(ports):
        if n in name.lower():
            return idx
    return None


class Hardware:
    """Physical device (input + output) found by name substring; reconnects on unplug."""

    def __init__(self, needle: str, tag: str, q: queue.Queue):
        self.needle, self.tag, self.q = needle, tag, q
        self.min: rtmidi.MidiIn | None = None
        self.mout: rtmidi.MidiOut | None = None
        self._last_try = 0.0

    @property
    def connected(self) -> bool:
        return self.min is not None

    def try_open(self, force: bool = False) -> bool:
        now = time.monotonic()
        if self.connected or (not force and now - self._last_try < 2.0):
            return self.connected
        self._last_try = now
        mi, mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        ii, oo = _find(mi.get_ports(), self.needle), _find(mo.get_ports(), self.needle)
        if ii is None or oo is None:
            mi.delete()
            mo.delete()
            return False
        mi.open_port(ii)
        mo.open_port(oo)
        mi.ignore_types(sysex=False, timing=True, active_sense=True)
        mi.set_callback(lambda ev, _=None: self.q.put((self.tag, ev[0])))
        self.min, self.mout = mi, mo
        log.info("%s conectado: %s", self.tag, mi.get_port_name(ii))
        return True

    def still_present(self) -> bool:
        probe = rtmidi.MidiIn()
        try:
            return _find(probe.get_ports(), self.needle) is not None
        finally:
            probe.delete()

    def send(self, msg: list[int]) -> None:
        if self.mout is not None:
            self.mout.send_message(msg)

    def close(self) -> None:
        for p in (self.min, self.mout):
            if p is not None:
                p.close_port()
                p.delete()
        self.min = self.mout = None


class IacPair:
    """Two persistent IAC buses: we write to `to_pt`, Pro Tools writes to `from_pt`.
    They survive bridge restarts, so Pro Tools never loses the HUI device."""

    def __init__(self, to_pt: str, from_pt: str, tag: str, q: queue.Queue):
        self.mi, self.mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        oi, ii = _find(self.mo.get_ports(), to_pt), _find(self.mi.get_ports(), from_pt)
        if oi is None or ii is None:
            missing = to_pt if oi is None else from_pt
            raise SystemExit(f"No existe el bus IAC '{missing}'. Créalo en Configuración de Audio MIDI > "
                             "Ventana > Mostrar estudio MIDI > IAC Driver.")
        self.mo.open_port(oi)
        self.mi.open_port(ii)
        self.mi.ignore_types(sysex=False, timing=True, active_sense=True)
        self.mi.set_callback(lambda ev, _=None: q.put((tag, ev[0])))
        log.info("HUI por IAC: envío a '%s', recibo de '%s'", self.mo.get_port_name(oi), self.mi.get_port_name(ii))

    send = lambda self, msg: self.mo.send_message(msg)  # noqa: E731

    def close(self) -> None:
        for p in (self.mi, self.mo):
            p.close_port()
            p.delete()


class VirtualPort:
    """Virtual in+out pair with one name; Pro Tools sees it as a MIDI device."""

    def __init__(self, name: str, tag: str, q: queue.Queue):
        self.mi, self.mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        self.mi.open_virtual_port(name)
        self.mo.open_virtual_port(name)
        self.mi.ignore_types(sysex=False, timing=True, active_sense=True)
        self.mi.set_callback(lambda ev, _=None: q.put((tag, ev[0])))
        log.info("puerto virtual '%s' creado", name)

    def send(self, msg: list[int]) -> None:
        self.mo.send_message(msg)

    def close(self) -> None:
        for p in (self.mi, self.mo):
            p.close_port()
            p.delete()
