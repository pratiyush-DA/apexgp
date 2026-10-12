"""Tree-sitter AST Parser for TypeScript and JavaScript Source Code

Supports .ts, .tsx, .js, .jsx files.
Extracts classes, interfaces, types, functions, methods, tests,
imports, exports, and call relationships with precise locations.
"""

from typing import List, Optional
import tree_sitter
import tree_sitter_javascript
import tree_sitter_typescript

from packages.core.parsers.base import (
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ParseResult,
)


class TypeScriptJavaScriptParser:
    def __init__(self):
        self.ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        self.tsx_lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
        self.js_lang = tree_sitter.Language(tree_sitter_javascript.language())

        self.ts_parser = tree_sitter.Parser(self.ts_lang)
        self.tsx_parser = tree_sitter.Parser(self.tsx_lang)
        self.js_parser = tree_sitter.Parser(self.js_lang)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        encoded = code.encode("utf-8")
        ext = file_path.lower().split(".")[-1] if "." in file_path else "ts"

        if ext == "tsx":
            tree = self.tsx_parser.parse(encoded)
            lang = "typescript"
        elif ext in ("js", "jsx", "mjs", "cjs"):
            tree = self.js_parser.parse(encoded)
            lang = "javascript"
        else:
            tree = self.ts_parser.parse(encoded)
            lang = "typescript"

        lines = code.splitlines(keepends=True)
        result = ParseResult(file_path=file_path, language=lang)
        is_test_file = any(t in file_path.lower() for t in ("test", "spec"))

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
            self._handle_import(node, code_bytes, result)
            return

        # 2. Interfaces
        if node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                if_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
                qual_name = f"{scope_prefix}{if_name}" if scope_prefix else if_name
                result.nodes.append(
                    ExtractedNode(
                        name=if_name,
                        node_type="INTERFACE",
                        qualified_name=qual_name,
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        start_col=node.start_point.column,
                        end_col=node.end_point.column,
                        language=result.language,
                        signature=f"interface {if_name}",
                        docstring=self._extract_jsdoc(node, code_bytes),
                    )
                )
                return

        # 3. Type Aliases
        if node.type == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                type_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
                qual_name = f"{scope_prefix}{type_name}" if scope_prefix else type_name
                result.nodes.append(
                    ExtractedNode(
                        name=type_name,
                        node_type="TYPE",
                        qualified_name=qual_name,
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        start_col=node.start_point.column,
                        end_col=node.end_point.column,
                        language=result.language,
                        signature=f"type {type_name}",
                        docstring=self._extract_jsdoc(node, code_bytes),
                    )
                )
                return

        # 4. Classes
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
                qual_name = f"{scope_prefix}{cls_name}" if scope_prefix else cls_name
                is_test = is_test_file or "Test" in cls_name
                result.nodes.append(
                    ExtractedNode(
                        name=cls_name,
                        node_type="TEST" if is_test else "CLASS",
                        qualified_name=qual_name,
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        start_col=node.start_point.column,
                        end_col=node.end_point.column,
                        language=result.language,
                        signature=f"class {cls_name}",
                        docstring=self._extract_jsdoc(node, code_bytes),
                    )
                )

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
                return

        # 5. Functions & Methods
        if node.type in ("function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            if name_node:
                fn_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
                qual_name = f"{scope_prefix}{fn_name}" if scope_prefix else fn_name
                is_method = node.type == "method_definition" or bool(scope_prefix)
                is_test = is_test_file or fn_name.startswith("test")
                node_type = "TEST" if is_test else ("METHOD" if is_method else "FUNCTION")

                result.nodes.append(
                    ExtractedNode(
                        name=fn_name,
                        node_type=node_type,
                        qualified_name=qual_name,
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        start_col=node.start_point.column,
                        end_col=node.end_point.column,
                        language=result.language,
                        signature=f"{fn_name}()",
                        docstring=self._extract_jsdoc(node, code_bytes),
                    )
                )

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
                return

        # 6. Variable declaration (e.g. const add = (...) => ...)
        if node.type == "lexical_declaration":
            for declarator in node.children:
                if declarator.type == "variable_declarator":
                    name_node = declarator.child_by_field_name("name")
                    val_node = declarator.child_by_field_name("value")
                    if name_node and val_node:
                        var_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
                        qual_name = f"{scope_prefix}{var_name}" if scope_prefix else var_name
                        if val_node.type in ("arrow_function", "function_expression"):
                            is_test = is_test_file or var_name.startswith("test")
                            result.nodes.append(
                                ExtractedNode(
                                    name=var_name,
                                    node_type="TEST" if is_test else "FUNCTION",
                                    qualified_name=qual_name,
                                    start_line=declarator.start_point.row + 1,
                                    end_line=declarator.end_point.row + 1,
                                    start_col=declarator.start_point.column,
                                    end_col=declarator.end_point.column,
                                    language=result.language,
                                    signature=f"const {var_name} = () =>",
                                    docstring=self._extract_jsdoc(node, code_bytes),
                                )
                            )
                            # Traverse inside arrow function body
                            body = val_node.child_by_field_name("body")
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
                            continue

        # 7. Function Call / Test blocks
        if node.type == "call_expression":
            self._handle_call(node, code_bytes, enclosing_symbol, is_test_file, result)

        # Recurse
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

    def _handle_import(
        self, node: tree_sitter.Node, code_bytes: bytes, result: ParseResult
    ):
        raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        source_node = node.child_by_field_name("source")
        if not source_node:
            return

        module_path = (
            code_bytes[source_node.start_byte:source_node.end_byte]
            .decode("utf-8", errors="ignore")
            .strip("'\"")
        )
        is_relative = module_path.startswith(".")

        symbols = []
        for child in node.children:
            if child.type == "import_clause":
                for sub in child.children:
                    if sub.type == "identifier":
                        symbols.append(code_bytes[sub.start_byte:sub.end_byte].decode("utf-8", errors="ignore"))
                    elif sub.type == "named_imports":
                        for spec in sub.children:
                            if spec.type == "import_specifier":
                                n = spec.child_by_field_name("name")
                                if n:
                                    symbols.append(code_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="ignore"))

        result.imports.append(
            ExtractedImport(
                module_path=module_path,
                imported_symbols=symbols,
                line_number=node.start_point.row + 1,
                is_relative=is_relative,
                raw_statement=raw,
            )
        )

    def _handle_call(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        enclosing_symbol: str,
        is_test_file: bool,
        result: ParseResult,
    ):
        func_node = node.child_by_field_name("function")
        if not func_node:
            return

        callee_raw = code_bytes[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="ignore")
        callee_simple = callee_raw.split(".")[-1]
        raw_call = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

        # Test block detection (e.g. describe("calculator", ...), test("adds numbers", ...), it("works", ...))
        if callee_simple in ("describe", "test", "it") and is_test_file:
            args = node.child_by_field_name("arguments")
            test_desc = callee_simple
            if args and args.children:
                for arg in args.children:
                        raw_str = code_bytes[arg.start_byte:arg.end_byte].decode("utf-8", errors="ignore").strip("\"'")
                        test_desc = f"{callee_simple}: {raw_str}"
                        break
            result.nodes.append(
                ExtractedNode(
                    name=test_desc,
                    node_type="TEST",
                    qualified_name=test_desc,
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    start_col=node.start_point.column,
                    end_col=node.end_point.column,
                    language=result.language,
                    signature=f"{callee_simple}()",
                    docstring="",
                )
            )

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

    def _extract_jsdoc(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        # Check previous sibling for comment
        prev = node.prev_sibling
        if prev and prev.type == "comment":
            raw = code_bytes[prev.start_byte:prev.end_byte].decode("utf-8", errors="ignore")
            lines = [line.strip().lstrip("/*").rstrip("*/").lstrip("*").strip() for line in raw.splitlines()]
            return "\n".join(l for l in lines if l)
        return ""
