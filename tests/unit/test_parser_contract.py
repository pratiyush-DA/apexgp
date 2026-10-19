"""Standardized Reusable Contract Tests for All AST Parsers and Registry.

Enforces:
- Normalized ParseResult structure across all registered languages
- BaseParser interface compliance
- Valid 1-indexed line and column ranges
- Syntax error resilience on corrupt / malformed source
- Deterministic qualified name generation
- Dynamic language registration in isolated registries
"""

import pytest

from packages.core.parsers.base import BaseParser, ParseResult
from packages.core.parsers.registry import (
    LanguageRegistry,
    LanguageSpec,
    default_registry,
)


# Test matrices for registered languages
LANGUAGE_SAMPLES = {
    "python": {
        "valid": (
            "def calculate_total(items: list) -> int:\n"
            "    '''Docstring summary'''\n"
            "    return sum(items)\n\n"
            "class Invoice:\n"
            "    def process(self):\n"
            "        calculate_total([])\n"
        ),
        "invalid": "def broken_func(:\n    return {\n",
        "file": "invoice.py",
    },
    "typescript": {
        "valid": (
            "export interface User {\n"
            "    id: number;\n"
            "    name: string;\n"
            "}\n\n"
            "export class UserService {\n"
            "    getUser(): User {\n"
            "        return { id: 1, name: 'Alice' };\n"
            "    }\n"
            "}\n"
        ),
        "invalid": "class Broken { getUser(: string {",
        "file": "user.service.ts",
    },
    "javascript": {
        "valid": (
            "function computeTax(amount) {\n"
            "    return amount * 0.2;\n"
            "}\n\n"
            "class TaxCalculator {\n"
            "    calculate(amt) {\n"
            "        return computeTax(amt);\n"
            "    }\n"
            "}\n"
        ),
        "invalid": "function bad( { return ;;;",
        "file": "tax.js",
    },
    "go": {
        "valid": (
            "package accounting\n\n"
            "// Account represents a ledger account\n"
            "type Account struct {\n"
            "    Balance int\n"
            "}\n\n"
            "func (a *Account) Deposit(amount int) {\n"
            "    a.Balance += amount\n"
            "}\n"
        ),
        "invalid": "package accounting\nfunc (a *Account Deposit( {",
        "file": "account.go",
    },
    "rust": {
        "valid": (
            "pub struct Account {\n"
            "    pub balance: i32,\n"
            "}\n\n"
            "impl Account {\n"
            "    pub fn deposit(&mut self, amount: i32) {\n"
            "        self.balance += amount;\n"
            "    }\n"
            "}\n"
        ),
        "invalid": "fn broken( { let x = ;",
        "file": "account.rs",
    },
    "sql": {
        "valid": (
            "CREATE TABLE users (\n"
            "    id INT PRIMARY KEY,\n"
            "    name VARCHAR(50)\n"
            ");\n\n"
            "CREATE VIEW active_users AS\n"
            "SELECT * FROM users;\n"
        ),
        "invalid": "CREATE TABLE broken (id INT,,, ;;;",
        "file": "schema.sql",
    },
    "shell": {
        "valid": (
            "#!/usr/bin/env bash\n"
            "# Setup environment\n"
            "APP_ENV=\"production\"\n\n"
            "init_app() {\n"
            "    echo \"Initializing\"\n"
            "    setup_db\n"
            "}\n"
        ),
        "invalid": "function broken( { echo 1;\n",
        "file": "deploy.sh",
    },
    "json": {
        "valid": (
            "{\n"
            '  "name": "repopeek",\n'
            '  "version": "3.0.0",\n'
            '  "compilerOptions": {\n'
            '    "target": "ES2022"\n'
            "  }\n"
            "}\n"
        ),
        "invalid": '{\n  "broken": "unclosed\n',
        "file": "config.json",
    },
    "yaml": {
        "valid": (
            "version: '3.8'\n"
            "services:\n"
            "  web:\n"
            "    image: nginx:alpine\n"
            "    ports:\n"
            "      - '80:80'\n"
        ),
        "invalid": "key: : invalid\n  unbalanced: [",
        "file": "docker-compose.yml",
    },
}


