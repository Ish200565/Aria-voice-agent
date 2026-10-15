import json
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import config, tts
from .data import MOCK_ORDERS
from .llm import run_turn
from .prompts import GREETING, LIMIT_REACHED
from .sessions import store

app = FastAPI(title="Aria Voice Agent API")
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])

# --- tiny in-memory sliding-window rate limiter (protects the free LLM quota) ---
_hits: dict[tuple, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def rate_limit(request: Request, bucket: str, limit: int, window: int):
    q = _hits[(client_ip(request), bucket)]
    now = time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests, please slow down.")
    q.append(now)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Ultra-light on purpose: used by the page warm-up and the UptimeRobot keep-alive ping."""
    return {"status": "ok"}


@app.get("/api/orders")
async def sample_orders():
    """Feeds the 'Test Orders' helper panel so the UI never drifts from the real data."""
    return list(MOCK_ORDERS.values())


@app.post("/api/session")
async def create_session(request: Request):
    rate_limit(request, "session", limit=10, window=600)
    s = store.create()
    s.log("agent", GREETING)
    return {"session_id": s.id, "greeting": GREETING,
            "max_seconds": config.MAX_CALL_SECONDS, "max_turns": config.MAX_TURNS}


class ChatIn(BaseModel):
    session_id: str
    text: str = Field(max_length=500)


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    rate_limit(request, "chat", limit=30, window=60)
    session = store.get(body.session_id)
    if not session:
        raise HTTPException(404, "Unknown or expired session")

    async def events():
        if session.over_limit():
            session.log("agent", LIMIT_REACHED)
            yield f"data: {json.dumps({'type': 'sentence', 'text': LIMIT_REACHED})}\n\n"
            yield f"data: {json.dumps({'type': 'limit'})}\n\n"
            return
        async for ev in run_turn(session, body.text):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/tts")
async def tts_endpoint(request: Request, text: str, voice: str | None = None):
    rate_limit(request, "tts", limit=90, window=60)
    try:
        audio = await tts.synthesize(text, voice)
    except Exception as e:
        print(f"[tts] failed: {type(e).__name__}: {e}")
        raise HTTPException(503, "TTS unavailable")  # client falls back to browser speechSynthesis
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=3600"})
