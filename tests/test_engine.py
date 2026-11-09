from pathlib import Path

import copy

import pytest
import yaml

from fpbridge import faderport as fp
from fpbridge import hui
from fpbridge.engine import Engine
from fpbridge.keys import parse_combo

CFG = yaml.safe_load((Path(__file__).parent.parent / "fpbridge" / "default_config.yaml").read_text())
# The default maps SHIFT to undo; most tests exercise the SHIFT layer machinery, so
# they run on a copy where SHIFT is the modifier again.
CFG_MOD = copy.deepcopy(CFG)
CFG_MOD["buttons"].pop("shift", None)


@pytest.fixture
def rig():
    out = {"fp": [], "hui": [], "keys": []}
    e = Engine(CFG_MOD, out["fp"].append, out["hui"].append, out["keys"].append)
    e.sleep = lambda s: out["hui"].append(["wait", s])
    clock = [1000.0]
    e.now = lambda: clock[0]
    e.clock = clock
    e.follow = True  # follow tests exercise the feature even though it is off by default
    e.start()
    for v in out.values():
        v.clear()
    return e, out


def press(e, name, down=True):
    e.on_faderport([0x90, fp.BUTTONS[name], 0x7F if down else 0x00])


def tap(e, name):
    press(e, name, True)
    press(e, name, False)


def test_config_loads_every_button_known():
    assert set(CFG["buttons"]) <= set(fp.BUTTONS)


def test_play_is_momentary_hui_switch(rig):
    e, out = rig
    tap(e, "play")
    assert out["hui"] == [[0xB0, 0x0F, 0x0E], [0xB0, 0x2F, 0x44], [0xB0, 0x0F, 0x0E], [0xB0, 0x2F, 0x04]]


def test_solo_led_follows_pro_tools(rig):
    e, out = rig
    e.on_hui([0xB0, 0x0C, 0x00])  # zone strip 0
    e.on_hui([0xB0, 0x2C, 0x43])  # port 3 (solo) on
    assert [0x90, fp.BUTTONS["solo"], fp.LED_ON] in out["fp"]
    out["fp"].clear()
    e.on_hui([0xB0, 0x0C, 0x00])
    e.on_hui([0xB0, 0x2C, 0x03])
    assert [0x90, fp.BUTTONS["solo"], fp.LED_OFF] in out["fp"]


def test_ping_is_answered(rig):
    e, out = rig
    e.on_hui([0x90, 0x00, 0x00])
    assert out["hui"] == [[0x90, 0x00, 0x7F]]
    assert e.hui_online


def test_click_sends_c_and_toggles_led(rig):
    e, out = rig
    tap(e, "click")
    assert out["keys"] == ["c"]
    assert [0x90, fp.BUTTONS["click"], fp.LED_ON] in out["fp"]
    tap(e, "click")
    assert [0x90, fp.BUTTONS["click"], fp.LED_OFF] in out["fp"]


def test_shift_layer(rig):
    e, out = rig
    press(e, "shift")
    tap(e, "prev")
    press(e, "shift", False)
    tap(e, "prev")
    assert out["keys"] == ["cmd+z"]
    # prev without shift = previous track (HUI channel left) + select it
    assert out["hui"] == (hui.switch(0x0A, 0, True) + hui.switch(0x0A, 0, False) + [["wait", 0.04]]
                          + hui.switch(0, 1, True) + hui.switch(0, 1, False))


def test_pan_push_toggles_side(rig):
    e, out = rig
    press(e, "encoder_push")
    e.on_faderport([0xB0, 0x3C, 0x01])
    press(e, "encoder_push", False)
    pan = hui.switch(0x0B, 2, True) + hui.switch(0x0B, 2, False)
    assert out["hui"] == pan + [[0xB0, 0x40, 0x41]]  # toggle: release does nothing


def test_pan_button_does_not_toggle_side(rig):
    e, out = rig
    tap(e, "pan")
    assert out["hui"] == []


def test_encoder_alt_cc(rig):
    e, out = rig
    e.on_faderport([0xB0, 0x10, 0x41])
    assert out["hui"] == [[0xB0, 0x40, 0x01]]


def test_shift_release_goes_to_same_action(rig):
    """Press rewind with shift, release shift first: the release must match the press."""
    e, out = rig
    press(e, "shift")
    press(e, "rewind")  # shift -> hui_tap rtz
    press(e, "shift", False)
    press(e, "rewind", False)
    assert out["hui"] == hui.switch(0x0F, 0, True) + hui.switch(0x0F, 0, False)


