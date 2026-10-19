"""Unit tests for the Rust Tree-sitter Parser (RustParser).
"""

import pytest

from packages.core.parsers.rust_parser import RustParser
from packages.core.parsers.base import ParseResult


@pytest.fixture
def parser() -> RustParser:
    return RustParser()


def test_rust_parser_functions_and_docstrings(parser: RustParser):
    code = """
/// Computes the sum of two integers.
///
/// Multi-line explanation.
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn private_helper() {
    // does nothing
}
"""
    result = parser.parse_code(code, file_path="src/lib.rs")
    assert isinstance(result, ParseResult)
    assert len(result.nodes) == 2

    add_node = next(n for n in result.nodes if n.name == "add")
    assert add_node.node_type == "FUNCTION"
    assert add_node.qualified_name == "add"
    assert "Computes the sum of two integers." in add_node.docstring
    assert "Multi-line explanation." in add_node.docstring
    assert add_node.metadata["visibility"] == "public"
    assert "pub fn add(a: i32, b: i32) -> i32" in add_node.signature
    assert add_node.start_line == 5
    assert add_node.end_line == 7
    assert "add" in result.exports

    helper_node = next(n for n in result.nodes if n.name == "private_helper")
    assert helper_node.node_type == "FUNCTION"
    assert helper_node.qualified_name == "private_helper"
    assert helper_node.metadata["visibility"] == "private"
    assert "private_helper" not in result.exports


def test_rust_parser_structs_enums_and_traits(parser: RustParser):
    code = """
/// A 2D point structure.
#[derive(Debug, Clone)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

pub enum State {
    Idle,
    Processing(u32),
    Done { result: String },
}

pub trait Describable {
    fn describe(&self) -> String;
    fn default_desc(&self) -> &str {
        "default"
    }
}
"""
    result = parser.parse_code(code, file_path="src/geo.rs")

    struct_node = next(n for n in result.nodes if n.name == "Point")
    assert struct_node.node_type == "CLASS"
    assert struct_node.metadata["kind"] == "struct"
    assert struct_node.metadata["visibility"] == "public"
    assert "Point" in result.exports
    assert "A 2D point structure." in struct_node.docstring

    enum_node = next(n for n in result.nodes if n.name == "State")
    assert enum_node.node_type == "TYPE"
    assert enum_node.metadata["kind"] == "enum"
    assert enum_node.metadata["visibility"] == "public"
    assert "State" in result.exports

    trait_node = next(n for n in result.nodes if n.name == "Describable")
    assert trait_node.node_type == "INTERFACE"
    assert trait_node.metadata["kind"] == "trait"
    assert trait_node.metadata["visibility"] == "public"
    assert "Describable" in result.exports

    # Check trait methods inside Describable
    desc_node = next(n for n in result.nodes if n.name == "describe")
    assert desc_node.node_type == "METHOD"
    assert desc_node.qualified_name == "Describable::describe"
    assert desc_node.metadata["enclosing"] == "Describable"

    default_node = next(n for n in result.nodes if n.name == "default_desc")
    assert default_node.node_type == "METHOD"
    assert default_node.qualified_name == "Describable::default_desc"
    assert default_node.metadata["enclosing"] == "Describable"


def test_rust_parser_impl_blocks_and_trait_impls(parser: RustParser):
    code = """
pub struct Calculator {
    value: i32,
}

impl Calculator {
    pub fn new() -> Self {
        Calculator { value: 0 }
    }

    pub fn add(&mut self, delta: i32) {
        self.value += delta;
    }
}

pub trait Resettable {
    fn reset(&mut self);
}

impl Resettable for Calculator {
    fn reset(&mut self) {
        self.value = 0;
    }
}
"""
    result = parser.parse_code(code, file_path="src/calc.rs")

    new_node = next(n for n in result.nodes if n.name == "new")
    assert new_node.node_type == "METHOD"
    assert new_node.qualified_name == "Calculator::new"
    assert new_node.metadata["enclosing"] == "Calculator"
    assert new_node.metadata["visibility"] == "public"

    add_node = next(n for n in result.nodes if n.name == "add")
    assert add_node.node_type == "METHOD"
    assert add_node.qualified_name == "Calculator::add"
    assert add_node.metadata["enclosing"] == "Calculator"

    trait_reset = next(n for n in result.nodes if n.qualified_name == "Resettable::reset")
    assert trait_reset.node_type == "METHOD"
    assert trait_reset.metadata["enclosing"] == "Resettable"

    impl_reset = next(n for n in result.nodes if n.qualified_name == "Calculator::reset")
    assert impl_reset.node_type == "METHOD"
    assert impl_reset.metadata["enclosing"] == "Calculator"
    assert impl_reset.metadata["trait"] == "Resettable"


