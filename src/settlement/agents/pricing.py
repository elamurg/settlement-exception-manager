"""Price a model call in Decimal, from a versioned price list (config/pricing/v1.yaml)."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from settlement.agents.errors import PricingError
from settlement.agents.records import Usage

PER_MILLION = Decimal(1_000_000)
_TOP_LEVEL_KEYS = {"version", "currency", "models"}
_PRICE_KEYS = {"input", "output", "cache_read", "cache_write"}


@dataclass(frozen=True)
class ModelPrice:
    """Dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


@dataclass(frozen=True)
class PriceList:
    version: str
    currency: str
    models: Mapping[str, ModelPrice]


def cost_of(usage: Usage, model: str, prices: PriceList) -> Decimal:
    price = prices.models.get(model)
    if price is None:
        # Never price an unknown model at 0: a silent zero makes the budget lie.
        raise PricingError(prices.version, f"no price for model {model!r}")
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return total / PER_MILLION


def load_price_list(path: Path) -> PriceList:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise PricingError(path.name, f"cannot read file: {e}") from None
    return parse_price_list(text, source=path.name)


def parse_price_list(text: str, source: str = "pricing.yaml") -> PriceList:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PricingError(source, f"not valid YAML: {e}") from None
    if not isinstance(data, dict) or set(data) != _TOP_LEVEL_KEYS:
        raise PricingError(source, "expected exactly these keys: currency, models, version")
    raw_models = data["models"]
    if not isinstance(raw_models, dict) or not raw_models:
        raise PricingError(source, "models must be a non-empty mapping")
    return PriceList(
        version=_text(data["version"], source, "version"),
        currency=_text(data["currency"], source, "currency"),
        models={
            _text(name, source, "model name"): _price(raw, source, f"models.{name}")
            for name, raw in raw_models.items()
        },
    )


def _price(raw: Any, source: str, where: str) -> ModelPrice:
    if not isinstance(raw, dict) or set(raw) != _PRICE_KEYS:
        raise PricingError(source, f"{where} needs exactly: {', '.join(sorted(_PRICE_KEYS))}")
    return ModelPrice(
        input=_decimal(raw["input"], source, f"{where}.input"),
        output=_decimal(raw["output"], source, f"{where}.output"),
        cache_read=_decimal(raw["cache_read"], source, f"{where}.cache_read"),
        cache_write=_decimal(raw["cache_write"], source, f"{where}.cache_write"),
    )


def _text(value: Any, source: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PricingError(source, f"{where} must be a quoted, non-empty string, got {value!r}")
    return value.strip()


def _decimal(value: Any, source: str, where: str) -> Decimal:
    raw = _text(value, source, where)
    try:
        price = Decimal(raw)
    except InvalidOperation:
        raise PricingError(source, f"{where} is not a number: {raw!r}") from None
    if not price.is_finite() or price < 0:
        raise PricingError(source, f"{where} must be a number, 0 or more: {raw!r}")
    return price
