#!/bin/zsh
# Builds "FP Bridge.app" (menu bar app) and optionally installs it.
#
#   ./build_app.sh            -> build/FP Bridge.app
#   ./build_app.sh --install  -> also copies it to ~/Applications and opens it
#
# The app carries its own copy of the code and a Python venv (built with the
# system python3 from Apple's Command Line Tools), so it keeps working if this
# folder moves.
set -euo pipefail
cd "$(dirname "$0")"

APP="build/FP Bridge.app"
PY=${PYTHON:-python3}

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>FP Bridge</string>
  <key>CFBundleDisplayName</key><string>FP Bridge</string>
  <key>CFBundleIdentifier</key><string>com.fpbridge.menubar</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>fpbridge</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
</dict>
</plist>
PLIST

echo "Copiando código..."
cp -R fpbridge "$APP/Contents/Resources/fpbridge"
find "$APP/Contents/Resources/fpbridge" -name __pycache__ -prune -exec rm -rf {} +
cp fpbridge/assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

echo "Creando entorno de Python (tarda un minuto)..."
"$PY" -m venv "$APP/Contents/Resources/venv"
"$APP/Contents/Resources/venv/bin/pip" install -q --upgrade pip >/dev/null
"$APP/Contents/Resources/venv/bin/pip" install -q -r requirements-app.txt

cat > "$APP/Contents/MacOS/fpbridge" <<'LAUNCH'
#!/bin/zsh
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export FPBRIDGE_APP="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$HERE/Resources"
# Finder can start a script-based app under Rosetta; the MIDI library is native, so
# run Python natively on Apple Silicon. Errors before logging starts go to the launch log.
ARCH=()
[[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" == 1 ]] && ARCH=(arch -arm64)
exec "${ARCH[@]}" "$HERE/Resources/venv/bin/python3" -m fpbridge menubar 2>>"$HOME/Library/Logs/FP Bridge.launch.log"
LAUNCH
chmod +x "$APP/Contents/MacOS/fpbridge"

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "Listo: $APP"

if [[ "${1:-}" == "--install" ]]; then
  DEST=/Applications
  [[ -w "$DEST" ]] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
  osascript -e 'tell application "FP Bridge" to quit' >/dev/null 2>&1 || true
  pkill -f "fpbridge menubar" 2>/dev/null || true
  sleep 1
  rm -rf "$DEST/FP Bridge.app"
  [[ "$DEST" == /Applications ]] && rm -rf "$HOME/Applications/FP Bridge.app"
  cp -R "$APP" "$DEST/"
  echo "Instalado en $DEST/FP Bridge.app"
  open "$DEST/FP Bridge.app"
fi
