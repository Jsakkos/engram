from engram_bot.history import Item, build_messages


def test_merges_and_names_speakers():
    out = build_messages(
        [
            Item("user", "hi", "ann"),
            Item("user", "it broke", "bob"),
            Item("assistant", "what version?"),
            Item("user", "0.39", "ann"),
        ]
    )
    assert out == [
        {"role": "user", "content": "ann: hi\n\nbob: it broke"},
        {"role": "assistant", "content": "what version?"},
        {"role": "user", "content": "ann: 0.39"},
    ]


def test_starts_and_ends_on_user():
    out = build_messages(
        [Item("assistant", "welcome"), Item("user", "q", "ann"), Item("assistant", "a")]
    )
    assert out == [{"role": "user", "content": "ann: q"}]
    assert build_messages([Item("assistant", "only me")]) == []


def test_skips_empty_and_truncates():
    out = build_messages([Item("user", "   ", "x"), Item("user", "y" * 10000, "x")])
    assert len(out) == 1 and out[0]["content"].endswith("(message truncated)")