def test_rust_parser_modules_and_nesting(parser: RustParser):
    code = """
pub mod external_mod;

mod inner_mod {
    pub fn helper() -> bool {
        true
    }

    mod deep {
        pub fn deep_helper() {}
    }
}
"""
    result = parser.parse_code(code, file_path="src/lib.rs")

    # external_mod is a declaration
    ext_mod = next(n for n in result.nodes if n.name == "external_mod")
    assert ext_mod.node_type == "MODULE"
    assert ext_mod.qualified_name == "external_mod"
    assert ext_mod.metadata["external"] is True

    # Also created an import for external_mod
    imp = next(i for i in result.imports if i.module_path == "external_mod")
    assert imp.imported_symbols == ["external_mod"]

    # inner_mod is inline
    inner_mod = next(n for n in result.nodes if n.name == "inner_mod")
    assert inner_mod.node_type == "MODULE"
    assert inner_mod.qualified_name == "inner_mod"
    assert inner_mod.metadata["external"] is False

    # Check qualified names for nested symbols
    helper = next(n for n in result.nodes if n.name == "helper")
    assert helper.node_type == "FUNCTION"
    assert helper.qualified_name == "inner_mod::helper"

    deep_helper = next(n for n in result.nodes if n.name == "deep_helper")
    assert deep_helper.node_type == "FUNCTION"
    assert deep_helper.qualified_name == "inner_mod::deep::deep_helper"


def test_rust_parser_use_declarations_and_re_exports(parser: RustParser):
    code = """
use std::collections::HashMap;
use crate::models::{User, Account as Acc};
use super::parent_util;
use self::local::*;
pub use crate::api::Client;
pub use crate::constants::*;
"""
    result = parser.parse_code(code, file_path="src/service.rs")

    # 1. std::collections::HashMap
    std_imp = next(i for i in result.imports if i.module_path == "std::collections")
    assert std_imp.imported_symbols == ["HashMap"]
    assert std_imp.is_relative is False

    # 2. crate::models::{User, Account as Acc}
    user_imp = next(i for i in result.imports if i.module_path == "crate::models" and "User" in i.imported_symbols)
    assert user_imp.alias is None

    acc_imp = next(i for i in result.imports if i.module_path == "crate::models" and "Account" in i.imported_symbols)
    assert acc_imp.alias == "Acc"

    # 3. super::parent_util
    super_imp = next(i for i in result.imports if i.module_path == "super")
    assert super_imp.imported_symbols == ["parent_util"]
    assert super_imp.is_relative is True

    # 4. self::local::*
    wild_imp = next(i for i in result.imports if i.module_path == "self::local")
    assert wild_imp.imported_symbols == []
    assert wild_imp.is_relative is True

    # 5. Re-exports
    client_re = next(r for r in result.re_exports if r.module_path == "crate::api")
    assert client_re.exported_symbols == {"Client": "Client"}
    assert client_re.is_wildcard is False

    const_re = next(r for r in result.re_exports if r.module_path == "crate::constants")
    assert const_re.is_wildcard is True


def test_rust_parser_calls_extraction(parser: RustParser):
    code = """
fn execute() {
    let mut calc = Calculator::new();
    calc.add(42);
    math::multiply(2, 3);
    log_status();
}
"""
    result = parser.parse_code(code, file_path="src/main.rs")
    callee_names = [c.callee_name for c in result.calls]

    assert "Calculator::new" in callee_names
    assert "calc.add" in callee_names
    assert "math::multiply" in callee_names
    assert "log_status" in callee_names

    for call in result.calls:
        assert call.caller_name == "execute"
        assert call.line_number >= 3


def test_rust_parser_test_attributes_and_modules(parser: RustParser):
    code = """
#[test]
fn test_addition() {
    assert_eq!(1 + 1, 2);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn verify_sub() {
        assert_eq!(2 - 1, 1);
    }

    fn helper_in_tests() {}
}
"""
    result = parser.parse_code(code, file_path="src/lib.rs")

    add_test = next(n for n in result.nodes if n.name == "test_addition")
    assert add_test.node_type == "TEST"

    sub_test = next(n for n in result.nodes if n.name == "verify_sub")
    assert sub_test.node_type == "TEST"
    assert sub_test.qualified_name == "tests::verify_sub"

    # helper_in_tests inside cfg(test) module
    helper = next(n for n in result.nodes if n.name == "helper_in_tests")
    assert helper.node_type == "TEST"


def test_rust_parser_type_aliases_and_constants(parser: RustParser):
    code = """
pub const MAX_RETRIES: u32 = 5;
pub static mut ACTIVE_CONNECTIONS: i32 = 0;
pub type Callback = fn(i32) -> bool;
"""
    result = parser.parse_code(code, file_path="src/config.rs")

    retries = next(n for n in result.nodes if n.name == "MAX_RETRIES")
    assert retries.node_type == "VARIABLE"
    assert retries.metadata["kind"] == "const"
    assert retries.metadata["visibility"] == "public"

    conns = next(n for n in result.nodes if n.name == "ACTIVE_CONNECTIONS")
    assert conns.node_type == "VARIABLE"
    assert conns.metadata["kind"] == "static"
    assert conns.metadata["visibility"] == "public"

    cb = next(n for n in result.nodes if n.name == "Callback")
    assert cb.node_type == "TYPE"
    assert cb.metadata["kind"] == "type_alias"
    assert cb.metadata["visibility"] == "public"


def test_rust_parser_syntax_errors(parser: RustParser):
    broken_code = """
fn broken(x: i32 -> {
    let y = ;
}
"""
    result = parser.parse_code(broken_code, file_path="src/bad.rs")
    assert len(result.syntax_errors) > 0
    assert any("line" in err.lower() for err in result.syntax_errors)
