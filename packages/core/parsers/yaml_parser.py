"""PyYAML AST Parser for YAML Files (.yaml, .yml).

Extracts:
- Mappings (TYPE): Structural YAML objects, services, schemas, configurations
- Keys/Properties (VARIABLE): Scalar properties and sequences
- Sequences of Mappings: Nested items indexed deterministically (e.g. "services[0]")
- Cross-file References (ExtractedImport):
  - YAML / JSON Schema $ref references (e.g. "./common.yaml#/definitions/User")
  - Configuration extends references (e.g. "./base.yaml")
- Bounded Graph Depth: Limits structural traversal to depth <= 4 to prevent node explosion
- Multi-document Support: Handles multiple YAML documents separated by "---" safely
- Anchors and Aliases: Resolves anchors and handles cyclic / recursive aliases without infinite recursion
- Syntax Error Resilience: Catches YAMLError cleanly and records error line and message
- Declarative Guarantee: Zero artificial CALLS edges generated
"""

from typing import Any, Dict, List, Optional, Set, Tuple
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)

MAX_YAML_DEPTH = 4


class YamlParser(BaseParser):
    """PyYAML-based AST parser for YAML configuration and schema files (.yaml, .yml)."""

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        """Parses YAML source code into a standardized ParseResult."""
        if not code or not code.strip():
            return ParseResult(
                file_path=file_path,
                language="yaml",
                nodes=[],
                imports=[],
                calls=[],
                exports=[],
                re_exports=[],
                syntax_errors=[],
            )

        docs: List[Node] = []
        syntax_errors: List[str] = []

        try:
            for doc in yaml.compose_all(code):
                if doc is not None:
                    docs.append(doc)
        except yaml.YAMLError as ye:
            line = 1
            if hasattr(ye, "problem_mark") and ye.problem_mark:
                line = ye.problem_mark.line + 1
            syntax_errors.append(f"YAML syntax error at line {line}: {str(ye)}")
        except Exception as ex:
            syntax_errors.append(f"YAML parse error: {str(ex)}")

        nodes: List[ExtractedNode] = []
        imports: List[ExtractedImport] = []
        visited_nodes: Set[int] = set()

        for idx, doc in enumerate(docs):
            doc_prefix = f"doc[{idx}]" if len(docs) > 1 else ""
            self._traverse_node(
                node=doc,
                current_path=doc_prefix,
                enclosing="",
                depth=1,
                nodes=nodes,
                imports=imports,
                visited_nodes=visited_nodes,
            )

        # Deterministic sorting
        nodes.sort(key=lambda n: (n.start_line, n.qualified_name))
        imports.sort(key=lambda i: (i.line_number, i.module_path))

        return ParseResult(
            file_path=file_path,
            language="yaml",
            nodes=nodes,
            imports=imports,
            calls=[],  # YAML is declarative; zero artificial calls
            exports=[n.name for n in nodes if n.node_type == "TYPE"],
            re_exports=[],
            syntax_errors=syntax_errors,
        )

    def _traverse_node(
        self,
        node: Node,
        current_path: str,
        enclosing: str,
        depth: int,
        nodes: List[ExtractedNode],
        imports: List[ExtractedImport],
        visited_nodes: Set[int],
    ) -> None:
        """Traverses YAML nodes extracting mappings, keys, and values while bounding depth."""
        if depth > MAX_YAML_DEPTH:
            return

        node_id = id(node)
        if node_id in visited_nodes:
            return
        visited_nodes.add(node_id)

        if isinstance(node, MappingNode):
            for key_node, val_node in node.value:
                key_name = str(key_node.value) if isinstance(key_node, ScalarNode) else ""
                if not key_name:
                    continue

                # YAML Merge key (<<: *anchor)
                if key_name == "<<":
                    self._traverse_node(
                        node=val_node,
                        current_path=current_path,
                        enclosing=enclosing,
                        depth=depth,
                        nodes=nodes,
                        imports=imports,
                        visited_nodes=visited_nodes,
                    )
                    continue

                qname = f"{current_path}.{key_name}" if current_path else key_name
                start_line = key_node.start_mark.line + 1
                end_line = max(val_node.end_mark.line + 1, start_line)

                # Check for cross-file imports ($ref, extends)
                if key_name == "$ref" and isinstance(val_node, ScalarNode):
                    self._handle_ref_import(str(val_node.value).strip(), start_line, imports)

                elif key_name == "extends":
                    if isinstance(val_node, ScalarNode):
                        self._handle_extends_import(str(val_node.value).strip(), start_line, imports)
                    elif isinstance(val_node, SequenceNode):
                        for item in val_node.value:
                            if isinstance(item, ScalarNode):
                                self._handle_extends_import(
                                    str(item.value).strip(),
                                    item.start_mark.line + 1,
                                    imports,
                                )

                # Map to TYPE if mapping, VARIABLE if scalar/sequence
                if isinstance(val_node, MappingNode):
                    node_type = "TYPE"
                    yaml_type = "mapping"
                elif isinstance(val_node, SequenceNode):
                    node_type = "VARIABLE"
                    yaml_type = "sequence"
                else:
                    node_type = "VARIABLE"
                    yaml_type = "scalar"

                nodes.append(
                    ExtractedNode(
                        name=key_name,
                        node_type=node_type,
                        qualified_name=qname,
                        start_line=start_line,
                        end_line=end_line,
                        start_col=key_node.start_mark.column,
                        end_col=val_node.end_mark.column,
                        language="yaml",
                        signature=f'"{key_name}": {yaml_type}',
                        docstring="",
                        metadata={
                            "yaml_type": yaml_type,
                            "enclosing": current_path,
                            "depth": depth,
                        },
                    )
                )

                if isinstance(val_node, (MappingNode, SequenceNode)):
                    self._traverse_node(
                        node=val_node,
                        current_path=qname,
                        enclosing=current_path,
                        depth=depth + 1,
                        nodes=nodes,
                        imports=imports,
                        visited_nodes=visited_nodes,
                    )

        elif isinstance(node, SequenceNode):
            elem_idx = 0
            for elem in node.value:
                if isinstance(elem, (MappingNode, SequenceNode)):
                    elem_path = f"{current_path}[{elem_idx}]" if current_path else f"[{elem_idx}]"
                    self._traverse_node(
                        node=elem,
                        current_path=elem_path,
                        enclosing=current_path,
                        depth=depth + 1,
                        nodes=nodes,
                        imports=imports,
                        visited_nodes=visited_nodes,
                    )
                    elem_idx += 1

    def _handle_ref_import(self, ref_val: str, line_num: int, imports: List[ExtractedImport]) -> None:
        """Extracts ExtractedImport for local $ref paths."""
        if not ref_val or ref_val.startswith("#"):
            return
        if any(ref_val.startswith(proto) for proto in ("http://", "https://", "urn:")):
            return

        parts = ref_val.split("#", 1)
        file_part = parts[0].strip()
        fragment = parts[1].strip() if len(parts) > 1 else ""

        if not file_part:
            return

        if fragment:
            frag_clean = fragment.strip("/").replace("/", ".")
            leaf = frag_clean.split(".")[-1]
            imported_symbols = [frag_clean, leaf] if frag_clean != leaf else [leaf]
        else:
            imported_symbols = ["*"]

        imports.append(
            ExtractedImport(
                module_path=file_part,
                imported_symbols=imported_symbols,
                line_number=line_num,
                raw_statement=f'$ref: "{ref_val}"',
            )
        )

    def _handle_extends_import(self, ext_val: str, line_num: int, imports: List[ExtractedImport]) -> None:
        """Extracts ExtractedImport for local configuration extends."""
        if not ext_val or any(ext_val.startswith(proto) for proto in ("http://", "https://", "urn:")):
            return

        imports.append(
            ExtractedImport(
                module_path=ext_val,
                imported_symbols=["*"],
                line_number=line_num,
                raw_statement=f'extends: "{ext_val}"',
            )
        )
