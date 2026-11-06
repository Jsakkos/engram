"""Settings export and import (#731).

The file carries every user-editable setting except per-install state, keeps
secrets out unless the user asks for them, and goes back in through the same
validators as PUT /api/config. These tests pin each of those promises.
"""

import json

import pytest
from loguru import logger

from app.api.routes import ConfigUpdate
from app.models.app_config import AppConfig
from app.services import config_transfer as ct
from app.services.config_service import get_config
from tests.unit.conftest import _unit_session_factory

TOKEN = "eyJsecret-token-value"


async def _seed(**fields) -> None:
    async with _unit_session_factory() as session:
        session.add(AppConfig(**fields))
        await session.commit()


async def _reset_to_defaults(**keep) -> None:
    """Replace the config row with a fresh default one, as on a new install."""
    from sqlalchemy import delete

    async with _unit_session_factory() as session:
        await session.execute(delete(AppConfig))
        session.add(AppConfig(**keep))
        await session.commit()


def _file(settings: dict, **meta) -> dict:
    return {
        "format": "engram-settings",
        "schema_version": 1,
        "engram_version": "0.41.0",
        "includes_secrets": False,
        "settings": settings,
        **meta,
    }


def _row(preview: dict, key: str) -> dict:
    return next(f for f in preview["fields"] if f["key"] == key)


def test_every_config_update_field_is_classified():
    """A new setting is exported by default; dropping it needs a deliberate exclusion."""
    exportable = set(ct.exportable_fields())
    assert set(ConfigUpdate.model_fields) == exportable | ct.EXCLUDED_FIELDS
    assert not exportable & ct.EXCLUDED_FIELDS
    assert ct.SECRET_FIELDS <= exportable
    assert ct.PATH_FIELDS <= exportable


def test_secret_set_is_the_one_config_service_protects():
    """update_config's never-blank-a-secret rule and the export opt-in must agree."""
    assert ct.SECRET_FIELDS == {
        "makemkv_key",
        "tmdb_api_key",
        "tvdb_api_key",
        "ai_api_key",
        "opensubtitles_api_key",
        "opensubtitles_password",
        "discdb_api_key",
        "discord_webhook_url",
    }


async def test_export_without_secrets_omits_secrets_and_excluded(tmp_path):
    await _seed(tmdb_api_key=TOKEN, staging_path=str(tmp_path), setup_complete=True)
    data = await ct.build_export(include_secrets=False)

    assert data["format"] == "engram-settings"
    assert data["schema_version"] == 1
    assert data["includes_secrets"] is False
    assert data["engram_version"]
    assert data["exported_at"]
    assert not (ct.SECRET_FIELDS | ct.EXCLUDED_FIELDS) & set(data["settings"])
    assert data["settings"]["staging_path"] == str(tmp_path)
    assert TOKEN not in json.dumps(data)


async def test_export_omits_unset_values_except_clearable_ones():
    """A null in the file means "leave alone" except for the two clearable fields,
    so an unset fpcalc_path must not be written as a null that import then refuses."""
    await _seed(fpcalc_path=None, import_watch_path=None)
    settings = (await ct.build_export(include_secrets=False))["settings"]
    assert "fpcalc_path" not in settings
    assert "import_watch_path" in settings
    assert settings["import_watch_path"] is None


async def test_export_with_secrets_includes_them():
    await _seed(tmdb_api_key=TOKEN)
    data = await ct.build_export(include_secrets=True)
    assert data["includes_secrets"] is True
    assert data["settings"]["tmdb_api_key"] == TOKEN
    assert not ct.EXCLUDED_FIELDS & set(data["settings"])


async def test_round_trip_restores_non_secret_settings(tmp_path):
    """The acceptance criterion: export, fresh install, import, everything is back."""
    movies, tv, staging = tmp_path / "movies", tmp_path / "tv", tmp_path / "staging"
    for p in (movies, tv, staging):
        p.mkdir()
    seeded = {
        "library_movies_path": str(movies),
        "library_tv_path": str(tv),
        "staging_path": str(staging),
        "extras_policy": "skip",
        "always_review": True,
        "naming_tv_show_format": "{show} ({year})",
        "discord_notify_ripped": True,
        "discord_template_completed": "Done: {{title}}",
        "max_concurrent_matches": 4,
        "analyst_movie_dominance_threshold": 0.75,
        "opensubtitles_username": "someone",
        "conflict_resolution_default": "skip",
    }
    await _seed(**seeded)
    exported = await ct.build_export(include_secrets=False)

    await _reset_to_defaults()
    keys = list(exported["settings"])
    applied = await ct.apply_import(exported, keys)

    assert set(applied) == set(keys)
    config = await get_config()
    for key, value in seeded.items():
        assert getattr(config, key) == value, key


