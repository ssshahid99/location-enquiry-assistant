"""Deterministic price calculator. Reads config/pricing.yaml; uses Decimal so totals match to the paisa."""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from math import ceil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CENT = Decimal("0.01")


class PriceUnavailable(Exception):
    """No verified list price (e.g. HERE) - the answer must abstain, not guess."""


@lru_cache(maxsize=1)
def load_pricing() -> dict:
    return yaml.safe_load((ROOT / "config" / "pricing.yaml").read_text(encoding="utf-8"))


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def usd_to_inr(usd: Decimal | float | str) -> Decimal:
    return money(Decimal(str(usd)) * Decimal(str(load_pricing()["fx"]["usd_inr"])))


@dataclass
class Quote:
    sku: str
    provider: str
    currency: str
    units: int
    cost: Decimal                    # in `currency`, for the published tiers only
    cost_inr: Decimal
    breakdown: list = field(default_factory=list)   # dicts: from, to, units, rate, per, cost
    contact_sales: bool = False      # usage beyond the last published tier
    contact_sales_units: int = 0
    notes: list = field(default_factory=list)

    @property
    def billable_units(self) -> int:
        return sum(b["units"] for b in self.breakdown if b["rate"] > 0)

    @property
    def free_units(self) -> int:
        return sum(b["units"] for b in self.breakdown if b["rate"] == 0)


def quote(sku_id: str, units: int, *, tier_scale: int = 1) -> Quote:
    """Cost of `units` in one month. tier_scale multiplies tier bounds (for requests-vs-pairs semantics)."""
    p = load_pricing()
    if sku_id not in p["skus"]:
        raise PriceUnavailable(f"no verified price for {sku_id}")
    sku = p["skus"][sku_id]
    per = Decimal(sku["per"])
    total, prev, remaining = Decimal(0), 0, units
    breakdown = []
    for up_to, rate in sku["tiers"]:
        if remaining <= 0:
            break
        bound = None if up_to is None else up_to * tier_scale
        span = remaining if bound is None else min(remaining, bound - prev)
        if span > 0:
            c = Decimal(str(rate)) * Decimal(span) / per
            total += c
            breakdown.append({"from": prev + 1, "to": prev + span, "units": span, "rate": Decimal(str(rate)),
                              "per": sku["per"], "cost": c})
            remaining -= span
        if bound is not None:
            prev = bound
    contact = remaining > 0
    q = Quote(sku=sku_id, provider=sku["provider"], currency=sku["currency"], units=units,
              cost=money(total), cost_inr=money(total) if sku["currency"] == "INR" else usd_to_inr(total),
              breakdown=breakdown, contact_sales=contact, contact_sales_units=max(remaining, 0))
    if contact:
        q.notes.append(f"{remaining:,} units exceed the last published tier; contact sales for that portion")
    return q


# ---- unit helpers: turning a described workload into billable units ----

def matrix_elements(origins: int, destinations: int, calls: int = 1) -> int:
    return origins * destinations * calls


def ola_distance_matrix(origins: int, destinations: int, calls: int, *, basic: bool = False) -> Quote:
    """Ola bills per origin-destination pair. Whether the 100k free allowance counts pairs or requests is
    UNCONFIRMED; config/pricing.yaml `ola.distance_matrix_free_counts` selects the interpretation."""
    pairs = matrix_elements(origins, destinations, calls)
    mode = load_pricing()["ola"]["distance_matrix_free_counts"]
    sku = "ola.distance_matrix_basic" if basic else "ola.distance_matrix"
    q = quote(sku, pairs, tier_scale=(origins * destinations if mode == "requests" else 1))
    q.notes.append(f"free allowance and tier bounds counted in {mode} (UNCONFIRMED with Ola)")
    return q


def google_autocomplete_session_units(sessions: int, keystrokes_per_session: int, cap: int = 12) -> int:
    """Autocomplete (New) with a session token that ends in Place Details: only the first `cap` requests
    in a session are billed; the rest are free."""
    return sessions * min(keystrokes_per_session, cap)


def requests_needed(total_pairs: int, pairs_per_request: int) -> int:
    return ceil(total_pairs / pairs_per_request)
