"""Tree-sitter AST Parser for Python Source Code

Extracts classes, functions, methods, tests, signatures, docstrings,
imports, and call relationships with precise byte and line ranges.
"""

from typing import Dict, List, Optional, Tuple
import tree_sitter
import tree_sitter_python

from packages.core.parsers.base import (
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ParseResult,
)


class PythonParser:
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_python.language())
        self.parser = tree_sitter.Parser(self.language)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        encoded = code.encode("utf-8")
        tree = self.parser.parse(encoded)
        lines = code.splitlines(keepends=True)

        result = ParseResult(file_path=file_path, language="python")
        is_test_file = "test" in file_path.lower()

        # Traverse AST
        self._traverse_node(
            node=tree.root_node,
            code_bytes=encoded,
            lines=lines,
            file_path=file_path,
            is_test_file=is_test_file,
            scope_prefix="",
            enclosing_symbol="<module>",
            result=result,
        )

        return result

    def _traverse_node(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        lines: List[str],
        file_path: str,
        is_test_file: bool,
        scope_prefix: str,
        enclosing_symbol: str,
        result: ParseResult,
    ):
        if node.type == "ERROR":
            result.syntax_errors.append(
                f"Syntax error at line {node.start_point.row + 1}:{node.start_point.column}"
            )
            return

        # 1. Imports
        if node.type == "import_statement":
            self._handle_import_statement(node, code_bytes, result)
            return

        if node.type == "import_from_statement":
            self._handle_import_from_statement(node, code_bytes, result)
            return

        # 2. Decorated Definition
        if node.type == "decorated_definition":
            dec_nodes = []
            inner_def = None
            for child in node.children:
                if child.type == "decorator":
                    dec_nodes.append(child)
                elif child.type in ("function_definition", "class_definition"):
                    inner_def = child

            if inner_def:
                raw_decs, dec_names, route_info = self._extract_decorators_and_route(dec_nodes, code_bytes)
                if inner_def.type == "function_definition":
                    self._handle_function_definition(
                        node=inner_def,
                        code_bytes=code_bytes,
                        lines=lines,
                        file_path=file_path,
                        is_test_file=is_test_file,
                        scope_prefix=scope_prefix,
                        enclosing_symbol=enclosing_symbol,
                        result=result,
                        decorators=raw_decs,
                        decorator_names=dec_names,
                        route_info=route_info,
                        start_line_override=node.start_point.row + 1,
                    )
                    return
                elif inner_def.type == "class_definition":
                    self._handle_class_definition(
                        node=inner_def,
                        code_bytes=code_bytes,
                        lines=lines,
                        file_path=file_path,
                        is_test_file=is_test_file,
                        scope_prefix=scope_prefix,
                        enclosing_symbol=enclosing_symbol,
                        result=result,
                        decorators=raw_decs,
                        decorator_names=dec_names,
                        start_line_override=node.start_point.row + 1,
                    )
                    return

        # 3. Function / Method Definition (Undecorated)
        if node.type == "function_definition":
            self._handle_function_definition(
                node=node,
                code_bytes=code_bytes,
                lines=lines,
                file_path=file_path,
                is_test_file=is_test_file,
                scope_prefix=scope_prefix,
                enclosing_symbol=enclosing_symbol,
                result=result,
            )
            return

        # 4. Class Definition (Undecorated)
        if node.type == "class_definition":
            self._handle_class_definition(
                node=node,
                code_bytes=code_bytes,
                lines=lines,
                file_path=file_path,
                is_test_file=is_test_file,
                scope_prefix=scope_prefix,
                enclosing_symbol=enclosing_symbol,
                result=result,
            )
            return

        # 4. Function Call
        if node.type == "call":
            self._handle_call(node, code_bytes, enclosing_symbol, result)

        # Recurse through children
        for child in node.children:
            self._traverse_node(
                node=child,
                code_bytes=code_bytes,
                lines=lines,
                file_path=file_path,
                is_test_file=is_test_file,
                scope_prefix=scope_prefix,
                enclosing_symbol=enclosing_symbol,
                result=result,
            )

    def _handle_import_statement(
        self, node: tree_sitter.Node, code_bytes: bytes, result: ParseResult
    ):
        raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        line_num = node.start_point.row + 1
        # import a, b as c
        for child in node.children:
            if child.type == "dotted_name":
                mod_name = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                result.imports.append(
                    ExtractedImport(
                        module_path=mod_name,
                        imported_symbols=[mod_name],
                        line_number=line_num,
                        is_relative=False,
                        raw_statement=raw,
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node:
                    mod = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
                    alias = (
                        code_bytes[alias_node.start_byte:alias_node.end_byte].decode("utf-8", errors="ignore")
                        if alias_node
                        else None
                    )
                    result.imports.append(
                        ExtractedImport(
                            module_path=mod,
                            imported_symbols=[mod],
                            alias=alias,
                            line_number=line_num,
                            is_relative=False,
                            raw_statement=raw,
                        )
                    )

    def _handle_import_from_statement(
        self, node: tree_sitter.Node, code_bytes: bytes, result: ParseResult
    ):
        raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        line_num = node.start_point.row + 1

        mod_node = node.child_by_field_name("module_name")
        module_path = (
            code_bytes[mod_node.start_byte:mod_node.end_byte].decode("utf-8", errors="ignore")
            if mod_node
            else ""
        )

        # Detect relative imports (e.g. from . import utils or from ..core import x)
        is_relative = False
        dots = 0
        for child in node.children:
            if child.type == "relative_import":
                is_relative = True
                module_path = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                break
            elif child.type == "import_prefix":
                is_relative = True
                dots = len(child.children)
                break

        if dots > 0 and mod_node:
            module_path = ("." * dots) + module_path
            is_relative = True

        symbols = []
        for child in node.children:
            if child.type == "dotted_name" and child != mod_node:
                sym = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                symbols.append(sym)
            elif child.type == "aliased_import":
                n = child.child_by_field_name("name")
                if n:
                    sym = code_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")
                    symbols.append(sym)
            elif child.type == "wildcard_import":
                symbols.append("*")

        result.imports.append(
            ExtractedImport(
                module_path=module_path,
                imported_symbols=symbols,
                line_number=line_num,
                is_relative=is_relative,
                raw_statement=raw,
            )
        )

    def _handle_call(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        enclosing_symbol: str,
        result: ParseResult,
    ):
        func_node = node.child_by_field_name("function")
        if not func_node:
            return

        callee_raw = code_bytes[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="ignore")
        # Extract direct identifier if method call (e.g. self.add -> add, math.sqrt -> sqrt)
        callee_simple = callee_raw.split(".")[-1]

        raw_call = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        # Cap length of raw call
        if len(raw_call) > 120:
            raw_call = raw_call[:117] + "..."

        result.calls.append(
            ExtractedCall(
                caller_name=enclosing_symbol,
                callee_name=callee_simple,
                line_number=node.start_point.row + 1,
                raw_call=raw_call,
            )
        )

    def _get_child_text(
        self, node: tree_sitter.Node, field_name: str, code_bytes: bytes
    ) -> Optional[str]:
        child = node.child_by_field_name(field_name)
        if child:
            return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
        return None

    def _extract_function_signature(
        self, node: tree_sitter.Node, code_bytes: bytes
    ) -> str:
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        ret_node = node.child_by_field_name("return_type")

        name = (
            code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
            if name_node
            else "func"
        )
        params = (
            code_bytes[params_node.start_byte:params_node.end_byte].decode("utf-8", errors="ignore")
            if params_node
            else "()"
        )
        ret = (
            " -> " + code_bytes[ret_node.start_byte:ret_node.end_byte].decode("utf-8", errors="ignore")
            if ret_node
            else ""
        )
        return f"def {name}{params}{ret}"

    def _extract_docstring(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        body = node.child_by_field_name("body")
        if not body or not body.children:
            return ""
        first = body.children[0]
        if first.type == "expression_statement" and first.children:
            expr = first.children[0]
            if expr.type == "string":
                raw = code_bytes[expr.start_byte:expr.end_byte].decode("utf-8", errors="ignore")
                return raw.strip('"""').strip("'''").strip('"').strip("'").strip()
        return ""

    def _extract_superclasses(
        self, node: tree_sitter.Node, code_bytes: bytes
    ) -> List[str]:
        args = node.child_by_field_name("superclasses")
        if not args:
            return []
        supers = []
        for child in args.children:
            if child.type in ("identifier", "attribute"):
                supers.append(
                    code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                )
        return supers

    def _extract_decorators_and_route(
        self, decorator_nodes: List[tree_sitter.Node], code_bytes: bytes
    ) -> Tuple[List[str], List[str], Optional[Dict[str, str]]]:
        """Extracts decorator strings, decorator identifiers, and route info if present."""
        raw_decorators: List[str] = []
        decorator_names: List[str] = []
        route_info: Optional[Dict[str, str]] = None

        http_methods = {"get", "post", "put", "delete", "patch", "options", "head"}

        for dec in decorator_nodes:
            raw_text = code_bytes[dec.start_byte:dec.end_byte].decode("utf-8", errors="ignore").strip()
            raw_decorators.append(raw_text)

            dec_name = ""
            call_node = None
            for child in dec.children:
                if child.type == "identifier":
                    dec_name = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                elif child.type == "attribute":
                    dec_name = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                elif child.type == "call":
                    call_node = child
                    func_child = child.child_by_field_name("function")
                    if func_child:
                        dec_name = code_bytes[func_child.start_byte:func_child.end_byte].decode("utf-8", errors="ignore")

            if dec_name:
                decorator_names.append(dec_name)

            # Route detection (e.g. @app.get("/items"), @app.get(path="/items"), @router.post("/calc"), @api.route("/path", methods=["GET", "POST"]))
            if call_node and not route_info:
                parts = dec_name.split(".")
                attr_method = parts[-1].lower() if parts else ""

                path_val = None
                method_val = None
                methods_list: List[str] = []

                args = call_node.child_by_field_name("arguments")
                if args:
                    for arg in args.children:
                        if arg.type == "string":
                            path_val = code_bytes[arg.start_byte:arg.end_byte].decode("utf-8", errors="ignore").strip("'\"")
                            break
                        elif arg.type == "keyword_argument":
                            key_node = arg.child_by_field_name("name")
                            if key_node:
                                key_text = code_bytes[key_node.start_byte:key_node.end_byte].decode("utf-8", errors="ignore")
                                if key_text in ("path", "rule", "endpoint", "url"):
                                    val_node = arg.child_by_field_name("value")
                                    if val_node and val_node.type == "string":
                                        path_val = code_bytes[val_node.start_byte:val_node.end_byte].decode("utf-8", errors="ignore").strip("'\"")
                                        break

                if attr_method in http_methods and path_val:
                    method_val = attr_method.upper()
                    methods_list = [method_val]
                elif attr_method in ("route", "api_route") and path_val:
                    method_val = "GET"
                    methods_list = ["GET"]
                    if args:
                        for arg in args.children:
                            if arg.type == "keyword_argument":
                                key_node = arg.child_by_field_name("name")
                                if key_node and code_bytes[key_node.start_byte:key_node.end_byte].decode("utf-8", errors="ignore") == "methods":
                                    val_node = arg.child_by_field_name("value")
                                    if val_node:
                                        parsed_methods = []
                                        if val_node.type in ("list", "tuple", "set"):
                                            for item in val_node.children:
                                                if item.type == "string":
                                                    m_str = code_bytes[item.start_byte:item.end_byte].decode("utf-8", errors="ignore").strip("'\"").upper()
                                                    if m_str in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                                                        parsed_methods.append(m_str)
                                        elif val_node.type == "string":
                                            m_str = code_bytes[val_node.start_byte:val_node.end_byte].decode("utf-8", errors="ignore").strip("'\"").upper()
                                            if m_str in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                                                parsed_methods.append(m_str)

                                        if parsed_methods:
                                            methods_list = parsed_methods
                                            method_val = parsed_methods[0]
                                    break

                if path_val and method_val:
                    route_info = {
                        "path": path_val,
                        "method": method_val,
                        "methods": methods_list,
                        "decorator": dec_name,
                        "raw_decorator": raw_text,
                    }

        return raw_decorators, decorator_names, route_info

    def _handle_function_definition(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        lines: List[str],
        file_path: str,
        is_test_file: bool,
        scope_prefix: str,
        enclosing_symbol: str,
        result: ParseResult,
        decorators: Optional[List[str]] = None,
        decorator_names: Optional[List[str]] = None,
        route_info: Optional[Dict[str, str]] = None,
        start_line_override: Optional[int] = None,
    ):
        func_name = self._get_child_text(node, "name", code_bytes)
        if not func_name:
            return

        qual_name = f"{scope_prefix}{func_name}" if scope_prefix else func_name
        is_method = bool(scope_prefix)
        is_test = (
            is_test_file
            or func_name.startswith("test_")
            or func_name.endswith("_test")
        )
        node_type = "TEST" if is_test else ("METHOD" if is_method else "FUNCTION")

        sig = self._extract_function_signature(node, code_bytes)
        doc = self._extract_docstring(node, code_bytes)

        start_line = start_line_override or (node.start_point.row + 1)
        end_line = node.end_point.row + 1

        node_metadata: Dict[str, Any] = {
            "is_async": node.child_by_field_name("async") is not None,
            "enclosing": scope_prefix.rstrip("."),
        }
        if decorators:
            node_metadata["decorators"] = decorators
        if decorator_names:
            node_metadata["decorator_names"] = decorator_names
        if route_info:
            node_metadata["route"] = route_info

        result.nodes.append(
            ExtractedNode(
                name=func_name,
                node_type=node_type,
                qualified_name=qual_name,
                start_line=start_line,
                end_line=end_line,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="python",
                signature=sig,
                docstring=doc,
                metadata=node_metadata,
            )
        )

        # Process function body with this function as enclosing_symbol
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._traverse_node(
                    node=child,
                    code_bytes=code_bytes,
                    lines=lines,
                    file_path=file_path,
                    is_test_file=is_test_file,
                    scope_prefix=f"{qual_name}.",
                    enclosing_symbol=qual_name,
                    result=result,
                )

    def _handle_class_definition(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        lines: List[str],
        file_path: str,
        is_test_file: bool,
        scope_prefix: str,
        enclosing_symbol: str,
        result: ParseResult,
        decorators: Optional[List[str]] = None,
        decorator_names: Optional[List[str]] = None,
        start_line_override: Optional[int] = None,
    ):
        class_name = self._get_child_text(node, "name", code_bytes)
        if not class_name:
            return

        qual_name = f"{scope_prefix}{class_name}" if scope_prefix else class_name
        is_test = (
            is_test_file
            or class_name.startswith("Test")
            or class_name.endswith("Test")
        )
        node_type = "TEST" if is_test else "CLASS"

        doc = self._extract_docstring(node, code_bytes)
        superclasses = self._extract_superclasses(node, code_bytes)

        start_line = start_line_override or (node.start_point.row + 1)
        end_line = node.end_point.row + 1

        node_metadata: Dict[str, Any] = {"superclasses": superclasses}
        if decorators:
            node_metadata["decorators"] = decorators
        if decorator_names:
            node_metadata["decorator_names"] = decorator_names

        result.nodes.append(
            ExtractedNode(
                name=class_name,
                node_type=node_type,
                qualified_name=qual_name,
                start_line=start_line,
                end_line=end_line,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="python",
                signature=f"class {class_name}({', '.join(superclasses)})" if superclasses else f"class {class_name}",
                docstring=doc,
                metadata=node_metadata,
            )
        )

        # Process class body
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._traverse_node(
                    node=child,
                    code_bytes=code_bytes,
                    lines=lines,
                    file_path=file_path,
                    is_test_file=is_test_file,
                    scope_prefix=f"{qual_name}.",
                    enclosing_symbol=qual_name,
                    result=result,
                )