async def test_preview_unknown_keys_listed_not_applied():
    await _seed()
    preview = await ct.preview_import(_file({"future_thing": 1}))
    assert preview["unknown"] == ["future_thing"]
    assert not any(f["key"] == "future_thing" for f in preview["fields"])
    with pytest.raises(ct.ImportRejected) as exc:
        await ct.apply_import(_file({"future_thing": 1}), ["future_thing"])
    assert "future_thing" in exc.value.errors


async def test_excluded_keys_in_file_are_not_offered():
    await _seed()
    preview = await ct.preview_import(_file({"allow_lan_access": True}))
    assert not any(f["key"] == "allow_lan_access" for f in preview["fields"])
    assert "allow_lan_access" in preview["unknown"]


async def test_preview_invalid_values_use_put_messages():
    await _seed()
    preview = await ct.preview_import(
        _file(
            {
                "naming_episode_format": "{nope}",
                "extras_policy": "maybe",
                "discord_webhook_url": "http://127.0.0.1/hook",
            },
            includes_secrets=True,
        )
    )
    naming = _row(preview, "naming_episode_format")
    assert naming["status"] == "invalid"
    assert naming["selected"] is False
    assert "naming_episode_format" in naming["error"]

    extras = _row(preview, "extras_policy")
    assert extras["status"] == "invalid"
    assert "extras_policy" in extras["error"]

    hook = _row(preview, "discord_webhook_url")
    assert hook["status"] == "invalid"
    assert "discord_webhook_url" in hook["error"]
    # A rejected secret is still never echoed back.
    assert "127.0.0.1" not in json.dumps(preview)


async def test_preview_type_error_is_field_error():
    await _seed()
    preview = await ct.preview_import(_file({"max_concurrent_matches": "three"}))
    row = _row(preview, "max_concurrent_matches")
    assert row["status"] == "invalid"
    assert row["error"]
    assert row["selected"] is False


async def test_preview_secret_rows_carry_no_values():
    await _seed(tmdb_api_key="eyJstored")
    preview = await ct.preview_import(_file({"tmdb_api_key": TOKEN}, includes_secrets=True))
    row = _row(preview, "tmdb_api_key")
    assert row["secret"] is True
    assert row["status"] == "change"
    assert row["current"] == "***"
    assert row["incoming"] == "***"
    assert row["selected"] is True
    dumped = json.dumps(preview)
    assert TOKEN not in dumped
    assert "eyJstored" not in dumped


async def test_preview_same_secret_is_unchanged():
    await _seed(tmdb_api_key=TOKEN)
    preview = await ct.preview_import(_file({"tmdb_api_key": TOKEN}))
    assert _row(preview, "tmdb_api_key")["status"] == "same"


async def test_preview_missing_path_unselected(tmp_path):
    await _seed()
    missing = str(tmp_path / "not-here" / "TV")
    preview = await ct.preview_import(_file({"library_tv_path": missing}))
    row = _row(preview, "library_tv_path")
    assert row["status"] == "change"
    assert row["path_missing"] is True
    assert row["selected"] is False


async def test_preview_existing_path_selected(tmp_path):
    await _seed()
    preview = await ct.preview_import(_file({"library_tv_path": str(tmp_path)}))
    row = _row(preview, "library_tv_path")
    assert row["path_missing"] is False
    assert row["selected"] is True


async def test_preview_same_value_is_unchanged():
    await _seed(extras_policy="skip")
    preview = await ct.preview_import(_file({"extras_policy": "skip"}))
    row = _row(preview, "extras_policy")
    assert row["status"] == "same"
    assert row["selected"] is False


