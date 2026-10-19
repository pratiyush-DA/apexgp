"""Tree-sitter AST Parser for Rust Source Code (.rs).

Extracts functions, methods, structs, enums, traits, type aliases,
constants/statics, modules, imports (`use`), re-exports (`pub use`),
and function/method calls with precise locations and metadata.
"""

from typing import Any, Dict, List, Optional, Tuple
import tree_sitter
import tree_sitter_rust

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)


class RustParser(BaseParser):
    """Tree-sitter based AST parser for the Rust programming language."""

    def __init__(self):
        self.rust_lang = tree_sitter.Language(tree_sitter_rust.language())
        self.parser = tree_sitter.Parser(self.rust_lang)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        encoded = code.encode("utf-8")
        tree = self.parser.parse(encoded)
        result = ParseResult(file_path=file_path, language="rust")

        if tree.root_node.has_error:
            self._collect_errors(tree.root_node, result)

        is_test_file = file_path.endswith("_test.rs") or "test" in file_path.lower()
        self._traverse_scope(
            container_node=tree.root_node,
            code_bytes=encoded,
            file_path=file_path,
            prefix="",
            is_test_context=is_test_file,
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

    def _get_visibility(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        for child in node.children:
            if child.type == "visibility_modifier":
                vis = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore").strip()
                if "crate" in vis:
                    return "crate"
                return "public"
        return "private"

    def _extract_docstring(self, doc_lines: List[str]) -> str:
        clean = []
        for line in doc_lines:
            line = line.strip()
            if line.startswith("///"):
                clean.append(line[3:].strip())
            elif line.startswith("//!"):
                clean.append(line[3:].strip())
            elif line.startswith("/**") and line.endswith("*/"):
                clean.append(line[3:-2].strip())
            else:
                clean.append(line)
        return "\n".join(clean).strip()

    def _traverse_scope(
        self,
        container_node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        is_test_context: bool,
        result: ParseResult,
    ):
        pending_docs: List[str] = []
        pending_attrs: List[str] = []

        for child in container_node.children:
            nt = child.type

            if nt in ("line_comment", "block_comment"):
                raw_comment = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                if raw_comment.startswith("///") or raw_comment.startswith("//!") or raw_comment.startswith("/**"):
                    pending_docs.append(raw_comment)
                continue

            if nt == "attribute_item":
                raw_attr = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                pending_attrs.append(raw_attr)
                continue

            docstring = self._extract_docstring(pending_docs) if pending_docs else ""
            attrs = list(pending_attrs)
            pending_docs.clear()
            pending_attrs.clear()

            is_item_test = is_test_context or any("test" in a for a in attrs)

            if nt == "use_declaration":
                self._handle_use(child, code_bytes, prefix, result)
            elif nt == "mod_item":
                self._handle_mod(child, code_bytes, file_path, prefix, is_item_test, docstring, result)
            elif nt == "function_item":
                self._handle_function(child, code_bytes, file_path, prefix, None, is_item_test, docstring, result)
            elif nt == "struct_item":
                self._handle_struct(child, code_bytes, file_path, prefix, docstring, result)
            elif nt == "enum_item":
                self._handle_enum(child, code_bytes, file_path, prefix, docstring, result)
            elif nt == "trait_item":
                self._handle_trait(child, code_bytes, file_path, prefix, is_item_test, docstring, result)
            elif nt == "impl_item":
                self._handle_impl(child, code_bytes, file_path, prefix, is_item_test, result)
            elif nt == "type_item":
                self._handle_type_alias(child, code_bytes, file_path, prefix, docstring, result)
            elif nt in ("const_item", "static_item"):
                self._handle_const_static(child, code_bytes, file_path, prefix, docstring, result)

    def _handle_use(self, node: tree_sitter.Node, code_bytes: bytes, prefix: str, result: ParseResult):
        raw_stmt = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
        is_pub = any(c.type == "visibility_modifier" for c in node.children)
        arg = node.child_by_field_name("argument")
        if not arg:
            return

        items = self._parse_use_argument(arg, code_bytes)
        for mod_p, sym, alias, is_wc in items:
            is_rel = mod_p.startswith("super") or mod_p.startswith("self") or mod_p.startswith(".")
            syms = [] if is_wc else ([sym] if sym else [])
            result.imports.append(
                ExtractedImport(
                    module_path=mod_p,
                    imported_symbols=syms,
                    alias=alias,
                    line_number=node.start_point.row + 1,
                    is_relative=is_rel,
                    raw_statement=raw_stmt,
                )
            )
            if is_pub:
                exported_syms = {}
                if sym and not is_wc:
                    exported_syms[alias or sym] = sym
                result.re_exports.append(
                    ExtractedReExport(
                        module_path=mod_p,
                        exported_symbols=exported_syms,
                        is_wildcard=is_wc,
                        line_number=node.start_point.row + 1,
                        raw_statement=raw_stmt,
                    )
                )

    def _parse_use_argument(
        self,
        arg_node: tree_sitter.Node,
        code_bytes: bytes,
        prefix: str = "",
    ) -> List[Tuple[str, str, Optional[str], bool]]:
        results: List[Tuple[str, str, Optional[str], bool]] = []
        nt = arg_node.type
        raw = code_bytes[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="ignore")

        if nt == "identifier":
            sym = raw
            mod_p = prefix.rstrip("::") if prefix else sym
            results.append((mod_p, sym, None, False))
        elif nt == "scoped_identifier":
            path_node = arg_node.child_by_field_name("path")
            name_node = arg_node.child_by_field_name("name")
            p_str = code_bytes[path_node.start_byte:path_node.end_byte].decode("utf-8", errors="ignore") if path_node else ""
            n_str = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore") if name_node else ""
            full_path = f"{prefix}{p_str}" if prefix else p_str
            results.append((full_path, n_str, None, False))
        elif nt == "use_wildcard":
            raw_wc = raw.rstrip("::*")
            full_path = f"{prefix}{raw_wc}" if prefix else raw_wc
            results.append((full_path, "*", None, True))
        elif nt == "use_as_clause":
            path_node = arg_node.child_by_field_name("path")
            alias_node = arg_node.child_by_field_name("alias")
            p_str = code_bytes[path_node.start_byte:path_node.end_byte].decode("utf-8", errors="ignore") if path_node else ""
            a_str = code_bytes[alias_node.start_byte:alias_node.end_byte].decode("utf-8", errors="ignore") if alias_node else ""
            if "::" in p_str:
                mod_p, sym = p_str.rsplit("::", 1)
                full_mod = f"{prefix}{mod_p}" if prefix else mod_p
            else:
                full_mod = prefix.rstrip("::") if prefix else p_str
                sym = p_str
            results.append((full_mod, sym, a_str, False))
        elif nt == "scoped_use_list":
            path_node = arg_node.child_by_field_name("path")
            list_node = arg_node.child_by_field_name("list")
            p_str = code_bytes[path_node.start_byte:path_node.end_byte].decode("utf-8", errors="ignore") if path_node else ""
            new_prefix = f"{prefix}{p_str}::" if prefix else f"{p_str}::"
            if list_node:
                for c in list_node.children:
                    if c.type in ("identifier", "scoped_identifier", "use_as_clause", "use_wildcard", "scoped_use_list"):
                        results.extend(self._parse_use_argument(c, code_bytes, new_prefix))
        elif nt == "use_list":
            for c in arg_node.children:
                if c.type in ("identifier", "scoped_identifier", "use_as_clause", "use_wildcard", "scoped_use_list"):
                    results.extend(self._parse_use_argument(c, code_bytes, prefix))
        return results

    def _handle_mod(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        is_test_context: bool,
        docstring: str,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        mod_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        qual_name = f"{prefix}{mod_name}"
        vis = self._get_visibility(node, code_bytes)

        body_node = node.child_by_field_name("body")
        if body_node is None:
            # External module declaration e.g. "pub mod math;"
            result.nodes.append(
                ExtractedNode(
                    name=mod_name,
                    node_type="MODULE",
                    qualified_name=qual_name,
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    start_col=node.start_point.column,
                    end_col=node.end_point.column,
                    language="rust",
                    signature=f"mod {mod_name}",
                    docstring=docstring,
                    metadata={"visibility": vis, "external": True},
                )
            )
            raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
            result.imports.append(
                ExtractedImport(
                    module_path=mod_name,
                    imported_symbols=[mod_name],
                    alias=None,
                    line_number=node.start_point.row + 1,
                    is_relative=True,
                    raw_statement=raw,
                )
            )
        else:
            # Inline module e.g. "mod inner { ... }"
            is_mod_test = is_test_context or mod_name == "tests" or "test" in mod_name
            result.nodes.append(
                ExtractedNode(
                    name=mod_name,
                    node_type="MODULE",
                    qualified_name=qual_name,
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    start_col=node.start_point.column,
                    end_col=node.end_point.column,
                    language="rust",
                    signature=f"mod {mod_name}",
                    docstring=docstring,
                    metadata={"visibility": vis, "external": False},
                )
            )
            self._traverse_scope(
                container_node=body_node,
                code_bytes=code_bytes,
                file_path=file_path,
                prefix=f"{qual_name}::",
                is_test_context=is_mod_test,
                result=result,
            )

    def _handle_struct(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        docstring: str,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        struct_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        qual_name = f"{prefix}{struct_name}"
        vis = self._get_visibility(node, code_bytes)

        result.nodes.append(
            ExtractedNode(
                name=struct_name,
                node_type="CLASS",
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="rust",
                signature=f"struct {struct_name}",
                docstring=docstring,
                metadata={"kind": "struct", "visibility": vis},
            )
        )
        if vis == "public":
            result.exports.append(qual_name)

    def _handle_enum(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        docstring: str,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        enum_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        qual_name = f"{prefix}{enum_name}"
        vis = self._get_visibility(node, code_bytes)

        result.nodes.append(
            ExtractedNode(
                name=enum_name,
                node_type="TYPE",
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="rust",
                signature=f"enum {enum_name}",
                docstring=docstring,
                metadata={"kind": "enum", "visibility": vis},
            )
        )
        if vis == "public":
            result.exports.append(qual_name)

    def _handle_trait(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        is_test_context: bool,
        docstring: str,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        trait_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        qual_name = f"{prefix}{trait_name}"
        vis = self._get_visibility(node, code_bytes)

        result.nodes.append(
            ExtractedNode(
                name=trait_name,
                node_type="INTERFACE",
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="rust",
                signature=f"trait {trait_name}",
                docstring=docstring,
                metadata={"kind": "trait", "visibility": vis},
            )
        )
        if vis == "public":
            result.exports.append(qual_name)

        # Methods inside trait declaration_list
        body_node = node.child_by_field_name("body")
        if body_node:
            for child in body_node.children:
                if child.type in ("function_item", "function_signature_item"):
                    self._handle_function(
                        child,
                        code_bytes,
                        file_path,
                        prefix,
                        enclosing=trait_name,
                        is_test=False,
                        docstring="",
                        result=result,
                    )

    def _handle_impl(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        is_test_context: bool,
        result: ParseResult,
    ):
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        type_name = code_bytes[type_node.start_byte:type_node.end_byte].decode("utf-8", errors="ignore")

        trait_node = node.child_by_field_name("trait")
        trait_name = (
            code_bytes[trait_node.start_byte:trait_node.end_byte].decode("utf-8", errors="ignore")
            if trait_node
            else None
        )

        body_node = node.child_by_field_name("body")
        if not body_node:
            return

        pending_docs: List[str] = []
        pending_attrs: List[str] = []
        for child in body_node.children:
            if child.type in ("line_comment", "block_comment"):
                raw_comment = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                if raw_comment.startswith("///") or raw_comment.startswith("//!") or raw_comment.startswith("/**"):
                    pending_docs.append(raw_comment)
                continue
            if child.type == "attribute_item":
                raw_attr = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
                pending_attrs.append(raw_attr)
                continue

            docstring = self._extract_docstring(pending_docs) if pending_docs else ""
            attrs = list(pending_attrs)
            pending_docs.clear()
            pending_attrs.clear()

            is_item_test = is_test_context or any("test" in a for a in attrs)

            if child.type == "function_item":
                self._handle_function(
                    child,
                    code_bytes,
                    file_path,
                    prefix,
                    enclosing=type_name,
                    is_test=is_item_test,
                    docstring=docstring,
                    result=result,
                    trait_name=trait_name,
                )

    def _handle_type_alias(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        docstring: str,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        type_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        qual_name = f"{prefix}{type_name}"
        vis = self._get_visibility(node, code_bytes)

        result.nodes.append(
            ExtractedNode(
                name=type_name,
                node_type="TYPE",
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="rust",
                signature=f"type {type_name}",
                docstring=docstring,
                metadata={"kind": "type_alias", "visibility": vis},
            )
        )
        if vis == "public":
            result.exports.append(qual_name)

    def _handle_const_static(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        docstring: str,
        result: ParseResult,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        var_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")
        qual_name = f"{prefix}{var_name}"
        vis = self._get_visibility(node, code_bytes)
        kind = "const" if node.type == "const_item" else "static"

        result.nodes.append(
            ExtractedNode(
                name=var_name,
                node_type="VARIABLE",
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="rust",
                signature=f"{kind} {var_name}",
                docstring=docstring,
                metadata={"kind": kind, "visibility": vis},
            )
        )
        if vis == "public":
            result.exports.append(qual_name)

    def _handle_function(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        file_path: str,
        prefix: str,
        enclosing: Optional[str],
        is_test: bool,
        docstring: str,
        result: ParseResult,
        trait_name: Optional[str] = None,
    ):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        fn_name = code_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore")

        is_fn_test = is_test or fn_name.startswith("test_") or fn_name == "test"
        if is_fn_test:
            node_type = "TEST"
        elif enclosing:
            node_type = "METHOD"
        else:
            node_type = "FUNCTION"

        if enclosing:
            qual_name = f"{prefix}{enclosing}::{fn_name}"
        else:
            qual_name = f"{prefix}{fn_name}"

        vis = self._get_visibility(node, code_bytes)

        body_node = node.child_by_field_name("body")
        if body_node:
            sig = code_bytes[node.start_byte:body_node.start_byte].decode("utf-8", errors="ignore").strip()
        else:
            sig = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip().rstrip(";")

        metadata: Dict[str, Any] = {"visibility": vis}
        if enclosing:
            metadata["enclosing"] = enclosing
        if trait_name:
            metadata["trait"] = trait_name

        result.nodes.append(
            ExtractedNode(
                name=fn_name,
                node_type=node_type,
                qualified_name=qual_name,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                start_col=node.start_point.column,
                end_col=node.end_point.column,
                language="rust",
                signature=sig,
                docstring=docstring,
                metadata=metadata,
            )
        )
        if vis == "public" and not enclosing:
            result.exports.append(qual_name)

        if body_node:
            self._extract_calls(body_node, code_bytes, caller_name=qual_name, result=result)

    def _extract_calls(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        caller_name: str,
        result: ParseResult,
    ):
        def _walk(n):
            if n.type == "call_expression":
                fn_node = n.child_by_field_name("function")
                if fn_node:
                    raw_call = code_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="ignore").splitlines()[0]
                    callee_str = ""
                    if fn_node.type in ("identifier", "scoped_identifier"):
                        callee_str = code_bytes[fn_node.start_byte:fn_node.end_byte].decode("utf-8", errors="ignore")
                    elif fn_node.type == "field_expression":
                        field_node = fn_node.child_by_field_name("field")
                        if field_node:
                            val_node = fn_node.child_by_field_name("value")
                            v_str = (
                                code_bytes[val_node.start_byte:val_node.end_byte].decode("utf-8", errors="ignore")
                                if val_node
                                else ""
                            )
                            f_str = code_bytes[field_node.start_byte:field_node.end_byte].decode("utf-8", errors="ignore")
                            callee_str = f"{v_str}.{f_str}" if v_str else f_str

                    if callee_str:
                        result.calls.append(
                            ExtractedCall(
                                caller_name=caller_name,
                                callee_name=callee_str,
                                line_number=n.start_point.row + 1,
                                raw_call=raw_call[:300],
                            )
                        )
            elif n.type == "token_tree":
                # Handle invocations inside macros (e.g. assert_eq!(run(), 30), assert!(p.check()))
                children = n.children
                for i, child in enumerate(children):
                    if child.type == "token_tree" and child.text and child.text.startswith(b"("):
                        callee_str = ""
                        if (
                            i >= 3
                            and children[i - 2].type in ("::", ".")
                            and children[i - 1].type == "identifier"
                            and children[i - 3].type == "identifier"
                        ):
                            sep = children[i - 2].type
                            id1 = children[i - 3].text.decode("utf-8", errors="ignore")
                            id2 = children[i - 1].text.decode("utf-8", errors="ignore")
                            callee_str = f"{id1}{sep}{id2}"
                        elif i >= 1 and children[i - 1].type == "identifier":
                            callee_str = children[i - 1].text.decode("utf-8", errors="ignore")

                        if callee_str and callee_str not in ("assert", "assert_eq", "assert_ne", "panic"):
                            raw_snippet = code_bytes[children[i - 1].start_byte:child.end_byte].decode("utf-8", errors="ignore")
                            result.calls.append(
                                ExtractedCall(
                                    caller_name=caller_name,
                                    callee_name=callee_str,
                                    line_number=children[i - 1].start_point.row + 1,
                                    raw_call=raw_snippet[:300],
                                )
                            )

            for child in n.children:
                _walk(child)

        _walk(node)
