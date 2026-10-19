"""Tree-sitter AST Parser for Python Source Code

Extracts classes, functions, methods, tests, signatures, docstrings,
imports, and call relationships with precise byte and line ranges.
Implements non-recursive TreeCursor traversal with enter/exit state stacks
to eliminate Windows C stack access violations on deep AST structures.
"""

import bisect
import re
from typing import Any, Dict, List, Optional, Tuple
import tree_sitter
import tree_sitter_python

from packages.core.parsers.base import (
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ParseResult,
)

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


class PythonParser:
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_python.language())
        self.parser = tree_sitter.Parser(self.language)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        code_bytes = code.encode("utf-8")
        tree = self.parser.parse(code_bytes)
        cursor = tree.walk()

        result = ParseResult(file_path=file_path, language="python")
        is_test_file = "test" in file_path.lower()

        # Build fast line offsets for byte -> (line, col) mapping to avoid C-level TSPoint ABI bugs
        line_offsets = [0]
        for line in code_bytes.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1] + len(line))

        def byte_to_point(b: int) -> Tuple[int, int]:
            row = bisect.bisect_right(line_offsets, b) - 1
            col = b - line_offsets[row]
            return row + 1, col

        # Scope stack: (depth, scope_prefix, enclosing_symbol)
        scope_stack = [(0, "", "<module>")]
        # Def stack: frames for classes and functions
        def_stack: List[Dict[str, Any]] = []
        # Pending decorator info: (start_line, raw_decs, dec_names, route_info)
        pending_decorators: Optional[Tuple[int, List[str], List[str], Optional[Dict[str, Any]]]] = None
        # Pending call: when visiting call, wait for function node
        pending_call: Optional[Dict[str, Any]] = None

        visited = False
        while True:
            node = cursor.node
            depth = cursor.depth
            field = cursor.field_name
            ntype = node.type
            skip_children = False

            if not visited:
                # Unwind scope stack and frame stacks when ascending or moving to a sibling
                while len(scope_stack) > 1 and scope_stack[-1][0] >= depth:
                    scope_stack.pop()
                while def_stack and def_stack[-1]["depth"] >= depth:
                    def_stack.pop()
                if pending_call and pending_call["depth"] >= depth:
                    pending_call = None

                _, scope_prefix, enclosing_symbol = scope_stack[-1]

                # 1. Syntax Error
                if ntype == "ERROR":
                    s_row, s_col = byte_to_point(node.start_byte)
                    result.syntax_errors.append(f"Syntax error at line {s_row}:{s_col}")
                    skip_children = True

                # 2. Imports
                elif ntype == "import_statement":
                    raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                    s_row, _ = byte_to_point(node.start_byte)
                    body = raw.strip()
                    if body.startswith("import "):
                        body = body[7:].strip()
                    for item in body.split(","):
                        item = item.strip()
                        if not item:
                            continue
                        if " as " in item:
                            parts = item.split(" as ")
                            mod_name = parts[0].strip()
                            alias = parts[1].strip()
                        else:
                            mod_name = item
                            alias = None
                        result.imports.append(ExtractedImport(
                            module_path=mod_name,
                            imported_symbols=[mod_name],
                            alias=alias,
                            line_number=s_row,
                            is_relative=False,
                            raw_statement=raw,
                        ))
                    skip_children = True

                elif ntype == "import_from_statement":
                    raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                    s_row, _ = byte_to_point(node.start_byte)
                    body = raw.strip()
                    is_relative = False
                    module_path = ""
                    symbols: List[str] = []

                    m_from = re.match(r"^from\s+([^\s]+)\s+import\s+(.+)$", body, re.DOTALL)
                    if m_from:
                        mod_part = m_from.group(1).strip()
                        sym_part = m_from.group(2).strip().strip("()")
                        is_relative = mod_part.startswith(".")
                        module_path = mod_part

                        for item in sym_part.split(","):
                            item = item.strip()
                            if not item:
                                continue
                            if item == "*":
                                symbols.append("*")
                            elif " as " in item:
                                parts = item.split(" as ")
                                symbols.append(parts[0].strip())
                            else:
                                symbols.append(item)

                    result.imports.append(ExtractedImport(
                        module_path=module_path,
                        imported_symbols=symbols,
                        line_number=s_row,
                        is_relative=is_relative,
                        raw_statement=raw,
                    ))
                    skip_children = True

                # 3. Decorated Definition
                elif ntype == "decorated_definition":
                    s_row, _ = byte_to_point(node.start_byte)
                    raw_text = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                    raw_decs: List[str] = []
                    dec_names: List[str] = []
                    route_info: Optional[Dict[str, Any]] = None

                    dec_matches = re.findall(r"@([a-zA-Z0-9_\.]+)(\([^\)]*\))?", raw_text)
                    for base_name, args_part in dec_matches:
                        full_dec = f"@{base_name}{args_part}".strip()
                        raw_decs.append(full_dec)
                        dec_names.append(base_name)

                        if not route_info and args_part:
                            parts = base_name.split(".")
                            attr_m = parts[-1].lower() if parts else ""
                            arg_content = args_part.strip("()").strip()

                            path_val = None
                            method_val = None
                            methods_list: List[str] = []

                            m_path = re.search(r'''(?:^|(?:\b(?:path|rule|endpoint|url)\s*=\s*))["']([^"']+)["']''', arg_content)
                            if m_path:
                                path_val = m_path.group(1)

                            if attr_m in HTTP_METHODS and path_val:
                                method_val = attr_m.upper()
                                methods_list = [method_val]
                            elif attr_m in ("route", "api_route") and path_val:
                                m_methods = re.search(r'''\bmethods\s*=\s*\[([^\]]+)\]''', arg_content)
                                if m_methods:
                                    methods_list = [m.strip(" '\"").upper() for m in m_methods.group(1).split(",")]
                                    method_val = methods_list[0] if methods_list else "GET"
                                else:
                                    method_val = "GET"
                                    methods_list = ["GET"]

                            if path_val and method_val:
                                route_info = {
                                    "path": path_val,
                                    "method": method_val,
                                    "methods": methods_list,
                                    "decorator": base_name,
                                    "raw_decorator": full_dec,
                                }

                    pending_decorators = (s_row, raw_decs, dec_names, route_info)

                # 4. Function / Class Definition
                elif ntype in ("function_definition", "class_definition"):
                    start_override, decs, dec_names, route_info = (
                        pending_decorators if pending_decorators else (None, None, None, None)
                    )
                    pending_decorators = None

                    s_row, s_col = byte_to_point(node.start_byte)
                    e_row, e_col = byte_to_point(node.end_byte)

                    def_stack.append({
                        "depth": depth,
                        "type": ntype,
                        "scope_prefix": scope_prefix,
                        "enclosing_symbol": enclosing_symbol,
                        "start_line": start_override or s_row,
                        "end_line": e_row,
                        "start_col": s_col,
                        "end_col": e_col,
                        "name": "",
                        "params": "()",
                        "ret": "",
                        "is_async": False,
                        "superclasses": [],
                        "docstring": "",
                        "decorators": decs,
                        "decorator_names": dec_names,
                        "route_info": route_info,
                        "in_body": False,
                        "body_depth": -1,
                        "body_first_stmt": True,
                        "node_created": False,
                        "extracted_node": None,
                    })

                # 5. Calls
                elif ntype == "call":
                    raw_call = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                    if len(raw_call) > 120:
                        raw_call = raw_call[:117] + "..."
                    s_row, _ = byte_to_point(node.start_byte)
                    pending_call = {
                        "depth": depth,
                        "line": s_row,
                        "raw_call": raw_call,
                        "caller": enclosing_symbol,
                    }

                # Process child field names for active definition
                if def_stack:
                    curr = def_stack[-1]
                    if not curr["in_body"]:
                        if field == "name":
                            curr["name"] = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                        elif field == "parameters":
                            curr["params"] = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                        elif field == "return_type":
                            curr["ret"] = " -> " + code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                        elif ntype == "async":
                            curr["is_async"] = True
                        elif field == "superclasses" or ntype == "argument_list":
                            raw_supers = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip("()")
                            curr["superclasses"] = [s.strip() for s in raw_supers.split(",") if s.strip()]
                        elif field == "body" or ntype == "block":
                            curr["in_body"] = True
                            curr["body_depth"] = depth

                            name = curr["name"]
                            sp = curr["scope_prefix"]
                            qual_name = f"{sp}{name}" if sp else name
                            curr["qual_name"] = qual_name

                            if curr["type"] == "class_definition":
                                is_test = is_test_file or name.startswith("Test") or name.endswith("Test")
                                node_type = "TEST" if is_test else "CLASS"
                                meta: Dict[str, Any] = {"superclasses": curr["superclasses"]}
                                if curr["decorators"]:
                                    meta["decorators"] = curr["decorators"]
                                if curr["decorator_names"]:
                                    meta["decorator_names"] = curr["decorator_names"]
                                sig = f"class {name}({', '.join(curr['superclasses'])})" if curr["superclasses"] else f"class {name}"
                                node_obj = ExtractedNode(
                                    name=name,
                                    node_type=node_type,
                                    qualified_name=qual_name,
                                    start_line=curr["start_line"],
                                    end_line=curr["end_line"],
                                    start_col=curr["start_col"],
                                    end_col=curr["end_col"],
                                    language="python",
                                    signature=sig,
                                    docstring="",
                                    metadata=meta,
                                )
                                result.nodes.append(node_obj)
                                curr["extracted_node"] = node_obj
                                scope_stack.append((curr["depth"], f"{qual_name}.", qual_name))
                            else:
                                is_method = bool(sp)
                                is_test = is_test_file or name.startswith("test_") or name.endswith("_test")
                                node_type = "TEST" if is_test else ("METHOD" if is_method else "FUNCTION")
                                sig = f"def {name}{curr['params']}{curr['ret']}"
                                meta = {"is_async": curr["is_async"], "enclosing": sp.rstrip(".")}
                                if curr["decorators"]:
                                    meta["decorators"] = curr["decorators"]
                                if curr["decorator_names"]:
                                    meta["decorator_names"] = curr["decorator_names"]
                                if curr["route_info"]:
                                    meta["route"] = curr["route_info"]
                                node_obj = ExtractedNode(
                                    name=name,
                                    node_type=node_type,
                                    qualified_name=qual_name,
                                    start_line=curr["start_line"],
                                    end_line=curr["end_line"],
                                    start_col=curr["start_col"],
                                    end_col=curr["end_col"],
                                    language="python",
                                    signature=sig,
                                    docstring="",
                                    metadata=meta,
                                )
                                result.nodes.append(node_obj)
                                curr["extracted_node"] = node_obj
                                scope_stack.append((curr["depth"], f"{qual_name}.", qual_name))

                            curr["node_created"] = True
                    else:
                        # Inside body: capture docstring from first statement
                        if curr["body_first_stmt"]:
                            if depth == curr["body_depth"] + 1:
                                if ntype != "expression_statement":
                                    curr["body_first_stmt"] = False
                            elif depth == curr["body_depth"] + 2:
                                if ntype == "string":
                                    raw_doc = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                                    doc = raw_doc.strip('"""').strip("'''").strip('"').strip("'").strip()
                                    if curr["extracted_node"]:
                                        curr["extracted_node"].docstring = doc
                                curr["body_first_stmt"] = False

                # Resolve pending call function identifier
                if pending_call and depth > pending_call["depth"]:
                    if field == "function" or (depth == pending_call["depth"] + 1 and ntype in ("identifier", "attribute")):
                        callee_raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                        callee = callee_raw.split(".")[-1]
                        result.calls.append(ExtractedCall(
                            caller_name=pending_call["caller"],
                            callee_name=callee,
                            line_number=pending_call["line"],
                            raw_call=pending_call["raw_call"],
                        ))
                        pending_call = None

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

        return result
