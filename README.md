# Aria – AI Voice CX Agent for Aura Skincare (Datastraw assessment)

Browser voice agent: Web Speech STT -> Groq LLM (tool calling, streamed) -> edge-tts neural Indian voice.
**Everything runs on free tiers.** (Frontend, summary and Section 9 answers land on Day 2.)

## Backend – run locally
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # add your free GROQ_API_KEY
python -m pytest tests -q                               # 35 tests, no network needed
python list_models.py                                   # shows which Groq models YOUR key can use
python cli.py                                           # chat with Aria in the terminal
uvicorn app.main:app --reload                           # API on http://localhost:8000
```

## API
| Endpoint | Purpose |
|---|---|
| `GET/HEAD /health` | Ultra-light keep-alive / warm-up check (no LLM, no TTS) |
| `GET /api/orders` | Sample orders for the "Test Orders" helper panel |
| `POST /api/session` | Start a call -> `session_id` + greeting |
| `POST /api/chat` | `{session_id, text}` -> SSE stream of `sentence` / `tool_start` / `done` events |
| `GET /api/tts?text=` | MP3 for one sentence (503 => browser falls back to `speechSynthesis`) |

## Design notes
- **Policy lives in code, not just the prompt**: `tools.py` computes return/cancel eligibility; the LLM only phrases it.
- **Quota-aware**: noise ("um") costs 0 LLM calls; 1 call/turn (2 with a tool); history capped; per-call turn/time caps; per-IP rate limits; 70B -> 8B automatic fallback.
- **Per-session order copies**: cancelling ORD-103 in one test never affects the next evaluator.
- **Latency**: LLM tokens stream into sentences (first chunk may cut at a comma); TTS is cached.

## Deploy backend (Render free)
1. Push repo to GitHub -> Render "New + Blueprint" (uses `render.yaml`) -> set `GROQ_API_KEY`.
2. Keep-alive: add UptimeRobot (or cron-job.org) monitors on `https://<service>.onrender.com/health` every 5 min.
