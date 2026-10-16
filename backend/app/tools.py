"""Tools available to the LLM for order lookups, returns, and cancellations."""
import json
import re
import uuid

_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

RETURN_WINDOW_HOURS = 7 * 24
DAMAGE_REPORT_HOURS = 48


def normalize_order_id(raw) -> str | None:
    """'order one zero one' / 'ord 101' / 'ORD-101' / '1 0 1' -> 'ORD-101'. None if no digits."""
    if raw is None:
        return None
    s = str(raw).lower()
    s = re.sub(r"\bo\s*\.?\s*r\s*\.?\s*d\b", " ", s)              # spoken letters "o r d"
    s = re.sub(r"\border(?:\s*(?:id|number|no))?\b", " ", s)
    s = re.sub(r"\bord(?=\d|\b)", " ", s)
    s = re.sub(r"\b(\w+)\s+hundred(?:\s+and)?\s+(\w+)\b", r"\1 zero \2", s)
    tokens = re.findall(r"[a-z]+|\d+", s)
    digits = "".join(_WORDS.get(t, t if t.isdigit() else "") for t in tokens)
    return f"ORD-{digits}" if digits else None


def _lookup(orders: dict, order_id):
    oid = normalize_order_id(order_id)
    if not oid:
        return None, {
            "found": False,
            "error": "missing_or_unreadable_order_id",
            "message": "No usable order ID. Ask the customer to say it again (ORD followed by three digits).",
        }
    order = orders.get(oid)
    if not order:
        return None, {
            "found": False,
            "order_id": oid,
            "error": "order_not_found",
            "message": f"No order found for {oid}. Ask the customer to repeat or verify the ID. Do not guess.",
        }
    return order, None


def get_order_details(orders: dict, order_id=None) -> dict:
    order, err = _lookup(orders, order_id)
    if err:
        return err
    status = order["status"]
    result = {
        "found": True,
        "order_id": order["order_id"],
        "product": order["product"],
        "value_inr": order["value_inr"],
        "status": status,
        "courier": order["courier"],
        "tracking_id": order["tracking_id"],
        "note": order["note"],
        "can_cancel": status == "Processing",
        "reply_hint": "Answer in one short sentence: status, courier, expected time. Do not read the product, price or tracking ID unless the customer asks.",
        "can_refuse_at_doorstep": status in ("Shipped", "Out for Delivery"),
    }
    if order["hours_since_delivery"] is not None:
        result["days_since_delivery"] = order["hours_since_delivery"] // 24
    return result


def check_return_eligibility(orders: dict, order_id=None, days_since_delivery=None,
                             product_opened=None, issue_type="change_of_mind") -> dict:
    """Pure policy check. Works with an order ID OR a customer-stated number of days."""
    hours = None
    if order_id:
        order, err = _lookup(orders, order_id)
        if err:
            return err
        if order["status"] != "Delivered":
            tip = {
                "Processing": "Not delivered yet. It can be cancelled instead (status is Processing).",
                "Shipped": "Not delivered yet. Cannot be cancelled, but customer may refuse delivery at the doorstep.",
                "Out for Delivery": "Not delivered yet. Cannot be cancelled, but customer may refuse delivery at the doorstep.",
            }.get(order["status"], "Not delivered yet, so returns do not apply.")
            return {"eligible": False, "reasons": ["not_delivered_yet"], "message": tip}
        hours = order["hours_since_delivery"]
    elif days_since_delivery is not None:
        try:
            hours = float(days_since_delivery) * 24
        except (TypeError, ValueError):
            hours = None
    if hours is None:
        return {"eligible": None, "needs_info": "order_id_or_days_since_delivery",
                "message": "Ask for the order ID, or how many days ago it was delivered."}

    if issue_type == "damaged_or_defective":
        if hours <= DAMAGE_REPORT_HOURS:
            return {"eligible": True, "resolution": "replacement",
                    "message": "Damaged/defective items reported within 48 hours qualify for a REPLACEMENT "
                               "(not a refund). Customer must share photos. Do not promise timelines."}
        return {"eligible": False, "reasons": ["outside_48_hour_damage_window"],
                "message": "Damage must be reported within 48 hours of delivery with photos. Window has passed."}

    reasons = []
    if hours > RETURN_WINDOW_HOURS:
        reasons.append("outside_7_day_window")
    if product_opened is True:
        reasons.append("opened_or_used")
    if reasons:
        return {"eligible": False, "reasons": reasons,
                "message": "Returns need: within 7 days of delivery, unopened, unused, original packaging. "
                           "Politely explain which condition fails. Do NOT promise a refund or exception."}
    if product_opened is None:
        return {"eligible": None, "needs_info": "product_opened",
                "message": "Within the 7-day window. Ask whether the product is unopened and unused."}
    return {"eligible": True, "resolution": "return",
            "message": "Eligible under policy if unopened, unused and in original packaging. The policy does not "
                       "state refund amounts or timelines - do not invent any. Offer to log a request for the team."}


