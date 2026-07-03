from app.core.extractor import MakeMKVExtractor, _build_rip_commands


def test_skip_set_registration_and_clear():
    ext = MakeMKVExtractor()
    ext.skip_title_index(5, 3)
    ext.skip_title_index(5, 7)
    assert ext._skipped_indices[5] == {3, 7}

    ext.unskip_title_index(5, 3)
    assert ext._skipped_indices[5] == {7}

    # Unknown job / index is a no-op, never raises.
    ext.unskip_title_index(999, 1)
    ext.unskip_title_index(5, 999)
    assert ext._skipped_indices[5] == {7}


def test_build_rip_commands_all_selected_uses_all_pass():
    cmds = _build_rip_commands("makemkvcon", "dev:F:", "/out", None)
    assert len(cmds) == 1
    title_index, cmd = cmds[0]
    assert title_index is None  # "all" pass has no single title index
    assert cmd[-1] == "/out"
    assert "all" in cmd


def test_build_rip_commands_subset_is_per_title_with_indices():
    cmds = _build_rip_commands("makemkvcon", "dev:F:", "/out", [2, 4])
    assert [ti for ti, _ in cmds] == [2, 4]
    assert all(str(ti) in cmd for ti, cmd in cmds)
