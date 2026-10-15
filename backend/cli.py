"""Text-mode test harness (Day 1 de-risking): python cli.py
Chat with Aria in the terminal - same code path the browser uses, minus voice."""
import asyncio

from app.llm import run_turn
from app.prompts import GREETING
from app.sessions import store


async def main():
    s = store.create()
    print(f"Aria: {GREETING}\n(type 'quit' to exit, 'orders' to show current order statuses)\n")
    while True:
        text = input("You: ").strip()
        if text.lower() in {"quit", "exit"}:
            break
        if text.lower() == "orders":
            print({k: v["status"] for k, v in s.orders.items()})
            continue
        async for ev in run_turn(s, text):
            if ev["type"] == "sentence":
                print(f"Aria: {ev['text']}")
            elif ev["type"] == "tool_start":
                print(f"  [tool] {ev['name']}")
            elif ev["type"] == "done":
                print(f"  [ttfs={ev.get('ttfs_ms')}ms llm_calls={ev.get('llm_calls')}]")


if __name__ == "__main__":
    asyncio.run(main())
