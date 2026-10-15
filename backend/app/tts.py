"""Free neural TTS via edge-tts (no API key, no quota). Cached so repeated lines are instant.
If it fails (e.g. blocked on the host), the endpoint returns 503 and the browser falls back
to speechSynthesis."""
import asyncio
import re
from collections import OrderedDict

import edge_tts

from . import config

_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_CACHE_MAX = 300


async def synthesize(text: str, voice: str | None = None) -> bytes:
    text = re.sub(r"\s+", " ", text).strip()[:400]
    if not text:
        raise ValueError("empty text")
    voice = voice or config.TTS_VOICE
    key = (text, voice)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    async def _run() -> bytes:
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=config.TTS_RATE).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        if not buf:
            raise RuntimeError("no audio returned")
        return bytes(buf)

    audio = await asyncio.wait_for(_run(), timeout=8)
    _cache[key] = audio
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return audio
