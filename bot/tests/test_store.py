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


def test_follows_relays_and_meta():
    s = Store(":memory:")
    s.add_follow(10, 1, 7, "issue")
    s.add_follow(10, 1, 7, "issue")  # idempotent per thread
    s.add_follow(10, 2, 8, "comment", "closed", "completed")
    assert [f.thread_id for f in s.follows_for(10)] == [1, 2]
    assert s.followed_issues() == {10}
    # Joining an already-closed issue never gets a "fixed in vX" notice for the old close...
    assert s.follows_awaiting_release() == []
    # ...until it's reopened and fixed again.
    joined = s.follows_for(10)[1]
    s.set_follow_state(joined.id, "open", None, None)
    s.set_follow_state(joined.id, "closed", "completed", "2026-10-12T00:00:00Z")
    assert [f.thread_id for f in s.follows_awaiting_release()] == [2]
    s.set_follow_release(joined.id, "v1")
    # A fresh follow closed as completed is announced too.
    s.add_follow(11, 3, 9, "issue")
    s.set_follow_state(s.follows_for(11)[0].id, "closed", "completed", "2026-10-11T00:00:00Z")
    assert [f.thread_id for f in s.follows_awaiting_release()] == [3]
    r = s.add_relay(500, 10, 1, 7, "Jsakkos", "u")
    assert r is not None and s.add_relay(500, 10, 1, 7, "Jsakkos", "u") is None
    s.remove_relay(r)
    assert s.relay(r) is None
    s.set_meta("k", "v")
    s.set_meta("k", "w")
    assert s.get_meta("k") == "w" and s.get_meta("missing") is None


def test_feedback_and_digest():
    s = Store(":memory:")
    s.add_thread(1, 7, "forum")
    s.add_bot_message(100, 1, "claude-haiku-5-5")
    assert s.is_bot_message(100) and not s.is_bot_message(101)
    s.set_feedback(100, 7, -1)
    s.set_feedback(100, 8, -1)
    assert s.thread_downvoted(1)
    s.set_feedback(100, 7, None)
    s.set_feedback(100, 8, 1)  # changed their mind
    assert not s.thread_downvoted(1)
    s.set_feedback(100, 9, -1)
    s.set_feedback(100, 10, -1)
    d = s.add_draft(1, 7, {"title": "Skip fails"})
    s.claim_draft(d)
    s.finish_draft(d, 760, "https://gh/760")
    digest = s.digest(time.time() - 60)
    assert digest.threads == 1 and digest.drafts == 1
    assert digest.filed == [(760, {"title": "Skip fails"}, "https://gh/760")]
    assert digest.thumbs_up == 1 and digest.thumbs_down == 2
    assert digest.downvoted == [(1, 100)]
