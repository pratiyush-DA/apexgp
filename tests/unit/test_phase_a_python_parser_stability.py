"""Phase 9.13 Phase A: Python Parser Stability & Deep Recursion Tests

Validates non-recursive TreeCursor AST traversal on deeply nested synthetic structures
and real-world complex modules (smart_data_correction_engine.py).
Ensures zero native access violations (0xC0000005) occur on Windows x64.
"""

import os
import subprocess
import sys
import pytest
from packages.core.parsers.python_parser import PythonParser

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "testing",
    "dcnc",
    "smart_data_correction_engine.py",
)


def test_smart_data_correction_engine_in_process():
    """Verifies that smart_data_correction_engine.py parses cleanly in-process without crashing."""
    if not os.path.exists(FIXTURE_PATH):
        pytest.skip(f"Fixture not found at {FIXTURE_PATH}")

    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        code = f.read()

    parser = PythonParser()
    res = parser.parse_code(code, file_path="smart_data_correction_engine.py")

    assert res.language == "python"
    assert len(res.syntax_errors) == 0
    assert len(res.nodes) >= 15
    assert len(res.calls) >= 150
    assert len(res.imports) >= 5

    node_names = {n.name for n in res.nodes}
    assert "SmartDataCorrectionEngine" in node_names


def test_smart_data_correction_engine_subprocess_isolation():
    """Runs parsing in an isolated subprocess to assert zero native access violation (0xC0000005)."""
    if not os.path.exists(FIXTURE_PATH):
        pytest.skip(f"Fixture not found at {FIXTURE_PATH}")

    py_code = (
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from packages.core.parsers.python_parser import PythonParser\n"
        f"code = open(r'{FIXTURE_PATH}', 'r', encoding='utf-8').read()\n"
        "parser = PythonParser()\n"
        "res = parser.parse_code(code, 'smart_data_correction_engine.py')\n"
        "assert len(res.nodes) >= 15\n"
        "print('SUBPROCESS_PARSE_OK')\n"
    )

    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", py_code],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    )

    assert proc.returncode == 0, f"Process crashed with returncode {proc.returncode}. Stderr: {proc.stderr}"
    assert "SUBPROCESS_PARSE_OK" in proc.stdout


def test_deeply_nested_synthetic_classes_and_functions():
    """Verifies that 60+ levels of nested classes and functions parse without stack overflow."""
    depth = 65
    code_lines = []
    for i in range(depth):
        indent = "    " * i
        code_lines.append(f"{indent}class NestedLevel{i}:")
        code_lines.append(f"{indent}    '''Docstring for level {i}'''")
        code_lines.append(f"{indent}    def method_level_{i}(self):")
        code_lines.append(f"{indent}        '''Method docstring for level {i}'''")
        code_lines.append(f"{indent}        def inner_helper_{i}():")
        code_lines.append(f"{indent}            return {i}")

    code = "\n".join(code_lines)

    parser = PythonParser()
    res = parser.parse_code(code, file_path="nested.py")

    assert len(res.syntax_errors) == 0
    # 65 classes + 65 methods + 65 inner helpers = 195 nodes
    assert len(res.nodes) == depth * 3

    # Check qualified name hierarchy
    qual_names = {n.qualified_name for n in res.nodes}
    assert "NestedLevel0" in qual_names
    assert "NestedLevel0.method_level_0" in qual_names
    assert "NestedLevel0.method_level_0.inner_helper_0" in qual_names
    assert "NestedLevel0.NestedLevel1.method_level_1" in qual_names


def test_empty_file_and_whitespace():
    """Verifies that empty strings and whitespace-only files parse cleanly."""
    parser = PythonParser()
    for empty_input in ("", "   \n\n\t  \n"):
        res = parser.parse_code(empty_input, file_path="empty.py")
        assert len(res.nodes) == 0
        assert len(res.calls) == 0
        assert len(res.imports) == 0
        assert len(res.syntax_errors) == 0


def test_syntax_error_resilience():
    """Verifies that malformed Python syntax records syntax errors and does not crash."""
    bad_code = "def unclosed_func(x, y:\n    return x +\n"
    parser = PythonParser()
    res = parser.parse_code(bad_code, file_path="bad.py")
    assert len(res.syntax_errors) > 0


def test_calls_with_nested_expressions():
    """Verifies that nested and chained function calls are properly attributed to enclosing caller."""
    code = (
        "def compute(data):\n"
        "    result = transform(clean(validate(data)))\n"
        "    logger.info(format_message('done', result))\n"
        "    return result\n"
    )
    parser = PythonParser()
    res = parser.parse_code(code, file_path="caller.py")
    assert len(res.nodes) == 1
    assert res.nodes[0].name == "compute"

    callees = [c.callee_name for c in res.calls]
    assert "validate" in callees
    assert "clean" in callees
    assert "transform" in callees
    assert "format_message" in callees
    assert "info" in callees
    assert all(c.caller_name == "compute" for c in res.calls)
