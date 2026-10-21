"""Config update model and the validation PUT /api/config and settings import share.

Split out of routes.py so ``app.services.config_transfer`` (#731) can use them
without importing routes, which imports the services layer back.
"""

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel


class ConfigUpdate(BaseModel):
    """Request model for updating configuration."""

    makemkv_path: str | None = None
    makemkv_key: str | None = None
    staging_path: str | None = None
    library_movies_path: str | None = None
    library_tv_path: str | None = None
    tmdb_api_key: str | None = None
    tvdb_api_key: str | None = None
    max_concurrent_matches: int | None = None
    enable_gpu_acceleration: bool | None = None
    # Background pre-transcription (transcript cache prewarmer)
    enable_background_pretranscription: bool | None = None
    pretranscribe_full_file: bool | None = None
    ffmpeg_path: str | None = None
    conflict_resolution_default: str | None = None
    # Analyst thresholds
    analyst_movie_min_duration: int | None = None
    analyst_tv_duration_variance: int | None = None
    analyst_tv_min_cluster_size: int | None = None
    analyst_tv_min_duration: int | None = None
    analyst_tv_max_duration: int | None = None
    analyst_movie_dominance_threshold: float | None = None
    # Ripping coordination
    ripping_file_poll_interval: float | None = None
    ripping_stability_checks: int | None = None
    ripping_file_ready_timeout: float | None = None
    # Sentinel monitoring
    sentinel_poll_interval: float | None = None
    # Stale-job watchdog
    watchdog_enabled: bool | None = None
    watchdog_poll_seconds: int | None = None
    timeout_identifying_seconds: int | None = None
    timeout_ripping_seconds: int | None = None
    timeout_matching_seconds: int | None = None
    timeout_organizing_seconds: int | None = None
    timeout_backing_up_seconds: int | None = None
    # Disc backup
    backup_before_rip: bool | None = None
    backup_path: str | None = None
    # Drive behavior
    auto_eject_enabled: bool | None = None
    # Staging cleanup
    staging_cleanup_policy: str | None = None
    staging_cleanup_days: int | None = None
    # Extras & naming
    extras_policy: str | None = None
    always_review: bool | None = None
    naming_season_format: str | None = None
    naming_episode_format: str | None = None
    naming_movie_format: str | None = None
    naming_movie_file_format: str | None = None
    naming_tv_show_format: str | None = None
    # Episode ordering (#200) — global default output ordering
    episode_ordering_preference: str | None = None
    # AI identification
    ai_identification_enabled: bool | None = None
    ai_provider: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_local_base_url: str | None = None
    ai_episode_matching_enabled: bool | None = None
    # Staging watcher
    staging_watch_enabled: bool | None = None
    # TheDiscDB
    discdb_enabled: bool | None = None
    # TheDiscDB Contributions
    discdb_contributions_enabled: bool | None = None
    discdb_contribution_tier: int | None = None
    discdb_export_path: str | None = None
    discdb_api_key: str | None = None
    discdb_api_url: str | None = None
    # OpenSubtitles.com
    opensubtitles_api_key: str | None = None
    opensubtitles_username: str | None = None
    opensubtitles_password: str | None = None
    # Import watch folder
    import_watch_path: str | None = None
    import_destination_mode: str | None = None
    # Network access
    allow_lan_access: bool | None = None
    # Onboarding
    setup_complete: bool | None = None
    # Chromaprint fingerprinting (Phase 1)
    fpcalc_path: str | None = None
    enable_fingerprint_contributions: bool | None = None
    # Chromaprint Phase 2
    fingerprint_server_url: str | None = None
    fingerprint_disclosure_accepted: bool | None = None
    # Notifications
    discord_webhook_url: str | None = None
    discord_template_completed: str | None = None
    discord_template_failed: str | None = None
    discord_template_review: str | None = None
    discord_template_ripped: str | None = None
    discord_notify_completed: bool | None = None
    discord_notify_failed: bool | None = None
    discord_notify_review: bool | None = None
    discord_notify_ripped: bool | None = None
    discord_mention_review: str | None = None
    dashboard_base_url: str | None = None