def test_fader_touch_move_and_motor(rig):
    e, out = rig
    press(e, "fader_touch")
    e.on_faderport([0xE0, 0x00, 0x40])  # 0x2000
    press(e, "fader_touch", False)
    assert out["hui"][:2] == hui.switch(0, 0, True)
    assert hui.fader(0, 0x2000) == out["hui"][2:4]
    assert out["hui"][4:6] == hui.switch(0, 0, False)
    # PT moves the fader -> motor
    out["fp"].clear()
    e.on_hui([0xB0, 0x00, 0x10])
    e.on_hui([0xB0, 0x20, 0x05])
    assert out["fp"] == [[0xE0, 0x05, 0x10]]


def test_motor_ignored_while_touched(rig):
    e, out = rig
    press(e, "fader_touch")
    out["fp"].clear()
    e.on_hui([0xB0, 0x00, 0x10])
    e.on_hui([0xB0, 0x20, 0x05])
    assert out["fp"] == []


def test_fader_without_touch_autotouches(rig):
    e, out = rig
    e.on_faderport([0xE0, 0x00, 0x20])
    assert out["hui"][:2] == hui.switch(0, 0, True)
    e.tick(now=1e12)
    assert out["hui"][-2:] == hui.switch(0, 0, False)


def test_encoder_modes(rig):
    e, out = rig
    # pan: B0 3C 01 (no direction bit) = clockwise by default
    e.on_faderport([0xB0, 0x3C, 0x01])
    assert out["hui"] == [[0xB0, 0x40, 0x41]]
    out["hui"].clear()
    e.set_mode("channel")  # track navigation mode (no button by default)
    assert e._rgb_sent["pan"] == (18, 18, 18)  # pan dimmed (idle white)
    e.on_faderport([0xB0, 0x3C, 0x42])  # 2 steps ccw
    bank = hui.switch(0x0A, 0, True) + hui.switch(0x0A, 0, False) + [["wait", 0.04]]
    select = hui.switch(0, 1, True) + hui.switch(0, 1, False)
    assert out["hui"] == bank + select + bank  # second select is held back
    out["hui"].clear()
    e.tick(now=e.clock[0] + 0.7)
    assert out["hui"] == select
    out["hui"].clear()
    tap(e, "scroll")
    assert out["keys"] == ["cmd+s"]  # scroll = save session


def test_automation_button_holds_mode_and_taps_auto_twice(rig):
    e, out = rig
    tap(e, "touch")
    combo = (hui.switch(0x18, 5, True) + hui.switch(0, 4, True) + hui.switch(0, 4, False)
             + hui.switch(0x18, 5, False))
    assert out["hui"] == combo + combo


def test_hui_display_parsing():
    s = hui.HostState()
    s.feed([0xF0, 0x00, 0x00, 0x66, 0x05, 0x00, 0x10, 0x00, ord("V"), ord("o"), ord("x"), ord(" "), 0xF7])
    assert s.scribble[0] == "Vox "


def test_key_combos():
    assert parse_combo("c") == (0x08, 0)
    code, flags = parse_combo("cmd+shift+z")
    assert code == 0x06 and flags == (1 << 20) | (1 << 17)
    assert parse_combo("keycode:42")[0] == 42
    with pytest.raises(ValueError):
        parse_combo("hyper+q")


def test_bad_config_fails_early():
    bad = {"buttons": {"play": {"press": "hui transport.plya"}}}
    with pytest.raises(ValueError):
        Engine(bad, print, print, print)


def test_fast_next_never_double_clicks(rig):
    e, out = rig
    select = hui.switch(0, 1, True) + hui.switch(0, 1, False)
    bank = hui.switch(0x0A, 2, True) + hui.switch(0x0A, 2, False) + [["wait", 0.04]]
    tap(e, "next")
    e.clock[0] += 0.1
    tap(e, "next")
    e.clock[0] += 0.1
    tap(e, "next")
    assert out["hui"] == bank + select + bank + bank  # banks move, only one select so far
    out["hui"].clear()
    e.tick(now=e.clock[0])  # 0.2 s after first select: still too early
    assert out["hui"] == []
    e.tick(now=1000.0 + 0.6)
    assert out["hui"] == select  # exactly one more select, 0.6 s after the first


