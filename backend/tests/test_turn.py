"""Tests the streaming + tool-calling loop with a FAKE Groq stream (no network, no quota)."""
import asyncio
import json
from types import SimpleNamespace as NS

from app import llm
from app.sessions import SessionStore


def chunk(content=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])


def tool_delta(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class FakeStream:
    def __init__(self, chunks): self.chunks = chunks
    def __aiter__(self): return self._gen()
    async def _gen(self):
        for c in self.chunks:
            yield c


def run(session, text, scripted, monkeypatch):
    calls = {"n": 0, "messages": []}

    async def fake_create(messages):
        calls["messages"].append(messages)
        stream = FakeStream(scripted[calls["n"]])
        calls["n"] += 1
        return stream

    monkeypatch.setattr(llm, "_create_stream", fake_create)

    async def collect():
        return [ev async for ev in llm.run_turn(session, text)]
    return asyncio.run(collect()), calls


def test_tool_call_then_spoken_answer(monkeypatch):
    s = SessionStore().create()
    scripted = [
        [chunk(tool_calls=[tool_delta(0, "call_1", "get_order_details", '{"order_id":')]),
         chunk(tool_calls=[tool_delta(0, None, None, ' "ORD-101"}')])],
        [chunk("Your order is out for delivery. "), chunk("It should arrive by six PM today.")],
    ]
    events, calls = run(s, "where is order one zero one", scripted, monkeypatch)
    kinds = [e["type"] for e in events]
    assert kinds == ["tool_start", "sentence", "sentence", "done"]
    assert events[-1]["llm_calls"] == 2
    # the tool result really reached the model on the second call
    tool_msgs = [m for m in calls["messages"][1] if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["status"] == "Out for Delivery"
    assert s.tool_log[0]["name"] == "get_order_details"
    assert [t["role"] for t in s.transcript] == ["customer", "agent"]


def test_noise_costs_no_llm_call(monkeypatch):
    s = SessionStore().create()
    events, calls = run(s, "um", [], monkeypatch)
    assert calls["n"] == 0 and events[-1]["llm_calls"] == 0 and s.turns == 0


def test_llm_outage_degrades_gracefully(monkeypatch):
    s = SessionStore().create()

    async def boom(messages): raise llm.LLMUnavailable("429")
    monkeypatch.setattr(llm, "_create_stream", boom)

    async def collect(): return [ev async for ev in llm.run_turn(s, "hello there")]
    events = asyncio.run(collect())
    assert events[0]["type"] == "sentence" and "trouble" in events[0]["text"]
    assert events[-1]["type"] == "done"


def test_history_trim_never_orphans_tool_messages():
    s = SessionStore().create()
    for i in range(10):
        s.messages += [{"role": "user", "content": f"q{i}"},
                       {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]},
                       {"role": "tool", "tool_call_id": "x", "content": "{}"},
                       {"role": "assistant", "content": f"a{i}"}]
    h = s.history()
    assert h[0]["role"] == "user" and len(h) <= 16


# ---- model fallback (the bug you hit: 404 model_not_found must fall through, not crash) ----
import httpx
import groq
import pytest


def _err(cls, status, msg):
    resp = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    return cls(msg, response=resp, body=None)


class _FakeClient:
    def __init__(self, behaviour):
        self.behaviour, self.tried = behaviour, []
        self.chat = NS(completions=NS(create=self._create))
        self.models = NS(list=self._list)

    async def _list(self):
        return NS(data=[NS(id=m) for m in self.behaviour])

    async def _create(self, **kw):
        self.tried.append(kw["model"])
        out = self.behaviour[kw["model"]]
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def fresh_llm(monkeypatch):
    monkeypatch.setattr(llm, "_available", None)
    monkeypatch.setattr(llm, "_dead", set())
    monkeypatch.setattr(llm.config, "PRIMARY_MODEL", "big")
    monkeypatch.setattr(llm.config, "FALLBACK_MODEL", "small")
    monkeypatch.setattr(llm.config, "EXTRA_MODELS", ["extra"])


def test_not_found_falls_through_to_next_model(fresh_llm, monkeypatch):
    fake = _FakeClient({"big": _err(groq.NotFoundError, 404, "model_not_found"), "small": "STREAM"})
    monkeypatch.setattr(llm, "_client", fake)
    assert asyncio.run(llm._create_stream([])) == "STREAM"
    assert fake.tried == ["big", "small"] and "big" in llm._dead


def test_decommissioned_400_and_ratelimit_both_fall_through(fresh_llm, monkeypatch):
    fake = _FakeClient({"big": _err(groq.BadRequestError, 400, "model_decommissioned"),
                        "small": _err(groq.RateLimitError, 429, "slow down"), "extra": "STREAM"})
    monkeypatch.setattr(llm, "_client", fake)
    assert asyncio.run(llm._create_stream([])) == "STREAM"
    assert fake.tried == ["big", "small", "extra"]


def test_model_missing_from_account_is_skipped_without_calling_it(fresh_llm, monkeypatch):
    fake = _FakeClient({"small": "STREAM", "extra": "STREAM"})   # 'big' not in models.list()
    monkeypatch.setattr(llm, "_client", fake)
    asyncio.run(llm._create_stream([]))
    assert fake.tried == ["small"]


def test_all_models_gone_raises_unavailable(fresh_llm, monkeypatch):
    nf = lambda: _err(groq.NotFoundError, 404, "model_not_found")
    fake = _FakeClient({"big": nf(), "small": nf(), "extra": nf()})
    monkeypatch.setattr(llm, "_client", fake)
    with pytest.raises(llm.LLMUnavailable):
        asyncio.run(llm._create_stream([]))
