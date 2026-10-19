"""Unit tests for Phase 9.9: YamlParser.

Validates:
- Empty and whitespace YAML handling
- Extraction of mappings (TYPE), sequences (VARIABLE), and scalars (VARIABLE)
- Array / sequence element indexing (e.g. items[0], items[1])
- Duplicate property / key names in different scopes
- Depth bounding (depth <= 4)
- Multi-document YAML ("---") scoping (doc[0] vs doc[1])
- Anchors and aliases, including merge key (<<) and recursive aliases
- JSON / YAML Schema $ref extraction
- Configuration extends extraction (string and list)
- Declarative guarantee: zero artificial CALLS edges
- Syntax error resilience and error diagnostics
"""

import pytest
from packages.core.parsers.yaml_parser import YamlParser, MAX_YAML_DEPTH


@pytest.fixture
def parser() -> YamlParser:
    return YamlParser()


def test_empty_and_whitespace_yaml(parser: YamlParser):
    res_empty = parser.parse_code("", file_path="empty.yaml")
    assert res_empty.language == "yaml"
    assert len(res_empty.nodes) == 0
    assert len(res_empty.imports) == 0
    assert len(res_empty.calls) == 0
    assert len(res_empty.syntax_errors) == 0

    res_ws = parser.parse_code("   \n\n\t  \n", file_path="whitespace.yml")
    assert res_ws.language == "yaml"
    assert len(res_ws.nodes) == 0


def test_mappings_and_scalar_properties(parser: YamlParser):
    code = """
app:
  name: my-service
  version: 1.0.0
  active: true
  port: 8080
"""
    res = parser.parse_code(code, file_path="app.yaml")
    assert res.language == "yaml"
    assert len(res.syntax_errors) == 0

    nodes_by_name = {n.name: n for n in res.nodes}
    assert "app" in nodes_by_name
    assert nodes_by_name["app"].node_type == "TYPE"
    assert nodes_by_name["app"].metadata["yaml_type"] == "mapping"

    assert "name" in nodes_by_name
    assert nodes_by_name["name"].node_type == "VARIABLE"
    assert nodes_by_name["name"].qualified_name == "app.name"
    assert nodes_by_name["name"].metadata["enclosing"] == "app"

    assert "version" in nodes_by_name
    assert nodes_by_name["version"].qualified_name == "app.version"

    assert "active" in nodes_by_name
    assert "port" in nodes_by_name


def test_sequences_and_nested_mappings(parser: YamlParser):
    code = """
services:
  - name: web
    image: nginx
  - name: db
    image: postgres
"""
    res = parser.parse_code(code, file_path="docker.yaml")
    assert res.language == "yaml"

    qnames = {n.qualified_name: n for n in res.nodes}
    assert "services" in qnames
    assert qnames["services"].node_type == "VARIABLE"
    assert qnames["services"].metadata["yaml_type"] == "sequence"

    assert "services[0].name" in qnames
    assert qnames["services[0].name"].node_type == "VARIABLE"
    assert "services[0].image" in qnames

    assert "services[1].name" in qnames
    assert "services[1].image" in qnames


def test_duplicate_keys_in_different_scopes(parser: YamlParser):
    code = """
client:
  id: 101
  status: active
order:
  id: 202
  status: pending
"""
    res = parser.parse_code(code, file_path="data.yaml")

    nodes_by_qname = {n.qualified_name: n for n in res.nodes}
    assert "client.id" in nodes_by_qname
    assert "order.id" in nodes_by_qname
    assert nodes_by_qname["client.id"].metadata["enclosing"] == "client"
    assert nodes_by_qname["order.id"].metadata["enclosing"] == "order"


