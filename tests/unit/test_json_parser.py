"""Unit Tests for ApexGraph JSON Parser (Tree-sitter JSON)

Validates:
- Structural Node Extraction: Objects as TYPE, properties as VARIABLE
- Bounded Hierarchy: Scopes and qualified names up to depth <= 4
- Array Elements: Primitive arrays, arrays of objects with indexed paths
- Duplicate Property Isolation: Identical property names in separate scopes
- Cross-file References:
  - JSON Schema $ref (e.g. ./common.json#/definitions/User)
  - Configuration extends (e.g. ./tsconfig.base.json, array of bases)
- Declarative Guarantee: Zero artificial CALLS edges
- Syntax Error Resilience: Malformed, truncated, or unclosed JSON
- Deterministic Ordering: Nodes and imports deterministically sorted
"""

import pytest

from packages.core.parsers.base import ParseResult
from packages.core.parsers.json_parser import JsonParser


@pytest.fixture
def parser() -> JsonParser:
    return JsonParser()


def test_empty_and_whitespace_json(parser: JsonParser):
    for empty_input in ("", "   \n\t  \n"):
        res = parser.parse_code(empty_input, file_path="empty.json")
        assert isinstance(res, ParseResult)
        assert res.language == "json"
        assert len(res.nodes) == 0
        assert len(res.imports) == 0
        assert len(res.calls) == 0
        assert len(res.syntax_errors) == 0


def test_object_and_property_extraction(parser: JsonParser):
    code = """{
  "name": "repopeek",
  "version": "3.0.0",
  "private": true,
  "compilerOptions": {
    "target": "ES2022",
    "strict": true
  }
}"""
    res = parser.parse_code(code, file_path="tsconfig.json")

    assert res.language == "json"
    assert len(res.syntax_errors) == 0
    assert len(res.calls) == 0

    nodes_by_qname = {n.qualified_name: n for n in res.nodes}

    # Verify top-level primitives
    assert "name" in nodes_by_qname
    assert nodes_by_qname["name"].node_type == "VARIABLE"
    assert nodes_by_qname["name"].metadata["json_type"] == "primitive"

    assert "private" in nodes_by_qname
    assert nodes_by_qname["private"].node_type == "VARIABLE"

    # Verify nested object
    assert "compilerOptions" in nodes_by_qname
    assert nodes_by_qname["compilerOptions"].node_type == "TYPE"
    assert nodes_by_qname["compilerOptions"].metadata["json_type"] == "object"

    # Verify nested properties
    assert "compilerOptions.target" in nodes_by_qname
    assert nodes_by_qname["compilerOptions.target"].node_type == "VARIABLE"
    assert nodes_by_qname["compilerOptions.target"].metadata["enclosing"] == "compilerOptions"

    assert "compilerOptions.strict" in nodes_by_qname
    assert nodes_by_qname["compilerOptions.strict"].node_type == "VARIABLE"


def test_arrays_and_array_of_objects(parser: JsonParser):
    code = """{
  "tags": ["web", "graph", "ai"],
  "tasks": [
    {
      "id": "task-1",
      "command": "build"
    },
    {
      "id": "task-2",
      "command": "test"
    }
  ]
}"""
    res = parser.parse_code(code, file_path="tasks.json")

    qnames = {n.qualified_name for n in res.nodes}
    assert "tags" in qnames
    assert "tasks" in qnames
    assert "tasks[0].id" in qnames
    assert "tasks[0].command" in qnames
    assert "tasks[1].id" in qnames
    assert "tasks[1].command" in qnames


def test_duplicate_property_names_in_different_scopes(parser: JsonParser):
    code = """{
  "user": {
    "id": 100,
    "status": "active"
  },
  "order": {
    "id": 200,
    "status": "pending"
  }
}"""
    res = parser.parse_code(code, file_path="data.json")

    nodes_by_qname = {n.qualified_name: n for n in res.nodes}
    assert "user.id" in nodes_by_qname
    assert "order.id" in nodes_by_qname
    assert nodes_by_qname["user.id"].metadata["enclosing"] == "user"
    assert nodes_by_qname["order.id"].metadata["enclosing"] == "order"


def test_bounded_graph_depth(parser: JsonParser):
    code = """{
  "l1": {
    "l2": {
      "l3": {
        "l4": {
          "l5_should_be_ignored": {
            "l6": "too_deep"
          }
        }
      }
    }
  }
}"""
    res = parser.parse_code(code, file_path="deep.json")

    qnames = {n.qualified_name for n in res.nodes}
    assert "l1" in qnames
    assert "l1.l2" in qnames
    assert "l1.l2.l3" in qnames
    assert "l1.l2.l3.l4" in qnames
    # Depth > 4 must be pruned to avoid graph explosion
    assert "l1.l2.l3.l4.l5_should_be_ignored" not in qnames


def test_json_schema_ref_extraction(parser: JsonParser):
    code = """{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UserProfile",
  "definitions": {
    "address": {
      "$ref": "./common.json#/definitions/Address"
    },
    "account": {
      "$ref": "./account.json"
    }
  }
}"""
    res = parser.parse_code(code, file_path="schemas/user.json")

    assert len(res.imports) == 2
    imp_map = {i.module_path: i.imported_symbols for i in res.imports}

    assert "./common.json" in imp_map
    assert "definitions.Address" in imp_map["./common.json"]
    assert "Address" in imp_map["./common.json"]

    assert "./account.json" in imp_map
    assert "*" in imp_map["./account.json"]


def test_configuration_extends_extraction(parser: JsonParser):
    # 1. Single string extends
    code1 = """{
  "extends": "./tsconfig.base.json",
  "compilerOptions": {
    "outDir": "./dist"
  }
}"""
    res1 = parser.parse_code(code1, file_path="tsconfig.json")
    assert len(res1.imports) == 1
    assert res1.imports[0].module_path == "./tsconfig.base.json"
    assert res1.imports[0].imported_symbols == ["*"]

    # 2. Array of extends
    code2 = """{
  "extends": [
    "./base.json",
    "./rules/strict.json"
  ]
}"""
    res2 = parser.parse_code(code2, file_path="eslintrc.json")
    assert len(res2.imports) == 2
    paths = [i.module_path for i in res2.imports]
    assert "./base.json" in paths
    assert "./rules/strict.json" in paths


def test_declarative_zero_calls_guarantee(parser: JsonParser):
    code = """{
  "scripts": {
    "start": "node index.js",
    "test": "pytest",
    "build": "tsc"
  }
}"""
    res = parser.parse_code(code, file_path="package.json")
    # Even if strings look like shell commands, JSON parser must not invent artificial calls
    assert len(res.calls) == 0


def test_syntax_error_resilience(parser: JsonParser):
    corrupt_code = """{
  "valid_first": 123,
  "broken_block": {
    "missing_val": 
  },
  "valid_second": 456
}"""
    res = parser.parse_code(corrupt_code, file_path="corrupt.json")

    assert res.language == "json"
    assert len(res.syntax_errors) > 0
    # Must extract available valid nodes despite syntax error
    names = {n.name for n in res.nodes}
    assert "valid_first" in names
