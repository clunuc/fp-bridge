"""FP Bridge menu bar app (rumps). The bridge runs in a background thread."""

from __future__ import annotations

import logging
import logging.handlers
import os
import plistlib
import subprocess
import threading
from pathlib import Path

import rumps

from . import paths
from .bridge import AlreadyRunning, Bridge

log = logging.getLogger("fpbridge.menubar")

AGENT_LABEL = "com.fpbridge.menubar"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def _setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _app_path() -> str | None:
    """The .app bundle we were launched from (set by its launcher script)."""
    return os.environ.get("FPBRIDGE_APP")


def record_mute_enabled(config: Path) -> bool:
    import yaml

    try:
        cfg = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        return bool((cfg.get("record_mute") or {}).get("enabled", False))
    except Exception:
        return False


def set_record_mute(config: Path, on: bool) -> None:
    """Flip record_mute.enabled in the YAML text, keeping the user's comments and layout."""
    import re

    text = config.read_text(encoding="utf-8")
    value = "true" if on else "false"
    new, n = re.subn(r"(?m)^(record_mute:\s*\n(?:[ \t]+#.*\n)*[ \t]+enabled:[ \t]*)\S+", rf"\g<1>{value}", text)
    if n == 0:
        new = text.rstrip("\n") + f"\n\nrecord_mute:\n  enabled: {value}\n"
    config.write_text(new, encoding="utf-8")


class FPBridgeApp(rumps.App):
    def __init__(self) -> None:
        icon = str(paths.PACKAGE_DIR / "assets" / "menubar.png")
        super().__init__("FP Bridge", icon=icon, template=True, title=None, quit_button=None)
        self.bridge = Bridge(paths.user_config())
        self.item_fp = rumps.MenuItem("FaderPort: …")
        self.item_pt = rumps.MenuItem("Pro Tools: …")
        self.item_err = rumps.MenuItem("")
        self.item_monitor = rumps.MenuItem("Monitorización directa (mutear al grabar)",
                                           callback=self.toggle_record_mute)
        self.item_monitor.state = record_mute_enabled(paths.user_config())
        self.item_login = rumps.MenuItem("Abrir al iniciar sesión", callback=self.toggle_login)
        self.item_login.state = AGENT_PLIST.exists()
        self.menu = [
            self.item_fp,
            self.item_pt,
            self.item_err,
            None,
            self.item_monitor,
            None,
            rumps.MenuItem("Editar configuración…", callback=self.edit_config),
            rumps.MenuItem("Recargar configuración", callback=self.reload_config),
            rumps.MenuItem("Ver registro…", callback=self.show_log),
            None,
            self.item_login,
            rumps.MenuItem("Salir", callback=self.quit),
        ]
        self.item_err.hidden = True
        self.thread = threading.Thread(target=self._run_bridge, daemon=True, name="bridge")
        self.thread.start()
        self.timer = rumps.Timer(self.refresh, 1)
        self.timer.start()

    def _run_bridge(self) -> None:
        try:
            self.bridge.run()
        except AlreadyRunning as e:
            self.bridge.error = str(e)
        except Exception as e:
            log.exception("el puente se ha detenido")
            self.bridge.error = f"El puente se ha detenido: {e}"

    # ----------------------------------------------------------- status
    def refresh(self, _=None) -> None:
        b = self.bridge
        fp_ok, pt_ok = b.faderport_connected, b.protools_connected
        self.item_fp.title = "FaderPort: conectado" if fp_ok else "FaderPort: no encontrado (¿modo Studio One?)"
        self.item_pt.title = ("Pro Tools: conectado" if pt_ok
                              else f"Pro Tools: esperando (Peripherals → HUI → '{b.hui_port_name or 'FP Bridge HUI'}')")
        if b.error:
            self.item_err.title = "⚠ " + b.error[:120]
            self.item_err.hidden = False
        else:
            self.item_err.hidden = True
        dead = not self.thread.is_alive()
        ok = fp_ok and pt_ok and not b.error and not dead
        title = None if ok else "⚠"
        if self.title != title:
            self.title = title

    # ---------------------------------------------------------- actions
    def toggle_record_mute(self, sender) -> None:
        on = not sender.state
        set_record_mute(paths.user_config(), on)
        if self.bridge.engine is not None:
            self.bridge.engine.record_mute = on
        sender.state = on
        log.info("monitorización directa (mutear al grabar): %s", "on" if on else "off")

    def edit_config(self, _) -> None:
        subprocess.run(["open", "-t", str(paths.user_config())], check=False)

    def reload_config(self, _) -> None:
        self.bridge.request_reload()

    def show_log(self, _) -> None:
        subprocess.run(["open", "-a", "Console", str(paths.log_file())], check=False)

    def toggle_login(self, sender) -> None:
        if AGENT_PLIST.exists():
            AGENT_PLIST.unlink()
            sender.state = False
            return
        app = _app_path()
        if not app:
            rumps.alert("FP Bridge", "Esta opción solo funciona desde FP Bridge.app.")
            return
        AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_PLIST, "wb") as f:
            plistlib.dump({"Label": AGENT_LABEL, "ProgramArguments": ["/usr/bin/open", "-a", app],
                           "RunAtLoad": True}, f)
        sender.state = True

    def quit(self, _) -> None:
        self.bridge.stop()
        self.thread.join(timeout=2)
        rumps.quit_application()


def main() -> None:
    _setup_logging()
    FPBridgeApp().run()
