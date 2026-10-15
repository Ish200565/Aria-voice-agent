"""Prints the model ids your Groq key can use. Run: python list_models.py
Then set PRIMARY_MODEL / FALLBACK_MODEL in .env to chat models from this list (prefer ones that
support tool use; see https://console.groq.com/docs/tool-use)."""
import asyncio

from app.llm import client


async def main():
    ids = sorted(m.id for m in (await client().models.list()).data)
    skip = ("whisper", "tts", "guard", "embed")
    print("Chat-capable candidates:")
    for i in ids:
        if not any(k in i for k in skip):
            print("  ", i)
    print("\nOther (audio/safety):", [i for i in ids if any(k in i for k in skip)])


asyncio.run(main())