@pytest.mark.parametrize(
    "bad",
    [
        [],
        "text",
        {"format": "other", "settings": {}},
        {"format": "engram-settings", "settings": []},
        {"format": "engram-settings"},
    ],
)
def test_parse_rejects_bad_files(bad):
    with pytest.raises(ct.SettingsFileError):
        ct.parse_settings_file(bad)


def test_parse_warns_on_newer_schema():
    settings, warnings = ct.parse_settings_file(_file({}, schema_version=99))
    assert settings == {}
    assert warnings


async def test_apply_rejects_excluded_and_invalid_atomically():
    await _seed(extras_policy="keep")
    with pytest.raises(ct.ImportRejected) as exc:
        await ct.apply_import(
            _file({"extras_policy": "skip", "allow_lan_access": True}),
            ["extras_policy", "allow_lan_access"],
        )
    assert "allow_lan_access" in exc.value.errors
    assert (await get_config()).extras_policy == "keep"

    with pytest.raises(ct.ImportRejected) as exc:
        await ct.apply_import(
            _file({"extras_policy": "skip", "naming_movie_format": "{bad}"}),
            ["extras_policy", "naming_movie_format"],
        )
    assert "naming_movie_format" in exc.value.errors
    assert (await get_config()).extras_policy == "keep"


async def test_apply_rejects_key_not_in_file():
    await _seed()
    with pytest.raises(ct.ImportRejected) as exc:
        await ct.apply_import(_file({}), ["extras_policy"])
    assert "extras_policy" in exc.value.errors


async def test_apply_only_writes_selected_keys():
    await _seed(extras_policy="keep", always_review=False)
    await ct.apply_import(
        _file({"extras_policy": "skip", "always_review": True}), ["extras_policy"]
    )
    config = await get_config()
    assert config.extras_policy == "skip"
    assert config.always_review is False


async def test_apply_leaves_unmentioned_settings():
    await _seed(always_review=True, extras_policy="keep")
    await ct.apply_import(_file({"extras_policy": "skip"}), ["extras_policy"])
    config = await get_config()
    assert config.always_review is True
    assert config.extras_policy == "skip"


async def test_apply_empty_secret_does_not_blank():
    await _seed(tmdb_api_key=TOKEN)
    preview = await ct.preview_import(_file({"tmdb_api_key": ""}))
    assert not any(f["key"] == "tmdb_api_key" for f in preview["fields"])
    await ct.apply_import(_file({"tmdb_api_key": "", "extras_policy": "skip"}), ["extras_policy"])
    assert (await get_config()).tmdb_api_key == TOKEN


async def test_null_clears_only_nullable_fields(tmp_path):
    await _seed(import_watch_path=str(tmp_path), extras_policy="skip")
    preview = await ct.preview_import(_file({"import_watch_path": None, "extras_policy": None}))
    assert _row(preview, "import_watch_path")["status"] == "change"
    assert not any(f["key"] == "extras_policy" for f in preview["fields"])
    await ct.apply_import(_file({"import_watch_path": None}), ["import_watch_path"])
    assert (await get_config()).import_watch_path is None


async def test_secrets_never_logged():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        await _seed(tmdb_api_key="eyJstored")
        exported = await ct.build_export(include_secrets=True)
        await ct.preview_import(_file({"tmdb_api_key": TOKEN}, includes_secrets=True))
        await ct.apply_import(
            _file({"tmdb_api_key": TOKEN}, includes_secrets=True), ["tmdb_api_key"]
        )
    finally:
        logger.remove(sink)
    assert exported["settings"]["tmdb_api_key"] == "eyJstored"
    text = "\n".join(lines)
    assert TOKEN not in text
    assert "eyJstored" not in text
    assert (await get_config()).tmdb_api_key == TOKEN


async def test_preview_never_probes_network_paths(monkeypatch):
    """A crafted file naming a UNC share must not make Windows authenticate to
    that host before the user has even seen the preview."""
    import app.core.organizer as organizer

    touched: list[str] = []
    monkeypatch.setattr(ct.os.path, "exists", lambda p: touched.append(p) or False)
    monkeypatch.setattr(
        organizer, "check_library_writable", lambda p, **k: touched.append(str(p)) or None
    )
    await _seed()
    unc = "\\" * 2 + "attacker.example" + "\\" + "share" + "\\" + "TV"
    preview = await ct.preview_import(_file({"library_tv_path": unc, "ffmpeg_path": unc}))
    assert not any("attacker" in t for t in touched), touched
    for key in ("library_tv_path", "ffmpeg_path"):
        row = _row(preview, key)
        assert row["status"] == "change"
        assert row["path_unchecked"] is True
        assert row["path_missing"] is False
        assert row["selected"] is False


