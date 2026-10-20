"""The slice of the GitHub REST API the bot needs, for one repository.

Auth is a GitHub App installation token (minted from the app's private key and cached
until shortly before it expires) or, for simple setups, a fine-grained personal token.
The bot only ever reads issues, releases and the docs tree, and writes issues, comments
and its own two labels.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass

import httpx
import jwt

log = logging.getLogger(__name__)

API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class IssueRef:
    number: int
    title: str
    state: str
    url: str
    labels: tuple[str, ...] = ()
    body: str = ""
    comments: int = 0
    state_reason: str | None = None


@dataclass(frozen=True)
class CommentRef:
    id: int
    url: str


@dataclass(frozen=True)
class IssueChange:
    number: int
    title: str
    state: str
    state_reason: str | None
    closed_at: str | None
    url: str


@dataclass(frozen=True)
class Comment:
    id: int
    issue_number: int
    author: str
    author_is_bot: bool
    association: str  # OWNER | MEMBER | COLLABORATOR | CONTRIBUTOR | NONE ...
    body: str
    url: str
    created_at: str


def _issue(data: dict) -> IssueRef:
    return IssueRef(
        number=data["number"],
        title=data["title"],
        state=data["state"],
        url=data["html_url"],
        labels=tuple(lbl["name"] for lbl in data.get("labels", [])),
        body=data.get("body") or "",
        comments=data.get("comments", 0),
        state_reason=data.get("state_reason"),
    )


class GitHub:
    def __init__(
        self,
        repo: str,
        *,
        token: str | None = None,
        app_id: str | None = None,
        private_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token and not (app_id and private_key):
            raise ValueError("GitHub needs a token or an app id + private key")
        self.repo = repo
        self._static_token = token
        self._app_id = app_id
        self._private_key = private_key
        self._install_token: str | None = None
        self._install_expiry = 0.0
        self._http = httpx.AsyncClient(
            base_url=API,
            timeout=20.0,
            follow_redirects=True,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "engram-discord-bot",
            },
        )

    def for_repo(self, repo: str) -> GitHub:
        """A client for another repo that shares this one's credentials and connection.

        Used for the docs repo (BOT_DOCS_REPO). Token minting stays bound to this client,
        so a GitHub App only installed on the issues repo still authenticates; reading
        a public repo with that token works. Close only the original client.
        """
        if repo == self.repo:
            return self
        child = copy.copy(self)
        child.repo = repo
        child._token = self._token  # bound to self: the installation lookup uses our repo
        return child

    async def check_access(self) -> None:
        """Fail fast on bad credentials: read the repo, then its issues."""
        r = await self._request("GET", f"/repos/{self.repo}")
        self._raise(r, f"read {self.repo}")
        r = await self._request("GET", f"/repos/{self.repo}/issues", params={"per_page": 1})
        self._raise(r, f"read issues in {self.repo}")

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- auth -------------------------------------------------------------------------

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def _token(self) -> str:
        if self._static_token:
            return self._static_token
        if self._install_token and time.time() < self._install_expiry - 300:
            return self._install_token
        app_auth = {"Authorization": f"Bearer {self._app_jwt()}"}
        r = await self._http.get(f"/repos/{self.repo}/installation", headers=app_auth)
        self._raise(r, "look up the app installation")
        install_id = r.json()["id"]
        r = await self._http.post(
            f"/app/installations/{install_id}/access_tokens", headers=app_auth
        )
        self._raise(r, "mint an installation token")
        self._install_token = r.json()["token"]
        self._install_expiry = time.time() + 3600  # tokens live one hour
        return self._install_token

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {await self._token()}"
        return await self._http.request(method, path, headers=headers, **kwargs)

    @staticmethod
    def _raise(r: httpx.Response, action: str) -> None:
        if r.is_success:
            return
        try:
            message = r.json().get("message", r.text)
        except ValueError:
            message = r.text
        raise GitHubError(
            f"GitHub refused to {action}: {r.status_code} {message}", status=r.status_code
        )

    # -- issues -----------------------------------------------------------------------

    async def search_issues(self, query: str, limit: int = 6) -> list[IssueRef]:
        q = f"repo:{self.repo} is:issue {query}"
        r = await self._request("GET", "/search/issues", params={"q": q, "per_page": limit})
        self._raise(r, "search issues")
        return [_issue(item) for item in r.json().get("items", [])[:limit]]

    async def get_issue(self, number: int) -> IssueRef | None:
        r = await self._request("GET", f"/repos/{self.repo}/issues/{number}")
        if r.status_code in (404, 410):
            return None
        self._raise(r, f"read issue #{number}")
        data = r.json()
        if "pull_request" in data:
            return None
        return _issue(data)

    async def create_issue(self, title: str, body: str, labels: list[str]) -> IssueRef:
        r = await self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        self._raise(r, "create the issue")
        return _issue(r.json())

    async def create_comment(self, number: int, body: str) -> CommentRef:
        r = await self._request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": body}
        )
        self._raise(r, f"comment on #{number}")
        data = r.json()
        return CommentRef(id=data["id"], url=data["html_url"])

    async def issues_updated_since(self, since: str, max_pages: int = 5) -> list[IssueChange]:
        """Issues (not PRs) whose state, labels, body or comments changed since ``since``."""
        changes: list[IssueChange] = []
        for page in range(1, max_pages + 1):
            r = await self._request(
                "GET",
                f"/repos/{self.repo}/issues",
                params={
                    "state": "all",
                    "since": since,
                    "sort": "updated",
                    "direction": "asc",
                    "per_page": 100,
                    "page": page,
                },
            )
            self._raise(r, "list updated issues")
            batch = r.json()
            changes += [
                IssueChange(
                    number=i["number"],
                    title=i["title"],
                    state=i["state"],
                    state_reason=i.get("state_reason"),
                    closed_at=i.get("closed_at"),
                    url=i["html_url"],
                )
                for i in batch
                if "pull_request" not in i
            ]
            if len(batch) < 100:
                break
        else:
            log.warning(
                "more than %d issues changed since %s; some were skipped", max_pages * 100, since
            )
        return changes

    async def comments_since(self, since: str, max_pages: int = 5) -> list[Comment]:
        """Issue comments across the repo created or edited since ``since``, oldest first."""
        comments: list[Comment] = []
        for page in range(1, max_pages + 1):
            r = await self._request(
                "GET",
                f"/repos/{self.repo}/issues/comments",
                params={
                    "since": since,
                    "sort": "updated",
                    "direction": "asc",
                    "per_page": 100,
                    "page": page,
                },
            )
            self._raise(r, "list recent comments")
            batch = r.json()
            for c in batch:
                user = c.get("user") or {}
                comments.append(
                    Comment(
                        id=c["id"],
                        issue_number=int(c["issue_url"].rsplit("/", 1)[1]),
                        author=user.get("login", "someone"),
                        author_is_bot=user.get("type") == "Bot",
                        association=c.get("author_association", "NONE"),
                        body=c.get("body") or "",
                        url=c["html_url"],
                        created_at=c["created_at"],
                    )
                )
            if len(batch) < 100:
                break
        else:
            log.warning("more than %d comments since %s; some were skipped", max_pages * 100, since)
        return comments

    # -- labels -----------------------------------------------------------------------

    async def list_labels(self) -> set[str]:
        names: set[str] = set()
        page = 1
        while True:
            r = await self._request(
                "GET", f"/repos/{self.repo}/labels", params={"per_page": 100, "page": page}
            )
            self._raise(r, "list labels")
            batch = r.json()
            names.update(lbl["name"] for lbl in batch)
            if len(batch) < 100:
                return names
            page += 1

    async def ensure_label(self, name: str, color: str, description: str) -> None:
        r = await self._request(
            "POST",
            f"/repos/{self.repo}/labels",
            json={"name": name, "color": color, "description": description},
        )
        if r.status_code == 422:  # already exists
            return
        self._raise(r, f"create label {name!r}")

    # -- releases & files -------------------------------------------------------------

    async def latest_release(self) -> dict | None:
        r = await self._request("GET", f"/repos/{self.repo}/releases/latest")
        if r.status_code == 404:
            return None
        self._raise(r, "read the latest release")
        data = r.json()
        return {
            "tag": data["tag_name"],
            "name": data.get("name") or data["tag_name"],
            "published_at": data.get("published_at"),
            "url": data["html_url"],
        }

    async def head_commit(self, branch: str) -> str:
        r = await self._request(
            "GET",
            f"/repos/{self.repo}/commits/{branch}",
            headers={"Accept": "application/vnd.github.sha"},
        )
        self._raise(r, f"resolve {branch}")
        return r.text.strip()

    async def tree_paths(self, commit: str) -> list[str]:
        r = await self._request(
            "GET", f"/repos/{self.repo}/git/trees/{commit}", params={"recursive": "1"}
        )
        self._raise(r, "read the repository tree")
        return [e["path"] for e in r.json()["tree"] if e["type"] == "blob"]

    async def file_text(self, path: str, ref: str) -> str:
        r = await self._request(
            "GET",
            f"/repos/{self.repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        self._raise(r, f"read {path}")
        return r.text
