"""ApexGraph Parser Registry and Dispatcher
"""

import os
from typing import Optional
from packages.core.parsers.base import ParseResult
from packages.core.parsers.python_parser import PythonParser
from packages.core.parsers.ts_js_parser import TypeScriptJavaScriptParser

_python_parser = PythonParser()
_ts_js_parser = TypeScriptJavaScriptParser()

SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}


def is_supported_file(file_path: str) -> bool:
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def parse_source_file(file_path: str, code: str) -> Optional[ParseResult]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".py":
        return _python_parser.parse_code(code, file_path=file_path)
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        return _ts_js_parser.parse_code(code, file_path=file_path)
    return None
