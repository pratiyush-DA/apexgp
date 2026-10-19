"""Tree-sitter AST Parser for Go Source Code

Supports .go and *_test.go files.
Extracts packages, functions, methods, structs, interfaces, type aliases,
variables/constants, tests, imports, and call relationships with precise locations.
"""

from typing import Any, Dict, List, Optional
import tree_sitter
import tree_sitter_go

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)


class GoParser(BaseParser):
    """Tree-sitter based parser for the Go programming language."""

    def __init__(self):
        self.go_lang = tree_sitter.Language(tree_sitter_go.language())
        self.parser = tree_sitter.Parser(self.go_lang)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        encoded = code.encode("utf-8")
        tree = self.parser.parse(encoded)
        result = ParseResult(file_path=file_path, language="go")

        if tree.root_node.has_error:
            self._collect_errors(tree.root_node, result)

        is_test_file = file_path.endswith("_test.go") or "_test." in file_path

        # 1. Extract package name from package_clause
        package_name = ""
        for child in tree.root_node.children:
            if child.type == "package_clause":
                for c in child.children:
                    if c.type == "package_identifier":
                        package_name = encoded[c.start_byte:c.end_byte].decode("utf-8", errors="ignore")
                        break
                break

        # 2. Traverse top-level nodes
        for child in tree.root_node.children:
            self._handle_top_level_node(
                node=child,
                code_bytes=encoded,
                file_path=file_path,
                package_name=package_name,
                is_test_file=is_test_file,
                result=result,
            )

        return result

    def _collect_errors(self, node: tree_sitter.Node, result: ParseResult):
        if node.is_error or node.type == "ERROR":
            result.syntax_errors.append(
                f"Syntax error at line {node.start_point.row + 1}, column {node.start_point.column}"
            )
        for child in node.children:
            if child.has_error:
                self._collect_errors(child, result)

    def _handle_top_level_node(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        package_name: str,
        is_test_file: bool,
        result: ParseResult,
    ):
        if node.type == "import_declaration":
            self._handle_import(node, code_bytes, result)
        elif node.type == "function_declaration":
            self._handle_function(node, code_bytes, file_path, package_name, is_test_file, result)
        elif node.type == "method_declaration":
            self._handle_method(node, code_bytes, file_path, package_name, is_test_file, result)
        elif node.type == "type_declaration":
            self._handle_type(node, code_bytes, file_path, package_name, result)
        elif node.type in ("var_declaration", "const_declaration"):
            self._handle_var_const(node, code_bytes, file_path, package_name, result)

    def _handle_import(self, node: tree_sitter.Node, code_bytes: bytes, result: ParseResult):
        raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
        specs = []
        for child in node.children:
            if child.type == "import_spec":
                specs.append(child)
            elif child.type == "import_spec_list":
                for gc in child.children:
                    if gc.type == "import_spec":
                        specs.append(gc)

        for spec in specs:
            path_node = spec.child_by_field_name("path")
            if not path_node:
                continue
            raw_path = code_bytes[path_node.start_byte:path_node.end_byte].decode("utf-8", errors="ignore").strip('"')
            name_node = spec.child_by_field_name("name")
            alias = None
            if name_node:
                alias = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore").strip()

            result.imports.append(
                ExtractedImport(
                    module_path=raw_path,
                    imported_symbols=[],
                    alias=alias if alias not in (None, "", "_", ".") else None,
                    line_number=spec.start_point.row + 1,
                    is_relative=raw_path.startswith("."),
                    raw_statement=raw,
                )
            )

    def _handle_function(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        package_name: str,
        is_test_file: bool,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        fn_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        is_test = is_test_file and (
            fn_name.startswith("Test") or fn_name.startswith("Benchmark") or fn_name.startswith("Example")
        )
        node_type = "TEST" if is_test else "FUNCTION"
        params_node = node.child_by_field_name("parameters")
        params_text = (
            code_bytes[params_node.start_byte:params_node.end_byte].decode("utf-8", errors="ignore")
            if params_node
            else "()"
        )
        result_node = node.child_by_field_name("result")
        result_text = (
            f" {code_bytes[result_node.start_byte:result_node.end_byte].decode('utf-8', errors='ignore')}"
            if result_node
            else ""
        )
        sig = f"func {fn_name}{params_text}{result_text}"

        meta = {
            "package": package_name,
            "exported": bool(fn_name and fn_name[0].isupper()),
        }
        if is_test:
            meta["test_kind"] = "unit"

        result.nodes.append(
            ExtractedNode(
                name=fn_name,
                node_type=node_type,
                qualified_name=fn_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="go",
                signature=sig,
                docstring=self._extract_doc(node, code_bytes),
                metadata=meta,
            )
        )
        if fn_name and fn_name[0].isupper() and not is_test:
            result.exports.append(fn_name)

        body_node = node.child_by_field_name("body")
        if body_node:
            self._extract_calls(body_node, code_bytes, enclosing_symbol=fn_name, result=result)

    def _handle_method(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        package_name: str,
        is_test_file: bool,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        rcv_node = node.child_by_field_name("receiver")
        if not name_node or not rcv_node:
            return

        m_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        rcv_text = code_bytes[rcv_node.start_byte:rcv_node.end_byte].decode("utf-8", errors="ignore").strip()

        receiver_base = ""
        is_pointer = False
        receiver_type = ""
        for p in rcv_node.children:
            if p.type == "parameter_declaration":
                t_node = p.child_by_field_name("type")
                if t_node:
                    receiver_type = code_bytes[t_node.start_byte:t_node.end_byte].decode("utf-8", errors="ignore").strip()
                    if t_node.type == "pointer_type":
                        is_pointer = True
                        for pt in t_node.children:
                            if pt.type == "type_identifier":
                                receiver_base = code_bytes[pt.start_byte:pt.end_byte].decode("utf-8", errors="ignore")
                                break
                    elif t_node.type == "type_identifier":
                        receiver_base = receiver_type

        qual_name = f"{receiver_base}.{m_name}" if receiver_base else m_name
        params_node = node.child_by_field_name("parameters")
        params_text = (
            code_bytes[params_node.start_byte:params_node.end_byte].decode("utf-8", errors="ignore")
            if params_node
            else "()"
        )
        result_node = node.child_by_field_name("result")
        result_text = (
            f" {code_bytes[result_node.start_byte:result_node.end_byte].decode('utf-8', errors='ignore')}"
            if result_node
            else ""
        )
        sig = f"func {rcv_text} {m_name}{params_text}{result_text}"

        meta = {
            "package": package_name,
            "exported": bool(m_name and m_name[0].isupper()),
            "receiver": receiver_type,
            "receiver_base": receiver_base,
            "is_pointer_receiver": is_pointer,
            "enclosing": receiver_base,
        }

        result.nodes.append(
            ExtractedNode(
                name=m_name,
                node_type="METHOD",
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="go",
                signature=sig,
                docstring=self._extract_doc(node, code_bytes),
                metadata=meta,
            )
        )
        if m_name and m_name[0].isupper():
            result.exports.append(qual_name)

        body_node = node.child_by_field_name("body")
        if body_node:
            self._extract_calls(body_node, code_bytes, enclosing_symbol=qual_name, result=result)

    def _handle_type(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        package_name: str,
        result: ParseResult,
    ):
        specs = []
        for child in node.children:
            if child.type == "type_spec":
                specs.append(child)
            elif child.type == "type_spec_list":
                for gc in child.children:
                    if gc.type == "type_spec":
                        specs.append(gc)

        for child in specs:
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if not name_node or not type_node:
                continue

            t_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
            if type_node.type == "struct_type":
                node_type = "CLASS"
                kind = "struct"
                sig = f"type {t_name} struct"
            elif type_node.type == "interface_type":
                node_type = "INTERFACE"
                kind = "interface"
                sig = f"type {t_name} interface"
            else:
                node_type = "TYPE"
                kind = "alias"
                t_val = code_bytes[type_node.start_byte:type_node.end_byte].decode("utf-8", errors="ignore")
                sig = f"type {t_name} {t_val}"

            meta = {
                "package": package_name,
                "exported": bool(t_name and t_name[0].isupper()),
                "kind": kind,
            }

            result.nodes.append(
                ExtractedNode(
                    name=t_name,
                    node_type=node_type,
                    qualified_name=t_name,
                    start_line=child.start_point.row + 1,
                    end_line=child.end_point.row + 1,
                    start_col=child.start_point.column,
                    end_col=child.end_point.column,
                    language="go",
                    signature=sig,
                    docstring=self._extract_doc(node, code_bytes),
                    metadata=meta,
                )
            )
            if t_name and t_name[0].isupper():
                result.exports.append(t_name)

    def _handle_var_const(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        package_name: str,
        result: ParseResult,
    ):
        is_const = node.type == "const_declaration"
        spec_type = "const_spec" if is_const else "var_spec"
        specs = []
        for child in node.children:
            if child.type == spec_type:
                specs.append(child)
            elif child.type in ("const_spec_list", "var_spec_list"):
                for gc in child.children:
                    if gc.type == spec_type:
                        specs.append(gc)

        for spec in specs:
            name_node = spec.child_by_field_name("name")
            id_nodes = [name_node] if name_node else [c for c in spec.children if c.type == "identifier"]
            for id_n in id_nodes:
                v_name = code_bytes[id_n.start_byte:id_n.end_byte].decode("utf-8", errors="ignore")
                raw_sig = code_bytes[spec.start_byte:spec.end_byte].decode("utf-8", errors="ignore").splitlines()[0]
                meta = {
                    "package": package_name,
                    "exported": bool(v_name and v_name[0].isupper()),
                    "is_const": is_const,
                }
                result.nodes.append(
                    ExtractedNode(
                        name=v_name,
                        node_type="VARIABLE",
                        qualified_name=v_name,
                        start_line=spec.start_point.row + 1,
                        end_line=spec.end_point.row + 1,
                        start_col=spec.start_point.column,
                        end_col=spec.end_point.column,
                        language="go",
                        signature=raw_sig,
                        docstring=self._extract_doc(node, code_bytes),
                        metadata=meta,
                    )
                )
                if v_name and v_name[0].isupper():
                    result.exports.append(v_name)

    def _extract_calls(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        enclosing_symbol: str,
        result: ParseResult,
    ):
        if node.type == "call_expression":
            fn_node = node.child_by_field_name("function")
            if fn_node:
                raw_call = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
                if len(raw_call) > 120:
                    raw_call = raw_call[:117] + "..."
                callee = code_bytes[fn_node.start_byte:fn_node.end_byte].decode("utf-8", errors="ignore").strip()
                result.calls.append(
                    ExtractedCall(
                        caller_name=enclosing_symbol,
                        callee_name=callee,
                        line_number=node.start_point.row + 1,
                        raw_call=raw_call,
                    )
                )

        for child in node.children:
            self._extract_calls(child, code_bytes, enclosing_symbol, result)

    def _extract_doc(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        comments = []
        curr = node.prev_sibling
        while curr and curr.type == "comment":
            raw = code_bytes[curr.start_byte:curr.end_byte].decode("utf-8", errors="ignore").strip()
            lines = [line.strip().lstrip("//").strip() for line in raw.splitlines()]
            comments.insert(0, "\n".join(l for l in lines if l))
            curr = curr.prev_sibling
        return "\n".join(comments).strip()
