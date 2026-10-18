"""CSV import reads rows one at a time, keeps at most MAX_IMPORT_ROWS of them
and stops at values far longer than any column allows."""

import io
import tracemalloc

import pytest

from orderflow.extensions import db
from orderflow.models import Product
from orderflow.services import catalog

HEADER = "sku,name,price,gst_percent,category,unit,description,active\n"
# Just under the 1 MB upload limit: half a million one-letter rows
TINY_ROWS = (HEADER + "a\n" * 520_000).encode()


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def upload(client, content):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.post(
        "/app/products/import",
        data={"file": (io.BytesIO(content), "products.csv")},
        content_type="multipart/form-data",
    )


def peak_memory_of(function, *args):
    tracemalloc.start()
    try:
        try:
            function(*args)
        except catalog.CatalogError as error:
            outcome = str(error)
        else:
            outcome = None
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak, outcome


def test_a_file_of_tiny_rows_is_refused_without_building_every_row():
    assert len(TINY_ROWS) < 1024 * 1024
    peak, message = peak_memory_of(catalog.read_products_csv, TINY_ROWS)
    assert message.startswith("This file has 520,000 products. Import up to 5,000 at a time")
    # Building a row object for each line took over 200 MB here; reading them
    # one at a time needs little more than the decoded file itself
    assert peak < 25 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"


def test_rows_past_the_limit_are_counted_but_not_kept(monkeypatch):
    monkeypatch.setattr(catalog, "MAX_IMPORT_ROWS", 3)
    built = []
    real = catalog._csv_row
    monkeypatch.setattr(catalog, "_csv_row", lambda *args: built.append(args[0]) or real(*args))
    data = (HEADER + "".join(f"A-{n},Item,10,18,,,,\n" for n in range(10)) + "\n\n").encode()
    with pytest.raises(catalog.CatalogError, match="This file has 10 products. Import up to 3 at a time"):
        catalog.read_products_csv(data)
    assert built == [2, 3, 4]


def test_a_valid_file_at_the_limit_still_imports(client, signed_in, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_IMPORT_ROWS", 3)
    response = upload(client, HEADER + "".join(f"A-{n},Item {n},10,18,,,,\n" for n in range(3)))
    assert response.status_code == 302
    assert db.session.scalar(db.select(db.func.count(Product.id))) == 3


def test_the_route_refuses_a_megabyte_of_tiny_rows(client, signed_in):
    response = upload(client, TINY_ROWS)
    assert response.status_code == 200
    assert "This file has 520,000 products. Import up to 5,000 at a time" in response.get_data(as_text=True)


def test_a_value_far_longer_than_any_column_stops_the_import():
    long_value = "x" * (catalog.MAX_CSV_CELL_LENGTH + 1)
    data = (HEADER + "A-1,One,10,18,,,,\n" + f'A-2,Two,10,18,,,"{long_value}",\n' + "A-3,Three,10,18,,,,\n").encode()
    with pytest.raises(catalog.CatalogError) as caught:
        catalog.read_products_csv(data)
    assert str(caught.value).startswith(
        "We couldn't read this CSV file: a value on row 3 is longer than 5,000 characters."
    )
    assert "check that it ends with one too" in str(caught.value)


def test_a_missing_closing_quote_that_swallows_rows_is_explained(client, signed_in):
    rows = "".join(f"A-{n},Item,10,18,,,,\n" for n in range(2, 3000))
    # The quote opened on row 2 only closes on the last row, so it swallows everything between
    content = HEADER + 'A-1,"Item,10,18,,,,\n' + rows + 'A-3000,Item",10,18,,,,\n'
    body = upload(client, content).get_data(as_text=True)
    assert "a value on row 2 is longer than 5,000 characters" in body
    assert "check that it ends with one too" in body
    assert db.session.scalars(db.select(Product)).all() == []


def test_a_value_over_the_csv_reader_limit_is_explained(client, signed_in):
    body = upload(client, HEADER + f'A-1,"{"x" * 200_000}",10,18,,,,\n').get_data(as_text=True)
    assert "We couldn&#39;t read this CSV file: one value in this file is longer than 5,000 characters." in body


def test_a_long_description_is_still_a_row_problem(client, signed_in):
    body = upload(client, HEADER + f"A-1,One,10,18,,,{'d' * 2500},\n").get_data(as_text=True)
    assert "Keep the description to 2,000 characters or fewer." in body
    assert "longer than 5,000 characters" not in body


def test_a_pasted_paragraph_in_the_header_is_shortened_in_the_message():
    data = ("sku,name,price,gst_percent," + "word " * 200 + "\nA-1,One,10,18,x\n").encode()
    with pytest.raises(catalog.CatalogError) as caught:
        catalog.read_products_csv(data)
    message = str(caught.value)
    assert message.startswith("We don't recognise the column “word_word_word")
    assert "…”" in message and len(message) < 200


def test_header_problems_are_found_before_any_row_is_read(monkeypatch):
    built = []
    monkeypatch.setattr(catalog, "_csv_row", lambda *args: built.append(args[0]))
    with pytest.raises(catalog.CatalogError, match="missing the column gst_percent"):
        catalog.read_products_csv(("sku,name,price\n" + "A-1,One,10\n" * 100).encode())
    assert built == []
