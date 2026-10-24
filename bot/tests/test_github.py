import json

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from engram_bot.github import GitHub, GitHubError


def issue_json(n, **over):
    data = {
        "number": n,
        "title": f"Issue {n}",
        "state": "open",
        "html_url": f"https://github.com/o/r/issues/{n}",
        "labels": [{"name": "bug"}],
        "body": "body",
        "comments": 0,
        "state_reason": None,
    }
    data.update(over)
    return data


async def test_search_scopes_query_to_repo_issues():
    seen = {}

    def handler(request: httpx.Request):
        seen["q"] = request.url.params["q"]
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"items": [issue_json(1), issue_json(2)]})

    gh = GitHub("o/r", token="t0k", transport=httpx.MockTransport(handler))
    hits = await gh.search_issues("skip track 400")
    assert seen["q"] == "repo:o/r is:issue skip track 400"
    assert seen["auth"] == "Bearer t0k"
    assert [h.number for h in hits] == [1, 2]


async def test_create_issue_and_errors():
    def handler(request: httpx.Request):
        if request.url.path == "/repos/o/r/issues":
            body = json.loads(request.content)
            assert body["labels"] == ["bug", "from-discord"]
            return httpx.Response(201, json=issue_json(800, title=body["title"]))
        return httpx.Response(403, json={"message": "Resource not accessible"})

    gh = GitHub("o/r", token="t", transport=httpx.MockTransport(handler))
    issue = await gh.create_issue("T", "B", ["bug", "from-discord"])
    assert issue.number == 800 and issue.title == "T"
    try:
        await gh.create_comment(800, "x")
    except GitHubError as e:
        assert "403" in str(e) and "Resource not accessible" in str(e)
    else:
        raise AssertionError("expected GitHubError")


async def test_get_issue_hides_pull_requests_and_missing():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/5"):
            return httpx.Response(200, json=issue_json(5, pull_request={}))
        return httpx.Response(404, json={"message": "Not Found"})

    gh = GitHub("o/r", token="t", transport=httpx.MockTransport(handler))
    assert await gh.get_issue(5) is None
    assert await gh.get_issue(6) is None


async def test_app_auth_mints_and_caches_installation_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key()
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        auth = request.headers["Authorization"].removeprefix("Bearer ")
        if request.url.path == "/repos/o/r/installation":
            assert jwt.decode(auth, public, algorithms=["RS256"])["iss"] == "123"
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/app/installations/42/access_tokens":
            return httpx.Response(201, json={"token": "ghs_inst"})
        assert auth == "ghs_inst"
        return httpx.Response(200, json=[])

    gh = GitHub("o/r", app_id="123", private_key=pem, transport=httpx.MockTransport(handler))
    await gh.list_labels()
    await gh.list_labels()
    assert calls.count("/app/installations/42/access_tokens") == 1


async def test_polling_endpoints_parse_and_skip_prs():
    def handler(request: httpx.Request):
        assert request.url.params["since"] == "2026-10-11T00:00:00Z"
        if request.url.path == "/repos/o/r/issues":
            return httpx.Response(
                200,
                json=[
                    issue_json(1, state="closed", state_reason="completed", closed_at="x"),
                    issue_json(2, pull_request={}),
                ],
            )
        if request.url.path == "/repos/o/r/issues/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9,
                        "issue_url": "https://api.github.com/repos/o/r/issues/1",
                        "user": {"login": "Jsakkos", "type": "User"},
                        "author_association": "OWNER",
                        "body": "which drive?",
                        "html_url": "https://gh/c/9",
                        "created_at": "2026-10-11T01:00:00Z",
                    }
                ],
            )
        return httpx.Response(404)

    gh = GitHub("o/r", token="t", transport=httpx.MockTransport(handler))
    changes = await gh.issues_updated_since("2026-10-11T00:00:00Z")
    assert [(c.number, c.state_reason) for c in changes] == [(1, "completed")]
    (c,) = await gh.comments_since("2026-10-11T00:00:00Z")
    assert (c.issue_number, c.author, c.association, c.author_is_bot) == (
        1,
        "Jsakkos",
        "OWNER",
        False,
    )


async def test_create_comment_returns_id():
    def handler(request: httpx.Request):
        return httpx.Response(201, json={"id": 77, "html_url": "https://gh/c/77"})

    gh = GitHub("o/r", token="t", transport=httpx.MockTransport(handler))
    ref = await gh.create_comment(1, "x")
    assert (ref.id, ref.url) == (77, "https://gh/c/77")


async def test_docs_client_reads_its_repo_with_the_issue_repos_app_token():
    """The app is installed on the test repo only; Engram's public docs must still load."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.url.path, request.headers["Authorization"]))
        if request.url.path == "/repos/me/engram-bot-test/installation":
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/repos/Jsakkos/engram/installation":
            return httpx.Response(404, json={"message": "Not Found"})  # not installed there
        if request.url.path == "/app/installations/42/access_tokens":
            return httpx.Response(201, json={"token": "ghs_inst"})
        if request.url.path == "/repos/Jsakkos/engram/releases/latest":
            return httpx.Response(
                200, json={"tag_name": "v1", "published_at": None, "html_url": "u"}
            )
        return httpx.Response(404, json={"message": "unexpected"})

    issues = GitHub(
        "me/engram-bot-test", app_id="1", private_key=pem, transport=httpx.MockTransport(handler)
    )
    docs = issues.for_repo("Jsakkos/engram")
    assert docs.repo == "Jsakkos/engram" and issues.repo == "me/engram-bot-test"
    assert (await docs.latest_release())["tag"] == "v1"
    assert ("/repos/Jsakkos/engram/releases/latest", "Bearer ghs_inst") in calls
    assert not any(path == "/repos/Jsakkos/engram/installation" for path, _ in calls)
    assert issues.for_repo("me/engram-bot-test") is issues


async def test_check_access_reports_the_status():
    def handler(request: httpx.Request):
        return httpx.Response(401, json={"message": "Bad credentials"})

    gh = GitHub("o/r", token="not-a-github-token", transport=httpx.MockTransport(handler))
    try:
        await gh.check_access()
    except GitHubError as e:
        assert e.status == 401 and "Bad credentials" in str(e)
    else:
        raise AssertionError("expected GitHubError")
