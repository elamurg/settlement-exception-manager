"""Command line: python -m settlement.generation --trades 5000 --seed 42"""

import argparse
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from settlement.generation.files import write_all
from settlement.generation.generator import (
    DEFAULT_BREAK_RATES,
    DEFAULT_TRADE_DATE,
    GenerationConfig,
    generate,
    scaled_rates,
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m settlement.generation",
        description="Generate a trade book, a custodian statement and a manifest of breaks.",
    )
    parser.add_argument("--trades", type=int, required=True, help="number of trades to book")
    parser.add_argument("--seed", type=int, required=True, help="same seed, same files")
    parser.add_argument("--out", type=Path, default=Path("data/generated"), help="output directory")
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=DEFAULT_TRADE_DATE,
        help="trade date, YYYY-MM-DD (settlement is the next business day)",
    )
    parser.add_argument(
        "--break-rate",
        type=float,
        default=None,
        help="share of trades with a break, keeping the default mix of types (default 0.10)",
    )
    args = parser.parse_args(argv)

    rates = DEFAULT_BREAK_RATES if args.break_rate is None else scaled_rates(args.break_rate)
    try:
        config = GenerationConfig(
            trades=args.trades, seed=args.seed, trade_date=args.trade_date, break_rates=rates
        )
    except ValueError as error:
        parser.error(str(error))

    data = generate(config)
    write_all(data, args.out)

    print(
        f"Wrote {len(data.book)} book rows, {len(data.statement)} statement lines "
        f"and {len(data.breaks)} injected breaks to {args.out}/"
    )


if __name__ == "__main__":
    main()
