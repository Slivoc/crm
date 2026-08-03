import ast
import re
from pathlib import Path


def _load_scraper_helpers():
    source = Path("routes/parts_list_ai.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_parse_monroe_inventory", "_apply_monroe_availability"}
    ]
    namespace = {"re": re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), source, "exec"), namespace)
    return namespace["_parse_monroe_inventory"], namespace["_apply_monroe_availability"]


_parse_monroe_inventory, _apply_monroe_availability = _load_scraper_helpers()


def test_parse_monroe_inventory_treats_search_card_out_of_stock_as_zero():
    card_text = "CR2662-3-03\nCHERRY RIVET (CR2662-3-03)\nOut of Stock\nRequest Quote"

    assert _parse_monroe_inventory(card_text) == 0


def test_parse_monroe_inventory_treats_metadata_out_of_stock_as_zero():
    assert _parse_monroe_inventory("out_of_stock") == 0


def test_parse_monroe_inventory_reads_available_unit_count():
    assert _parse_monroe_inventory("1,250 units available") == 1250


def test_parse_monroe_inventory_reads_in_stock_unit_count():
    assert _parse_monroe_inventory("Availability: 37 units in stock") == 37


def test_parse_monroe_inventory_leaves_unknown_availability_unset():
    assert _parse_monroe_inventory("Contact us for availability") is None


def test_out_of_stock_availability_clears_transient_price_and_marks_no_bid():
    result = {
        "product_name": "CR2662-3-03",
        "unit_price": 12.34,
        "inventory": None,
        "error": None,
    }

    inventory = _apply_monroe_availability(result, "Out of Stock")

    assert inventory == 0
    assert result["inventory"] == 0
    assert result["unit_price"] is None
    assert "no bid" in result["error"]


def test_unknown_availability_clears_price_and_marks_no_bid():
    result = {
        "product_name": "CR2662-3-03",
        "unit_price": 12.34,
        "inventory": None,
        "error": None,
    }

    inventory = _apply_monroe_availability(result, "Contact us for availability")

    assert inventory is None
    assert result["inventory"] is None
    assert result["unit_price"] is None
    assert "no confirmed inventory" in result["error"]