def test_marker_adds_memory_location(rig):
    e, out = rig
    tap(e, "marker")
    assert out["keys"] == ["cmd+shift+h"]


def test_new_button_shortcuts(rig):
    e, out = rig
    tap(e, "master"); tap(e, "section"); tap(e, "bypass")
    assert out["keys"] == ["cmd+shift+0", "cmd+k"]  # bypass: no action for now
    assert out["hui"] == [] and e.led_state("bypass") == fp.LED_OFF


def _name(e, name):
    e.on_hui([0xF0, 0x00, 0x00, 0x66, 0x05, 0x00, 0x10, 0x00] + [ord(c) for c in name] + [0xF7])


def test_link_toggles_trim_with_one_press_each_way(rig):
    e, out = rig
    _name(e, "Vox ")
    e.on_hui([0xB0, 0x0C, 0x18]); e.on_hui([0xB0, 0x2C, 0x45])  # PT: touch LED on
    out["hui"].clear(); out["fp"].clear()
    tap(e, "link")
    trim, touch, auto = (0x18, 0), (0x18, 5), (0, 4)
    def combo(m):
        return hui.switch(*m, True) + hui.switch(*auto, True) + hui.switch(*auto, False) + hui.switch(*m, False)
    assert combo(trim) == out["hui"]
    assert e._rgb_sent["link"] == (127, 60, 0)  # ochre = trim
    out["hui"].clear()
    tap(e, "link")
    assert combo(trim) == out["hui"]  # Trim is an on/off switch in Pro Tools
    assert e._rgb_sent["link"] == (18, 18, 18)  # idle white


def test_trim_led_follows_track(rig):
    e, out = rig
    _name(e, "Vox ")
    tap(e, "link")
    _name(e, "Gtr ")
    assert e.led_state("link") == fp.LED_OFF
    _name(e, "Vox ")
    assert e.led_state("link") == fp.LED_ON


# ---------------------------------------------------------------- follow selection
from types import SimpleNamespace as NS

from fpbridge.ptsl_follow import selection_event


def _trk(i, name, sel=False, hidden=False, parent="", is_open=True):
    return NS(index=i, name=name, parent_folder_name=parent,
              track_attributes=NS(is_selected=2 if sel else 1, is_hidden=2 if hidden else 1, is_open=is_open))


def test_surface_tracks_skip_hidden_and_closed_folders():
    tracks = [_trk(1, "Kick"), _trk(2, "Hidden", hidden=True), _trk(3, "Vox BUS", is_open=False),
              _trk(4, "Vox 1", parent="Vox BUS"), _trk(5, "Bass", sel=True)]
    assert selection_event(tracks) == {"index": 2, "name": "Bass", "count": 3}


def _led(e, zone, port, on):
    e.on_hui([0xB0, 0x0C, zone]); e.on_hui([0xB0, 0x2C, (0x40 if on else 0) | port])


def test_click_track_in_pro_tools_moves_bank(rig):
    e, out = rig
    e.on_ptsl({"index": 19, "name": "Gtr", "count": 30})
    taps = lambda z, p, n: (hui.switch(z, p, True) + hui.switch(z, p, False)) * n
    # unknown position: home (30//8+2 = 5 bank lefts), then 2 banks + 3 chans right
    assert out["hui"] == taps(0x0A, 1, 5) + taps(0x0A, 3, 2) + taps(0x0A, 2, 3)
    out["hui"].clear()
    e.on_ptsl({"index": 17, "name": "Bass", "count": 30})  # known position: relative move
    assert out["hui"] == taps(0x0A, 0, 2)
    out["hui"].clear()
    tap(e, "next")  # our own next: Pro Tools then reports index 18 -> nothing to do
    out["hui"].clear()
    e.on_ptsl({"index": 18, "name": "x", "count": 30})
    assert out["hui"] == []


def test_end_of_session_moves_fader_to_other_strip(rig):
    e, out = rig
    e.on_ptsl({"index": 29, "name": "Last", "count": 30})
    _led(e, 3, 1, True)  # PT shows the selected track on strip 4: it won't bank further
    e.tick(now=e.clock[0] + 1)  # first check: try 3 chans right
    e.tick(now=e.clock[0] + 2)  # still on strip 4 -> fader follows strip 4
    assert e.strip == 3
    out["hui"].clear()
    tap(e, "solo")
    assert out["hui"][:2] == hui.switch(3, 3, True)


