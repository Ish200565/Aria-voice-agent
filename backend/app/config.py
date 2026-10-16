import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "openai/gpt-oss-20b")
# Tried (in order) after PRIMARY/FALLBACK if those are missing on your Groq account.
EXTRA_MODELS = [m.strip() for m in os.getenv(
    "EXTRA_MODELS",
    "meta-llama/llama-4-scout-17b-16e-instruct,meta-llama/llama-4-maverick-17b-128e-instruct",
).split(",") if m.strip()]

TTS_VOICE = os.getenv("TTS_VOICE", "en-IN-NeerjaNeural")
TTS_RATE = os.getenv("TTS_RATE", "+5%")

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Free-tier protection
MAX_TURNS = int(os.getenv("MAX_TURNS", "20"))
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "300"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "16"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "150"))
# Reasoning tokens count against completion cap; keep effort low for response speed.
REASONING_MAX_TOKENS = int(os.getenv("REASONING_MAX_TOKENS", "1000"))
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")
MAX_TOOL_HOPS = 3
