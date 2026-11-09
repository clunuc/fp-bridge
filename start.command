#!/bin/zsh
# Doble clic para arrancar FP Bridge. Ctrl+C (o cerrar la ventana) para pararlo.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Instalando dependencias (solo la primera vez)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
exec .venv/bin/python -m fpbridge run "$@"
