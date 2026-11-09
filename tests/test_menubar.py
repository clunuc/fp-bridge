from fpbridge.menubar import record_mute_enabled, set_record_mute


def test_record_mute_toggle_keeps_the_rest_of_the_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n\n# comment\nrecord_mute:\n  enabled: true\nother: 2\n")
    set_record_mute(p, False)
    assert p.read_text() == "a: 1\n\n# comment\nrecord_mute:\n  enabled: false\nother: 2\n"
    assert not record_mute_enabled(p)
    set_record_mute(p, True)
    assert record_mute_enabled(p)


def test_record_mute_toggle_adds_the_block_if_missing(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n")
    set_record_mute(p, True)
    assert record_mute_enabled(p)
