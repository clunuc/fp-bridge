"""The running bridge: ports + engine + main loop. Used by the CLI and the menu bar app."""

from __future__ import annotations

import fcntl
import logging
import queue
import threading
import time
from pathlib import Path

import yaml

from .engine import Engine
from .keys import KeySender
from .midi_io import Hardware, IacPair, VirtualPort
from . import paths

log = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    pass


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Bridge:
    def __init__(self, config_path: Path, dry_keys: bool = False):
        self.config_path = Path(config_path)
        self.dry_keys = dry_keys
        self.engine: Engine | None = None
        self.fp_dev: Hardware | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._lock = None

    # ------------------------------------------------------------ status
    @property
    def faderport_connected(self) -> bool:
        return bool(self.fp_dev and self.fp_dev.connected)

    @property
    def protools_connected(self) -> bool:
        return bool(self.engine and self.engine.hui_online)

    @property
    def hui_port_name(self) -> str:
        return (self._cfg.get("hui", {}) or {}).get("port_name", "FP Bridge HUI") if hasattr(self, "_cfg") else ""

    def stop(self) -> None:
        self._stop.set()

    def request_reload(self) -> None:
        self._force_reload = True

    # -------------------------------------------------------------- run
    def _acquire_lock(self) -> None:
        self._lock = open(paths.lock_file(), "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning("Ya hay un FP Bridge en marcha (en la barra de menús o en un terminal).")

    def run(self) -> None:
        self._acquire_lock()
        self._force_reload = False
        cfg = self._cfg = load_config(self.config_path)
        q: queue.Queue = queue.Queue()
        fp_dev = self.fp_dev = Hardware(cfg.get("faderport", {}).get("port", "FP2"), "fp", q)
        hcfg = cfg.get("hui", {}) or {}
        if hcfg.get("iac_to_protools") and hcfg.get("iac_from_protools"):
            hui_port = IacPair(hcfg["iac_to_protools"], hcfg["iac_from_protools"], "hui", q)
        else:
            hui_port = VirtualPort(hcfg.get("port_name", "FP Bridge HUI"), "hui", q)
        keys = KeySender((cfg.get("keys") or {}).get("target", "frontmost"), dry_run=self.dry_keys)
        engine = self.engine = Engine(cfg, fp_dev.send, hui_port.send, keys.send)

        log.info("config: %s", self.config_path)
        log.info("En Pro Tools: Setup > Peripherals > MIDI Controllers > HUI, puerto '%s'",
                 hcfg.get("port_name", "FP Bridge HUI"))
        for _ in range(10):  # CoreMIDI can take a moment to list devices to a new client
            if fp_dev.try_open(force=True):
                break
            time.sleep(0.1)
        else:
            log.warning("FaderPort no encontrado; lo sigo buscando... (¿encendido en modo Studio One?)")

        watcher = None
        if cfg.get("follow_selection", False):
            from .ptsl_follow import SelectionWatcher

            watcher = SelectionWatcher(q)
            watcher.start()

        started = fp_dev.connected
        if started:
            engine.start()
        last_check = time.monotonic()
        cfg_mtime = self.config_path.stat().st_mtime
        try:
            while not self._stop.is_set():
                try:
                    tag, msg = q.get(timeout=0.02)
                    try:
                        if tag == "fp":
                            engine.on_faderport(msg)
                        elif tag == "ptsl":
                            engine.on_ptsl(msg)
                        else:
                            engine.on_hui(msg)
                    except Exception:
                        log.exception("error procesando %s %s", tag, msg)
                except queue.Empty:
                    pass
                engine.tick()
                now = time.monotonic()
                if now - last_check > 2.0 or self._force_reload:
                    last_check = now
                    try:
                        mtime = self.config_path.stat().st_mtime
                    except OSError:
                        mtime = cfg_mtime
                    if mtime != cfg_mtime or self._force_reload:
                        self._force_reload = False
                        cfg_mtime = mtime
                        new = self._reload(engine, fp_dev, hui_port, keys)
                        if new is not None:
                            engine = self.engine = new
                    if fp_dev.connected and not fp_dev.still_present():
                        log.warning("FaderPort desconectado")
                        fp_dev.close()
                        started = False
                    if not fp_dev.connected and fp_dev.try_open() and not started:
                        engine._leds.clear()
                        engine.start()
                        started = True
        finally:
            log.info("cerrando...")
            if watcher is not None:
                watcher.stop()
            if fp_dev.connected:
                engine.stop()
                time.sleep(0.05)
            fp_dev.close()
            hui_port.close()
            self._lock.close()

    def _reload(self, old: Engine, fp_dev, hui_port, keys) -> Engine | None:
        """Rebuild the engine from the edited config, keeping what Pro Tools already told us."""
        try:
            new = Engine(load_config(self.config_path), fp_dev.send, hui_port.send, keys.send)
        except Exception as e:  # bad YAML or unknown action: keep running the old mapping
            log.error("config con errores, sigo con la anterior: %s", e)
            self.error = f"Error en la configuración: {e}"
            return None
        self.error = None
        new.host = old.host
        new.hui_online, new.last_ping = old.hui_online, old.last_ping
        new.last_host_fader = old.last_host_fader
        new.fader_touched = old.fader_touched
        new.trim_tracks = old.trim_tracks
        new.bank_pos, new.track_count = old.bank_pos, old.track_count
        new.strip = old.strip
        new._seen_select_led = old._seen_select_led
        new.send_on, new.send_idx = old.send_on, old.send_idx
        new._mode_before_send = old._mode_before_send
        new.auto_modes, new._track_name = old.auto_modes, old._track_name
        new.recording, new._rec_muted = old.recording, old._rec_muted
        if old.mode in new.modes:
            new.mode = old.mode
        if fp_dev.connected:
            new.start()
        log.info("config recargada")
        return new