async def validate_config_update(update_data: dict, *, probe_paths: bool = True) -> dict:
    """Validate and normalize a config update the way ``PUT /api/config`` does.

    Raises ``HTTPException`` with the same status and detail PUT always has, and
    returns the normalized dict. Shared with settings import (#731) so a file
    cannot get past a check the Settings form is held to.

    ``probe_paths=False`` skips the library writability probe, the one check
    that touches the filesystem. The settings-import preview passes it for a
    network path from an uploaded file, so merely previewing a crafted file
    cannot make Windows authenticate to the host it names.
    """
    # Validate fingerprint_server_url against SSRF before persisting
    if update_data.get("fingerprint_server_url"):
        from app.core.security import is_safe_remote_url

        if not is_safe_remote_url(update_data["fingerprint_server_url"]):
            raise HTTPException(
                status_code=422,
                detail="fingerprint_server_url must be an http/https URL pointing to a non-internal host",
            )

    # Validate discord_webhook_url against SSRF before persisting
    if update_data.get("discord_webhook_url"):
        from app.core.security import is_safe_remote_url

        if not is_safe_remote_url(update_data["discord_webhook_url"]):
            raise HTTPException(
                status_code=422,
                detail="discord_webhook_url must be an http/https URL pointing to a non-internal host",
            )

    # Validate the AI model override before persisting. The Gemini adapter
    # interpolates this into a request URL path, so an unchecked value stored
    # here becomes a request-forgery primitive executed on the next match.
    # A blank value is the documented way to revert to the provider default and
    # is deliberately allowed through.
    if update_data.get("ai_model"):
        from app.core.ai_client import _is_safe_model_name

        if not _is_safe_model_name(update_data["ai_model"]):
            raise HTTPException(
                status_code=422,
                detail=(
                    "ai_model must be a plain model id such as 'gemini-2.5-flash-lite' "
                    "or 'anthropic/claude-haiku-4-5-20251001'"
                ),
            )

    # Validated on write for the same reason ai_model is: this value is
    # interpolated into an outbound request URL, so an unchecked value stored
    # here becomes a request-forgery primitive executed on the next match. A
    # blank value is the documented way to revert to the provider default and is
    # deliberately allowed through.
    if update_data.get("ai_local_base_url"):
        from app.core.security import is_safe_local_ai_url

        if not is_safe_local_ai_url(update_data["ai_local_base_url"]):
            raise HTTPException(
                status_code=422,
                detail=(
                    "ai_local_base_url must be a plain http(s) URL with no credentials "
                    "or query string, such as 'http://localhost:11434/v1'"
                ),
            )

    # Validate Discord notification templates before persisting
    from app.core.discord_notifier import validate_discord_template

    for field in (
        "discord_template_completed",
        "discord_template_failed",
        "discord_template_review",
        "discord_template_ripped",
    ):
        if update_data.get(field):
            error = validate_discord_template(update_data[field])
            if error:
                raise HTTPException(status_code=422, detail=f"{field}: {error}")

    # Validate the dashboard base URL. NOT is_safe_remote_url; see the docstring
    # on is_safe_dashboard_url. A LAN address is the expected value and the
    # server never fetches this URL.
    if update_data.get("dashboard_base_url"):
        from app.core.security import is_safe_dashboard_url

        if not is_safe_dashboard_url(update_data["dashboard_base_url"]):
            raise HTTPException(
                status_code=422,
                detail="dashboard_base_url must be an http/https URL with no embedded credentials",
            )

    # Validate naming format strings before persisting
    from app.core.organizer import (
        ALLOWED_EPISODE_PLACEHOLDERS,
        ALLOWED_MOVIE_FILE_PLACEHOLDERS,
        ALLOWED_MOVIE_PLACEHOLDERS,
        ALLOWED_TV_PLACEHOLDERS,
        ALLOWED_TV_SHOW_PLACEHOLDERS,
        validate_naming_format,
    )

    format_checks = [
        ("naming_season_format", ALLOWED_TV_PLACEHOLDERS),
        ("naming_episode_format", ALLOWED_EPISODE_PLACEHOLDERS),
        ("naming_movie_format", ALLOWED_MOVIE_PLACEHOLDERS),
        ("naming_movie_file_format", ALLOWED_MOVIE_FILE_PLACEHOLDERS),
        ("naming_tv_show_format", ALLOWED_TV_SHOW_PLACEHOLDERS),
    ]
    for field, allowed in format_checks:
        if field in update_data:
            error = validate_naming_format(update_data[field], allowed)
            if error:
                raise HTTPException(status_code=400, detail=f"{field}: {error}")

    # Tidy every path the user gave us before it is validated or stored: strip the
    # quotes Windows' "Copy as path" adds, expand ~, settle separators, and put a
    # UNC path into one canonical spelling so "//server/share" and
    # "\\server\share" are the same setting rather than two that behave alike.
    from app.core.paths import normalize_user_path

    for _field in (
        "library_movies_path",
        "library_tv_path",
        "staging_path",
        "import_watch_path",
        "subtitles_cache_path",
        "discdb_export_path",
    ):
        if update_data.get(_field):
            update_data[_field] = normalize_user_path(update_data[_field])

    # Validate library paths are actually writable before persisting (#563).
    # A path Engram cannot write to used to be accepted silently and only
    # surfaced as an opaque organize failure after a full rip: the exact
    # trap Docker users fall into with PUID/volume mismatches.
    from app.core.organizer import check_library_writable
    from app.services.config_service import get_config as get_current_config

    # Only validate a path the user actually CHANGED. ConfigWizard PUTs the full
    # config object on every save, so validating unconditionally would let a
    # transiently unreachable library (NAS reboot, externally changed perms)
    # block saving any unrelated setting until the path was fixed.
    #
    # create=False: only an EXISTING but unwritable path is rejected. Validating
    # must not create directories as a side effect, and a path on a share that
    # is not mounted yet is not an error to save.
    _path_fields = ("library_movies_path", "library_tv_path", "staging_path")
    if probe_paths and any(update_data.get(f) for f in _path_fields):
        _current = await get_current_config()
        for field in _path_fields:
            new_value = update_data.get(field)
            if not new_value or new_value == getattr(_current, field, None):
                continue
            reason = await asyncio.to_thread(check_library_writable, new_value, create=False)
            if reason:
                raise HTTPException(status_code=422, detail=f"{field}: {reason}")

    # Validate extras_policy
    if "extras_policy" in update_data:
        if update_data["extras_policy"] not in ("keep", "skip", "ask"):
            raise HTTPException(
                status_code=400,
                detail="extras_policy must be 'keep', 'skip', or 'ask'",
            )

    # Validate episode_ordering_preference (#200) — reject absolute (deferred) and unknowns
    if "episode_ordering_preference" in update_data:
        from app.core.episode_ordering import ALLOWED_ORDERINGS

        if update_data["episode_ordering_preference"] not in ALLOWED_ORDERINGS:
            raise HTTPException(
                status_code=422,
                detail=f"episode_ordering_preference must be one of {sorted(ALLOWED_ORDERINGS)}",
            )

    return update_data
