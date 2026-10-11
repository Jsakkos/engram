from datetime import UTC, datetime, timedelta

from engram_bot.followup import FollowUp
from engram_bot.github import Comment, GitHubError, IssueChange
from engram_bot.store import Store

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class FakeGitHub:
    def __init__(self):
        self.issues: list[IssueChange] = []
        self.comments: list[Comment] = []
        self.release = {"tag": "v0.39.0", "published_at": "2026-10-09T00:00:00Z", "url": "u"}
        self.fail = False
        self.since_seen = []

    async def issues_updated_since(self, since):
        if self.fail:
            raise GitHubError("boom")
        self.since_seen.append(since)
        return list(self.issues)

    async def comments_since(self, since):
        return list(self.comments)

    async def latest_release(self):
        return self.release


class Sink:
    def __init__(self):
        self.sent: list[tuple[int, str, int | None]] = []
        self.result: int | None = 1

    async def __call__(self, thread_id, content, relay_id):
        self.sent.append((thread_id, content, relay_id))
        return self.result


def issue(n=10, state="open", reason=None, closed_at=None):
    return IssueChange(n, f"Issue {n}", state, reason, closed_at, f"https://gh/issues/{n}")


def comment(id=500, n=10, assoc="OWNER", bot=False, created="2030-01-01T00:00:00Z", body="hi?"):
    return Comment(id, n, "Jsakkos", bot, assoc, body, f"https://gh/c/{id}", created)


async def setup(follows=((10, 1),)):
    gh, sink, store = FakeGitHub(), Sink(), Store(":memory:")
    for number, thread in follows:
        store.add_follow(number, thread, 7, "issue")
    fu = FollowUp(gh, store, sink)
    await fu.poll(T0)  # first run: sets the cursor and the release baseline only
    assert sink.sent == []
    return gh, sink, store, fu


async def test_closed_as_completed_then_released():
    gh, sink, store, fu = await setup()
    gh.issues = [issue(state="closed", reason="completed", closed_at="2026-10-11T12:30:00Z")]
    await fu.poll(T0 + timedelta(hours=1))
    assert len(sink.sent) == 1 and "closed as completed" in sink.sent[0][1]

    await fu.poll(T0 + timedelta(hours=2))  # same state again: no repeat
    assert len(sink.sent) == 1

    gh.release = {"tag": "v0.40.0", "published_at": "2026-10-12T00:00:00Z", "url": "rel"}
    await fu.poll(T0 + timedelta(hours=3))
    assert "v0.40.0" in sink.sent[-1][1] and "#10" in sink.sent[-1][1]
    await fu.poll(T0 + timedelta(hours=4))
    assert len(sink.sent) == 2  # announced once


async def test_release_cut_before_the_fix_is_not_announced():
    gh, sink, store, fu = await setup()
    gh.issues = [issue(state="closed", reason="completed", closed_at="2026-10-13T00:00:00Z")]
    gh.release = {"tag": "v0.40.0", "published_at": "2026-10-12T00:00:00Z", "url": "rel"}
    await fu.poll(T0 + timedelta(hours=1))
    assert not any("v0.40.0" in text for _, text, _ in sink.sent)


async def test_not_planned_duplicate_and_reopen_messages():
    gh, sink, store, fu = await setup()
    gh.issues = [issue(state="closed", reason="not_planned")]
    await fu.poll(T0 + timedelta(hours=1))
    gh.issues = [issue(state="open")]
    await fu.poll(T0 + timedelta(hours=2))
    gh.issues = [issue(state="closed", reason="duplicate")]
    await fu.poll(T0 + timedelta(hours=3))
    texts = [t for _, t, _ in sink.sent]
    assert "not planned" in texts[0] and "reopened" in texts[1] and "duplicate" in texts[2]


async def test_unfollowed_issues_are_ignored():
    gh, sink, store, fu = await setup()
    gh.issues = [issue(n=99, state="closed", reason="completed")]
    gh.comments = [comment(n=99)]
    await fu.poll(T0 + timedelta(hours=1))
    assert sink.sent == []


async def test_maintainer_comments_are_relayed_once_with_a_reply_button():
    gh, sink, store, fu = await setup(follows=((10, 1), (10, 2)))
    gh.comments = [
        comment(id=1, body="Which drive model?"),
        comment(id=2, assoc="NONE", body="me too"),  # random user
        comment(id=3, bot=True),  # a bot
        comment(id=4, created="2000-01-01T00:00:00Z"),  # before the report was filed
    ]
    store.add_own_comment(5)
    gh.comments.append(comment(id=5))  # the bot's own comment (token-auth setups)
    await fu.poll(T0 + timedelta(hours=1))
    assert [(t, r is not None) for t, _, r in sink.sent] == [(1, True), (2, True)]
    assert "Which drive model?" in sink.sent[0][1]
    relay = store.relay(sink.sent[0][2])
    assert relay.issue_number == 10 and relay.reporter_id == 7

    await fu.poll(T0 + timedelta(hours=2))  # edited/overlapping: still once
    assert len(sink.sent) == 2


async def test_failed_sends_are_retried():
    gh, sink, store, fu = await setup()
    sink.result = None
    gh.issues = [issue(state="closed", reason="completed")]
    gh.comments = [comment()]
    await fu.poll(T0 + timedelta(hours=1))
    sink.result = 1
    await fu.poll(T0 + timedelta(hours=2))
    texts = [t for _, t, _ in sink.sent]
    assert sum("closed as completed" in t for t in texts) == 2
    assert sum("commented" in t for t in texts) == 2


async def test_github_errors_keep_the_cursor():
    gh, sink, store, fu = await setup()
    gh.fail = True
    await fu.poll(T0 + timedelta(hours=1))
    assert store.get_meta("poll_since") == "2026-10-11T12:00:00Z"
    gh.fail = False
    await fu.poll(T0 + timedelta(hours=2))
    assert gh.since_seen[-1] == "2026-10-11T11:58:00Z"  # old cursor minus the overlap


async def test_long_comments_are_trimmed_and_quoted():
    gh, sink, store, fu = await setup()
    gh.comments = [comment(body="line one\n\n" + "x" * 3000)]
    await fu.poll(T0 + timedelta(hours=1))
    text = sink.sent[0][1]
    assert "> line one" in text and len(text) < 2000
