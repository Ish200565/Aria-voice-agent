"""Post-call outcome generation using structured LLM output and deterministic tool logs."""
import json
import re

import groq

from . import llm

INTENTS = ["ORDER_TRACKING", "ORDER_CANCELLATION", "RETURN_REFUND", "POLICY_QUERY", "SHIPPING_QUERY",
           "PAYMENT_QUERY", "PRODUCT_INFO", "OUT_OF_SCOPE", "ESCALATION", "OTHER"]
STATUSES = ["RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED", "ESCALATED"]
SENTIMENTS = ["positive", "neutral", "negative"]
_ORDER_RE = re.compile(r"^ORD-\d{3}$")

PROMPT = f"""You summarise a finished customer-support voice call for Aura Skincare.
Return ONLY a JSON object with exactly these keys:
- "customer_intent": the customer's MAIN goal, one of {INTENTS}
- "other_intents": list of other intents from the same list (may be empty)
- "order_id": main order ID like "ORD-101" if one was discussed, else null
- "resolution_status": one of {STATUSES}. RESOLVED only if the customer's main request was answered or completed.
  Use UNRESOLVED if the agent could not help (e.g. missing information), ESCALATED if handed to a human.
- "call_summary": 1-3 plain sentences: what the customer wanted and what the agent said or did.
- "customer_sentiment": one of {SENTIMENTS}
- "follow_up_needed": true/false
Use only facts in the transcript. Do not invent anything."""


def _extract_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None


def _facts(session) -> dict:
    """Deterministic facts straight from the tool log."""
    orders, actions, escalated = [], [], False
    for t in session.tool_log:
        name, result = t["name"], t.get("result") or {}
        try:
            args = json.loads(t.get("args") or "{}")
        except json.JSONDecodeError:
            args = {}
        oid = result.get("order_id") if result.get("found", True) else None
        if oid and oid not in orders:
            orders.append(oid)
        if name == "cancel_order" and result.get("success"):
            actions.append(f"Cancelled {oid}")
        elif name == "cancel_order" and result.get("reason"):
            actions.append(f"Cancellation refused for {oid}: {result['reason']}")
        elif name == "check_return_eligibility":
            verdict = {True: "eligible", False: "not eligible", None: "needs more info"}[result.get("eligible")]
            actions.append(f"Return check{' for ' + oid if oid else ''}: {verdict}")
        elif name == "get_order_details" and not result.get("found"):
            actions.append(f"Order lookup failed for {args.get('order_id')}")
        elif name == "get_order_details":
            actions.append(f"Looked up {oid}")
        elif name == "escalate_to_human" and result.get("success"):
            escalated = True
            actions.append(f"Escalated to human team ({result.get('ticket_id')})")
    return {"orders": orders, "actions": actions, "escalated": escalated}


def _fallback(session, facts) -> dict:
    """No-LLM summary so the end screen never shows an error."""
    used = {t["name"] for t in session.tool_log}
    text = " ".join(m["text"].lower() for m in session.transcript if m["role"] == "customer")
    if facts["escalated"]:
        intent = "ESCALATION"
    elif "cancel_order" in used:
        intent = "ORDER_CANCELLATION"
    elif "check_return_eligibility" in used:
        intent = "RETURN_REFUND"
    elif "get_order_details" in used:
        intent = "ORDER_TRACKING"
    elif re.search(r"cash on delivery|\bcod\b|payment|upi", text):
        intent = "PAYMENT_QUERY"
    elif re.search(r"shipping|delivery charge", text):
        intent = "SHIPPING_QUERY"
    else:
        intent = "OTHER"
    last = next((m["text"] for m in reversed(session.transcript) if m["role"] == "agent"), "")
    return {"customer_intent": intent, "other_intents": [], "resolution_status": "PARTIALLY_RESOLVED",
            "call_summary": f"Customer call about {intent.lower().replace('_', ' ')}. Last agent reply: {last}".strip(),
            "customer_sentiment": "neutral", "follow_up_needed": False}


async def _call_llm(transcript_text: str):
    chain = await llm._model_chain()
    chain = chain[1:] + chain[:1] if len(chain) > 1 else chain       # smaller/faster model first
    for model in chain:
        try:
            resp = await llm.client().chat.completions.create(
                model=model, temperature=0, stream=False, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": transcript_text}],
                **llm._model_kwargs(model))
            data = _extract_json(resp.choices[0].message.content)
            if isinstance(data, dict):
                return data
        except (groq.NotFoundError, groq.PermissionDeniedError):
            llm._dead.add(model)
        except Exception as e:
            print(f"[summary] {model}: {type(e).__name__}: {e}")
    return None


def _pick(value, allowed, default):
    return value if value in allowed else default


async def build_summary(session) -> dict:
    cached = getattr(session, "summary", None)
    if cached:
        return cached
    customer_turns = [m for m in session.transcript if m["role"] == "customer"]
    facts = _facts(session)
    if not customer_turns:
        out = {"customer_intent": "OTHER", "other_intents": [], "order_id": None,
               "resolution_status": "UNRESOLVED", "call_summary": "The customer ended the call without speaking.",
               "customer_sentiment": "neutral", "follow_up_needed": False}
    else:
        lines = [f"{'Customer' if m['role'] == 'customer' else 'Agent'}: {m['text']}" for m in session.transcript]
        if facts["actions"]:
            lines.append("TOOL ACTIONS (ground truth): " + "; ".join(facts["actions"]))
        raw = await _call_llm("\n".join(lines)) or {}
        base = _fallback(session, facts)
        order_id = raw.get("order_id") if _ORDER_RE.match(str(raw.get("order_id") or "")) else None
        if order_id not in facts["orders"]:                         # never trust an ID the tools never saw
            order_id = facts["orders"][-1] if facts["orders"] else None
        summary_text = raw.get("call_summary")
        out = {
            "customer_intent": _pick(raw.get("customer_intent"), INTENTS, base["customer_intent"]),
            "other_intents": [i for i in (raw.get("other_intents") or []) if i in INTENTS][:4],
            "order_id": order_id,
            "resolution_status": "ESCALATED" if facts["escalated"]
                                 else _pick(raw.get("resolution_status"), STATUSES, base["resolution_status"]),
            "call_summary": summary_text.strip() if isinstance(summary_text, str) and summary_text.strip()
                            else base["call_summary"],
            "customer_sentiment": _pick(raw.get("customer_sentiment"), SENTIMENTS, "neutral"),
            "follow_up_needed": bool(raw.get("follow_up_needed", False)),
        }
    out["orders_discussed"] = facts["orders"]
    out["actions_taken"] = facts["actions"]
    out["turns"] = len(customer_turns)
    session.summary = out
    return out
