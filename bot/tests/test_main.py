import httpx
import pytest

from engram_bot.__main__ import credential_problem
from engram_bot.config import Settings
from engram_bot.github import GitHub


def gh_returning(status: int, json=None, **kwargs) -> GitHub:
    def handler(request: httpx.Request):
        return httpx.Response(status, json=json if json is not None else {"message": "x"})

    return GitHub("me/test", transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_rejected_token_is_fatal_and_names_the_variable(status):
    settings = Settings(discord_token="d", github_repo="me/test", github_token="t")
    problem = await credential_problem(gh_returning(status, token="t"), settings)
    assert problem and "GITHUB_TOKEN" in problem and "Issues: Read and write" in problem
    assert "Contents: Read-only if the repo is private" in problem


async def test_rejected_app_names_the_app_variables():
    settings = Settings(
        discord_token="d", github_repo="me/test", github_app_id="1", github_app_private_key="k"
    )
    gh = gh_returning(404, app_id="1", private_key="not a pem key")
    problem = await credential_problem(gh, settings)
    assert problem and "GITHUB_APP_ID" in problem and "installed on me/test" in problem


async def test_server_errors_and_success_are_not_fatal():
    settings = Settings(discord_token="d", github_repo="me/test", github_token="t")
    assert await credential_problem(gh_returning(502, token="t"), settings) is None
    assert await credential_problem(gh_returning(200, json=[], token="t"), settings) is None
