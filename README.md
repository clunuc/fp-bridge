# FP Bridge: FaderPort V2 → Pro Tools (HUI) + atajos de teclado

*English summary below.*

App de la barra de menús de macOS que hace de puente entre un **PreSonus
FaderPort V2** (el de un solo fader, 2018) y **Pro Tools**. Funciona como un
Bome MIDI Translator hecho a medida. El FaderPort deja de hablar HUI
directamente con Pro Tools, y el puente traduce cada botón:

```
FaderPort V2 (modo nativo) ──► FP Bridge ──► puerto virtual "FP Bridge HUI" ──► Pro Tools
                               │  ◄── LEDs, motor del fader, nombres de pista ◄──┘
                               └──► atajos de teclado (Click, Master, Section...)
```

Así **todos** los botones hacen algo, incluidos los que PreSonus deja vacíos en
HUI (Click, Section, Master, Link...). Cada botón se configura en un archivo de
texto y los cambios se aplican al guardarlo.

## Instalación

Necesitas macOS 11 o posterior y las Command Line Tools de Apple (`xcode-select --install`).

```bash
git clone https://github.com/clunuc/fp-bridge.git
cd fp-bridge
./build_app.sh --install
```

Se crea **FP Bridge.app** en Aplicaciones (o en `~/Applications` si no tienes
permiso de escritura en `/Applications`) y se abre. En la barra de menús
aparece un icono de fader, con **⚠** al lado si falta algo por conectar.

## Puesta en marcha (una sola vez)

1. **FaderPort en modo Studio One (nativo):** apágalo, mantén **NEXT** mientras
   lo enciendes y pulsa **SOLO**.
2. **Pro Tools → Setup → Peripherals → MIDI Controllers:**
   - Si tenías un HUI apuntando a "PreSonus FP2", ponlo a *none*.
   - Crea un **HUI** con *Receive From* y *Send To* = **FP Bridge HUI**, *#Ch's* = 8.
3. **Pro Tools → Setup → MIDI → Input Devices:** desmarca **PreSonus FP2**, para
   que las notas del FaderPort no entren en tus pistas MIDI.
4. **Permiso de Accesibilidad**, necesario para los atajos de teclado: Ajustes
   del Sistema → Privacidad y seguridad → Accesibilidad → activa **FP Bridge**.
   macOS lo pide la primera vez. Sin él, descarta los atajos sin avisar.
5. En el menú del icono del fader, marca **Abrir al iniciar sesión**.

Si cierras y vuelves a abrir FP Bridge con Pro Tools abierto, vuelve a elegir
**FP Bridge HUI** en Peripherals y pulsa OK: Pro Tools no reconecta solo un
puerto que ha desaparecido.

## Menú

- **FaderPort / Pro Tools:** estado de la conexión.
- **Editar configuración…:** abre tu configuración. Al guardarla se aplica en
  unos 2 segundos, sin reiniciar. Si tiene un error, sigue funcionando con la
  anterior y el menú muestra el aviso.
- **Ver registro…:** `~/Library/Logs/FP Bridge.log`.
- **Abrir al iniciar sesión.**

Tu configuración está en `~/Library/Application Support/FP Bridge/config.yaml`.
La primera vez se copia de [fpbridge/default_config.yaml](fpbridge/default_config.yaml).

## Mapa por defecto

| Botón | Normal | Con ⌘ en el teclado del Mac |
|---|---|---|
| Solo / Mute / Arm | HUI del canal (con LED) | igual |
| Shift | deshacer (⌘Z) | |
| Bypass | **nueva toma**: para, Create Playlist (⌃\ + Enter) y arma la grabación; mantener = Bounce Mix (⌥⌘B) | |
| Touch / Write / Read | automatización Touch / Write / Read | Latch / Trim / Off |
| Prev / Next | pista anterior / siguiente, y la selecciona | deshacer / rehacer |
| Pan | encoder = pan; pulsar el encoder alterna pan L ↔ R | |
| Channel | **fader ↔ envío**: el fader controla el nivel de un envío (HUI flip). El knob elige el envío A–E y el LED de color lo indica (A rojo, B naranja, C amarillo, D verde, E azul). Pulsar Channel o el knob = volver al volumen | |
| Scroll | guardar sesión (⌘S) | |
| Marker | nueva memory location (⇧⌘H) | |
| Click | metrónomo (tecla C) | |
| Master | ventana Mix/Edit (⇧⌘0, ⌘= en teclado español) | |
| Section | pre-roll on/off (⌘K) | |
| Link | automatización Trim on/off (LED encendido = Trim) | |
| Rew / FF | rebobinar / avanzar | inicio / final |
| Stop / Play / Rec | transporte (con LED) | |
| Loop | loop | Quick Punch |
| Pedal | espacio (play/stop) | |
| Fader | volumen de la pista, motorizado | |

