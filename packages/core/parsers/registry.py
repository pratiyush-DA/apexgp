"""ApexGraph Language Registry and Extensibility Framework.

Centralizes:
- Supported extensions and language names
- Language families for cross-language call compatibility
- Runtime prefixes for property graph node namespaces
- Package-level versus file-level scoping models
- Standardized BaseParser factory creation and dispatch
"""

from dataclasses import dataclass
import os
from typing import Callable, Dict, Optional, Tuple

from packages.core.parsers.base import BaseParser, ParseResult


@dataclass(frozen=True)
class LanguageSpec:
    """Encapsulates all metadata and parsing capabilities for a supported programming language."""

    name: str
    extensions: Tuple[str, ...]
    family: str
    runtime_prefix: str
    parser_factory: Callable[[], BaseParser]
    is_package_scoped: bool = False


class LanguageRegistry:
    """Thread-safe registry mapping file extensions and language names to LanguageSpecs and parsers."""

    def __init__(self):
        self._specs_by_name: Dict[str, LanguageSpec] = {}
        self._specs_by_ext: Dict[str, LanguageSpec] = {}
        self._parser_instances: Dict[str, BaseParser] = {}

    def register(self, spec: LanguageSpec) -> None:
        """Registers a LanguageSpec and indexes its extensions and name."""
        self._specs_by_name[spec.name.lower()] = spec
        for ext in spec.extensions:
            self._specs_by_ext[ext.lower()] = spec

    def get_spec_by_extension(self, ext_or_path: str) -> Optional[LanguageSpec]:
        """Looks up a LanguageSpec by file extension or file path."""
        ext = os.path.splitext(ext_or_path)[1].lower() if "." in ext_or_path else ext_or_path.lower()
        if not ext.startswith(".") and f".{ext}" in self._specs_by_ext:
            ext = f".{ext}"
        return self._specs_by_ext.get(ext)

    def get_spec_by_name(self, name: str) -> Optional[LanguageSpec]:
        """Looks up a LanguageSpec by language name."""
        spec = self._specs_by_name.get(name.lower())
        if not spec and name.lower() in ("bash", "sh", "zsh"):
            return self._specs_by_name.get("shell")
        return spec

    def get_parser(self, language_or_ext: str) -> Optional[BaseParser]:
        """Retrieves or lazily instantiates the BaseParser for a given language or extension."""
        spec = self.get_spec_by_name(language_or_ext) or self.get_spec_by_extension(language_or_ext)
        if not spec:
            return None
        if spec.name not in self._parser_instances:
            self._parser_instances[spec.name] = spec.parser_factory()
        return self._parser_instances[spec.name]

    def is_supported_file(self, file_path: str) -> bool:
        """Checks if a given file has an extension registered in the registry."""
        return self.get_spec_by_extension(file_path) is not None

    def get_supported_extensions(self) -> Dict[str, str]:
        """Returns a mapping of lowercase extension -> language name."""
        return {ext: spec.name for ext, spec in self._specs_by_ext.items()}

    def get_language_families(self) -> Dict[str, str]:
        """Returns a mapping of language name -> language family."""
        return {name: spec.family for name, spec in self._specs_by_name.items()}

    def get_runtime_prefix(self, language: str) -> str:
        """Returns the node ID namespace prefix for a language, defaulting to the language name."""
        spec = self.get_spec_by_name(language)
        if spec:
            return spec.runtime_prefix
        return language

    def are_languages_call_compatible(self, lang_a: str, lang_b: str) -> bool:
        """Checks if two languages belong to the same execution runtime family for in-process calls."""
        spec_a = self.get_spec_by_name(lang_a)
        spec_b = self.get_spec_by_name(lang_b)
        fam_a = spec_a.family if spec_a else lang_a
        fam_b = spec_b.family if spec_b else lang_b
        return fam_a == fam_b

    def is_package_scoped(self, language_or_path: str) -> bool:
        """Returns True if the language or file belongs to a package-scoped language (e.g. Go)."""
        spec = self.get_spec_by_name(language_or_path) or self.get_spec_by_extension(language_or_path)
        return spec.is_package_scoped if spec else False

    def parse_source_file(self, file_path: str, code: str) -> Optional[ParseResult]:
        """Parses a source file using the registered parser for its extension."""
        spec = self.get_spec_by_extension(file_path)
        if not spec:
            return None
        parser = self.get_parser(spec.name)
        if not parser:
            return None
        return parser.parse_code(code, file_path=file_path)


# Singleton lazy parser factories
_python_parser_instance: Optional[BaseParser] = None
_ts_js_parser_instance: Optional[BaseParser] = None
_go_parser_instance: Optional[BaseParser] = None
_rust_parser_instance: Optional[BaseParser] = None
_sql_parser_instance: Optional[BaseParser] = None
_shell_parser_instance: Optional[BaseParser] = None
_json_parser_instance: Optional[BaseParser] = None
_yaml_parser_instance: Optional[BaseParser] = None


def _get_python_parser() -> BaseParser:
    global _python_parser_instance
    if _python_parser_instance is None:
        from packages.core.parsers.python_parser import PythonParser
        BaseParser.register(PythonParser)
        _python_parser_instance = PythonParser()
    return _python_parser_instance


