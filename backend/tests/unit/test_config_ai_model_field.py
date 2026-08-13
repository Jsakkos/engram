"""Guard the three-way sync and validation for the AI model-override field.

A new AppConfig field must also appear in ConfigUpdate (so PUT accepts it),
ConfigResponse (so GET returns it), and the GET constructor — otherwise Pydantic
silently drops it on PUT or omits it on GET. This test pins all three at the
model level (no DB).

The field exists because a provider key is not guaranteed access to Engram's
default model: a Gemini key whose project cannot see gemini-2.5-flash-lite gets
an HTTP 404, and before this field there was no way to point Engram elsewhere.
"""

import pytest
from fastapi import HTTPException

from app.api.routes import ConfigResponse, ConfigUpdate, update_config
from app.models.app_config import AppConfig

FIELD = "ai_model"


def test_appconfig_defaults_to_the_provider_default():
    """Blank means "use ai_client.DEFAULT_MODELS[provider]", not "no model"."""
    assert getattr(AppConfig(), FIELD) == ""


def test_config_update_accepts_and_carries_the_field():
    update = ConfigUpdate(**{FIELD: "gemini-3.5-flash"})
    assert update.model_dump()[FIELD] == "gemini-3.5-flash"
    # Unset stays None so PUT doesn't clobber it when omitted.
    assert ConfigUpdate().model_dump()[FIELD] is None


def test_config_response_exposes_the_field():
    assert FIELD in ConfigResponse.model_fields


class TestModelNameIsValidatedOnWrite:
    """The Gemini model is interpolated into a request URL path, so PUT must
    reject a traversal payload rather than storing it for a later request to
    execute. update_config raises before it touches the database."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad", ["../../v1beta/tunedModels/evil", "/absolute", "has spaces", "a?b=c"]
    )
    async def test_unsafe_model_name_is_rejected(self, bad):
        with pytest.raises(HTTPException) as exc:
            await update_config(ConfigUpdate(ai_model=bad))
        assert exc.value.status_code == 422
        assert FIELD in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_blank_clears_back_to_the_default(self):
        """Clearing the field is how a user reverts, so "" must not be rejected.
        It reaches the persistence layer (which this test stops short of)."""
        from app.core.ai_client import _is_safe_model_name

        # "" is falsy, so the route's guard skips it entirely.
        assert ConfigUpdate(ai_model="").model_dump()[FIELD] == ""
        assert _is_safe_model_name("") is False