def test_gaps_in_ptsl_index_count_as_tracks():
    # PT index 3 is missing from PTSL (a track type it doesn't list) but HUI banks through it
    tracks = [_trk(1, "A"), _trk(2, "B"), _trk(4, "D", sel=True), _trk(5, "E")]
    assert selection_event(tracks) == {"index": 3, "name": "D", "count": 5}


def test_search_neighbour_banks_when_track_not_visible(rig):
    e, out = rig
    _led(e, 5, 1, False)  # Pro Tools has sent SELECT LEDs before
    e.bank_pos = 0
    e.on_ptsl({"index": 16, "name": "X", "count": 40})
    out["hui"].clear()
    taps = lambda z, p, n: (hui.switch(z, p, True) + hui.switch(z, p, False)) * n
    e.tick(now=e.clock[0] + 1)  # not visible -> try one bank left
    assert out["hui"] == taps(0x0A, 1, 1)
    out["hui"].clear()
    _led(e, 2, 1, True)  # found it on strip 3
    e.tick(now=e.clock[0] + 2)
    assert out["hui"] == taps(0x0A, 2, 2)  # 2 chans right puts it on the fader


# ---------------------------------------------------------------- sends on the fader
def test_channel_toggles_fader_to_send_and_back(rig):
    e, out = rig
    t = lambda name: hui.switch(*hui.SWITCHES[name], True) + hui.switch(*hui.SWITCHES[name], False)
    tap(e, "channel")
    assert out["hui"] == t("assign.send_a") + [["wait", 0.05]] + t("assign2.shift")
    assert e.send_on and e.mode == "send"
    assert e.led_state("channel") == fp.LED_ON
    assert e._rgb_sent["channel"] == (127, 0, 0)  # red = send A
    out["hui"].clear()
    tap(e, "channel")
    assert out["hui"] == t("assign2.shift") + [["wait", 0.05]] + t("assign.pan")
    assert not e.send_on and e.mode == "pan"


def test_knob_chooses_send_while_on_send(rig):
    e, out = rig
    tap(e, "channel")
    out["hui"].clear(); out["fp"].clear()
    e.on_faderport([0xB0, 0x3C, 0x01])  # knob one step clockwise -> send B
    assert out["hui"] == hui.switch(0x0B, 6, True) + hui.switch(0x0B, 6, False)
    assert fp.led_color("channel", (127, 50, 0))[0] in out["fp"]  # orange = B
    e.on_faderport([0xB0, 0x3C, 0x42])  # two steps back -> wraps to E
    assert e.send_idx == 4
    press(e, "encoder_push"); press(e, "encoder_push", False)  # push = back to volume
    assert not e.send_on


def test_send_remembered_next_time(rig):
    e, out = rig
    tap(e, "channel")
    e.on_faderport([0xB0, 0x3C, 0x02])  # -> send C
    tap(e, "channel")
    out["hui"].clear()
    tap(e, "channel")
    assert out["hui"][:2] == hui.switch(0x0B, 5, True)  # starts on send C again


def test_pan_button_leaves_send_mode_and_is_harmless_otherwise(rig):
    e, out = rig
    t = lambda name: hui.switch(*hui.SWITCHES[name], True) + hui.switch(*hui.SWITCHES[name], False)
    tap(e, "pan")  # already on volume: must not touch PT (PAN would swap L/R)
    assert out["hui"] == []
    tap(e, "channel")
    out["hui"].clear()
    tap(e, "pan")
    assert out["hui"] == t("assign2.shift") + [["wait", 0.05]] + t("assign.pan")
    assert not e.send_on and e.mode == "pan"


def test_flip_state_follows_pro_tools_led(rig):
    """PT left flipped (e.g. after a restart): LEDs and the next press must agree with PT."""
    e, out = rig
    _led(e, 0x0C, 3, True)  # Pro Tools: FLIP is on
    assert e.send_on and e.mode == "send"
    assert e.led_state("channel") == fp.LED_ON and e.led_state("pan") == fp.LED_OFF
    out["hui"].clear()
    tap(e, "channel")  # so this press turns it OFF
    t = lambda name: hui.switch(*hui.SWITCHES[name], True) + hui.switch(*hui.SWITCHES[name], False)
    assert out["hui"] == t("assign2.shift") + [["wait", 0.05]] + t("assign.pan")
    _led(e, 0x0C, 3, False)
    assert not e.send_on and e.mode == "pan"


