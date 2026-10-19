"""Tree-sitter AST Parser for Shell Scripts (.sh, .bash, .zsh).

Extracts:
- Functions (FUNCTION): Posix func() and bash function func() definitions
- Variables (VARIABLE): Top-level and local variable assignments (export, readonly, local, declare, typeset)
- Sourced Scripts (ExtractedImport): `source ./lib.sh` and `. ./lib.sh`
- Command & Function Calls (ExtractedCall): Calls to user functions, subshells, and commands
- Syntax Error Resilience: Resilient partial extraction on malformed scripts
"""

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import tree_sitter
import tree_sitter_bash

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)


SHELL_BUILTINS: Set[str] = {
    "echo", "printf", "cd", "pwd", "shift", "return", "exit",
    "export", "local", "readonly", "declare", "typeset",
    "test", "[", "[[", "read", "true", "false", ":",
    "set", "unset", "trap", "eval", "exec", "source", ".",
    "alias", "unalias", "builtin", "command", "type", "hash",
    "help", "history", "jobs", "kill", "bg", "fg", "wait",
    "suspend", "times", "ulimit", "umask", "shopt", "bind",
    "complete", "compgen", "pushd", "popd", "dirs", "let",
    "emulate", "autoload", "enable", "disable",
}


class ShellParser(BaseParser):
    """Tree-sitter based AST parser for Shell scripts (.sh, .bash, .zsh)."""

    def __init__(self):
        self.bash_lang = tree_sitter.Language(tree_sitter_bash.language())
        self.parser = tree_sitter.Parser(self.bash_lang)

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        """Parses shell script source code into a standardized ParseResult."""
        if not code or not code.strip():
            return ParseResult(
                file_path=file_path,
                language="shell",
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
        calls: List[ExtractedCall] = []
        syntax_errors: List[str] = []

        if tree.root_node.has_error:
            self._collect_errors(tree.root_node, syntax_errors, encoded)

        lines = code.splitlines()

        # Recursively traverse the AST
        self._traverse_node(
            node=tree.root_node,
            code_bytes=encoded,
            lines=lines,
            enclosing_fn="",
            nodes=nodes,
            imports=imports,
            calls=calls,
        )

        # Deterministic sorting
        nodes.sort(key=lambda n: (n.start_line, n.qualified_name))
        calls.sort(key=lambda c: (c.line_number, c.callee_name))
        imports.sort(key=lambda i: (i.line_number, i.module_path))

        return ParseResult(
            file_path=file_path,
            language="shell",
            nodes=nodes,
            imports=imports,
            calls=calls,
            exports=[n.name for n in nodes if n.node_type == "FUNCTION"],
            re_exports=[],
            syntax_errors=syntax_errors,
        )

    def _collect_errors(self, node: tree_sitter.Node, syntax_errors: List[str], code_bytes: bytes) -> None:
        """Recursively collects syntax error nodes."""
        if node.is_error or node.type == "ERROR":
            line = node.start_point.row + 1
            snippet = code_bytes[node.start_byte:min(node.end_byte, node.start_byte + 50)].decode("utf-8", errors="ignore").replace("\n", " ")
            syntax_errors.append(f"Shell syntax error at line {line}: {snippet}")
        for child in node.children:
            self._collect_errors(child, syntax_errors, code_bytes)

    def _traverse_node(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        lines: List[str],
        enclosing_fn: str,
        nodes: List[ExtractedNode],
        imports: List[ExtractedImport],
        calls: List[ExtractedCall],
    ) -> None:
        """Traverses AST nodes extracting functions, variables, sourced files, and calls."""
        # 1. Function Definition: name() { ... } or function name() { ... }
        if node.type == "function_definition":
            fn_name_node = node.child_by_field_name("name")
            if fn_name_node:
                fn_name = code_bytes[fn_name_node.start_byte:fn_name_node.end_byte].decode("utf-8", errors="ignore").strip()
                if fn_name:
                    start_line = node.start_point.row + 1
                    end_line = node.end_point.row + 1
                    start_col = node.start_point.column
                    end_col = node.end_point.column

                    # Extract docstring from preceding comments
                    docstring = self._extract_preceding_docstring(lines, node.start_point.row)

                    nodes.append(
                        ExtractedNode(
                            name=fn_name,
                            node_type="FUNCTION",
                            qualified_name=fn_name,
                            start_line=start_line,
                            end_line=end_line,
                            start_col=start_col,
                            end_col=end_col,
                            language="shell",
                            signature=f"{fn_name}()",
                            docstring=docstring,
                            metadata={"enclosing": enclosing_fn},
                        )
                    )

                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            self._traverse_node(
                                node=child,
                                code_bytes=code_bytes,
                                lines=lines,
                                enclosing_fn=fn_name,
                                nodes=nodes,
                                imports=imports,
                                calls=calls,
                            )
                    return

        # 2. Variable Assignment: VAR=val
        elif node.type == "variable_assignment":
            parent_decl = node.parent if node.parent and node.parent.type == "declaration_command" else None
            self._handle_variable_assignment(
                node=node,
                code_bytes=code_bytes,
                enclosing_fn=enclosing_fn,
                nodes=nodes,
                parent_decl=parent_decl,
            )

        # 3. Declaration command: export VAR, readonly VAR, declare -a ARR
        elif node.type == "declaration_command":
            # Extract bare variable declarations that do not have an assignment
            for child in node.children:
                if child.type == "variable_name":
                    self._handle_bare_variable_declaration(
                        node=child,
                        code_bytes=code_bytes,
                        enclosing_fn=enclosing_fn,
                        nodes=nodes,
                        parent_decl=node,
                    )

        # 4. Command invocation (including source and .)
        elif node.type == "command":
            self._handle_command(
                node=node,
                code_bytes=code_bytes,
                enclosing_fn=enclosing_fn,
                imports=imports,
                calls=calls,
            )

        # Recurse for all child nodes (e.g. compound statements, pipelines, substitutions)
        for child in node.children:
            self._traverse_node(
                node=child,
                code_bytes=code_bytes,
                lines=lines,
                enclosing_fn=enclosing_fn,
                nodes=nodes,
                imports=imports,
                calls=calls,
            )

    def _handle_bare_variable_declaration(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        enclosing_fn: str,
        nodes: List[ExtractedNode],
        parent_decl: Optional[tree_sitter.Node] = None,
    ) -> None:
        """Extracts bare VARIABLE node from a variable_name inside declaration_command."""
        var_name = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
        if not var_name:
            return

        start_line = node.start_point.row + 1
        end_line = node.end_point.row + 1
        start_col = node.start_point.column
        end_col = node.end_point.column

        decl_text = parent_decl.text.decode("utf-8", errors="ignore") if parent_decl else ""
        decl_lower = decl_text.lower()

        is_exported = "export" in decl_lower
        is_readonly = "readonly" in decl_lower
        is_local = "local" in decl_lower or bool(enclosing_fn)

        qualified_name = f"{enclosing_fn}.{var_name}" if enclosing_fn else var_name

        nodes.append(
            ExtractedNode(
                name=var_name,
                node_type="VARIABLE",
                qualified_name=qualified_name,
                start_line=start_line,
                end_line=end_line,
                start_col=start_col,
                end_col=end_col,
                language="shell",
                signature=decl_text.replace("\n", " ").strip(),
                docstring="",
                metadata={
                    "enclosing": enclosing_fn,
                    "is_exported": is_exported,
                    "is_readonly": is_readonly,
                    "is_local": is_local,
                },
            )
        )

    def _handle_variable_assignment(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        enclosing_fn: str,
        nodes: List[ExtractedNode],
        parent_decl: Optional[tree_sitter.Node] = None,
    ) -> None:
        """Extracts VARIABLE node from a variable_assignment AST node."""
        var_name_node = node.child_by_field_name("name")
        if not var_name_node:
            for c in node.children:
                if c.type == "variable_name":
                    var_name_node = c
                    break

        if not var_name_node:
            return

        var_name = code_bytes[var_name_node.start_byte:var_name_node.end_byte].decode("utf-8", errors="ignore").strip()
        if not var_name:
            return

        start_line = node.start_point.row + 1
        end_line = node.end_point.row + 1
        start_col = node.start_point.column
        end_col = node.end_point.column

        decl_text = parent_decl.text.decode("utf-8", errors="ignore") if parent_decl else ""
        decl_lower = decl_text.lower()

        is_exported = "export" in decl_lower
        is_readonly = "readonly" in decl_lower
        is_local = "local" in decl_lower or bool(enclosing_fn)

        full_text = node.text.decode("utf-8", errors="ignore").replace("\n", " ").strip()
        qualified_name = f"{enclosing_fn}.{var_name}" if enclosing_fn else var_name

        nodes.append(
            ExtractedNode(
                name=var_name,
                node_type="VARIABLE",
                qualified_name=qualified_name,
                start_line=start_line,
                end_line=end_line,
                start_col=start_col,
                end_col=end_col,
                language="shell",
                signature=full_text,
                docstring="",
                metadata={
                    "enclosing": enclosing_fn,
                    "is_exported": is_exported,
                    "is_readonly": is_readonly,
                    "is_local": is_local,
                },
            )
        )

    def _handle_command(
        self,
        node: tree_sitter.Node,
        code_bytes: bytes,
        enclosing_fn: str,
        imports: List[ExtractedImport],
        calls: List[ExtractedCall],
    ) -> None:
        """Extracts script sourcing as ExtractedImport and command calls as ExtractedCall."""
        cmd_name_node = node.child_by_field_name("name")
        if not cmd_name_node:
            return

        cmd_name = code_bytes[cmd_name_node.start_byte:cmd_name_node.end_byte].decode("utf-8", errors="ignore").strip()
        line_number = node.start_point.row + 1
        raw_cmd = node.text.decode("utf-8", errors="ignore").replace("\n", " ")[:200]

        # Case A: Script sourcing -> source file.sh or . file.sh
        if cmd_name in ("source", "."):
            # The target file path is the argument following the command name
            target_path = ""
            for child in node.children:
                if child != cmd_name_node and child.type in ("word", "string", "raw_string", "concatenation"):
                    target_path = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore").strip().strip("'\"")
                    break

            if target_path:
                imports.append(
                    ExtractedImport(
                        module_path=target_path,
                        imported_symbols=["*"],
                        line_number=line_number,
                        is_relative=target_path.startswith(("./", "../")),
                        raw_statement=raw_cmd,
                    )
                )
            return

        # Case B: Standard command / function invocation
        if cmd_name.lower() in SHELL_BUILTINS:
            return

        caller = enclosing_fn if enclosing_fn else "<module>"
        calls.append(
            ExtractedCall(
                caller_name=caller,
                callee_name=cmd_name,
                line_number=line_number,
                raw_call=raw_cmd,
            )
        )

    def _extract_preceding_docstring(self, lines: List[str], fn_row: int) -> str:
        """Extracts preceding comment lines as docstring."""
        comments: List[str] = []
        idx = fn_row - 1
        while idx >= 0:
            line = lines[idx].strip()
            if line.startswith("#") and not line.startswith("#!"):
                comments.insert(0, line.lstrip("#").strip())
                idx -= 1
            else:
                break
        return "\n".join(comments) if comments else ""