def test_registry_contains_expected_builtins():
    """Verifies that the built-in registry registers Python, TypeScript, JavaScript, Go, Rust, SQL, Shell, JSON, and YAML."""
    extensions = default_registry.get_supported_extensions()
    assert ".py" in extensions
    assert ".ts" in extensions
    assert ".tsx" in extensions
    assert ".js" in extensions
    assert ".jsx" in extensions
    assert ".go" in extensions
    assert ".rs" in extensions
    assert ".sql" in extensions
    assert ".sh" in extensions
    assert ".bash" in extensions
    assert ".zsh" in extensions
    assert ".json" in extensions
    assert ".yaml" in extensions
    assert ".yml" in extensions

    assert extensions[".py"] == "python"
    assert extensions[".ts"] == "typescript"
    assert extensions[".js"] == "javascript"
    assert extensions[".go"] == "go"
    assert extensions[".rs"] == "rust"
    assert extensions[".sql"] == "sql"
    assert extensions[".sh"] == "shell"
    assert extensions[".bash"] == "shell"
    assert extensions[".zsh"] == "shell"
    assert extensions[".json"] == "json"
    assert extensions[".yaml"] == "yaml"
    assert extensions[".yml"] == "yaml"

    families = default_registry.get_language_families()
    assert families["python"] == "python"
    assert families["typescript"] == "js_ts"
    assert families["javascript"] == "js_ts"
    assert families["go"] == "go"
    assert families["rust"] == "rust"
    assert families["sql"] == "sql"
    assert families["shell"] == "shell"
    assert families["json"] == "config"
    assert families["yaml"] == "config"

    assert default_registry.get_runtime_prefix("python") == "py"
    assert default_registry.get_runtime_prefix("typescript") == "ts"
    assert default_registry.get_runtime_prefix("javascript") == "js"
    assert default_registry.get_runtime_prefix("go") == "go"
    assert default_registry.get_runtime_prefix("rust") == "rs"
    assert default_registry.get_runtime_prefix("sql") == "sql"
    assert default_registry.get_runtime_prefix("shell") == "sh"
    assert default_registry.get_runtime_prefix("json") == "json"
    assert default_registry.get_runtime_prefix("yaml") == "yaml"

    assert default_registry.is_package_scoped("go") is True
    assert default_registry.is_package_scoped("foo.go") is True
    assert default_registry.is_package_scoped("python") is False
    assert default_registry.is_package_scoped("foo.py") is False
    assert default_registry.is_package_scoped("rust") is False
    assert default_registry.is_package_scoped("foo.rs") is False
    assert default_registry.is_package_scoped("shell") is False
    assert default_registry.is_package_scoped("deploy.sh") is False
    assert default_registry.is_package_scoped("json") is False
    assert default_registry.is_package_scoped("config.json") is False
    assert default_registry.is_package_scoped("yaml") is False
    assert default_registry.is_package_scoped("config.yaml") is False


@pytest.mark.parametrize("language", ["python", "typescript", "javascript", "go", "rust", "sql", "shell", "json", "yaml"])
def test_parser_contract_base_parser_instance(language):
    """Verifies that every parser satisfies the BaseParser protocol."""
    parser = default_registry.get_parser(language)
    assert parser is not None
    assert isinstance(parser, BaseParser)
    assert hasattr(parser, "parse_code")
    assert callable(parser.parse_code)