def test_bounded_graph_depth(parser: YamlParser):
    code = """
level1:
  level2:
    level3:
      level4:
        level5_pruned:
          deep_key: deep_value
"""
    res = parser.parse_code(code, file_path="deep.yaml")
    qnames = {n.qualified_name for n in res.nodes}

    assert "level1" in qnames
    assert "level1.level2" in qnames
    assert "level1.level2.level3" in qnames
    assert "level1.level2.level3.level4" in qnames
    # Depth > 4 must be pruned to avoid graph explosion
    assert "level1.level2.level3.level4.level5_pruned" not in qnames


def test_multi_document_yaml(parser: YamlParser):
    code = """
---
service: frontend
port: 3000
---
service: backend
port: 8000
"""
    res = parser.parse_code(code, file_path="services.yaml")
    assert res.language == "yaml"

    qnames = {n.qualified_name for n in res.nodes}
    assert "doc[0].service" in qnames
    assert "doc[0].port" in qnames
    assert "doc[1].service" in qnames
    assert "doc[1].port" in qnames


def test_anchors_aliases_and_merge_keys(parser: YamlParser):
    code = """
defaults: &base_config
  adapter: postgres
  pool: 5

development:
  <<: *base_config
  database: dev_db

production:
  <<: *base_config
  database: prod_db
"""
    res = parser.parse_code(code, file_path="database.yaml")
    assert res.language == "yaml"

    qnames = {n.qualified_name for n in res.nodes}
    assert "defaults" in qnames
    assert "defaults.adapter" in qnames
    assert "development.database" in qnames
    assert "production.database" in qnames


def test_recursive_alias_safety(parser: YamlParser):
    # Self-referencing recursive alias
    code = """
node_a: &cycle
  self: *cycle
"""
    # Parser must not enter infinite recursion
    res = parser.parse_code(code, file_path="recursive.yaml")
    assert res.language == "yaml"
    qnames = {n.qualified_name for n in res.nodes}
    assert "node_a" in qnames
    assert "node_a.self" in qnames


def test_yaml_schema_ref_extraction(parser: YamlParser):
    code = """
title: UserProfile
definitions:
  address:
    $ref: "./common.yaml#/definitions/Address"
  account:
    $ref: "./account.yml"
"""
    res = parser.parse_code(code, file_path="user_schema.yaml")
    assert len(res.imports) == 2

    imp1 = next(i for i in res.imports if i.module_path == "./common.yaml")
    assert "definitions.Address" in imp1.imported_symbols
    assert "Address" in imp1.imported_symbols

    imp2 = next(i for i in res.imports if i.module_path == "./account.yml")
    assert imp2.imported_symbols == ["*"]


def test_configuration_extends_extraction(parser: YamlParser):
    code1 = """
extends: "./base.yaml"
name: extended-config
"""
    res1 = parser.parse_code(code1, file_path="config.yaml")
    assert len(res1.imports) == 1
    assert res1.imports[0].module_path == "./base.yaml"
    assert res1.imports[0].imported_symbols == ["*"]

    code2 = """
extends:
  - "./base.yaml"
  - "./rules/strict.yml"
"""
    res2 = parser.parse_code(code2, file_path="multi_extends.yaml")
    assert len(res2.imports) == 2
    paths = [i.module_path for i in res2.imports]
    assert "./base.yaml" in paths
    assert "./rules/strict.yml" in paths


def test_declarative_zero_calls_guarantee(parser: YamlParser):
    code = """
pipeline:
  steps:
    - run: npm install
    - run: pytest
    - run: docker build .
"""
    res = parser.parse_code(code, file_path="ci.yaml")
    # Even if strings resemble shell commands, declarative YAML strictly produces zero calls
    assert len(res.calls) == 0


def test_syntax_error_resilience(parser: YamlParser):
    corrupt_code = """
valid_key: 123
broken_block: : [unbalanced
"""
    res = parser.parse_code(corrupt_code, file_path="corrupt.yaml")
    assert res.language == "yaml"
    assert len(res.syntax_errors) > 0
    assert "YAML syntax error" in res.syntax_errors[0]
