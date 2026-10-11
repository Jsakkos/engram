from types import SimpleNamespace as NS

import anthropic
import httpx

from engram_bot import intake
from engram_bot.agent import DRAFT_TEXT, FALLBACK_TEXT, REFUSAL_TEXT, Agent, Prices
from engram_bot.github import IssueRef
from engram_bot.prompts import TurnContext

LABELS = ("bug", "ux")


def usage(inp=10, out=5, read=0, write=0):
    return NS(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


def text(t):
    return NS(type="text", text=t)


def tool(name, args, id="t1"):
    return NS(type="tool_use", name=name, input=args, id=id)


def reply(*blocks, stop="end_turn", u=None):
    return NS(content=list(blocks), stop_reason=stop, usage=u or usage())


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        # The agent keeps appending to its list; snapshot what this call actually sent.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeGitHub:
    async def search_issues(self, query, limit=6):
        return [IssueRef(754, "Skip fails with HTTP 400", "open", "https://gh/754")]

    async def get_issue(self, number):
        return IssueRef(number, "t", "closed", "https://gh", state_reason="completed")


def make_agent(responses):
    messages = FakeMessages(responses)
    agent = Agent(
        NS(messages=messages),
        FakeGitHub(),
        model="claude-haiku-5-5",
        effort="medium",
        max_tokens=8000,
        system_prompt="SYSTEM",
        allowed_labels=LABELS,
        prices=Prices(input=0.10, output=0.50, cache_read=0.01, cache_write=0.20),
    )
    return agent, messages


CONVO = [{"role": "user", "content": "ann: how do I review?"}]


def draft_args(**over):
    data = {name: "" for name in intake._STRING_FIELDS}
    data.update(kind="bug", title="T", summary="S", labels=["ux"], duplicate_of=0)
    data.update(over)
    return data


async def test_plain_answer_request_shape():
    agent, fake = make_agent([reply(text("Open the Review tab."))])
    result = await agent.respond(CONVO, TurnContext(reporter_name="ann"), "KNOWLEDGE")
    assert result.text == "Open the Review tab."
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"] == {"effort": "medium"}
    assert "temperature" not in call  # rejected by Haiku 5.5
    # Stable instructions first, then the cached knowledge block.
    assert call["system"][0]["text"] == "SYSTEM"
    assert call["system"][1]["text"] == "KNOWLEDGE"
    assert call["system"][1]["cache_control"]["ttl"] == "1h"
    # Per-turn context rides as a trailing system message, not in the cached prefix.
    assert call["messages"][-1]["role"] == "system"
    assert "ann" in call["messages"][-1]["content"]
    assert [t["name"] for t in call["tools"]] == ["search_issues", "get_issue", "draft_issue"]


async def test_search_then_answer():
    agent, fake = make_agent(
        [
            reply(tool("search_issues", {"query": "skip 400"}), stop="tool_use"),
            reply(text("That's #754.")),
        ]
    )
    result = await agent.respond(CONVO, TurnContext(), "K")
    assert result.text == "That's #754."
    assert result.tools_used == ["search_issues"]
    second = fake.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert "#754" in second[-1]["content"][0]["content"]
    assert second[-2]["role"] == "assistant"


async def test_draft_ends_the_turn_without_an_extra_call():
    agent, fake = make_agent(
        [reply(text("Here you go."), tool("draft_issue", draft_args()), stop="tool_use")]
    )
    result = await agent.respond(CONVO, TurnContext(), "K")
    assert result.draft is not None and result.draft.labels == ["ux"]
    assert result.text == "Here you go."
    assert len(fake.calls) == 1


async def test_invalid_draft_is_returned_to_the_model():
    agent, fake = make_agent(
        [
            reply(tool("draft_issue", draft_args(title="")), stop="tool_use"),
            reply(tool("draft_issue", draft_args(), id="t2"), stop="tool_use"),
        ]
    )
    result = await agent.respond(CONVO, TurnContext(), "K")
    assert result.draft is not None and result.text == DRAFT_TEXT
    err = fake.calls[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "title is required" in err["content"]


async def test_no_draft_tool_in_ask_mode_or_when_blocked():
    agent, fake = make_agent([reply(text("a")), reply(text("b"))])
    await agent.respond(CONVO, TurnContext(mode="ask"), "K")
    await agent.respond(CONVO, TurnContext(can_file=False, cannot_file_reason="new"), "K")
    for call in fake.calls:
        assert "draft_issue" not in [t["name"] for t in call["tools"]]


async def test_refusal_and_api_errors_degrade_gracefully():
    agent, _ = make_agent([reply(stop="refusal")])
    assert (await agent.respond(CONVO, TurnContext(), "K")).text == REFUSAL_TEXT

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.InternalServerError(
        "boom", response=httpx.Response(500, request=request), body=None
    )
    agent, _ = make_agent([err])
    assert (await agent.respond(CONVO, TurnContext(), "K")).text == FALLBACK_TEXT


async def test_usage_and_cost_accumulate():
    agent, _ = make_agent(
        [
            reply(tool("get_issue", {"number": 3}), stop="tool_use", u=usage(100, 50, 50_000)),
            reply(text("closed"), u=usage(200, 20, 50_000)),
        ]
    )
    result = await agent.respond(CONVO, TurnContext(), "K")
    assert result.usage.cache_read == 100_000
    expected = (300 * 0.10 + 70 * 0.50 + 100_000 * 0.01) / 1_000_000
    assert abs(result.usage.cost_usd - expected) < 1e-12


def test_long_prompts_use_the_higher_rate_card():
    p = Prices(input=0.1, output=0.5, cache_read=0.01, cache_write=0.2, long_prompt_multiplier=5)
    assert p.cost(0, 0, 100_001, 0) == 5 * p.cost(0, 0, 100_000, 0) * 100_001 / 100_000
