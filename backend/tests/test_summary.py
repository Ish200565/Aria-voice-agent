import asyncio
import json

from app import summary
from app.sessions import SessionStore


def sess(turns, tools=()):
    s = SessionStore().create()
    for role, text in turns:
        s.log(role, text)
    for name, args, result in tools:
        s.tool_log.append({"name": name, "args": json.dumps(args), "result": result})
    return s


def run(s, llm_out, monkeypatch):
    async def fake(_): return llm_out
    monkeypatch.setattr(summary, "_call_llm", fake)
    return asyncio.run(summary.build_summary(s))


def test_happy_path_uses_llm_text_but_code_facts(monkeypatch):
    s = sess([("agent", "Hi"), ("customer", "where is ORD-101"), ("agent", "Out for delivery by six PM.")],
             [("get_order_details", {"order_id": "ORD-101"}, {"found": True, "order_id": "ORD-101"})])
    out = run(s, {"customer_intent": "ORDER_TRACKING", "order_id": "ORD-101", "resolution_status": "RESOLVED",
                  "call_summary": "Customer asked about ORD-101; it is out for delivery by 6 PM."}, monkeypatch)
    assert out["customer_intent"] == "ORDER_TRACKING" and out["order_id"] == "ORD-101"
    assert out["resolution_status"] == "RESOLVED" and out["orders_discussed"] == ["ORD-101"]


def test_fake_order_id_and_bad_enums_are_corrected(monkeypatch):
    s = sess([("customer", "cancel 103"), ("agent", "Done.")],
             [("cancel_order", {"order_id": "ORD-103"}, {"success": True, "order_id": "ORD-103"})])
    out = run(s, {"customer_intent": "MAKE_BELIEVE", "order_id": "ORD-777", "resolution_status": "??",
                  "call_summary": ""}, monkeypatch)
    assert out["order_id"] == "ORD-103" and out["customer_intent"] == "ORDER_CANCELLATION"
    assert out["resolution_status"] in summary.STATUSES and out["call_summary"]
    assert "Cancelled ORD-103" in out["actions_taken"]


def test_llm_down_still_gives_summary(monkeypatch):
    s = sess([("customer", "is cash on delivery available"), ("agent", "Yes, up to 2500 rupees.")])
    out = run(s, None, monkeypatch)
    assert out["customer_intent"] == "PAYMENT_QUERY" and out["call_summary"]


def test_escalation_forces_escalated_status(monkeypatch):
    s = sess([("customer", "talk to a human"), ("agent", "Logged.")],
             [("escalate_to_human", {"reason": "x"}, {"success": True, "ticket_id": "ESC-1"})])
    assert run(s, {"resolution_status": "RESOLVED"}, monkeypatch)["resolution_status"] == "ESCALATED"


def test_silent_call_makes_no_llm_call(monkeypatch):
    s = sess([("agent", "Hi")])
    async def boom(_): raise AssertionError("LLM must not be called")
    monkeypatch.setattr(summary, "_call_llm", boom)
    assert asyncio.run(summary.build_summary(s))["resolution_status"] == "UNRESOLVED"


def test_json_extraction_handles_fences():
    assert summary._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert summary._extract_json('Sure! {"a": 2} done') == {"a": 2}
