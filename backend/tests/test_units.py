from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.core.pagination import InvalidCursor, decode_cursor, encode_cursor
from app.core.security import hash_password, verify_password
from app.core.ssrf import UnsafeUrl, assert_public_http_url, normalise_url
from app.services import profit, scoring
from app.services.exports import safe_cell
from app.services.identifiers import make_gtin13, normalise_gtin, title_tokens


def test_password_hash_roundtrip():
    h = hash_password("s3cret-password!")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "s3cret-password!")
    assert not verify_password(h, "wrong")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.5",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://192.168.1.1",
        "http://0.0.0.0",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_ssrf_blocks_private_addresses(url):
    with pytest.raises(UnsafeUrl):
        assert_public_http_url(url)


def test_ssrf_blocks_hostnames_resolving_private():
    def resolver(host, port):
        return [(None, None, None, None, ("10.1.2.3", 0))]

    with pytest.raises(UnsafeUrl):
        assert_public_http_url("https://internal.example.com", resolver=resolver)


def test_ssrf_allows_public():
    def resolver(host, port):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    assert assert_public_http_url("example.com/x", resolver=resolver) == "https://example.com/x"


@pytest.mark.parametrize("url", ["ftp://example.com", "https://user:pass@example.com", "javascript:alert(1)"])
def test_normalise_url_rejects(url):
    with pytest.raises(AppError):
        normalise_url(url)


def test_cursor_tamper_detected():
    c = encode_cursor({"id": 5})
    assert decode_cursor(c) == {"id": 5}
    body, mac = c.split(".")
    with pytest.raises(InvalidCursor):
        decode_cursor(body + "." + "0" * len(mac))


def test_gtin_normalisation():
    ean = make_gtin13("501234567890")
    assert normalise_gtin(ean) == ean.zfill(14)
    assert normalise_gtin("0" + ean) == ean.zfill(14)  # GTIN-14 form of the same product
    upc = "036000291452"  # valid UPC-A
    assert normalise_gtin(upc) == "00036000291452"
    assert normalise_gtin("036000291453") is None  # bad check digit
    assert normalise_gtin("123") is None
    assert normalise_gtin("00000000000000") is None


def test_title_tokens_ignore_noise():
    assert title_tokens("[DEMO] The NEW Yoga Mat - Pack of 2!") == {"yoga", "mat"}


def test_formula_injection_neutralised():
    assert safe_cell('=HYPERLINK("http://evil")') == '\'=HYPERLINK("http://evil")'
    assert safe_cell("+cmd") == "'+cmd"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("Normal title") == "Normal title"
    assert safe_cell(Decimal("1.50")) == 1.5


# ----------------------------------------------------------------- profit
def test_profit_us_no_tax():
    r = profit.calculate(
        profit.CostInputs(
            selling_price=Decimal("30"),
            currency_code="USD",
            supplier_cost=Decimal("8"),
            shipping_cost=Decimal("2"),
            fees=profit.FeeInputs(referral_rate=Decimal("0.15"), fulfilment_fee=Decimal("4")),
        )
    )
    # net 30; fees 4.5 + 4 = 8.5; total = 8 + 2 + 8.5 = 18.5; profit 11.5
    assert r.total_cost == Decimal("18.50")
    assert r.profit == Decimal("11.50")
    assert r.margin_pct == Decimal("38.33")
    assert r.roi_pct == Decimal("115.00")
    assert r.is_complete


def test_profit_uk_vat_inclusive():
    r = profit.calculate(
        profit.CostInputs(
            selling_price=Decimal("24.00"),
            currency_code="GBP",
            supplier_cost=Decimal("5"),
            fees=profit.FeeInputs(referral_rate=Decimal("0.15")),
            tax_rate=Decimal("0.20"),
            prices_include_tax=True,
        )
    )
    assert r.net_revenue == Decimal("20.00")
    assert r.tax_amount == Decimal("4.00")
    assert r.marketplace_fees == Decimal("3.60")  # fee on the gross price
    assert r.profit == Decimal("11.40")


def test_break_even_and_target_price_are_consistent():
    base = dict(
        currency_code="GBP",
        supplier_cost=Decimal("7.5"),
        shipping_cost=Decimal("1.2"),
        packaging_cost=Decimal("0.3"),
        returns_allowance_rate=Decimal("0.03"),
        tax_rate=Decimal("0.2"),
        prices_include_tax=True,
        fees=profit.FeeInputs(
            referral_rate=Decimal("0.153"),
            fixed_fee=Decimal("0.25"),
            fulfilment_fee=Decimal("2.70"),
            payment_rate=Decimal("0.0"),
        ),
        target_margin_pct=Decimal("25"),
    )
    r = profit.calculate(profit.CostInputs(**base))
    at_break_even = profit.calculate(profit.CostInputs(**base, selling_price=r.break_even_price))
    assert abs(at_break_even.profit) <= Decimal("0.02")
    at_target = profit.calculate(profit.CostInputs(**base, selling_price=r.target_margin_price))
    assert abs(at_target.margin_pct - Decimal("25")) < Decimal("0.1")


def test_profit_guards_and_missing_inputs():
    r = profit.calculate(profit.CostInputs(selling_price=Decimal("0"), supplier_cost=Decimal("0")))
    assert r.margin_pct is None and r.roi_pct is None
    assert "marketplace_fees" in r.missing_inputs and not r.is_complete
    r = profit.calculate(profit.CostInputs(selling_price=Decimal("10")))
    assert "supplier_cost" in r.missing_inputs
    # fees consume the whole price: no break-even exists
    r = profit.calculate(
        profit.CostInputs(supplier_cost=Decimal("1"), fees=profit.FeeInputs(referral_rate=Decimal("1")))
    )
    assert r.break_even_price is None


# ---------------------------------------------------------------- scoring
def test_missing_data_never_inflates_score():
    full = [scoring.Component(n, 0, 80.0, "") for n in scoring.DEFAULT_WEIGHTS]
    s_full = scoring.combine(full)
    assert s_full.score == 80.0 and s_full.confidence == 1.0
    partial = [scoring.Component(n, 0, 80.0 if i < 3 else None, "") for i, n in enumerate(scoring.DEFAULT_WEIGHTS)]
    s_partial = scoring.combine(partial)
    assert s_partial.score < s_full.score
    assert s_partial.confidence < 1.0
    assert set(s_partial.missing) == set(list(scoring.DEFAULT_WEIGHTS)[3:])
    low = [scoring.Component(n, 0, 10.0 if i == 0 else None, "") for i, n in enumerate(scoring.DEFAULT_WEIGHTS)]
    assert scoring.combine(low).score <= 10.0
    assert scoring.combine([scoring.Component("demand", 0, None, "")]).score == 0.0


def test_demand_score_monotonic_in_rank():
    assert scoring.demand_score(100, None).score > scoring.demand_score(100_000, None).score
