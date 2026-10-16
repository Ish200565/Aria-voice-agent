"""One conversational turn: stream Groq -> split into sentences -> run tools -> stream the follow-up.
Yields small event dicts that main.py forwards to the browser as Server-Sent Events."""
import json
import re
import time
import uuid
from typing import AsyncIterator

import groq
from groq import AsyncGroq

from . import config
from .prompts import SORRY_TROUBLE, SORRY_UNCLEAR, SYSTEM_PROMPT
from .sentences import SentenceSplitter
from .sessions import Session
from .tools import TOOL_SCHEMAS, execute_tool

_client: AsyncGroq | None = None
_NOISE = {"um", "uh", "hmm", "hm", "mm", "ah", "oh", "huh", "erm", "uhh", "umm"}


def client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=config.GROQ_API_KEY, timeout=20.0, max_retries=0)
    return _client


def is_noise(text: str) -> bool:
    t = re.sub(r"[^\w\s]", "", text.lower()).strip()
    return len(t) < 2 or t in _NOISE


class LLMUnavailable(Exception):
    pass


_available: set[str] | None = None     # model ids your key can see (discovered once)
_dead: set[str] = set()                 # models that returned not-found / decommissioned


async def _model_chain() -> list[str]:
    """PRIMARY, FALLBACK, then EXTRA models - filtered to what your Groq key can actually use."""
    global _available
    if _available is None:
        try:
            listing = await client().models.list()
            _available = {m.id for m in listing.data}
        except Exception as e:
            print(f"[llm] could not list models ({type(e).__name__}); will just try the configured ones")
            _available = set()
    wanted = list(dict.fromkeys([config.PRIMARY_MODEL, config.FALLBACK_MODEL, *config.EXTRA_MODELS]))
    chain = [m for m in wanted if m not in _dead and (not _available or m in _available)]
    return chain or [m for m in wanted if m not in _dead]


def _model_kwargs(model: str) -> dict:
    """Configure token caps and reasoning effort for reasoning models."""
    if model.startswith("openai/gpt-oss"):
        return {"max_completion_tokens": config.REASONING_MAX_TOKENS,
                "reasoning_effort": config.REASONING_EFFORT, "include_reasoning": False}
    return {"max_tokens": config.MAX_TOKENS}


def _is_model_gone(e: Exception) -> bool:
    msg = str(e).lower()
    return "model_not_found" in msg or "decommission" in msg or "does not exist" in msg


async def _create_stream(messages: list):
    """Walk model chain with fallback on errors and retry on tool call failure."""
    last_err: Exception | None = None
    for model in await _model_chain():
        for attempt in range(2):
            try:
                return await client().chat.completions.create(
                    model=model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto",
                    temperature=0.2, stream=True, **_model_kwargs(model),
                )
            except groq.AuthenticationError as e:
                raise LLMUnavailable("Invalid or missing GROQ_API_KEY - check your .env") from e
            except (groq.NotFoundError, groq.PermissionDeniedError) as e:
                _dead.add(model)
                print(f"[llm] model '{model}' unavailable ({type(e).__name__}) -> trying next")
                last_err = e
                break
            except groq.BadRequestError as e:
                last_err = e
                if _is_model_gone(e):
                    _dead.add(model)
                    print(f"[llm] model '{model}' gone/decommissioned -> trying next")
                    break
                if "tool_use_failed" in str(e) and attempt == 0:
                    continue
                break
            except (groq.RateLimitError, groq.APIConnectionError, groq.APITimeoutError,
                    groq.InternalServerError) as e:
                last_err = e
                print(f"[llm] '{model}' {type(e).__name__} -> trying next")
                break
    hint = f" Models your key can see: {sorted(_available)}" if _available else ""
    raise LLMUnavailable(f"{last_err}.{hint}")


def _is_duplicate(s: str, spoken: list[str]) -> bool:
    norm = re.sub(r"[^\w\s]", "", s.lower()).strip()
    if not norm:
        return True
    for prev in spoken:
        p_norm = re.sub(r"[^\w\s]", "", prev.lower()).strip()
        if norm == p_norm or (len(norm) > 15 and (norm in p_norm or p_norm in norm)):
            return True
    return False


async def run_turn(session: Session, user_text: str) -> AsyncIterator[dict]:
    user_text = user_text.strip()

    # Cheap guard: no LLM call (and no quota) for empty/noise input.
    if is_noise(user_text):
        yield {"type": "sentence", "text": SORRY_UNCLEAR}
        yield {"type": "done", "reply": SORRY_UNCLEAR, "llm_calls": 0}
        return

    session.turns += 1
    session.messages.append({"role": "user", "content": user_text})
    session.log("customer", user_text)

    t0 = time.perf_counter()
    spoken: list[str] = []
    ttfs_ms = None
    llm_calls = 0

    try:
        for _hop in range(config.MAX_TOOL_HOPS + 1):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + session.history()
            stream = await _create_stream(messages)
            llm_calls += 1
            splitter = SentenceSplitter()
            text, calls, finish = "", {}, None

            async for chunk in stream:
                if not chunk.choices:
                    continue
                finish = getattr(chunk.choices[0], "finish_reason", None) or finish
                delta = chunk.choices[0].delta
                for tc in delta.tool_calls or []:
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        c["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            c["name"] = tc.function.name
                        if tc.function.arguments:
                            c["args"] += tc.function.arguments
                if delta.content:
                    text += delta.content
                    if not calls:
                        for s in splitter.feed(delta.content):
                            if not _is_duplicate(s, spoken):
                                if ttfs_ms is None:
                                    ttfs_ms = int((time.perf_counter() - t0) * 1000)
                                spoken.append(s)
                                yield {"type": "sentence", "text": s}

            if finish == "length":
                print("[llm] WARNING: reply cut off by the token limit - raise REASONING_MAX_TOKENS")
            if not calls:
                rest = splitter.flush()
                if rest and not _is_duplicate(rest, spoken):
                    if ttfs_ms is None:
                        ttfs_ms = int((time.perf_counter() - t0) * 1000)
                    spoken.append(rest)
                    yield {"type": "sentence", "text": rest}

            if not calls:
                if text.strip():
                    session.messages.append({"role": "assistant", "content": text})
                break

            tool_calls = [{"id": c["id"] or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                           "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                          for _, c in sorted(calls.items())]
            session.messages.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                name, args = tc["function"]["name"], tc["function"]["arguments"]
                yield {"type": "tool_start", "name": name}      # client plays a filler ("one moment...")
                result = execute_tool(name, args, session.orders)
                session.tool_log.append({"name": name, "args": args, "result": result})
                session.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                         "content": json.dumps(result)})
        else:
            raise LLMUnavailable("too many tool hops")

    except LLMUnavailable as e:
        print(f"[llm] UNAVAILABLE: {e}")
        spoken.append(SORRY_TROUBLE)
        yield {"type": "sentence", "text": SORRY_TROUBLE}
    except Exception as e:  # stream broke mid-way etc. Never crash the call.
        print(f"[llm] unexpected error: {type(e).__name__}: {e}")
        if not spoken:
            spoken.append(SORRY_TROUBLE)
            yield {"type": "sentence", "text": SORRY_TROUBLE}

    reply = " ".join(spoken)
    if reply:
        session.log("agent", reply)
    yield {"type": "done", "reply": reply, "ttfs_ms": ttfs_ms, "llm_calls": llm_calls}
