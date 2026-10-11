import pytest

from engram_bot import intake

LABELS = ("bug", "enhancement", "ux", "linux")


def tool_input(**over):
    data = {name: "" for name in intake._STRING_FIELDS}
    data.update(
        kind="bug",
        title="Skipping a track returns HTTP 400",
        summary="Skip fails.",
        labels=["ux", "made-up"],
        duplicate_of=0,
    )
    data.update(over)
    return data


def test_from_tool_input_filters_labels_and_validates():
    d = intake.Draft.from_tool_input(tool_input(), LABELS)
    assert d.labels == ["ux"]
    assert not d.is_comment
    with pytest.raises(ValueError):
        intake.Draft.from_tool_input(tool_input(kind="rant"), LABELS)
    with pytest.raises(ValueError):
        intake.Draft.from_tool_input(tool_input(title="  "), LABELS)


def test_issue_labels_always_include_bot_labels_and_kind():
    d = intake.Draft.from_tool_input(tool_input(kind="feature", labels=[]), LABELS)
    assert d.issue_labels(("from-discord", "needs-triage")) == [
        "enhancement",
        "from-discord",
        "needs-triage",
    ]


def test_render_bug_body():
    d = intake.Draft.from_tool_input(
        tool_input(
            engram_version="0.39.0",
            os="Windows 11",
            steps_to_reproduce="1. Insert disc\n2. Skip track",
            actual_behavior="HTTP 400 from @claude <b>",
            logs="ERROR job=4 400 Bad Request",
        ),
        LABELS,
    )
    body = intake.render_body(d, intake.Reporter("disc fan", "https://discord.com/channels/1/2"))
    assert "### Environment" in body and "| Engram version | 0.39.0 |" in body
    assert "### Steps to reproduce" in body
    assert "### Use case" not in body
    assert "<details>" in body and "ERROR job=4" in body
    assert "@claude" not in body and "<b>" not in body
    assert "https://discord.com/channels/1/2" in body
    assert "disc fan" in body


def test_render_feature_body_omits_empty_sections():
    d = intake.Draft.from_tool_input(
        tool_input(kind="feature", use_case="I rip box sets", proposed_solution=""), LABELS
    )
    body = intake.render_body(d, intake.Reporter("x", "https://t"))
    assert "### Use case" in body
    assert "### Proposed solution" not in body
    assert "### Steps to reproduce" not in body


def test_duplicate_becomes_comment():
    d = intake.Draft.from_tool_input(tool_input(duplicate_of=754), LABELS)
    assert d.is_comment
    assert intake.render_comment(d, intake.Reporter("x", "https://t")).startswith("Another report")


def test_json_round_trip_and_history_note():
    d = intake.Draft.from_tool_input(tool_input(), LABELS)
    assert intake.Draft.from_json(d.to_json()) == d
    assert "Draft 7" in intake.describe_for_history(d, 7)


def test_preview_fields_fit_discord():
    d = intake.Draft.from_tool_input(tool_input(actual_behavior="x" * 5000), LABELS)
    assert all(len(v) <= 1024 for _, v in intake.preview_fields(d))


def test_tool_schema_is_strict_and_requires_everything():
    tool = intake.draft_tool(LABELS)
    schema = tool["input_schema"]
    assert tool["strict"] is True
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["labels"]["items"]["enum"] == list(LABELS)
    assert "enum" not in intake.draft_tool(())["input_schema"]["properties"]["labels"]["items"]