@pytest.mark.parametrize("language", ["python", "typescript", "javascript", "go", "rust", "sql", "shell", "json", "yaml"])
def test_parser_contract_normalized_output_structure(language):
    """Verifies that all parsers return a normalized ParseResult adhering to model contracts."""
    sample_info = LANGUAGE_SAMPLES[language]
    parser = default_registry.get_parser(language)

    result = parser.parse_code(sample_info["valid"], file_path=sample_info["file"])

    assert isinstance(result, ParseResult)
    assert result.file_path == sample_info["file"]
    assert result.language == language
    assert isinstance(result.nodes, list)
    assert isinstance(result.imports, list)
    assert isinstance(result.calls, list)
    assert isinstance(result.exports, list)
    assert isinstance(result.re_exports, list)
    assert isinstance(result.syntax_errors, list)
    assert len(result.nodes) > 0

    # Invariant: 1-indexed, valid line ranges, non-empty names and qualified names
    for node in result.nodes:
        assert node.start_line >= 1, f"Node {node.name} has invalid start_line: {node.start_line}"
        assert node.end_line >= node.start_line, f"Node {node.name} has end_line < start_line"
        assert node.start_col >= 0
        assert node.end_col >= 0
        assert len(node.name) > 0
        assert len(node.qualified_name) > 0
        assert node.node_type in {
            "FILE", "MODULE", "CLASS", "INTERFACE", "FUNCTION",
            "METHOD", "VARIABLE", "TYPE", "TEST",
        }


@pytest.mark.parametrize("language", ["python", "typescript", "javascript", "go", "rust", "sql", "shell", "json", "yaml"])
def test_parser_contract_syntax_error_resilience(language):
    """Verifies that parsers do not raise unhandled exceptions on malformed source code."""
    sample_info = LANGUAGE_SAMPLES[language]
    parser = default_registry.get_parser(language)

    # Parsing must succeed without throwing an uncaught exception
    result = parser.parse_code(sample_info["invalid"], file_path=sample_info["file"])

    assert isinstance(result, ParseResult)
    assert result.file_path == sample_info["file"]
    assert result.language == language
    # Either errors were collected into syntax_errors or a resilient partial AST was produced
    assert isinstance(result.syntax_errors, list)


@pytest.mark.parametrize("language", ["python", "typescript", "javascript", "go", "rust", "sql", "shell", "json", "yaml"])
def test_parser_contract_deterministic_qualified_names(language):
    """Verifies that identical code produces identical qualified names on repeated parses."""
    sample_info = LANGUAGE_SAMPLES[language]
    parser = default_registry.get_parser(language)

    result_1 = parser.parse_code(sample_info["valid"], file_path=sample_info["file"])
    result_2 = parser.parse_code(sample_info["valid"], file_path=sample_info["file"])

    qnames_1 = [n.qualified_name for n in result_1.nodes]
    qnames_2 = [n.qualified_name for n in result_2.nodes]
    assert qnames_1 == qnames_2

    signatures_1 = [n.signature for n in result_1.nodes]
    signatures_2 = [n.signature for n in result_2.nodes]
    assert signatures_1 == signatures_2


def test_custom_language_dynamic_registration():
    """Verifies that a new language can be registered and dispatched in a clean registry instance."""
    clean_registry = LanguageRegistry()

    class CustomToyParser(BaseParser):
        def parse_code(self, code: str, file_path: str = "") -> ParseResult:
            return ParseResult(
                file_path=file_path,
                language="toy",
                nodes=[],
            )

    toy_spec = LanguageSpec(
        name="toy",
        extensions=(".toy", ".ty"),
        family="toy_lang",
        runtime_prefix="toy",
        parser_factory=CustomToyParser,
        is_package_scoped=False,
    )

    # Before registration
    assert clean_registry.is_supported_file("test.toy") is False
    assert clean_registry.parse_source_file("test.toy", "code") is None

    # Register
    clean_registry.register(toy_spec)

    # After registration
    assert clean_registry.is_supported_file("test.toy") is True
    assert clean_registry.is_supported_file("main.ty") is True
    assert clean_registry.get_runtime_prefix("toy") == "toy"
    assert clean_registry.are_languages_call_compatible("toy", "toy") is True
    assert clean_registry.are_languages_call_compatible("toy", "python") is False

    res = clean_registry.parse_source_file("test.toy", "foo = 1")
    assert res is not None
    assert res.language == "toy"
    assert res.file_path == "test.toy"
