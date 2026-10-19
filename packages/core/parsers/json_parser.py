"""Tree-sitter AST Parser for JSON Files (.json).

Extracts:
- Objects (TYPE): Structural JSON objects, schemas, sections
- Properties (VARIABLE): Key-value pairs (primitives and arrays)
- Cross-file References (ExtractedImport):
  - JSON Schema $ref references (e.g. "./common.json#/definitions/User")
  - Configuration extends references (e.g. "./tsconfig.base.json")
- Bounded Graph Depth: Limits structural traversal to depth <= 4 to prevent node explosion
- Syntax Error Resilience: Resilient error recovery and partial AST extraction on malformed JSON
- Declarative Guarantee: Zero artificial CALLS edges generated
"""

from typing import Any, Dict, List, Optional, Set, Tuple
import tree_sitter
import tree_sitter_json

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)

MAX_JSON_DEPTH = 4


class JsonParser(BaseParser):
    """Tree-sitter based AST parser for JSON files (.json)."""

    def __init__(self):
        self.json_lang = tree_sitter.Language(tree_sitter_json.language())
        self.parser = tree_sitter.Parser(self.json_lang)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        """Parses JSON source code into a standardized ParseResult."""
        if not code or not code.strip():
            return ParseResult(
                file_path=file_path,
                language="json",
                nodes=[],
                imports=[],
                calls=[],
                exports=[],
                re_exports=[],
                syntax_errors=[],
            )

        encoded = code.encode("utf-8")
        tree = self.parser.parse(encoded)

        nodes: List[ExtractedNode] = []
        imports: List[ExtractedImport] = []
        syntax_errors: List[str] = []

        if tree.root_node.has_error:
            self._collect_errors(tree.root_node, syntax_errors, encoded)

        # Traverse the AST starting from the root document
        self._traverse_node(
            node=tree.root_node,
            code_bytes=encoded,
            current_path="",
            enclosing="",
            depth=1,
            nodes=nodes,
            imports=imports,
        )

        # Deterministic ordering
        nodes.sort(key=lambda n: (n.start_line, n.qualified_name))
        imports.sort(key=lambda i: (i.line_number, i.module_path))

        return ParseResult(
            file_path=file_path,
            language="json",
            nodes=nodes,
            imports=imports,
            calls=[],  # JSON is declarative; zero artificial calls
            exports=[n.name for n in nodes if n.node_type == "TYPE"],
            re_exports=[],
            syntax_errors=syntax_errors,
        )

    def _collect_errors(self, node: tree_sitter.Node, syntax_errors: List[str], code_bytes: bytes) -> None:
        """Recursively collects syntax error nodes."""
        if node.is_error or node.is_missing or node.type == "ERROR":
            line = node.start_point.row + 1
            if node.is_missing:
                syntax_errors.append(f"JSON syntax error at line {line}: missing {node.type}")
            else:
                snippet = code_bytes[node.start_byte:min(node.end_byte, node.start_byte + 50)].decode("utf-8", errors="ignore").replace("\n", " ")
                syntax_errors.append(f"JSON syntax error at line {line}: {snippet}")
        for child in node.children:
            self._collect_errors(child, syntax_errors, code_bytes)

    def _traverse_node(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        current_path: str,
        enclosing: str,
        depth: int,
        nodes: List[ExtractedNode],
        imports: List[ExtractedImport],
    ) -> None:
        """Traverses AST nodes extracting pairs and handling depth bounding."""
        if depth > MAX_JSON_DEPTH:
            return

        if node.type == "pair":
            self._handle_pair(
                pair_node=node,
                code_bytes=code_bytes,
                parent_path=current_path,
                enclosing=enclosing,
                depth=depth,
                nodes=nodes,
                imports=imports,
            )
            return

        if node.type == "array":
            elem_idx = 0
            for child in node.children:
                if child.type in ("object", "array"):
                    elem_path = f"{current_path}[{elem_idx}]" if current_path else f"[{elem_idx}]"
                    self._traverse_node(
                        node=child,
                        code_bytes=code_bytes,
                        current_path=elem_path,
                        enclosing=current_path,
                        depth=depth + 1,
                        nodes=nodes,
                        imports=imports,
                    )
                    elem_idx += 1
            return

        # Recurse for container nodes (document, object, ERROR)
        for child in node.children:
            if child.type in ("pair", "object", "array", "ERROR", "document"):
                self._traverse_node(
                    node=child,
                    code_bytes=code_bytes,
                    current_path=current_path,
                    enclosing=enclosing,
                    depth=depth,
                    nodes=nodes,
                    imports=imports,
                )

    def _handle_pair(
        self,
        pair_node: tree_sitter.Node,
        code_bytes: bytes,
        parent_path: str,
        enclosing: str,
        depth: int,
        nodes: List[ExtractedNode],
        imports: List[ExtractedImport],
    ) -> None:
        """Extracts property node and checks for $ref or extends imports."""
        key_node = pair_node.child_by_field_name("key")
        val_node = pair_node.child_by_field_name("value")
        if not key_node or not val_node:
            return

        key_raw = code_bytes[key_node.start_byte:key_node.end_byte].decode("utf-8", errors="ignore").strip()
        key_name = key_raw.strip('"\'')
        if not key_name:
            return

        qname = f"{parent_path}.{key_name}" if parent_path else key_name
        line_num = pair_node.start_point.row + 1
        end_line = pair_node.end_point.row + 1

        val_type = val_node.type
        val_text = code_bytes[val_node.start_byte:min(val_node.end_byte, val_node.start_byte + 100)].decode("utf-8", errors="ignore").replace("\n", " ").strip()

        # Check for cross-file imports ($ref, extends)
        if key_name == "$ref" and val_type == "string":
            ref_val = val_text.strip('"\'')
            self._handle_ref_import(ref_val, line_num, pair_node.text.decode("utf-8", errors="ignore"), imports)

        elif key_name == "extends":
            if val_type == "string":
                ext_val = val_text.strip('"\'')
                self._handle_extends_import(ext_val, line_num, pair_node.text.decode("utf-8", errors="ignore"), imports)
            elif val_type == "array":
                for child in val_node.children:
                    if child.type == "string":
                        ext_val = child.text.decode("utf-8", errors="ignore").strip('"\'')
                        self._handle_extends_import(ext_val, child.start_point.row + 1, child.text.decode("utf-8", errors="ignore"), imports)

        # Map to TYPE if object, VARIABLE if primitive/array
        node_type = "TYPE" if val_type == "object" else "VARIABLE"
        json_meta_type = "object" if val_type == "object" else ("array" if val_type == "array" else "primitive")

        nodes.append(
            ExtractedNode(
                name=key_name,
                node_type=node_type,
                qualified_name=qname,
                start_line=line_num,
                end_line=end_line,
                start_col=pair_node.start_point.column,
                end_col=pair_node.end_point.column,
                language="json",
                signature=f'"{key_name}": {val_type}',
                docstring="",
                metadata={
                    "json_type": json_meta_type,
                    "enclosing": parent_path,
                    "depth": depth,
                },
            )
        )

        # If value is object or array, recurse up to MAX_JSON_DEPTH
        if val_type in ("object", "array"):
            if val_type == "array":
                elem_idx = 0
                for child in val_node.children:
                    if child.type in ("object", "array"):
                        elem_path = f"{qname}[{elem_idx}]"
                        self._traverse_node(
                            node=child,
                            code_bytes=code_bytes,
                            current_path=elem_path,
                            enclosing=qname,
                            depth=depth + 1,
                            nodes=nodes,
                            imports=imports,
                        )
                        elem_idx += 1
            else:
                for child in val_node.children:
                    if child.type in ("pair", "object", "array", "ERROR"):
                        self._traverse_node(
                            node=child,
                            code_bytes=code_bytes,
                            current_path=qname,
                            enclosing=parent_path,
                            depth=depth + 1,
                            nodes=nodes,
                            imports=imports,
                        )

    def _handle_ref_import(self, ref_val: str, line_no: int, raw_stmt: str, imports: List[ExtractedImport]) -> None:
        """Parses JSON Schema $ref values like ./common.json#/definitions/User."""
        if not ref_val:
            return

        file_target = ref_val
        fragment = ""
        if "#" in ref_val:
            file_target, fragment = ref_val.split("#", 1)

        file_target = file_target.strip()
        if not file_target:
            return  # Internal fragment only, e.g. #/definitions/User

        # Determine imported symbols from fragment pointer
        symbols = ["*"]
        if fragment:
            clean_frag = fragment.lstrip("/").replace("/", ".")
            if clean_frag:
                symbols = [clean_frag]
                leaf = clean_frag.split(".")[-1]
                if leaf and leaf not in symbols:
                    symbols.append(leaf)

        imports.append(
            ExtractedImport(
                module_path=file_target,
                imported_symbols=symbols,
                line_number=line_no,
                is_relative=file_target.startswith(("./", "../")),
                raw_statement=raw_stmt[:200],
            )
        )

    def _handle_extends_import(self, ext_val: str, line_no: int, raw_stmt: str, imports: List[ExtractedImport]) -> None:
        """Parses configuration extends target paths."""
        ext_val = ext_val.strip()
        if not ext_val:
            return

        imports.append(
            ExtractedImport(
                module_path=ext_val,
                imported_symbols=["*"],
                line_number=line_no,
                is_relative=ext_val.startswith(("./", "../")),
                raw_statement=raw_stmt[:200],
            )
        )
