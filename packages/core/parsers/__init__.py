"""ApexGraph Parser Registry and Dispatcher
"""

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)
from packages.core.parsers.registry import (
    LanguageRegistry,
    LanguageSpec,
    are_languages_call_compatible,
    default_registry,
    get_runtime_prefix,
    is_supported_file,
    parse_source_file,
)
from packages.core.parsers.go_parser import GoParser
from packages.core.parsers.json_parser import JsonParser
from packages.core.parsers.python_parser import PythonParser
from packages.core.parsers.rust_parser import RustParser
from packages.core.parsers.shell_parser import ShellParser
from packages.core.parsers.sql_parser import SQLParser
from packages.core.parsers.ts_js_parser import TypeScriptJavaScriptParser
from packages.core.parsers.yaml_parser import YamlParser

SUPPORTED_EXTENSIONS = default_registry.get_supported_extensions()

__all__ = [
    "BaseParser",
    "ExtractedCall",
    "ExtractedImport",
    "ExtractedNode",
    "ExtractedReExport",
    "ParseResult",
    "LanguageRegistry",
    "LanguageSpec",
    "default_registry",
    "is_supported_file",
    "parse_source_file",
    "get_runtime_prefix",
    "are_languages_call_compatible",
    "SUPPORTED_EXTENSIONS",
    "GoParser",
    "JsonParser",
    "PythonParser",
    "RustParser",
    "ShellParser",
    "SQLParser",
    "TypeScriptJavaScriptParser",
    "YamlParser",
]
