import bisect
import json
from typing import Any, Dict, List, Optional, Tuple
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
    """AST parser for JSON files (.json)."""

    def __init__(self):
        self.json_lang = tree_sitter.Language(tree_sitter_json.language())
        self.parser = tree_sitter.Parser(self.json_lang)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
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

        code_bytes = code.encode("utf-8")
        line_offsets = [0]
        for line in code_bytes.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1] + len(line))

        def byte_to_point(b: int) -> Tuple[int, int]:
            row = bisect.bisect_right(line_offsets, b) - 1
            col = b - line_offsets[row]
            return row + 1, col

        tree = self.parser.parse(code_bytes)
        nodes: List[ExtractedNode] = []
        imports: List[ExtractedImport] = []
        syntax_errors: List[str] = []

        if tree.root_node.has_error:
            def _raise_const(c):
                raise ValueError(f"Unquoted constant '{c}' is not valid JSON")
            try:
                json.loads(code, parse_constant=_raise_const)
            except Exception as jde:
                syntax_errors.append(f"JSON syntax error: {str(jde)}")
            if not syntax_errors:
                syntax_errors.append("JSON syntax error: malformed structure")

        cursor = tree.walk()
        visited = False
        # Scope stack: (cursor_depth, qualified_path, enclosing_path, json_depth)
        scope_stack = [(0, "", "", 0)]
        array_index_map: Dict[int, int] = {}

        while True:
            node = cursor.node
            depth = cursor.depth
            ntype = node.type
            skip_children = False

            if not visited:
                # Pop scopes deeper than current depth
                while len(scope_stack) > 1 and scope_stack[-1][0] >= depth:
                    scope_stack.pop()

                for d in list(array_index_map.keys()):
                    if d >= depth:
                        del array_index_map[d]

                curr_scope = scope_stack[-1]
                curr_path = curr_scope[1]
                curr_enclosing = curr_scope[2]
                curr_json_depth = curr_scope[3]

                if ntype == "pair" and curr_json_depth < MAX_JSON_DEPTH:
                    key_node = node.child_by_field_name("key")
                    val_node = node.child_by_field_name("value")

                    if key_node and val_node:
                        k_sb, k_eb = key_node.start_byte, key_node.end_byte
                        key_raw = code_bytes[k_sb:k_eb].decode("utf-8", errors="ignore").strip()
                        key_name = key_raw.strip('"\'')

                        if key_name:
                            qname = f"{curr_path}.{key_name}" if curr_path else key_name
                            v_sb, v_eb = val_node.start_byte, val_node.end_byte
                            val_type = val_node.type
                            val_text = code_bytes[v_sb:min(v_eb, v_sb + 100)].decode("utf-8", errors="ignore").replace("\n", " ").strip()

                            start_line, start_col = byte_to_point(node.start_byte)
                            end_line, end_col = byte_to_point(node.end_byte)

                            if key_name == "$ref" and val_type == "string":
                                ref_val = val_text.strip('"\'')
                                self._handle_ref_import(ref_val, start_line, imports)
                            elif key_name == "extends":
                                if val_type == "string":
                                    ext_val = val_text.strip('"\'')
                                    self._handle_extends_import(ext_val, start_line, imports)
                                elif val_type == "array":
                                    for idx in range(val_node.child_count):
                                        c = val_node.child(idx)
                                        if c and c.type == "string":
                                            c_text = code_bytes[c.start_byte:c.end_byte].decode("utf-8", errors="ignore").strip('"\'')
                                            c_line, _ = byte_to_point(c.start_byte)
                                            self._handle_extends_import(c_text, c_line, imports)

                            node_type = "TYPE" if val_type == "object" else "VARIABLE"
                            json_meta_type = "object" if val_type == "object" else ("array" if val_type == "array" else "primitive")

                            nodes.append(
                                ExtractedNode(
                                    name=key_name,
                                    node_type=node_type,
                                    qualified_name=qname,
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=start_col,
                                    end_col=end_col,
                                    language="json",
                                    signature=f'"{key_name}": {val_type}',
                                    docstring="",
                                    metadata={
                                        "json_type": json_meta_type,
                                        "enclosing": curr_path,
                                        "depth": curr_json_depth + 1,
                                    },
                                )
                            )

                            if val_type == "object":
                                scope_stack.append((depth, qname, curr_path, curr_json_depth + 1))
                            elif val_type == "array":
                                scope_stack.append((depth, qname, curr_path, curr_json_depth + 1))

                elif ntype == "array":
                    array_index_map[depth] = 0

                elif ntype in ("object", "array") and (depth - 1) in array_index_map:
                    idx = array_index_map[depth - 1]
                    array_index_map[depth - 1] += 1
                    elem_path = f"{curr_path}[{idx}]"
                    scope_stack.append((depth, elem_path, curr_path, curr_json_depth + 1))

                if not skip_children and cursor.goto_first_child():
                    visited = False
                    continue

            visited = False
            if cursor.goto_next_sibling():
                continue
            if cursor.goto_parent():
                visited = True
                continue
            break

        nodes.sort(key=lambda n: (n.start_line, n.qualified_name))
        imports.sort(key=lambda i: (i.line_number, i.module_path))

        return ParseResult(
            file_path=file_path,
            language="json",
            nodes=nodes,
            imports=imports,
            calls=[],
            exports=[n.name for n in nodes if n.node_type == "TYPE"],
            re_exports=[],
            syntax_errors=syntax_errors,
        )

    def _handle_ref_import(self, ref_val: str, line_no: int, imports: List[ExtractedImport]) -> None:
        if not ref_val:
            return
        file_target = ref_val
        fragment = ""
        if "#" in ref_val:
            file_target, fragment = ref_val.split("#", 1)
        file_target = file_target.strip()
        if not file_target:
            return
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
                import_type="relative" if file_target.startswith((".", "/")) else "package",
                line_number=line_no,
                raw_statement=f'"$ref": "{ref_val}"',
            )
        )

    def _handle_extends_import(self, ext_val: str, line_no: int, imports: List[ExtractedImport]) -> None:
        if not ext_val:
            return
        imports.append(
            ExtractedImport(
                module_path=ext_val,
                imported_symbols=["*"],
                import_type="relative" if ext_val.startswith((".", "/")) else "package",
                line_number=line_no,
                raw_statement=f'"extends": "{ext_val}"',
            )
        )
