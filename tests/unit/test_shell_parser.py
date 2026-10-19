"""Unit Tests for ApexGraph Shell Parser (Tree-sitter Bash)

Validates:
- POSIX and Bash Function Definitions: posix(), function bash_one, function bash_two()
- Docstring Extraction: Comments preceding function definitions
- Top-Level and Local Variables: export, readonly, declare, local, bare declarations
- Script Sourcing: source script.sh, . script.sh, variable-prefixed paths
- Command & Function Calls: Inside functions, top-level, command substitutions $(), backticks
- Shell Builtin Filtering: echo, printf, cd, exit, test filtered from call graph
- Zsh Syntax Compatibility: typeset, autoload, associative arrays
- Syntax Error Resilience: Malformed scripts, unclosed braces, corrupted syntax
"""

import pytest

from packages.core.parsers.base import ParseResult
from packages.core.parsers.shell_parser import ShellParser


@pytest.fixture
def parser() -> ShellParser:
    return ShellParser()


def test_empty_and_whitespace_scripts(parser: ShellParser):
    for empty_input in ("", "   \n\t  \n", "# Just a comment\n# Another comment\n"):
        res = parser.parse_code(empty_input, file_path="empty.sh")
        assert isinstance(res, ParseResult)
        assert res.language == "shell"
        assert len(res.nodes) == 0
        assert len(res.imports) == 0
        assert len(res.calls) == 0
        assert len(res.syntax_errors) == 0


def test_posix_and_bash_function_definitions(parser: ShellParser):
    code = """#!/usr/bin/env bash

# Calculate sum of two integers
posix_sum() {
    local a=$1
    local b=$2
    add_numbers "$a" "$b"
}

# Process transaction batch
function bash_batch {
    validate_batch
}

# Deploy service to target cluster
function deploy_service() {
    execute_deploy
}
"""
    res = parser.parse_code(code, file_path="deploy.sh")

    assert res.language == "shell"
    assert len(res.syntax_errors) == 0

    functions = [n for n in res.nodes if n.node_type == "FUNCTION"]
    assert len(functions) == 3

    f_names = [f.name for f in functions]
    assert f_names == ["posix_sum", "bash_batch", "deploy_service"]

    # Verify signatures and line ranges
    assert functions[0].signature == "posix_sum()"
    assert functions[0].start_line == 4
    assert "Calculate sum of two integers" in functions[0].docstring

    assert functions[1].signature == "bash_batch()"
    assert functions[1].start_line == 11
    assert "Process transaction batch" in functions[1].docstring

    assert functions[2].signature == "deploy_service()"
    assert functions[2].start_line == 16
    assert "Deploy service to target cluster" in functions[2].docstring

    assert res.exports == ["posix_sum", "bash_batch", "deploy_service"]


def test_variable_extractions_and_scopes(parser: ShellParser):
    code = """#!/usr/bin/env bash
APP_NAME="ApexGraph"
export PORT=8080
readonly MAX_RETRIES=5
declare -x HOST="localhost"
export BARE_FLAG
readonly BARE_READONLY

setup() {
    local timeout=30
    local retries=3
    run_worker
}
"""
    res = parser.parse_code(code, file_path="config.sh")

    vars_by_name = {n.name: n for n in res.nodes if n.node_type == "VARIABLE"}
    assert "APP_NAME" in vars_by_name
    assert vars_by_name["APP_NAME"].metadata.get("is_exported") is False
    assert vars_by_name["APP_NAME"].metadata.get("is_readonly") is False
    assert vars_by_name["APP_NAME"].metadata.get("is_local") is False

    assert "PORT" in vars_by_name
    assert vars_by_name["PORT"].metadata.get("is_exported") is True

    assert "MAX_RETRIES" in vars_by_name
    assert vars_by_name["MAX_RETRIES"].metadata.get("is_readonly") is True

    assert "BARE_FLAG" in vars_by_name
    assert vars_by_name["BARE_FLAG"].metadata.get("is_exported") is True

    assert "BARE_READONLY" in vars_by_name
    assert vars_by_name["BARE_READONLY"].metadata.get("is_readonly") is True

    # Local variables
    assert "timeout" in vars_by_name
    assert vars_by_name["timeout"].metadata.get("is_local") is True
    assert vars_by_name["timeout"].metadata.get("enclosing") == "setup"
    assert vars_by_name["timeout"].qualified_name == "setup.timeout"


def test_script_sourcing_detection(parser: ShellParser):
    code = """#!/usr/bin/env bash
source ./lib/utils.sh
. ./config/env.sh
source "$SCRIPT_DIR/helpers.sh"
. "${APP_ROOT}/common.sh"
source shared_lib.sh
"""
    res = parser.parse_code(code, file_path="main.sh")

    assert len(res.imports) == 5
    paths = [i.module_path for i in res.imports]
    assert "./lib/utils.sh" in paths
    assert "./config/env.sh" in paths
    assert "$SCRIPT_DIR/helpers.sh" in paths
    assert "${APP_ROOT}/common.sh" in paths
    assert "shared_lib.sh" in paths

    for imp in res.imports:
        assert imp.imported_symbols == ["*"]
        assert imp.line_number >= 2


def test_command_and_function_invocations(parser: ShellParser):
    code = """#!/usr/bin/env bash
init_cluster

process_data() {
    prepare_input
    OUTPUT=$(calculate_metrics)
    BACKTICK=`generate_token`
    cleanup_temp
}
"""
    res = parser.parse_code(code, file_path="pipeline.sh")

    call_map = [(c.caller_name, c.callee_name) for c in res.calls]
    assert ("<module>", "init_cluster") in call_map
    assert ("process_data", "prepare_input") in call_map
    assert ("process_data", "calculate_metrics") in call_map
    assert ("process_data", "generate_token") in call_map
    assert ("process_data", "cleanup_temp") in call_map


def test_shell_builtins_filtered(parser: ShellParser):
    code = """#!/usr/bin/env bash
echo "Starting application"
printf "User: %s\\n" "$USER"
cd /var/log || exit 1
return 0
shift
test -f /etc/passwd
[ -d /tmp ]
"""
    res = parser.parse_code(code, file_path="script.sh")
    # All builtins should be filtered from calls
    callees = [c.callee_name for c in res.calls]
    for builtin in ("echo", "printf", "cd", "exit", "return", "shift", "test", "["):
        assert builtin not in callees


def test_zsh_compatibility(parser: ShellParser):
    code = """#!/usr/bin/env zsh
typeset -A user_cache
typeset -i counter=0
autoload -Uz compinit

init_completion() {
    compinit
    apply_settings
}
"""
    res = parser.parse_code(code, file_path="zsh_setup.zsh")

    assert res.language == "shell"
    fn_names = [n.name for n in res.nodes if n.node_type == "FUNCTION"]
    assert "init_completion" in fn_names

    call_callees = [c.callee_name for c in res.calls]
    assert "apply_settings" in call_callees


def test_syntax_error_resilience(parser: ShellParser):
    corrupt_code = """#!/usr/bin/env bash
good_func() {
    echo "valid"
}

bad_func( {
    incomplete_call(
"""
    res = parser.parse_code(corrupt_code, file_path="corrupt.sh")

    assert res.language == "shell"
    # Parser should not crash, syntax errors should be captured
    assert len(res.syntax_errors) > 0
    # Good function should still be extracted
    fn_names = [n.name for n in res.nodes if n.node_type == "FUNCTION"]
    assert "good_func" in fn_names