def cancel_order(orders: dict, order_id=None) -> dict:
    order, err = _lookup(orders, order_id)
    if err:
        return err
    status = order["status"]
    oid = order["order_id"]
    if status == "Processing":
        order["status"] = "Cancelled"          # mutates the session copy only
        return {"success": True, "order_id": oid, "new_status": "Cancelled",
                "message": "Order cancelled. Do not state refund timelines (not in policy)."}
    if status == "Cancelled":
        return {"success": False, "order_id": oid, "reason": "already_cancelled"}
    if status in ("Shipped", "Out for Delivery"):
        return {"success": False, "order_id": oid, "reason": "already_shipped",
                "message": "Cancellation only works while Processing. Customer may refuse delivery at the doorstep."}
    return {"success": False, "order_id": oid, "reason": "already_delivered",
            "message": "Delivered orders cannot be cancelled. Mention the return policy if relevant."}


def escalate_to_human(orders: dict, reason: str = "", order_id=None) -> dict:
    ticket = f"ESC-{uuid.uuid4().hex[:6].upper()}"
    return {"success": True, "ticket_id": ticket,
            "message": "Request logged for the human support team. Do not promise a callback time."}


REGISTRY = {
    "get_order_details": get_order_details,
    "check_return_eligibility": check_return_eligibility,
    "cancel_order": cancel_order,
    "escalate_to_human": escalate_to_human,
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_order_details",
        "description": "Look up an order's status, product, value and courier info. Call whenever the customer "
                       "mentions or asks about a specific order.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order ID as the customer said it, e.g. ORD-101"}},
            "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "check_return_eligibility",
        "description": "Check return/replacement eligibility. Use order_id if known, otherwise "
                       "days_since_delivery as stated by the customer.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"},
            "days_since_delivery": {"type": "number"},
            "product_opened": {"type": "boolean", "description": "true if opened/used; omit if unknown"},
            "issue_type": {"type": "string", "enum": ["change_of_mind", "damaged_or_defective"]}}}}},
    {"type": "function", "function": {
        "name": "cancel_order",
        "description": "Cancel an order. ONLY after the customer has explicitly confirmed they want to cancel.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                       "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "escalate_to_human",
        "description": "Log a request for a human agent when you cannot help or the customer insists on a human.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string"}, "order_id": {"type": "string"}},
            "required": ["reason"]}}},
]


def execute_tool(name: str, raw_args: str, orders: dict) -> dict:
    fn = REGISTRY.get(name)
    if not fn:
        return {"error": "unknown_tool", "message": f"Tool {name} does not exist."}
    try:
        args = json.loads(raw_args) if raw_args and raw_args.strip() else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    try:
        return fn(orders, **args)
    except TypeError:
        return {"error": "bad_arguments", "message": "Invalid tool arguments. Ask the customer to clarify."}
