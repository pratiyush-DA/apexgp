"""Base Data Structures and Interfaces for AST Parsers
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BaseParser(ABC):
    """Abstract base class for all AST parsers in RepoPeek."""

    @abstractmethod
    def parse_code(self, code: str, file_path: str = "") -> "ParseResult":
        """Parses source code into a standardized ParseResult."""
        pass


class ExtractedNode(BaseModel):
    name: str
    node_type: str  # FILE, MODULE, CLASS, INTERFACE, FUNCTION, METHOD, VARIABLE, TYPE, TEST
    qualified_name: str
    start_line: int
    end_line: int
    start_col: int = 0
    end_col: int = 0
    language: str
    signature: str = ""
    docstring: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExtractedImport(BaseModel):
    module_path: str  # e.g., "./utils", "math", "express"
    imported_symbols: List[str] = Field(default_factory=list)  # ["add", "sub"]
    alias: Optional[str] = None
    line_number: int = 1
    is_relative: bool = False
    raw_statement: str = ""


class ExtractedCall(BaseModel):
    caller_name: str  # Enclosing function or "<module>"
    callee_name: str  # Function or method being invoked
    line_number: int = 1
    raw_call: str = ""


class ExtractedReExport(BaseModel):
    module_path: str  # e.g., "./math", "@/components/Button"
    exported_symbols: Dict[str, str] = Field(default_factory=dict)  # exported_name -> source_name
    is_wildcard: bool = False  # True for export * from ...
    line_number: int = 1
    raw_statement: str = ""


class ParseResult(BaseModel):
    file_path: str
    language: str
    nodes: List[ExtractedNode] = Field(default_factory=list)
    imports: List[ExtractedImport] = Field(default_factory=list)
    calls: List[ExtractedCall] = Field(default_factory=list)
    exports: List[str] = Field(default_factory=list)
    re_exports: List[ExtractedReExport] = Field(default_factory=list)
    syntax_errors: List[str] = Field(default_factory=list)
