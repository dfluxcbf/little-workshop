"""Weighted tokens: every token type priced per model, so cache reads, cache writes and output of
different models add up in one unit (docs/pillars/usage.md, "Metrics"). The prices are list prices in
US dollars per million tokens. On a subscription the result stands in for plan consumption: it is
never money spent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CACHE_WRITE_5M = 1.25  # × input price
CACHE_WRITE_1H = 2.0


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float  # × input price


# Matched by substring against the model id, first match wins; "" is the fallback.
DEFAULT_PRICES: dict[str, Price] = {
    "fable": Price(10.0, 50.0, 0.025),
    "mythos": Price(10.0, 50.0, 0.025),
    "opus": Price(4.0, 20.0, 0.05),
    "sonnet": Price(2.0, 10.0, 0.1),
    "haiku": Price(1.0, 5.0, 0.1),
    "": Price(2.0, 10.0, 0.1),
}
PRICING_FILE = "pricing.json"


def load_prices(config_dir: Path) -> dict[str, Price]:
    """The defaults, with whatever `<usage config dir>/pricing.json` overrides:
    {"opus": {"input": 4, "output": 20, "cache_read": 0.05}, ...}."""
    prices = dict(DEFAULT_PRICES)
    try:
        custom = json.loads((config_dir / PRICING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prices
    for key, value in (custom.items() if isinstance(custom, dict) else ()):
        try:
            prices[str(key)] = Price(float(value["input"]), float(value["output"]), float(value["cache_read"]))
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def price_for(model: str, prices: dict[str, Price]) -> Price:
    model = (model or "").lower()
    for key, price in prices.items():
        if key and key in model:
            return price
    return prices.get("", DEFAULT_PRICES[""])


def weighted(request: dict, prices: dict[str, Price]) -> dict[str, float]:
    """One request's weighted tokens by type: {input, cache_read, cache_write, output}, in dollars at
    list price. A cache write is priced by its 1-hour share when the source knew it, else from the
    cost Claude Code estimated, else as a 5-minute write."""
    price = price_for(request.get("model") or "", prices)
    per = price.input / 1_000_000
    out = {
        "input": (request.get("input_tokens") or 0) * per,
        "cache_read": (request.get("cache_read_tokens") or 0) * per * price.cache_read,
        "output": (request.get("output_tokens") or 0) * price.output / 1_000_000,
    }
    write = request.get("cache_write_tokens") or 0
    one_hour = request.get("cache_write_1h_tokens")
    if one_hour is not None:
        out["cache_write"] = (min(one_hour, write) * CACHE_WRITE_1H + max(write - one_hour, 0) * CACHE_WRITE_5M) * per
    elif request.get("cost_usd"):
        out["cache_write"] = max(request["cost_usd"] - sum(out.values()), 0.0)
    else:
        out["cache_write"] = write * CACHE_WRITE_5M * per
    return out
