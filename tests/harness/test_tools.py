"""Order tools implement docs/domain.md exactly, and confirmed_fields are honest."""

from __future__ import annotations

import pytest

from support_assistant.tools import (
    CHECK_RETURN_ELIGIBILITY,
    GET_ORDER_STATUS,
    TOOL_SCHEMAS,
    OrderTools,
)


@pytest.mark.parametrize(
    ("order_id", "eligible", "fee", "window_end", "estimate"),
    [
        ("PN-10001", False, None, None, None),  # processing: not delivered yet
        ("PN-10002", False, None, None, None),  # shipped: not delivered yet
        ("PN-10003", True, 0, "2026-10-27", 2999),  # unopened, day 22 of 30
        ("PN-10004", True, 10, "2026-10-24", 3149),  # opened BP-200, day 10 of 15
        ("PN-10005", False, 10, "2026-10-14", None),  # opened OX-02, day 20 > 15
        ("PN-10006", False, None, None, None),  # opened TH-01: hygiene
        ("PN-10007", False, None, None, None),  # cancelled
        ("PN-10008", False, None, None, None),  # Care+: not refundable
        ("PN-10009", False, 0, "2026-10-09", None),  # unopened, day 40 > 30
    ],
)
async def test_return_policy(order_tools, order_id, eligible, fee, window_end, estimate):
    r = await order_tools.check_return_eligibility(order_id)
    assert r.ok
    assert r.data["eligible"] is eligible
    assert r.data["restocking_fee_pct"] == fee
    assert r.data["return_window_ends"] == window_end
    assert r.data["refund_estimate_inr"] == estimate
    assert "refund_estimate_inr" not in r.confirmed_fields
    assert {"eligible", "reason"} <= set(r.confirmed_fields)
    assert all(r.data[k] is not None for k in r.confirmed_fields)


async def test_customer_statement_overrides_record(order_tools):
    # PN-10003 is unopened on file; if the customer says it was opened, day 22 > 15.
    r = await order_tools.check_return_eligibility("PN-10003", opened=True)
    assert r.data["eligible"] is False
    assert r.data["opened_basis"] == "customer_stated"


async def test_opened_th01_reason_mentions_hygiene(order_tools):
    r = await order_tools.check_return_eligibility("PN-10006")
    assert "hygiene" in r.data["reason"]


async def test_eta_confirmed_only_when_shipped_with_carrier_eta(order_tools):
    shipped = await order_tools.get_order_status("PN-10002")
    assert shipped.data["carrier_eta"] == "2026-10-21"
    assert "carrier_eta" in shipped.confirmed_fields
    for oid in ("PN-10001", "PN-10003", "PN-10007"):
        r = await order_tools.get_order_status(oid)
        assert "carrier_eta" not in r.confirmed_fields


async def test_call_validates_and_never_raises(order_tools: OrderTools):
    unknown = await order_tools.call("issue_refund", {"order_id": "PN-10004"})
    assert not unknown.ok and "unknown tool" in (unknown.error or "")
    bad = await order_tools.call(GET_ORDER_STATUS, {"order_id": "12345; drop table"})
    assert not bad.ok and "invalid arguments" in (bad.error or "")
    missing = await order_tools.call(GET_ORDER_STATUS, {"order_id": "PN-99999"})
    assert not missing.ok and missing.error == "order_not_found"
    normalized = await order_tools.call(CHECK_RETURN_ELIGIBILITY, {"order_id": "pn 10004"})
    assert normalized.ok and normalized.data["order_id"] == "PN-10004"
    disallowed = await order_tools.call(
        CHECK_RETURN_ELIGIBILITY, {"order_id": "PN-10004"}, allowed=[GET_ORDER_STATUS]
    )
    assert not disallowed.ok


async def test_store_outage_becomes_error_result():
    class Broken:
        async def get(self, order_id: str):
            raise ConnectionError("db down")

    r = await OrderTools(Broken()).get_order_status("PN-10001")
    assert not r.ok and "unavailable" in (r.error or "")


def test_schemas_are_openai_function_format():
    for name, schema in TOOL_SCHEMAS.items():
        assert schema["type"] == "function"
        assert schema["function"]["name"] == name
        assert schema["function"]["parameters"]["required"] == ["order_id"]
