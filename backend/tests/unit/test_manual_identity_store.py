"""Arm store: one-shot, drive-scoped manual identity payloads."""

from unittest.mock import AsyncMock

import pytest

from app.api.websocket import ConnectionManager
from app.services.manual_identity import ArmStore, ManualIdentity


@pytest.fixture
def identity() -> ManualIdentity:
    return ManualIdentity(
        title="Arrested Development",
        content_type="tv",
        season=1,
        tmdb_id=4589,
        disc_number=2,
    )


def test_consume_returns_payload_then_clears(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.consume("E:") == identity
    # One-shot: a second insert on the same drive must not reuse it.
    assert store.consume("E:") is None


def test_peek_does_not_consume(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.peek("E:") == identity
    assert store.peek("E:") == identity
    assert store.consume("E:") == identity


def test_arm_is_drive_scoped(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.consume("F:") is None
    assert store.consume("E:") == identity


def test_disarm_reports_whether_anything_was_armed(identity):
    store = ArmStore()
    store.arm("E:", identity)

    assert store.disarm("E:") is True
    assert store.disarm("E:") is False


def test_arming_twice_replaces_the_payload(identity):
    store = ArmStore()
    store.arm("E:", identity)
    replacement = ManualIdentity(title="The Office", content_type="tv", season=2)
    store.arm("E:", replacement)

    assert store.consume("E:") == replacement


def test_to_dict_is_json_safe(identity):
    assert identity.to_dict() == {
        "title": "Arrested Development",
        "content_type": "tv",
        "season": 1,
        "tmdb_id": 4589,
        "disc_number": 2,
    }


@pytest.mark.asyncio
async def test_broadcast_drive_armed_sends_identity(identity):
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()

    await manager.broadcast_drive_armed("E:", identity.to_dict())

    manager.broadcast.assert_awaited_once_with(
        {
            "type": "drive_armed",
            "drive_id": "E:",
            "identity": {
                "title": "Arrested Development",
                "content_type": "tv",
                "season": 1,
                "tmdb_id": 4589,
                "disc_number": 2,
            },
        }
    )


@pytest.mark.asyncio
async def test_broadcast_drive_armed_none_clears():
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()

    await manager.broadcast_drive_armed("E:", None)

    manager.broadcast.assert_awaited_once_with(
        {"type": "drive_armed", "drive_id": "E:", "identity": None}
    )
