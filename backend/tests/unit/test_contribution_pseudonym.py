"""Per-install pseudonym generation."""

import re

from app.services.contribution_pseudonym import generate_pseudonym, validate_pseudonym


def test_generate_pseudonym_is_uuid_v4():
    p = generate_pseudonym()
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", p), p


def test_generate_pseudonym_unique():
    assert generate_pseudonym() != generate_pseudonym()


def test_validate_pseudonym_accepts_uuid():
    assert validate_pseudonym(generate_pseudonym()) is True


def test_validate_pseudonym_rejects_garbage():
    assert validate_pseudonym("not-a-uuid") is False
    assert validate_pseudonym("") is False
    assert validate_pseudonym(None) is False  # type: ignore[arg-type]
