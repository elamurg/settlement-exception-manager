from decimal import Decimal
from pathlib import Path

import pytest

from settlement.agents.errors import PricingError
from settlement.agents.pricing import cost_of, load_price_list, parse_price_list
from settlement.agents.records import Usage
from settlement.domain.errors import InvalidModel

PRICES = parse_price_list(
    """
version: "test"
currency: "USD"
models:
  claude-opus-5-5: {input: "4.00", output: "20.00", cache_read: "0.20", cache_write: "5.00"}
"""
)
PRICE_FILE = Path(__file__).parent.parent / "config" / "pricing" / "v1.yaml"


def test_1000_in_and_500_out_on_opus_costs_one_point_four_cents() -> None:
    usage = Usage(input_tokens=1000, output_tokens=500)

    # 1,000 x $4 / 1M = $0.004, plus 500 x $20 / 1M = $0.010
    assert cost_of(usage, "claude-opus-5-5", PRICES) == Decimal("0.014")


def test_cache_tokens_are_priced_too() -> None:
    usage = Usage(
        input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000
    )

    assert cost_of(usage, "claude-opus-5-5", PRICES) == Decimal("5.20")


def test_an_unknown_model_is_an_error_not_free() -> None:
    with pytest.raises(PricingError, match="no price for model 'mystery-model'"):
        cost_of(Usage(input_tokens=1, output_tokens=1), "mystery-model", PRICES)


def test_the_real_price_list_loads() -> None:
    prices = load_price_list(PRICE_FILE)

    assert prices.version == "v1"
    assert prices.models["claude-opus-5-5"].input == Decimal("4.00")


@pytest.mark.parametrize("bad", ['input: "-1"', 'input: "lots"', "input: 4.00"])
def test_bad_prices_are_rejected(bad: str) -> None:
    text = f"""
version: "test"
currency: "USD"
models:
  some-model:
    {bad}
    output: "1"
    cache_read: "1"
    cache_write: "1"
"""
    with pytest.raises(PricingError):
        parse_price_list(text)


def test_negative_tokens_are_rejected() -> None:
    with pytest.raises(InvalidModel):
        Usage(input_tokens=-1, output_tokens=0)
