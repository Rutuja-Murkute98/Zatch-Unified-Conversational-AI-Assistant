"""
WHAT:
    Tests the two trimmers that shape what the model SEES - product lists
    and order history.

WHY THESE TWO:
    Both were written after watching real answers, not from theory.

    Product lists repeated themselves: a measured similar-products result
    returned "jeans" three times at Rs.800, because the catalogue holds
    several genuinely distinct rows that are indistinguishable to a
    shopper. A list that repeats itself reads as broken.

    Order history was 1,574 tokens of raw documents for five orders, and
    carried expectedDelivery dates a month in the past with nothing
    marking them late - so the assistant reported "expected by 27 July"
    on 26 August, which is true and useless. A model cannot spot that: it
    does not know today's date. The comparison has to happen here.

FLOW:
    Pure unit tests over the trimmer functions. No database, no LLM.
"""

from datetime import datetime, timedelta, timezone

from app.agent.tool_executor import _trim_order_history, _trim_product_list

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def product(name, price, discounted=None, _id="1" * 24):
    return {
        "_id": _id, "name": name, "price": price,
        "discountedPrice": discounted if discounted is not None else price,
        "category": "Men",
    }


def order(order_id, status, expected_days_ago=None, items=("thing",)):
    dates = {}
    if expected_days_ago is not None:
        dates["expectedDelivery"] = NOW - timedelta(days=expected_days_ago)
    return {
        "orderId": order_id, "status": status, "dates": dates,
        "items": [{"name": n, "qty": 1} for n in items],
        "pricing": {"total": 100},
        # Fields the model never needs, present to prove they are dropped.
        "buyerId": "x", "sellerId": "y", "statusHistory": [{"status": "pending"}],
        "review": {"images": []}, "deliveryAddress": {"city": "Pune"},
    }


class TestProductListDeduping:
    def test_identical_name_and_price_collapse(self):
        rows = _trim_product_list([
            product("jeans", 1000, 800, "a" * 24),
            product("jeans", 1000, 800, "b" * 24),
            product("jeans", 1000, 800, "c" * 24),
        ])
        assert len(rows) == 1, "the same line three times reads as broken"

    def test_same_name_different_price_is_kept(self):
        """A real choice the shopper can act on, not a duplicate."""
        rows = _trim_product_list([
            product("jeans", 1000, 800, "a" * 24),
            product("jeans", 1500, 1200, "b" * 24),
        ])
        assert len(rows) == 2

    def test_case_and_whitespace_do_not_defeat_it(self):
        rows = _trim_product_list([
            product("Jeans ", 1000, 800, "a" * 24),
            product("jeans", 1000, 800, "b" * 24),
        ])
        assert len(rows) == 1

    def test_distinct_products_all_survive(self):
        rows = _trim_product_list([
            product("jeans", 1000, 800, "a" * 24),
            product("Baggy Blue Street", 2099, 1449, "b" * 24),
            product("Denim Model", 1449, 1349, "c" * 24),
        ])
        assert len(rows) == 3

    def test_the_hard_cap_still_applies(self):
        rows = _trim_product_list([product(f"p{i}", 100 + i) for i in range(50)])
        assert len(rows) <= 8

    def test_empty_input_is_safe(self):
        assert _trim_product_list([]) == []
        assert _trim_product_list(None) == []


class TestOrderHistoryTrimming:
    def test_internal_fields_are_dropped(self):
        row = _trim_order_history([order("ORD1", "confirmed")])[0]
        for gone in ("buyerId", "sellerId", "statusHistory", "review",
                     "deliveryAddress"):
            assert gone not in row

    def test_what_the_answer_needs_survives(self):
        row = _trim_order_history([order("ORD1", "confirmed", items=("clock",))])[0]
        assert row["orderId"] == "ORD1"
        assert row["status"] == "confirmed"
        assert row["items"][0]["name"] == "clock"
        assert row["total"] == 100


class TestOverdueDetection:
    def test_a_late_in_progress_order_is_flagged(self):
        """The model cannot work this out - it does not know the date."""
        row = _trim_order_history([order("ORD1", "confirmed", expected_days_ago=29)])[0]
        assert row["isOverdue"] is True
        assert row["daysLate"] == 29

    def test_a_future_delivery_is_not_flagged(self):
        future = {
            **order("ORD1", "confirmed"),
            "dates": {"expectedDelivery": NOW + timedelta(days=3)},
        }
        row = _trim_order_history([future])[0]
        assert "isOverdue" not in row

    def test_a_delivered_order_is_never_late(self):
        """It arrived. Whether it beat its estimate is not the question a
        shopper is asking, and calling it overdue would be wrong."""
        row = _trim_order_history([order("ORD1", "delivered", expected_days_ago=40)])[0]
        assert "isOverdue" not in row

    def test_a_cancelled_order_is_never_late(self):
        row = _trim_order_history([order("ORD1", "cancelled", expected_days_ago=90)])[0]
        assert "isOverdue" not in row

    def test_a_missing_date_does_not_crash(self):
        row = _trim_order_history([order("ORD1", "confirmed", expected_days_ago=None)])[0]
        assert "isOverdue" not in row
        assert row["expectedDelivery"] is None