def test_send_led_from_pro_tools_sets_send_index(rig):
    e, out = rig
    _led(e, 0x0B, 5, True)  # Pro Tools: SEND C assigned
    assert e.send_idx == 2


def test_default_shift_button_is_undo():
    keys = []
    e = Engine(CFG, lambda m: None, lambda m: None, keys.append)
    tap(e, "shift")
    assert keys == ["cmd+z"] and not e.shift


def _smart_auto_leds(e):
    """Opt-in feature (off by default): Read/Touch/Write LEDs from the tracked mode."""
    e.buttons["read"].led = "auto read"
    e.buttons["touch"].led = "auto touch,latch"
    e.buttons["write"].led = "auto write"
    e.probe_auto = True


# ---------------------------------------------------------------- automation LEDs
def test_automation_leds_follow_track_mode(rig):
    e, out = rig
    _smart_auto_leds(e)
    _name(e, "Vox ")
    tap(e, "read")  # set from the FaderPort
    assert e.led_state("read") == fp.LED_ON and e.led_state("touch") == fp.LED_OFF
    _name(e, "Gtr ")
    e.clock[0] += 5  # later, the mode of Gtr is changed with the mouse
    _name(e, "Tch ")  # Pro Tools flashes it
    assert e._track_key() == "Gtr"
    assert e.led_state("touch") == fp.LED_ON and e.led_state("read") == fp.LED_OFF
    _name(e, "Vox ")
    assert e.led_state("read") == fp.LED_ON


def test_mode_text_after_own_press_is_the_old_mode(rig):
    """Pro Tools shows the mode from BEFORE our change: the LEDs must keep the new one."""
    e, out = rig
    _smart_auto_leds(e)
    _name(e, "Vox ")
    tap(e, "touch")
    _name(e, "Read")  # what the track had before
    _name(e, "Read")
    assert e.led_state("touch") == fp.LED_ON and e.led_state("read") == fp.LED_OFF


def test_link_trim_follows_pro_tools_after_toggle(rig):
    e, out = rig
    _smart_auto_leds(e)
    _name(e, "Inst")
    tap(e, "link")  # we guess trim ON...
    _name(e, "TRd ")  # ...but PT says trim WAS on before the press: so now it is OFF
    assert e.led_state("link") == fp.LED_OFF
    e.clock[0] += 5
    tap(e, "link")
    _name(e, "Read")  # was off -> now on
    assert e.led_state("link") == fp.LED_ON and e.led_state("read") == fp.LED_ON


def test_mode_text_not_after_our_press_is_truth(rig):
    e, out = rig
    _smart_auto_leds(e)
    _name(e, "Inst")
    e.clock[0] += 5
    _name(e, "TWrt")  # changed with the mouse: trim + write
    assert e.led_state("link") == fp.LED_ON and e.led_state("write") == fp.LED_ON


def test_scroll_triple_green_flash(rig):
    e, out = rig
    green = fp.led_color("scroll", (0, 127, 0))[0]
    dim = fp.led_color("scroll", (18, 18, 18))[0]
    out["fp"].clear()
    tap(e, "scroll")
    for i in range(8):
        e.tick(now=e.clock[0] + i * 0.11 + 0.01)
    seen = [m for m in out["fp"] if m in (green, dim)]
    assert seen.count(green) == 3 and seen[-1] == dim


def test_track_change_asks_pro_tools_for_the_mode(rig):
    e, out = rig
    _smart_auto_leds(e)
    e.on_hui([0x90, 0, 0])  # Pro Tools online
    _name(e, "Vox ")
    out["hui"].clear()
    e.tick(now=e.clock[0] + 0.5)
    auto = hui.switch(0, 4, True) + hui.switch(0, 4, False)
    assert out["hui"] == auto + auto  # AUTO twice: show the mode, close again
    _name(e, "Wrt ")  # Pro Tools' answer
    assert e.led_state("write") == fp.LED_ON and e._track_key() == "Vox"


def test_non_name_texts_are_not_track_changes(rig):
    e, out = rig
    _name(e, "Vox ")
    _name(e, "Auto")
    _name(e, " -  ")
    assert e._track_key() == "Vox"
