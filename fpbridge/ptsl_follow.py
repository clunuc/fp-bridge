"""Watch the track selection in Pro Tools through PTSL (Pro Tools Scripting SDK).

HUI tells a surface nothing about which track is selected outside its 8-strip bank,
so a background thread asks Pro Tools a few times per second and reports, through
the main queue, the position of the selected track among the tracks a control
surface can bank through (not hidden, not inside a closed folder).

Read-only: it never sends Pro Tools a command.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger(__name__)

_ON = (2, 3, 4)  # TrackAttributeState: set explicitly / implicitly / both


def surface_tracks(tracks) -> list:
    """Tracks in the order a HUI banks through them: not hidden, no closed folder above."""
    by_name = {t.name: t for t in tracks}

    def shown(t) -> bool:
        if t.track_attributes.is_hidden in _ON:
            return False
        parent, seen = t.parent_folder_name, set()
        while parent and parent not in seen:
            seen.add(parent)
            p = by_name.get(parent)
            if p is None:
                break
            if not p.track_attributes.is_open or p.track_attributes.is_hidden in _ON:
                return False
            parent = p.parent_folder_name
        return True

    return [t for t in sorted(tracks, key=lambda t: t.index) if shown(t)]


def selection_event(tracks) -> dict:
    """Position of the selected track on the surface. PTSL doesn't list every track (some
    types are missing, leaving gaps in `index`), but HUI banks through them too, so the
    position comes from Pro Tools' own index minus the tracks a surface skips."""
    tracks = sorted(tracks, key=lambda t: t.index)
    shown = {id(t) for t in surface_tracks(tracks)}
    pos, skipped = {}, 0
    for t in tracks:
        if id(t) in shown:
            pos[id(t)] = t.index - 1 - skipped
        else:
            skipped += 1
    sel = [t for t in tracks if id(t) in shown and t.track_attributes.is_selected in _ON]
    count = (tracks[-1].index - skipped) if tracks else 0
    one = sel[0] if len(sel) == 1 else None  # follow only a single selected track
    return {
        "index": pos[id(one)] if one else None,
        "name": one.name if one else None,
        "count": count,
    }


class SelectionWatcher(threading.Thread):
    def __init__(self, q: queue.Queue, interval: float = 0.25):
        super().__init__(daemon=True, name="ptsl-follow")
        self.q, self.interval = q, interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import ptsl
        except ImportError:
            log.warning("py-ptsl no instalado: el fader no seguirá la selección de Pro Tools")
            return
        engine, last = None, None
        while not self._stop.is_set():
            if engine is None:
                try:
                    engine = ptsl.Engine(company_name="fpbridge", application_name="FP Bridge")
                    log.info("PTSL conectado: el fader seguirá la pista seleccionada")
                    last = None
                except Exception:
                    self._stop.wait(5)
                    continue
            try:
                ev = selection_event(engine.track_list())
                key = (ev["index"], ev["name"], ev["count"])
                if key != last:
                    last = key
                    self.q.put(("ptsl", ev))
            except Exception as x:
                name = type(x).__name__
                if "Rpc" in name:  # Pro Tools closed
                    log.info("PTSL desconectado")
                    try:
                        engine.close()
                    except Exception:
                        pass
                    engine = None
                    self._stop.wait(5)
                    continue
                # Pro Tools busy (modal dialog, no session open...): try again later
            self._stop.wait(self.interval)
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass
