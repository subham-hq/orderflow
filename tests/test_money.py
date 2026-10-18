from decimal import Decimal

import pytest

from orderflow.money import (
    format_inr,
    format_inr_compact,
    gst_amount,
    paise_input_value,
    parse_gst_rate,
    parse_price,
    rate_input_value,
    rate_label,
    to_paise,
)


def test_every_two_decimal_price_converts_exactly():
    # float(x) * 100 truncation stored about 6.5% of these one paisa low in v1
    for paise in range(0, 1_000_000, 7):
        assert to_paise(f"{paise // 100}.{paise % 100:02d}") == paise


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0.29", 29),
        ("19.99", 1999),
        ("1,250.50", 125050),
        (" 10 ", 1000),
        ("1e3", 100000),
        ("0.005", 1),
        ("2.675", 268),
        (Decimal("4.35"), 435),
    ],
)
def test_to_paise(text, expected):
    assert to_paise(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "inf", "-inf", "nan", "1.2.3", None])
def test_to_paise_rejects_non_numbers(text):
    with pytest.raises(ValueError):
        to_paise(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,250", 1_250_00),
        ("1,250.50", 1_250_50),
        ("12,500.5", 12_500_50),
        ("123,456", 123_456_00),
        ("1,234,567", 1_234_567_00),  # international grouping
        ("12,34,567.89", 12_34_567_89),  # Indian grouping
        ("1,00,00,000", 1_00_00_000_00),
        ("-1,250", -1_250_00),
        ("1,250.", 1_250_00),
    ],
)
def test_commas_that_group_digits_are_accepted(text, expected):
    assert to_paise(text) == expected


@pytest.mark.parametrize(
    "text",
    ["12,50", "0,99", "1,2", "1.250,00", ",5", "5,", "1,,250", "1,234,56,789", "12,3456", "1,2345", "1, 250", "१,२५०"],
)
def test_misplaced_commas_are_refused_rather_than_dropped(text):
    # Dropping the comma read "12,50" as ₹1,250.00 and "1.250,00" as ₹1.25
    with pytest.raises(ValueError, match="commas only between groups of digits"):
        to_paise(text)


def test_a_decimal_comma_in_the_product_form_is_refused(client, login, owner):
    from orderflow.extensions import db
    from orderflow.models import Product

    login(owner)
    form = {"name": "Carton", "sku": "CTN-001", "unit": "box", "price": "12,50", "gst_rate": "18", "is_active": "y"}
    response = client.post("/app/products/new", data=form)
    assert response.status_code == 200
    assert "commas only between groups of digits" in response.get_data(as_text=True)
    assert db.session.scalar(db.select(db.func.count(Product.id))) == 0

    response = client.post("/app/products/new", data={**form, "price": "₹1,250.50"})
    assert response.status_code == 302
    assert db.session.scalar(db.select(Product.price_paise)) == 1_250_50


@pytest.mark.parametrize("text", ["0", "-5", "0.001", "100000000.01", "1e400"])
def test_parse_price_rejects_out_of_range(text):
    with pytest.raises(ValueError):
        parse_price(text)


def test_parse_price_accepts_upper_bound():
    assert parse_price("10000000") == 1_000_000_000


@pytest.mark.parametrize("text, bp", [("18", 1800), ("0.25", 25), ("", 0), (None, 0), ("5", 500), ("28", 2800)])
def test_parse_gst_rate(text, bp):
    assert parse_gst_rate(text) == bp


@pytest.mark.parametrize("text", ["-1", "101", "abc", "nan", "1,8"])
def test_parse_gst_rate_rejects_bad_values(text):
    with pytest.raises(ValueError):
        parse_gst_rate(text)


def test_gst_amount_rounds_half_up():
    assert gst_amount(10_000, 1800) == 1800
    assert gst_amount(1, 1800) == 0  # 0.18 paise
    assert gst_amount(3, 1800) == 1  # 0.54 paise
    assert gst_amount(25, 200) == 1  # 0.5 paise rounds up
    assert gst_amount(0, 1800) == 0


@pytest.mark.parametrize(
    "paise, text",
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (100_000, "₹1,000.00"),
        (12_345_678_90, "₹1,23,45,678.90"),
        (1_00_00_000_00, "₹1,00,00,000.00"),
        (-2_500_00, "-₹2,500.00"),
        (None, "₹0.00"),
    ],
)
def test_format_inr_uses_indian_grouping(paise, text):
    assert format_inr(paise) == text


def test_format_inr_compact():
    assert format_inr_compact(99_900) == "₹999"
    assert format_inr_compact(4_20_000) == "₹4.2K"
    assert format_inr_compact(3_10_00_000) == "₹3.1L"
    assert format_inr_compact(1_25_00_00_000) == "₹1.3Cr"


@pytest.mark.parametrize(
    "paise, text",
    [
        (0, "₹0"),
        (None, "₹0"),
        (99_949, "₹999"),
        (99_950, "₹1K"),  # ₹999.50 was "₹1000"
        (99_94_999, "₹99.9K"),
        (99_95_000, "₹1L"),  # ₹99,950 was "₹100K"
        (99_94_99_999, "₹99.9L"),
        (99_95_00_000, "₹1Cr"),  # ₹99,95,000 was "₹100L"
        (99_99_999_00, "₹1Cr"),
        (-99_95_000, "-₹1L"),
        (12_34_50_00_00_000, "₹1,234.5Cr"),
        (2_56_000_00_00_000_00, "₹2,56,000Cr"),  # large values are grouped too
    ],
)
def test_format_inr_compact_rounds_before_choosing_the_unit(paise, text):
    assert format_inr_compact(paise) == text


def test_rate_and_input_helpers():
    assert rate_label(1800) == "18%"
    assert rate_label(25) == "0.25%"
    assert rate_label(0) == "0%"
    assert rate_input_value(1250) == "12.5"
    assert paise_input_value(1999) == "19.99"
    assert paise_input_value(None) == ""
