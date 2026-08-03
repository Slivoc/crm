import ast
import re
from pathlib import Path


def _load_inventory_parser():
    source = Path("routes/parts_list_ai.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_monroe_inventory"
    )
    namespace = {"re": re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), source, "exec"), namespace)
    return namespace["_parse_monroe_inventory"]


_parse_monroe_inventory = _load_inventory_parser()


def test_parse_monroe_inventory_treats_search_card_out_of_stock_as_zero():
    card_text = "CR2662-3-03\nCHERRY RIVET (CR2662-3-03)\nOut of Stock\nRequest Quote"

    assert _parse_monroe_inventory(card_text) == 0


def test_parse_monroe_inventory_reads_available_unit_count():
    assert _parse_monroe_inventory("1,250 units available") == 1250


def test_parse_monroe_inventory_leaves_unknown_availability_unset():
    assert _parse_monroe_inventory("Contact us for availability") is None
