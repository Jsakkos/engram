from app.core.extractor import MakeMKVExtractor


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