def _get_ts_js_parser() -> BaseParser:
    global _ts_js_parser_instance
    if _ts_js_parser_instance is None:
        from packages.core.parsers.ts_js_parser import TypeScriptJavaScriptParser
        _ts_js_parser_instance = TypeScriptJavaScriptParser()
    return _ts_js_parser_instance


def _get_go_parser() -> BaseParser:
    global _go_parser_instance
    if _go_parser_instance is None:
        from packages.core.parsers.go_parser import GoParser
        _go_parser_instance = GoParser()
    return _go_parser_instance


def _get_rust_parser() -> BaseParser:
    global _rust_parser_instance
    if _rust_parser_instance is None:
        from packages.core.parsers.rust_parser import RustParser
        _rust_parser_instance = RustParser()
    return _rust_parser_instance


def _get_sql_parser() -> BaseParser:
    global _sql_parser_instance
    if _sql_parser_instance is None:
        from packages.core.parsers.sql_parser import SQLParser
        _sql_parser_instance = SQLParser()
    return _sql_parser_instance


def _get_shell_parser() -> BaseParser:
    global _shell_parser_instance
    if _shell_parser_instance is None:
        from packages.core.parsers.shell_parser import ShellParser
        _shell_parser_instance = ShellParser()
    return _shell_parser_instance


def _get_json_parser() -> BaseParser:
    global _json_parser_instance
    if _json_parser_instance is None:
        from packages.core.parsers.json_parser import JsonParser
        _json_parser_instance = JsonParser()
    return _json_parser_instance


def _get_yaml_parser() -> BaseParser:
    global _yaml_parser_instance
    if _yaml_parser_instance is None:
        from packages.core.parsers.yaml_parser import YamlParser
        _yaml_parser_instance = YamlParser()
    return _yaml_parser_instance


def create_default_registry() -> LanguageRegistry:
    """Instantiates and populates the default LanguageRegistry with built-in languages."""
    registry = LanguageRegistry()

    # 1. Python
    registry.register(
        LanguageSpec(
            name="python",
            extensions=(".py",),
            family="python",
            runtime_prefix="py",
            parser_factory=_get_python_parser,
            is_package_scoped=False,
        )
    )

    # 2. TypeScript
    registry.register(
        LanguageSpec(
            name="typescript",
            extensions=(".ts", ".tsx"),
            family="js_ts",
            runtime_prefix="ts",
            parser_factory=_get_ts_js_parser,
            is_package_scoped=False,
        )
    )

    # 3. JavaScript
    registry.register(
        LanguageSpec(
            name="javascript",
            extensions=(".js", ".jsx", ".mjs", ".cjs"),
            family="js_ts",
            runtime_prefix="js",
            parser_factory=_get_ts_js_parser,
            is_package_scoped=False,
        )
    )

    # 4. Go
    registry.register(
        LanguageSpec(
            name="go",
            extensions=(".go",),
            family="go",
            runtime_prefix="go",
            parser_factory=_get_go_parser,
            is_package_scoped=True,
        )
    )

    # 5. Rust
    registry.register(
        LanguageSpec(
            name="rust",
            extensions=(".rs",),
            family="rust",
            runtime_prefix="rs",
            parser_factory=_get_rust_parser,
            is_package_scoped=False,
        )
    )

    # 6. SQL
    registry.register(
        LanguageSpec(
            name="sql",
            extensions=(".sql",),
            family="sql",
            runtime_prefix="sql",
            parser_factory=_get_sql_parser,
            is_package_scoped=False,
        )
    )

    # 7. Shell (.sh, .bash, .zsh)
    registry.register(
        LanguageSpec(
            name="shell",
            extensions=(".sh", ".bash", ".zsh"),
            family="shell",
            runtime_prefix="sh",
            parser_factory=_get_shell_parser,
            is_package_scoped=False,
        )
    )

    # 8. JSON (.json)
    registry.register(
        LanguageSpec(
            name="json",
            extensions=(".json",),
            family="config",
            runtime_prefix="json",
            parser_factory=_get_json_parser,
            is_package_scoped=False,
        )
    )

    # 9. YAML (.yaml, .yml)
    registry.register(
        LanguageSpec(
            name="yaml",
            extensions=(".yaml", ".yml"),
            family="config",
            runtime_prefix="yaml",
            parser_factory=_get_yaml_parser,
            is_package_scoped=False,
        )
    )

    return registry


# Global default instance
default_registry: LanguageRegistry = create_default_registry()

# Facade functions preserving backward compatibility
def is_supported_file(file_path: str) -> bool:
    return default_registry.is_supported_file(file_path)


def parse_source_file(file_path: str, code: str) -> Optional[ParseResult]:
    return default_registry.parse_source_file(file_path, code)


def get_runtime_prefix(language: str) -> str:
    return default_registry.get_runtime_prefix(language)


def are_languages_call_compatible(lang_a: str, lang_b: str) -> bool:
    return default_registry.are_languages_call_compatible(lang_a, lang_b)