### ⚠ Configura los atajos de teclado antes de usarlo

Los botones que envían atajos de teclado vienen configurados para un Pro Tools
concreto, con atajos personalizados y teclado español. **Revísalos y cámbialos
por los tuyos** en **Editar configuración…**:

| Botón | Atajo por defecto | Por qué revisarlo |
|---|---|---|
| Click | `C` | atajo personalizado; por defecto Pro Tools no usa C para el metrónomo |
| Marker | `⇧⌘H` | atajo personalizado para crear memory locations (en Pro Tools estándar: Enter del teclado numérico, `kpenter`) |
| Master | `⇧⌘0` | es ⌘= en un teclado español; en un teclado de EE. UU. sería `cmd+=` |
| Section | `⌘K` | pre-roll |
| Scroll | `⌘S` | guardar |
| Pedal | `espacio` | play/stop |
| Bypass | `espacio`, `⌃\`, `Enter` | macro de nueva toma: usa Create Playlist (⌃\) de Pro Tools |

Los botones HUI (transporte, solo, mute, arm, automatización, fader, pan) no
dependen de tus atajos y funcionan igual en cualquier Pro Tools.

## Cambiar la configuración

Cada botón puede tener tres acciones:

- `press`: al pulsarlo.
- `hold`: al mantenerlo pulsado (`long_press_ms`, 500 ms por defecto). Si un botón tiene `hold`, su acción normal se ejecuta al soltarlo.
- `shift`: al pulsarlo mientras mantienes **⌘** en el teclado del Mac (`keyboard_modifier`).

Cualquier acción puede ser una lista de pasos (una macro).

```yaml
bypass:  {press: "key cmd+opt+b"}                     # un atajo
section: {press: ["key cmd+a", "key cmd+e"]}           # varios en orden
click:   {press: "key c", shift: "key cmd+opt+c"}      # otra acción con SHIFT
solo:    {press: "hui strip.solo", led: "hui strip.solo"}   # un switch HUI con su LED
```

Las acciones y los switches disponibles están explicados al principio del
archivo de configuración.

**Teclados que no son de EE. UU.:** las teclas se envían por posición física
(la de un teclado de EE. UU.). Letras y números coinciden. En los signos, usa
`keylearn` (abajo) y copia el `keycode:<n>` que te muestra.

## Desde el terminal (desarrollo)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m fpbridge run -v      # puente en el terminal, con todo el tráfico
.venv/bin/python -m fpbridge monitor     # qué envía cada botón del FaderPort
.venv/bin/python -m fpbridge keylearn    # código de cualquier tecla
.venv/bin/python -m fpbridge switches    # todos los switches HUI
.venv/bin/python -m pytest               # pruebas
```

Solo puede funcionar un puente a la vez. Si la app está abierta, `run` se niega
a arrancar.

## Si algo va al revés

- El encoder gira al revés → `faderport.encoder_cw_sets_bit`.
- El pan gira al revés → `hui.vpot_cw_sets_bit`.

## Opciones

- `follow_selection: true`: el fader salta a la pista que seleccionas en Pro
  Tools, consultándolo por PTSL (Pro Tools Scripting SDK). Viene desactivado.
- `hui.iac_to_protools` / `hui.iac_from_protools`: usa dos buses IAC en vez del
  puerto virtual, para que Pro Tools no pierda la conexión al reiniciar el puente.

## Archivos

- `fpbridge/hui.py`: protocolo HUI (switches, fader, V-pots, pantallas, ping).
- `fpbridge/faderport.py`: protocolo nativo del FaderPort, según el apéndice 8.2 del manual de PreSonus.
- `fpbridge/engine.py`: motor de traducción.
- `fpbridge/bridge.py`: puertos y bucle principal.
- `fpbridge/menubar.py`: app de la barra de menús.
- `fpbridge/keys.py`: atajos de teclado (Quartz) y `keylearn`.
- `fpbridge/ptsl_follow.py`: seguimiento de la selección por PTSL (opcional).
- `tools/make_icons.py`: dibuja los iconos (fpbridge/assets).

---

## English summary

FP Bridge is a macOS menu bar app that sits between a PreSonus FaderPort V2
(single fader, 2018) in native/Studio One mode and Pro Tools. It emulates a
HUI controller on a virtual MIDI port, so the fader, transport, solo, mute, arm
and automation work as usual. Buttons that HUI doesn't cover (Click, Section,
Master, Link...) send keyboard shortcuts instead. Every button is mapped in a
YAML file, which reloads when you save it.

Install with `./build_app.sh --install`, put the FaderPort in native mode
(hold NEXT at power-on, press SOLO), add a HUI controller on "FP Bridge HUI" in
Pro Tools' Peripherals, and grant Accessibility permission.

Not affiliated with PreSonus or Avid. The HUI protocol is implemented from
community reverse-engineering.
