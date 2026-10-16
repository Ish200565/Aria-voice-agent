import copy

import pytest

from app.data import MOCK_ORDERS
from app.sentences import SentenceSplitter
from app.tools import (cancel_order, check_return_eligibility, execute_tool,
                       get_order_details, normalize_order_id)


@pytest.fixture
def orders():
    return copy.deepcopy(MOCK_ORDERS)


@pytest.mark.parametrize("raw,expected", [
    ("ORD-101", "ORD-101"), ("ord 101", "ORD-101"), ("order 101", "ORD-101"),
    ("101", "ORD-101"), ("order one zero one", "ORD-101"), ("o r d one oh two", "ORD-102"),
    ("1 0 3", "ORD-103"), ("ORD101", "ORD-101"), ("one hundred and three", "ORD-103"),
    ("order ID is ord-102 please", "ORD-102"), ("", None), ("hello", None), (None, None),
])
def test_normalize(raw, expected):
    assert normalize_order_id(raw) == expected


def test_lookup_found(orders):
    r = get_order_details(orders, "order one zero one")
    assert r["found"] and r["status"] == "Out for Delivery" and not r["can_cancel"]
    assert r["can_refuse_at_doorstep"]


def test_lookup_not_found_and_missing(orders):
    assert get_order_details(orders, "ORD-999")["error"] == "order_not_found"
    assert get_order_details(orders, None)["error"] == "missing_or_unreadable_order_id"


def test_return_20_days_opened(orders):
    r = check_return_eligibility(orders, days_since_delivery=20, product_opened=True)
    assert r["eligible"] is False and set(r["reasons"]) == {"outside_7_day_window", "opened_or_used"}


def test_ord102_return_outside_window(orders):
    r = check_return_eligibility(orders, order_id="ORD-102", product_opened=False)
    assert r["eligible"] is False and "outside_7_day_window" in r["reasons"]


def test_ord102_damage_window_passed(orders):
    r = check_return_eligibility(orders, order_id="ORD-102", issue_type="damaged_or_defective")
    assert r["eligible"] is False


def test_return_within_window(orders):
    assert check_return_eligibility(orders, days_since_delivery=3)["needs_info"] == "product_opened"
    assert check_return_eligibility(orders, days_since_delivery=3, product_opened=False)["eligible"] is True
    assert check_return_eligibility(orders, days_since_delivery=3, product_opened=True)["eligible"] is False
    assert check_return_eligibility(orders, days_since_delivery=7, product_opened=False)["eligible"] is True


def test_damaged_within_48h(orders):
    r = check_return_eligibility(orders, days_since_delivery=1, issue_type="damaged_or_defective")
    assert r["eligible"] is True and r["resolution"] == "replacement"


def test_return_needs_info(orders):
    assert check_return_eligibility(orders)["needs_info"] == "order_id_or_days_since_delivery"


def test_return_not_delivered(orders):
    r = check_return_eligibility(orders, order_id="ORD-101")
    assert r["eligible"] is False and r["reasons"] == ["not_delivered_yet"]


def test_cancel_rules(orders):
    assert cancel_order(orders, "ORD-101")["reason"] == "already_shipped"
    assert cancel_order(orders, "ORD-102")["reason"] == "already_delivered"
    assert cancel_order(orders, "ORD-103")["success"] is True
    assert cancel_order(orders, "ORD-103")["reason"] == "already_cancelled"


def test_cancel_does_not_leak_across_sessions(orders):
    cancel_order(orders, "ORD-103")
    assert MOCK_ORDERS["ORD-103"]["status"] == "Processing"   # global DB untouched


def test_execute_tool_robustness(orders):
    assert execute_tool("nope", "{}", orders)["error"] == "unknown_tool"
    assert execute_tool("get_order_details", "{bad json", orders)["found"] is False
    assert execute_tool("get_order_details", '{"order_id": "ORD-101", "junk": 1}', orders)["error"] == "bad_arguments"


def test_splitter_basic_and_decimals():
    sp = SentenceSplitter()
    out = sp.feed("Your order is out for delivery. It costs 2.5 times more")
    assert out == ["Your order is out for delivery."]
    out2 = sp.feed("? Really. ")
    assert out2[0].startswith("It costs 2.5 times more")
    assert sp.flush() is None


def test_splitter_early_comma_and_markdown():
    sp = SentenceSplitter()
    # tiny lead-in ("Sure,") is NOT cut early - choppy audio chunks sound bad
    assert sp.feed("Sure, I checked your order and it should reach you by six PM today") == []
    sp = SentenceSplitter()
    out = sp.feed("I checked your order status just now, and it should reach you by six PM today")
    assert out == ["I checked your order status just now,"]
    assert sp.flush() == "and it should reach you by six PM today"
    assert SentenceSplitter().feed("**Hello** there. ") == ["Hello there."]
