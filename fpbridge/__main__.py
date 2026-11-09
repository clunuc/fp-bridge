"""FP Bridge command line.

  python -m fpbridge run [config.yaml] [-v]   arranca el puente en el terminal
  python -m fpbridge menubar                  arranca la app de la barra de menús
  python -m fpbridge monitor                  muestra lo que envía el FaderPort
  python -m fpbridge ports                    lista los puertos MIDI
  python -m fpbridge switches                 lista los switches HUI disponibles
  python -m fpbridge keylearn                 muestra el código de las teclas que pulses
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
from pathlib import Path

def cmd_run(args) -> None:
    from . import paths
    from .bridge import AlreadyRunning, Bridge

    bridge = Bridge(Path(args.config) if args.config else paths.user_config(), dry_keys=args.dry_keys)
    try:
        bridge.run()
    except AlreadyRunning as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        bridge.stop()


def cmd_menubar(args) -> None:
    from .menubar import main as menubar_main

    menubar_main()


def cmd_monitor(args) -> None:
    from . import faderport as fp
    from .midi_io import Hardware

    q: queue.Queue = queue.Queue()
    dev = Hardware(args.port, "fp", q)
    if not dev.try_open():
        sys.exit(f"No encuentro un puerto MIDI que contenga '{args.port}'. Usa: python -m fpbridge ports")
    print("Pulsa botones / mueve el fader y el encoder (Ctrl+C para salir)\n")
    try:
        while True:
            _, msg = q.get()
            hexs = " ".join(f"{b:02X}" for b in msg)
            print(f"{hexs:<12} -> {fp.parse(msg)}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()


def cmd_ports(args) -> None:
    from .midi_io import list_ports

    ins, outs = list_ports()
    print("Entradas MIDI:")
    for p in ins:
        print("  ", p)
    print("Salidas MIDI:")
    for p in outs:
        print("  ", p)


def cmd_switches(args) -> None:
    from . import hui

    print("Switches de canal (strip.<nombre>, o strip3.<nombre> para un canal concreto):")
    print("  ", ", ".join(f"strip.{n}" for n in hui.STRIP_PORTS))
    print("\nResto de switches:")
    for name in hui.SWITCHES:
        print("  ", name)


def cmd_keylearn(args) -> None:
    from .keys import keylearn

    keylearn()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="fpbridge", description="FaderPort V2 -> Pro Tools HUI bridge")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="arranca el puente")
    r.add_argument("config", nargs="?", default=None,
                   help="por defecto ~/Library/Application Support/FP Bridge/config.yaml")
    r.add_argument("-v", "--verbose", action="store_true", help="muestra todo el tráfico")
    r.add_argument("--dry-keys", action="store_true", help="no envía atajos de teclado, solo los muestra")
    m = sub.add_parser("monitor", help="muestra lo que envía el FaderPort")
    m.add_argument("--port", default="FP2")
    sub.add_parser("menubar", help="arranca la app de la barra de menús")
    sub.add_parser("ports")
    sub.add_parser("switches")
    sub.add_parser("keylearn")
    args = ap.parse_args(argv)

    if args.cmd != "menubar":  # the menu bar app logs to its own file
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
            format="%(asctime)s %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        )
    {
        "run": cmd_run,
        "menubar": cmd_menubar,
        "monitor": cmd_monitor,
        "ports": cmd_ports,
        "switches": cmd_switches,
        "keylearn": cmd_keylearn,
        None: lambda a: ap.print_help(),
    }[args.cmd](args)


if __name__ == "__main__":
    main()
