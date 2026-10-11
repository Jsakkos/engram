import time

from engram_bot.store import Store


def test_threads_and_mute():
    s = Store(":memory:")
    s.add_thread(1, 10, "forum")
    s.add_thread(1, 99, "mention")  # first registration wins
    assert s.thread(1).reporter_id == 10
    s.set_muted(1, True)
    assert s.thread(1).muted
    assert s.thread(2) is None


def test_draft_lifecycle_files_once():
    s = Store(":memory:")
    d = s.add_draft(1, 10, {"kind": "bug"})
    s.set_draft_message(d, 555)
    assert s.drafts_by_message([555])[555].id == d
    assert s.claim_draft(d)
    assert not s.claim_draft(d)  # double click
    s.release_draft(d)
    assert s.claim_draft(d)
    s.finish_draft(d, 760, "https://x")
    assert s.draft(d).status == "filed"
    assert s.issues_filed_since(10, time.time() - 60) == 1
    assert not s.cancel_draft(d)


def test_supersede_cancels_only_pending():
    s = Store(":memory:")
    a = s.add_draft(1, 10, {})
    b = s.add_draft(1, 10, {})
    s.claim_draft(b)
    assert [r.id for r in s.supersede_drafts(1)] == [a]
    assert s.draft(a).status == "cancelled"
    assert s.draft(b).status == "filing"


def test_usage_counters():
    s = Store(":memory:")
    for _ in range(3):
        s.record_usage(
            user_id=10,
            thread_id=1,
            input_tokens=1,
            output_tokens=1,
            cache_read=0,
            cache_write=0,
            cost_usd=0.5,
        )
    assert s.spend_since(time.time() - 60) == 1.5
    assert s.turns_since(10, time.time() - 60) == 3
    assert s.turns_since(11, time.time() - 60) == 0
