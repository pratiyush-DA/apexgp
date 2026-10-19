"""Unit Tests for Go Tree-sitter AST Parser (Phase 9.3)
"""

import pytest
from packages.core.parsers import is_supported_file, parse_source_file
from packages.core.parsers.go_parser import GoParser


def test_is_supported_file_go():
    assert is_supported_file("main.go") is True
    assert is_supported_file("pkg/math/calc.go") is True
    assert is_supported_file("pkg/math/calc_test.go") is True
    assert is_supported_file("main.py") is True
    assert is_supported_file("main.cpp") is False


def test_go_parser_functions_and_docstrings():
    code = """package math

// Add calculates the arithmetic sum of two integers.
func Add(a int, b int) int {
    return a + b
}

func privateHelper(msg string) {
    // helper body
}
"""
    parser = GoParser()
    res = parser.parse_code(code, file_path="pkg/math/calc.go")

    assert res.language == "go"
    assert len(res.nodes) == 2

    # Add function
    add_node = next(n for n in res.nodes if n.name == "Add")
    assert add_node.node_type == "FUNCTION"
    assert add_node.qualified_name == "Add"
    assert add_node.signature == "func Add(a int, b int) int"
    assert "Add calculates the arithmetic sum of two integers." in add_node.docstring
    assert add_node.metadata["package"] == "math"
    assert add_node.metadata["exported"] is True
    assert "Add" in res.exports

    # privateHelper function
    priv_node = next(n for n in res.nodes if n.name == "privateHelper")
    assert priv_node.node_type == "FUNCTION"
    assert priv_node.metadata["exported"] is False
    assert "privateHelper" not in res.exports


def test_go_parser_methods_and_receivers():
    code = """package calc

type Calculator struct {
    total int
}

// AddToTotal adds x to the calculator total.
func (c *Calculator) AddToTotal(x int) int {
    c.total += x
    return c.total
}

func (c Calculator) GetTotal() int {
    return c.total
}
"""
    parser = GoParser()
    res = parser.parse_code(code, file_path="calc.go")

    # Struct node
    calc_node = next(n for n in res.nodes if n.name == "Calculator")
    assert calc_node.node_type == "CLASS"
    assert calc_node.metadata["kind"] == "struct"
    assert calc_node.metadata["package"] == "calc"
    assert calc_node.metadata["exported"] is True

    # Pointer receiver method
    add_m = next(n for n in res.nodes if n.name == "AddToTotal")
    assert add_m.node_type == "METHOD"
    assert add_m.qualified_name == "Calculator.AddToTotal"
    assert add_m.metadata["receiver"] == "*Calculator"
    assert add_m.metadata["receiver_base"] == "Calculator"
    assert add_m.metadata["is_pointer_receiver"] is True
    assert add_m.metadata["enclosing"] == "Calculator"
    assert "AddToTotal adds x to the calculator total." in add_m.docstring

    # Value receiver method
    get_m = next(n for n in res.nodes if n.name == "GetTotal")
    assert get_m.node_type == "METHOD"
    assert get_m.qualified_name == "Calculator.GetTotal"
    assert get_m.metadata["receiver"] == "Calculator"
    assert get_m.metadata["receiver_base"] == "Calculator"
    assert get_m.metadata["is_pointer_receiver"] is False
    assert get_m.metadata["enclosing"] == "Calculator"


def test_go_parser_interfaces_and_type_aliases():
    code = """package service

type Worker interface {
    Process(taskID string) error
    Stop()
}

type UserID int64
"""
    parser = GoParser()
    res = parser.parse_code(code, file_path="service.go")

    worker_node = next(n for n in res.nodes if n.name == "Worker")
    assert worker_node.node_type == "INTERFACE"
    assert worker_node.metadata["kind"] == "interface"
    assert worker_node.metadata["exported"] is True

    user_node = next(n for n in res.nodes if n.name == "UserID")
    assert user_node.node_type == "TYPE"
    assert user_node.metadata["kind"] == "alias"
    assert user_node.metadata["exported"] is True


def test_go_parser_variables_and_constants():
    code = """package config

const MaxConnections = 100
const (
    TimeoutSec = 30
    RetryCount = 3
)

var DefaultPort = 8080
var (
    Host = "localhost"
    DebugMode = false
)
"""
    parser = GoParser()
    res = parser.parse_code(code, file_path="config.go")

    vars = {n.name: n for n in res.nodes if n.node_type == "VARIABLE"}
    assert "MaxConnections" in vars
    assert vars["MaxConnections"].metadata["is_const"] is True
    assert vars["MaxConnections"].metadata["exported"] is True

    assert "TimeoutSec" in vars
    assert vars["TimeoutSec"].metadata["is_const"] is True

    assert "DefaultPort" in vars
    assert vars["DefaultPort"].metadata["is_const"] is False

    assert "Host" in vars
    assert vars["Host"].metadata["is_const"] is False


def test_go_parser_imports():
    code = """package main

import "fmt"
import "os"
import (
    "net/http"
    u "github.com/org/repo/pkg/utils"
    . "github.com/org/repo/pkg/math"
    _ "github.com/lib/pq"
)
"""
    parser = GoParser()
    res = parser.parse_code(code, file_path="main.go")

    mods = {i.module_path: i for i in res.imports}
    assert "fmt" in mods
    assert mods["fmt"].alias is None
    assert mods["fmt"].is_relative is False

    assert "net/http" in mods
    assert "github.com/org/repo/pkg/utils" in mods
    assert mods["github.com/org/repo/pkg/utils"].alias == "u"

    assert "github.com/org/repo/pkg/math" in mods
    assert mods["github.com/org/repo/pkg/math"].alias is None  # '.' is stripped to None

    assert "github.com/lib/pq" in mods
    assert mods["github.com/lib/pq"].alias is None  # '_' blank import stripped to None


def test_go_parser_calls_and_test_detection():
    test_code = """package calc_test

import (
    "testing"
    "pkg/calc"
)

func TestAdd(t *testing.T) {
    res := calc.Add(2, 3)
    if res != 5 {
        t.Errorf("expected 5, got %d", res)
    }
}

func BenchmarkAdd(b *testing.B) {
    for i := 0; i < b.N; i++ {
        calc.Add(2, 3)
    }
}

func helperSetup() {
    // Non-test helper in test file
}
"""
    parser = GoParser()
    res = parser.parse_code(test_code, file_path="pkg/calc/calc_test.go")

    test_node = next(n for n in res.nodes if n.name == "TestAdd")
    assert test_node.node_type == "TEST"
    assert test_node.metadata["test_kind"] == "unit"

    bm_node = next(n for n in res.nodes if n.name == "BenchmarkAdd")
    assert bm_node.node_type == "TEST"

    helper_node = next(n for n in res.nodes if n.name == "helperSetup")
    assert helper_node.node_type == "FUNCTION"

    # Verify calls
    test_calls = [c for c in res.calls if c.caller_name == "TestAdd"]
    callee_names = {c.callee_name for c in test_calls}
    assert "calc.Add" in callee_names
    assert "t.Errorf" in callee_names


def test_parse_source_file_dispatch():
    code = "package main\nfunc Main() {}\n"
    res = parse_source_file("main.go", code)
    assert res is not None
    assert res.language == "go"
    assert len(res.nodes) == 1
    assert res.nodes[0].name == "Main"
