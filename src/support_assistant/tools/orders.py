"""Order records and the read-only store behind the order tools.

The JSON file stores dates as offsets from "today" so the demo data never goes stale; the store
resolves them against an injected clock, which also makes tests deterministic.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

Clock = Callable[[], datetime]

ORDER_ID_RE = re.compile(r"\bPN[-\s]?(\d{5})\b", re.IGNORECASE)
DEFAULT_ORDERS_PATH = Path(__file__).resolve().parents[3] / "data" / "orders.json"


def utc_now() -> datetime:
    return datetime.now(UTC)


def find_order_ids(text: str) -> list[str]:
    """All order ids in `text`, normalised to `PN-12345`, in order of appearance."""
    return [f"PN-{m.group(1)}" for m in ORDER_ID_RE.finditer(text)]


def normalize_order_id(value: str) -> str | None:
    """`pn 10001` / `PN10001` / `PN-10001` -> `PN-10001`; anything else -> None."""
    m = ORDER_ID_RE.fullmatch(value.strip())
    return f"PN-{m.group(1)}" if m else None


OrderStatus = Literal["processing", "shipped", "delivered", "cancelled", "active"]


class OrderItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    qty: int = 1
    unit_price_inr: int
    opened: bool | None = None  # as reported by the customer on file; None = unknown
    defective: bool = False


class Order(BaseModel):
    """An order with absolute dates (offsets already resolved against the clock)."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    status: OrderStatus
    placed_on: date
    shipping_method: str
    items: tuple[OrderItem, ...]
    carrier: str | None = None
    tracking_id: str | None = None
    carrier_eta: date | None = None
    delivered_on: date | None = None


@runtime_checkable
class OrderStore(Protocol):
    """System of record for orders. Async so a database-backed store can replace the JSON one."""

    async def get(self, order_id: str) -> Order | None: ...


class JsonOrderStore:
    def __init__(self, path: Path = DEFAULT_ORDERS_PATH, clock: Clock = utc_now) -> None:
        self._path = path
        self._clock = clock
        self._raw: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> dict[str, dict[str, Any]]:
        if self._raw is None:
            async with self._lock:
                if self._raw is None:
                    text = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
                    self._raw = {o["order_id"]: o for o in json.loads(text)["orders"]}
        return self._raw

    async def get(self, order_id: str) -> Order | None:
        rec = (await self._load()).get(order_id)
        return None if rec is None else self._resolve(rec)

    def _resolve(self, rec: dict[str, Any]) -> Order:
        today = self._clock().date()

        def ago(key: str) -> date | None:
            return today - timedelta(days=rec[key]) if rec.get(key) is not None else None

        eta_days = rec.get("carrier_eta_days_from_now")
        return Order(
            order_id=rec["order_id"],
            status=rec["status"],
            placed_on=today - timedelta(days=rec["placed_days_ago"]),
            shipping_method=rec.get("shipping_method", "standard"),
            items=tuple(OrderItem(**i) for i in rec["items"]),
            carrier=rec.get("carrier"),
            tracking_id=rec.get("tracking_id"),
            carrier_eta=today + timedelta(days=eta_days) if eta_days is not None else None,
            delivered_on=ago("delivered_days_ago"),
        )
