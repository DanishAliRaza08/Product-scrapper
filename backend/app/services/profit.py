"""Profit calculation with explicit tax treatment, fee profiles, dated FX and
break-even / target-margin pricing. All arithmetic uses Decimal.

Definitions (selling price P as listed, tax rate t):
  net revenue      = P / (1 + t) when listed prices include tax (e.g. UK VAT), else P
  marketplace fees = referral_rate x P + fixed + fulfilment   (fees are charged on the gross price)
  payment fees     = payment_rate x P + payment_fixed
  returns          = returns_allowance_rate x net revenue
  total cost       = supplier + shipping + marketplace fees + payment fees + ads + packaging + other + returns
  profit           = net revenue - total cost
  margin %         = profit / net revenue x 100            (guarded: None when net revenue is 0)
  ROI %            = profit / (supplier + shipping + packaging) x 100   (guarded)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

ZERO = Decimal("0")
CENT = Decimal("0.01")


def q(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(CENT, rounding=ROUND_HALF_UP)


class FeeInputs(BaseModel):
    referral_rate: Decimal = ZERO
    fixed_fee: Decimal = ZERO
    payment_rate: Decimal = ZERO
    payment_fixed_fee: Decimal = ZERO
    fulfilment_fee: Decimal = ZERO


class CostInputs(BaseModel):
    selling_price: Decimal | None = Field(default=None, ge=0)
    currency_code: str = "GBP"
    supplier_cost: Decimal | None = Field(default=None, ge=0)
    shipping_cost: Decimal = Field(default=ZERO, ge=0)
    advertising_cost: Decimal = Field(default=ZERO, ge=0)
    packaging_cost: Decimal = Field(default=ZERO, ge=0)
    other_costs: Decimal = Field(default=ZERO, ge=0)
    returns_allowance_rate: Decimal = Field(default=ZERO, ge=0, le=1)
    marketplace_fees_override: Decimal | None = Field(default=None, ge=0)
    fees: FeeInputs | None = None
    tax_rate: Decimal = Field(default=ZERO, ge=0, le=1)
    prices_include_tax: bool = False
    target_margin_pct: Decimal | None = Field(default=None, ge=0, lt=100)


@dataclass
class ProfitResult:
    currency_code: str
    selling_price: Decimal | None
    net_revenue: Decimal | None
    tax_amount: Decimal | None
    marketplace_fees: Decimal | None
    payment_fees: Decimal | None
    returns_allowance: Decimal | None
    total_cost: Decimal | None
    profit: Decimal | None
    margin_pct: Decimal | None
    roi_pct: Decimal | None
    break_even_price: Decimal | None
    target_margin_price: Decimal | None
    is_complete: bool
    missing_inputs: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    fx: list[dict] = field(default_factory=list)


def _solve_price(
    fixed_costs: Decimal, k: Decimal, variable_rate: Decimal, returns_rate: Decimal, margin: Decimal = ZERO
) -> Decimal | None:
    """Solve for P in: P*k - P*variable_rate - returns_rate*P*k - fixed = margin*P*k."""
    denominator = k * (1 - returns_rate - margin) - variable_rate
    if denominator <= 0:
        return None  # fees/returns consume the entire price: no price achieves the target
    return q(fixed_costs / denominator)


def calculate(inputs: CostInputs) -> ProfitResult:
    missing: list[str] = []
    assumptions: list[str] = []
    if inputs.selling_price is None:
        missing.append("selling_price")
    if inputs.supplier_cost is None:
        missing.append("supplier_cost")
    fees = inputs.fees
    if inputs.marketplace_fees_override is None and fees is None:
        missing.append("marketplace_fees")
    # Profit-derived figures need every cost input: an unknown cost is never treated as zero.
    costs_known = "supplier_cost" not in missing and "marketplace_fees" not in missing

    k = (Decimal(1) / (1 + inputs.tax_rate)) if inputs.prices_include_tax else Decimal(1)
    if inputs.prices_include_tax and inputs.tax_rate:
        assumptions.append(f"Listed price includes {inputs.tax_rate * 100:.1f}% tax, removed before profit.")
    elif inputs.tax_rate and not inputs.prices_include_tax:
        assumptions.append("Sales tax is collected on top of the listed price and is not seller revenue.")

    fees = fees or FeeInputs()
    variable_rate = fees.referral_rate + fees.payment_rate
    fixed_fees = fees.fixed_fee + fees.fulfilment_fee + fees.payment_fixed_fee
    base_costs = (
        (inputs.supplier_cost or ZERO)
        + inputs.shipping_cost
        + inputs.advertising_cost
        + inputs.packaging_cost
        + inputs.other_costs
    )
    if inputs.marketplace_fees_override is not None:
        variable_rate = ZERO
        fixed_fees = inputs.marketplace_fees_override
        assumptions.append("Marketplace fees use the explicit override amount.")

    break_even = _solve_price(base_costs + fixed_fees, k, variable_rate, inputs.returns_allowance_rate)
    target_price = None
    if inputs.target_margin_pct is not None:
        target_price = _solve_price(
            base_costs + fixed_fees, k, variable_rate, inputs.returns_allowance_rate, inputs.target_margin_pct / 100
        )

    if inputs.selling_price is None:
        return ProfitResult(
            inputs.currency_code,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            break_even if costs_known else None,
            target_price if costs_known else None,
            False,
            missing,
            assumptions,
        )

    p = inputs.selling_price
    net = p * k
    tax = p - net
    if inputs.marketplace_fees_override is not None:
        mkt_fees, pay_fees = inputs.marketplace_fees_override, ZERO
    else:
        mkt_fees = fees.referral_rate * p + fees.fixed_fee + fees.fulfilment_fee
        pay_fees = fees.payment_rate * p + fees.payment_fixed_fee
    returns = inputs.returns_allowance_rate * net
    total = base_costs + mkt_fees + pay_fees + returns
    profit = net - total
    margin = (profit / net * 100) if net > 0 else None
    invested = (inputs.supplier_cost or ZERO) + inputs.shipping_cost + inputs.packaging_cost
    roi = (profit / invested * 100) if invested > 0 else None
    return ProfitResult(
        currency_code=inputs.currency_code,
        selling_price=q(p),
        net_revenue=q(net),
        tax_amount=q(tax),
        marketplace_fees=q(mkt_fees),
        payment_fees=q(pay_fees),
        returns_allowance=q(returns),
        total_cost=q(total) if costs_known else None,
        profit=q(profit) if costs_known else None,
        margin_pct=q(margin) if costs_known else None,
        roi_pct=q(roi) if costs_known else None,
        break_even_price=break_even if costs_known else None,
        target_margin_price=target_price if costs_known else None,
        is_complete=not missing,
        missing_inputs=missing,
        assumptions=assumptions,
    )


def as_of_note(at: datetime | None) -> str:
    return f"Calculated {at.isoformat() if at else 'now'} from the cost inputs effective at that time."
