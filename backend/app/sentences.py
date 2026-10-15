"""Sentence splitter for streaming LLM tokens -> speakable chunks (drives low perceived latency)."""
import re

_END = re.compile(r'([.!?]+["\')\]]*)(\s+)')
_SOFT = re.compile(r"[,;:]\s")
_MD = re.compile(r"[*_`#>~]+")


def clean_for_speech(text: str) -> str:
    text = _MD.sub("", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


class SentenceSplitter:
    """feed() returns complete sentences. The FIRST chunk may be cut early at a comma so
    audio can start sooner."""

    def __init__(self, early_chars: int = 40, min_soft_pos: int = 20):
        self.buf = ""
        self.emitted = False
        self.early_chars = early_chars
        self.min_soft_pos = min_soft_pos

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while (m := _END.search(self.buf)):
            sentence = clean_for_speech(self.buf[: m.end(1)])
            self.buf = self.buf[m.end():]
            if sentence:
                out.append(sentence)
                self.emitted = True
        if not self.emitted and not out and len(self.buf) >= self.early_chars:
            last = None
            for last in _SOFT.finditer(self.buf):
                pass
            if last and last.start() >= self.min_soft_pos:
                chunk = clean_for_speech(self.buf[: last.start() + 1])
                self.buf = self.buf[last.end():]
                if chunk:
                    out.append(chunk)
                    self.emitted = True
        return out

    def flush(self) -> str | None:
        rest = clean_for_speech(self.buf)
        self.buf = ""
        return rest or None