async def test_local_path_rows_are_checked(tmp_path):
    await _seed()
    row = _row(
        await ct.preview_import(_file({"library_tv_path": str(tmp_path)})), "library_tv_path"
    )
    assert row["path_unchecked"] is False


@pytest.mark.parametrize(
    "path",
    [
        "BS??BSUNCBSattacker.exampleBSshare",  # NT namespace: no leading double backslash
        "BSBS.BSUNCBSattacker.exampleBSshare",  # device namespace
        "BSBS?BSUNCBSattacker.exampleBSshare",  # long-path prefix
        "BSDeviceBSMupBSattacker.exampleBSshare",
    ],
)
async def test_preview_never_probes_namespace_prefixed_paths(monkeypatch, path):
    """Windows reaches the network through these spellings too, and none of them
    starts with two separators, so a UNC check alone lets them through."""
    import app.core.organizer as organizer

    touched: list[str] = []
    monkeypatch.setattr(ct.os.path, "exists", lambda p: touched.append(p) or False)
    monkeypatch.setattr(
        organizer, "check_library_writable", lambda p, **k: touched.append(str(p)) or None
    )
    await _seed()
    path = path.replace("BS", chr(92))
    preview = await ct.preview_import(_file({"staging_path": path}))
    assert not any("attacker" in t for t in touched), touched
    row = _row(preview, "staging_path")
    assert row["path_unchecked"] is True
    assert row["selected"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("discdb_api_url", "https://evil.example.com"),
        ("ai_local_base_url", "http://evil.example.com:11434/v1"),
        ("fingerprint_server_url", "https://evil.example.com"),
    ],
)
async def test_endpoint_rows_need_review_and_start_unticked(key, value):
    """A shared file that repoints an endpoint would send the key this install
    already holds to a host the file chose, so the user must opt in per row."""
    await _seed()
    row = _row(await ct.preview_import(_file({key: value})), key)
    assert row["status"] == "change"
    assert row["needs_review"] is True
    assert row["selected"] is False


async def test_program_path_rows_need_review(tmp_path):
    """A tool path names a program Engram runs, so it is never pre-ticked even
    when it exists on this machine."""
    exe = tmp_path / "makemkvcon64.exe"
    exe.write_text("")
    await _seed()
    row = _row(await ct.preview_import(_file({"makemkv_path": str(exe)})), "makemkv_path")
    assert row["path_missing"] is False
    assert row["needs_review"] is True
    assert row["selected"] is False


async def test_ordinary_rows_do_not_need_review():
    await _seed(extras_policy="keep")
    row = _row(await ct.preview_import(_file({"extras_policy": "skip"})), "extras_policy")
    assert row["needs_review"] is False
    assert row["selected"] is True


async def test_webhook_row_needs_review_and_shows_only_the_host():
    """The webhook decides where notifications go, so it is opted into like the
    endpoints. Its path carries the token, so only the host is shown."""
    hook = "https://discord.com/api/webhooks/123/secret-token-part"
    await _seed(discord_webhook_url="https://discord.com/api/webhooks/1/old")
    preview = await ct.preview_import(_file({"discord_webhook_url": hook}, includes_secrets=True))
    row = _row(preview, "discord_webhook_url")
    assert row["needs_review"] is True
    assert row["selected"] is False
    assert row["incoming"] == "***"
    assert row["incoming_host"] == "discord.com"
    assert "secret-token-part" not in json.dumps(preview)


async def test_other_rows_have_no_incoming_host():
    await _seed(tmdb_api_key="eyJold")
    preview = await ct.preview_import(_file({"tmdb_api_key": TOKEN, "extras_policy": "skip"}))
    assert _row(preview, "tmdb_api_key")["incoming_host"] is None
    assert _row(preview, "tmdb_api_key")["selected"] is True
    assert _row(preview, "extras_policy")["incoming_host"] is None
