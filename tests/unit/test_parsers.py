"""Unit Tests for Python and TypeScript Tree-Sitter Parsers
"""

import os
import pytest
from packages.core.parsers.python_parser import PythonParser
from packages.core.parsers.ts_js_parser import TypeScriptJavaScriptParser

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_repo")


def test_python_parser_math_ops():
    parser = PythonParser()
    file_path = os.path.join(FIXTURES_DIR, "math_ops.py")
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    res = parser.parse_code(code, file_path="math_ops.py")
    assert res.language == "python"
    assert len(res.syntax_errors) == 0

    node_names = {n.name: n for n in res.nodes}
    assert "add" in node_names
    assert "multiply" in node_names

    add_node = node_names["add"]
    assert add_node.node_type == "FUNCTION"
    assert "def add" in add_node.signature
    assert "Adds two numbers together" in add_node.docstring


def test_python_parser_calculator():
    parser = PythonParser()
    file_path = os.path.join(FIXTURES_DIR, "calculator.py")
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    res = parser.parse_code(code, file_path="calculator.py")
    assert res.language == "python"

    node_names = {n.name: n for n in res.nodes}
    assert "Calculator" in node_names
    assert "calculate_sum" in node_names
    assert "calculate_product" in node_names

    calc_node = node_names["Calculator"]
    assert calc_node.node_type == "CLASS"
    assert "Stateful calculator" in calc_node.docstring

    sum_node = node_names["calculate_sum"]
    assert sum_node.node_type == "METHOD"
    assert sum_node.qualified_name == "Calculator.calculate_sum"

    # Verify imports
    assert len(res.imports) >= 1
    imp = res.imports[0]
    assert imp.module_path == "math_ops"
    assert "add" in imp.imported_symbols
    assert "multiply" in imp.imported_symbols

    # Verify calls
    callees = {c.callee_name for c in res.calls}
    assert "add" in callees
    assert "multiply" in callees


def test_python_parser_tests():
    parser = PythonParser()
    file_path = os.path.join(FIXTURES_DIR, "test_calculator.py")
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    res = parser.parse_code(code, file_path="test_calculator.py")
    node_types = {n.name: n.node_type for n in res.nodes}
    assert node_types.get("test_calculator_sum") == "TEST"
    assert node_types.get("test_calculator_product") == "TEST"


def test_typescript_parser_formatter():
    parser = TypeScriptJavaScriptParser()
    file_path = os.path.join(FIXTURES_DIR, "formatter.ts")
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    res = parser.parse_code(code, file_path="formatter.ts")
    assert res.language == "typescript"
    assert len(res.syntax_errors) == 0

    node_names = {n.name: n for n in res.nodes}
    assert "PrintOptions" in node_names
    assert node_names["PrintOptions"].node_type == "INTERFACE"

    assert "formatResult" in node_names
    assert node_names["formatResult"].node_type == "FUNCTION"


def test_typescript_parser_app():
    parser = TypeScriptJavaScriptParser()
    file_path = os.path.join(FIXTURES_DIR, "app.ts")
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    res = parser.parse_code(code, file_path="app.ts")
    assert len(res.imports) >= 1
    assert res.imports[0].module_path == "./formatter"
    assert "formatResult" in res.imports[0].imported_symbols

    callees = {c.callee_name for c in res.calls}
    assert "formatResult" in callees
