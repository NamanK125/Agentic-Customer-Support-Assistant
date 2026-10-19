"""Read-only order tools, their argument models and OpenAI-format schemas.

Every tool returns a ToolResult and never raises: unknown tool, invalid arguments, missing order
and store outages all become `ok=False` results the agent can reason about.

`confirmed_fields` lists only the keys the system of record actually confirmed (and that have a
value). `refund_estimate_inr` is never confirmed: the final amount depends on inspection.
`carrier_eta` is confirmed only while the order is shipped and the carrier has issued an ETA.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from support_assistant.contracts import ToolResult
from support_assistant.tools.orders import (
    Clock,
    Order,
    OrderItem,
    OrderStore,
    normalize_order_id,
    utc_now,
)

# ---- Policy (docs/domain.md, "Returns") -------------------------------------------------------

UNOPENED_WINDOW_DAYS = 30
OPENED_WINDOW_DAYS = 15
OPENED_RESTOCKING_FEE_PCT = 10
HYGIENE_SKUS = frozenset({"TH-01"})  # opened: not returnable unless defective
SUBSCRIPTION_SKUS = frozenset({"CARE-PLUS"})  # not refundable
WARRANTY_YEARS = {"BP-200": 2, "OX-02": 1, "TH-01": 1, "SC-03": 1}

GET_ORDER_STATUS = "get_order_status"
CHECK_RETURN_ELIGIBILITY = "check_return_eligibility"


# ---- Arguments ---------------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_id: str = Field(description="PulseNest order id, format PN-12345")

    @field_validator("order_id")
    @classmethod
    def _order_id(cls, v: str) -> str:
        normalized = normalize_order_id(v)
        if normalized is None:
            raise ValueError("order_id must look like PN-12345")
        return normalized


class GetOrderStatusArgs(_Args):
    pass


class CheckReturnEligibilityArgs(_Args):
    sku: str | None = Field(
        default=None, description="Product SKU in the order, e.g. BP-200; omit if only one item"
    )
    opened: bool | None = Field(
        default=None, description="Whether the customer says the item was opened; omit if unknown"
    )

    @field_validator("sku")
    @classmethod
    def _sku(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else None


def _schema(name: str, description: str, args: type[BaseModel]) -> dict[str, Any]:
    params = args.model_json_schema()
    params.pop("title", None)
    for prop in params.get("properties", {}).values():
        prop.pop("title", None)
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    GET_ORDER_STATUS: _schema(
        GET_ORDER_STATUS,
        "Look up an order's status, items, carrier/tracking and (only once shipped) "
        "the carrier ETA.",
        GetOrderStatusArgs,
    ),
    CHECK_RETURN_ELIGIBILITY: _schema(
        CHECK_RETURN_ELIGIBILITY,
        "Check whether an item in an order can be returned under PulseNest policy. Returns "
        "eligibility, reason, window end date, restocking fee, and an UNCONFIRMED refund estimate.",
        CheckReturnEligibilityArgs,
    ),
}


def _confirmed(data: dict[str, Any], keys: Iterable[str]) -> tuple[str, ...]:
    return tuple(k for k in keys if data.get(k) is not None)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


class OrderTools:
    """The tool belt for the tool agent. `call` is the only entry point an LLM can drive."""

    def __init__(self, store: OrderStore, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock
        self._impls: dict[str, tuple[type[_Args], Callable[..., Awaitable[ToolResult]]]] = {
            GET_ORDER_STATUS: (GetOrderStatusArgs, self.get_order_status),
            CHECK_RETURN_ELIGIBILITY: (CheckReturnEligibilityArgs, self.check_return_eligibility),
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._impls)

    def schemas(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        return [TOOL_SCHEMAS[n] for n in (names if names is not None else self._impls)]

    async def call(
        self, name: str, arguments: dict[str, Any], *, allowed: Iterable[str] | None = None
    ) -> ToolResult:
        allow = set(allowed) if allowed is not None else set(self._impls)
        if name not in self._impls or name not in allow:
            return ToolResult(tool=name, args=arguments, ok=False, error=f"unknown tool: {name}")
        model, impl = self._impls[name]
        try:
            args = model.model_validate(arguments)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return ToolResult(
                tool=name, args=arguments, ok=False, error=f"invalid arguments: {msg}"
            )
        return await impl(**args.model_dump())

    async def _lookup(self, tool: str, args: dict[str, Any]) -> Order | ToolResult:
        try:
            order = await self._store.get(args["order_id"])
        except Exception as exc:  # store outage must not crash the agent
            return ToolResult(
                tool=tool, args=args, ok=False, error=f"order system unavailable: {exc}"
            )
        if order is None:
            return ToolResult(tool=tool, args=args, ok=False, error="order_not_found")
        return order

    async def get_order_status(self, order_id: str) -> ToolResult:
        args = {"order_id": order_id}
        order = await self._lookup(GET_ORDER_STATUS, args)
        if isinstance(order, ToolResult):
            return order
        data: dict[str, Any] = {
            "order_id": order.order_id,
            "status": order.status,
            "placed_on": _iso(order.placed_on),
            "items": [{"sku": i.sku, "qty": i.qty} for i in order.items],
            "shipping_method": order.shipping_method,
            "carrier": order.carrier,
            "tracking_id": order.tracking_id,
            "carrier_eta": _iso(order.carrier_eta),
            "delivered_on": _iso(order.delivered_on),
        }
        keys = ["order_id", "status", "placed_on", "items", "shipping_method", "carrier",
                "tracking_id", "delivered_on"]  # fmt: skip
        if order.status == "shipped":  # a delivery date is a fact only once the carrier issues it
            keys.append("carrier_eta")
        return ToolResult(
            tool=GET_ORDER_STATUS,
            args=args,
            ok=True,
            data=data,
            confirmed_fields=_confirmed(data, keys),
        )

    async def check_return_eligibility(
        self, order_id: str, sku: str | None = None, opened: bool | None = None
    ) -> ToolResult:
        tool = CHECK_RETURN_ELIGIBILITY
        args = {"order_id": order_id, "sku": sku, "opened": opened}
        order = await self._lookup(tool, args)
        if isinstance(order, ToolResult):
            return order

        if sku is not None:
            matches = [i for i in order.items if i.sku == sku]
            if not matches:
                skus = [i.sku for i in order.items]
                return ToolResult(
                    tool=tool,
                    args=args,
                    ok=False,
                    error=f"sku {sku} not in order {order_id}; items: {skus}",
                )
            item = matches[0]
        elif len(order.items) == 1:
            item = order.items[0]
        else:
            skus = [i.sku for i in order.items]
            return ToolResult(
                tool=tool,
                args=args,
                ok=False,
                error=f"order has several items, specify sku: {skus}",
            )

        data = self._eligibility(order, item, opened)
        keys = ["order_id", "sku", "delivered_on", "eligible", "reason", "return_window_ends",
                "restocking_fee_pct"]  # fmt: skip
        return ToolResult(
            tool=tool, args=args, ok=True, data=data, confirmed_fields=_confirmed(data, keys)
        )

    def _eligibility(
        self, order: Order, item: OrderItem, opened_arg: bool | None
    ) -> dict[str, Any]:
        today = self._clock().date()
        if opened_arg is not None:
            opened, basis = opened_arg, "customer_stated"
        elif item.opened is not None:
            opened, basis = item.opened, "on_file"
        else:
            opened, basis = False, "assumed_unopened"

        data: dict[str, Any] = {
            "order_id": order.order_id,
            "sku": item.sku,
            "delivered_on": _iso(order.delivered_on),
            "opened": opened,
            "opened_basis": basis,
            "eligible": False,
            "reason": "",
            "return_window_ends": None,
            "restocking_fee_pct": None,
            "refund_estimate_inr": None,  # NEVER confirmed: final amount is set after inspection
        }

        def verdict(eligible: bool, reason: str) -> dict[str, Any]:
            data.update(eligible=eligible, reason=reason)
            return data

        if item.sku in SUBSCRIPTION_SKUS:
            return verdict(
                False,
                "Care+ subscription is not refundable; it can be cancelled anytime "
                "and the cancellation takes effect at the end of the billing month.",
            )
        if order.status == "cancelled":
            return verdict(
                False, "The order was cancelled before delivery, so there is nothing to return."
            )
        if order.status != "delivered" or order.delivered_on is None:
            return verdict(False, "Returns open once the order has been delivered.")

        delivered = order.delivered_on
        if item.defective:
            end = delivered + timedelta(days=365 * WARRANTY_YEARS.get(item.sku, 1))
            data.update(return_window_ends=_iso(end), restocking_fee_pct=0)
            return verdict(
                today <= end,
                "Defective item: replacement under warranty; a refund is "
                "issued only if a replacement is out of stock.",
            )
        if opened and item.sku in HYGIENE_SKUS:
            return verdict(
                False,
                "Opened TH-01 thermometers cannot be returned for hygiene reasons "
                "unless defective (defective units are replaced under warranty).",
            )

        window, fee = (
            (OPENED_WINDOW_DAYS, OPENED_RESTOCKING_FEE_PCT) if opened else (UNOPENED_WINDOW_DAYS, 0)
        )
        end = delivered + timedelta(days=window)
        state = "opened" if opened else "unopened"
        data.update(return_window_ends=_iso(end), restocking_fee_pct=fee)
        if today > end:
            return verdict(
                False,
                f"The {window}-day return window for {state} items ended on {end.isoformat()}.",
            )
        data["refund_estimate_inr"] = round(item.unit_price_inr * item.qty * (100 - fee) / 100)
        fee_note = f" with a {fee}% restocking fee" if fee else " for a full refund"
        return verdict(
            True, f"{state.capitalize()} item within the {window}-day return window{fee_note}."
        )
