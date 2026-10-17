"""ApexGraph Indexing Pipeline

Traverses repository files, parses supported ASTs, resolves cross-file
symbol calls and imports, and constructs the persistent property graph.
"""

import os
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session
from packages.core.models import (
    GraphEdgeModel,
    GraphNodeModel,
    RepositoryModel,
)
from packages.core.parsers import is_supported_file, parse_source_file
from packages.core.parsers.base import ExtractedNode, ParseResult

logger = logging.getLogger("apexgraph.indexer")

IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".idea",
    ".vscode",
}

MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_BYTES", 2 * 1024 * 1024))  # 2MB

LANGUAGE_FAMILIES: Dict[str, str] = {
    "python": "python",
    "typescript": "js_ts",
    "javascript": "js_ts",
}


def are_languages_call_compatible(lang_a: str, lang_b: str) -> bool:
    """Checks if two languages belong to the same execution runtime family for in-process AST calls."""
    fam_a = LANGUAGE_FAMILIES.get(lang_a, lang_a)
    fam_b = LANGUAGE_FAMILIES.get(lang_b, lang_b)
    return fam_a == fam_b


class RepositoryIndexer:
    def __init__(self, db_session: Session):
        self.db = db_session

    def index_repository(self, repository_id: str) -> RepositoryModel:
        repo = self.db.query(RepositoryModel).filter_by(id=repository_id).first()
        if not repo:
            raise ValueError(f"Repository {repository_id} not found")

        repo_root = os.path.realpath(repo.source_path)
        if not os.path.exists(repo_root) or not os.path.isdir(repo_root):
            repo.indexing_status = "failed"
            repo.error_message = f"Repository path '{repo.source_path}' does not exist or is not a directory."
            self.db.commit()
            return repo

        repo.indexing_status = "indexing"
        repo.error_message = None
        self.db.commit()

        start_time = time.time()
        try:
            # 1. Clear existing nodes and edges for clean index
            self.db.query(GraphEdgeModel).filter_by(repository_id=repository_id).delete()
            self.db.query(GraphNodeModel).filter_by(repository_id=repository_id).delete()
            self.db.commit()

            # 2. Collect and parse files
            all_parse_results: Dict[str, ParseResult] = {}
            line_counts: Dict[str, int] = {}
            file_sizes: Dict[str, int] = {}
            language_counts: Dict[str, int] = {}
            syntax_warnings: List[str] = []

            for root, dirs, files in os.walk(repo_root):
                # Prune ignored directories in-place
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

                for fname in files:
                    full_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(full_path, repo_root).replace("\\", "/")

                    if not is_supported_file(rel_path):
                        continue

                    try:
                        sz = os.path.getsize(full_path)
                        if sz > MAX_FILE_SIZE:
                            syntax_warnings.append(f"File {rel_path} exceeds size limit ({sz} bytes). Skipped.")
                            continue

                        file_sizes[rel_path] = sz
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()

                        lines = content.splitlines()
                        line_counts[rel_path] = len(lines)

                        parse_res = parse_source_file(rel_path, content)
                        if parse_res:
                            all_parse_results[rel_path] = parse_res
                            language_counts[parse_res.language] = language_counts.get(parse_res.language, 0) + 1
                            if parse_res.syntax_errors:
                                syntax_warnings.extend(
                                    f"{rel_path}: {err}" for err in parse_res.syntax_errors
                                )
                    except Exception as fe:
                        logger.warning(f"Error parsing {rel_path}: {fe}")
                        syntax_warnings.append(f"{rel_path}: Read error ({str(fe)})")

            # 3. Create Graph Nodes
            nodes_to_insert: List[GraphNodeModel] = []
            symbol_table: Dict[str, List[GraphNodeModel]] = {}  # name -> [nodes]
            nodes_by_id: Dict[str, GraphNodeModel] = {}
            file_nodes_by_path: Dict[str, GraphNodeModel] = {}

            # A. Create FILE nodes
            for rel_path, parse_res in all_parse_results.items():
                file_id = f"{repository_id}:file:{rel_path}"
                file_node = GraphNodeModel(
                    id=file_id,
                    repository_id=repository_id,
                    node_type="FILE",
                    name=os.path.basename(rel_path),
                    qualified_name=rel_path,
                    file_path=rel_path,
                    start_line=1,
                    end_line=max(line_counts.get(rel_path, 1), 1),
                    start_col=0,
                    end_col=0,
                    language=parse_res.language,
                    signature=f"file {os.path.basename(rel_path)}",
                    docstring="",
                    node_metadata={"size_bytes": file_sizes.get(rel_path, 0), "lines": line_counts.get(rel_path, 0)},
                )
                nodes_to_insert.append(file_node)
                nodes_by_id[file_id] = file_node
                file_nodes_by_path[rel_path] = file_node

                # B. Create symbol nodes inside file
                runtime_prefix = "py" if parse_res.language == "python" else ("ts" if parse_res.language == "typescript" else "js")
                for ex_node in parse_res.nodes:
                    node_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{ex_node.qualified_name}"
                    db_node = GraphNodeModel(
                        id=node_id,
                        repository_id=repository_id,
                        node_type=ex_node.node_type,
                        name=ex_node.name,
                        qualified_name=f"{rel_path}:{ex_node.qualified_name}",
                        file_path=rel_path,
                        start_line=ex_node.start_line,
                        end_line=ex_node.end_line,
                        start_col=ex_node.start_col,
                        end_col=ex_node.end_col,
                        language=ex_node.language,
                        signature=ex_node.signature,
                        docstring=ex_node.docstring,
                        node_metadata=ex_node.metadata,
                    )
                    nodes_to_insert.append(db_node)
                    nodes_by_id[node_id] = db_node

                    if ex_node.name not in symbol_table:
                        symbol_table[ex_node.name] = []
                    symbol_table[ex_node.name].append(db_node)

            self.db.bulk_save_objects(nodes_to_insert)
            self.db.commit()

            # 4. Construct Graph Edges
            edges_to_insert: List[GraphEdgeModel] = []
            edge_keys_seen: Set[str] = set()

            def add_edge(
                source_id: str,
                target_id: str,
                edge_type: str,
                file_path: str,
                line_no: int,
                confidence: float,
                evidence: str,
            ):
                # Ensure no dangling internal node edges
                if not target_id.startswith("ext:") and target_id not in nodes_by_id:
                    logger.warning(
                        f"Skipping edge with missing target internal node: {target_id}"
                    )
                    return

                edge_key = f"{source_id}:{edge_type}:{target_id}"
                if edge_key not in edge_keys_seen:
                    edge_keys_seen.add(edge_key)
                    edges_to_insert.append(
                        GraphEdgeModel(
                            id=edge_key,
                            repository_id=repository_id,
                            source_id=source_id,
                            target_id=target_id,
                            edge_type=edge_type,
                            confidence=confidence,
                            file_path=file_path,
                            line_number=line_no,
                            evidence=evidence[:500],
                        )
                    )

            # A. DEFINES and CONTAINS edges
            for rel_path, parse_res in all_parse_results.items():
                file_id = f"{repository_id}:file:{rel_path}"
                runtime_prefix = "py" if parse_res.language == "python" else ("ts" if parse_res.language == "typescript" else "js")
                for ex_node in parse_res.nodes:
                    node_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{ex_node.qualified_name}"
                    # File defines symbol
                    add_edge(
                        source_id=file_id,
                        target_id=node_id,
                        edge_type="DEFINES",
                        file_path=rel_path,
                        line_no=ex_node.start_line,
                        confidence=1.0,
                        evidence=f"{file_node.name} defines {ex_node.name}",
                    )

                    # Class contains method
                    enclosing = ex_node.metadata.get("enclosing")
                    if enclosing:
                        parent_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{enclosing}"
                        if parent_id in nodes_by_id:
                            add_edge(
                                source_id=parent_id,
                                target_id=node_id,
                                edge_type="CONTAINS",
                                file_path=rel_path,
                                line_no=ex_node.start_line,
                                confidence=1.0,
                                evidence=f"{enclosing} contains {ex_node.name}",
                            )

            # B. IMPORTS edges
            for rel_path, parse_res in all_parse_results.items():
                file_id = f"{repository_id}:file:{rel_path}"
                runtime_prefix = "py" if parse_res.language == "python" else ("ts" if parse_res.language == "typescript" else "js")

                for imp in parse_res.imports:
                    target_file_path = self._resolve_import_path(
                        base_file=rel_path,
                        import_path=imp.module_path,
                        known_files=set(all_parse_results.keys()),
                        base_language=parse_res.language,
                    )

                    if target_file_path:
                        target_file_id = f"{repository_id}:file:{target_file_path}"
                        add_edge(
                            source_id=file_id,
                            target_id=target_file_id,
                            edge_type="IMPORTS",
                            file_path=rel_path,
                            line_no=imp.line_number,
                            confidence=1.0,
                            evidence=imp.raw_statement,
                        )

                        # Link imported specific symbols
                        target_parse_res = all_parse_results.get(target_file_path)
                        target_runtime_prefix = (
                            "py"
                            if target_parse_res and target_parse_res.language == "python"
                            else ("ts" if target_parse_res and target_parse_res.language == "typescript" else "js")
                        )
                        for sym in imp.imported_symbols:
                            if sym == "*":
                                continue
                            target_sym_id = f"{repository_id}:{target_runtime_prefix}:{target_file_path}:{sym}"
                            if target_sym_id in nodes_by_id:
                                add_edge(
                                    source_id=file_id,
                                    target_id=target_sym_id,
                                    edge_type="REFERENCES",
                                    file_path=rel_path,
                                    line_no=imp.line_number,
                                    confidence=1.0,
                                    evidence=f"import {sym} from {imp.module_path}",
                                )
                    else:
                        # External package import (e.g. npm:react or py:sys)
                        external_id = f"ext:{runtime_prefix}:{imp.module_path}"
                        add_edge(
                            source_id=file_id,
                            target_id=external_id,
                            edge_type="IMPORTS",
                            file_path=rel_path,
                            line_no=imp.line_number,
                            confidence=1.0,
                            evidence=imp.raw_statement,
                        )

            # C. CALLS and TESTS edges
            for rel_path, parse_res in all_parse_results.items():
                runtime_prefix = "py" if parse_res.language == "python" else ("ts" if parse_res.language == "typescript" else "js")
                for call in parse_res.calls:
                    caller_id = (
                        f"{repository_id}:{runtime_prefix}:{rel_path}:{call.caller_name}"
                        if call.caller_name != "<module>"
                        else f"{repository_id}:file:{rel_path}"
                    )
                    if caller_id not in nodes_by_id:
                        caller_id = f"{repository_id}:file:{rel_path}"

                    # Look for matching callee in symbol table
                    raw_candidates = symbol_table.get(call.callee_name, [])
                    if not raw_candidates:
                        continue

                    # Filter candidates to only those compatible with caller's language family
                    callee_candidates = [
                        cand
                        for cand in raw_candidates
                        if are_languages_call_compatible(parse_res.language, cand.language)
                    ]
                    if not callee_candidates:
                        continue

                    # Prioritize candidate from same file or imported file
                    target_candidate = None
                    for cand in callee_candidates:
                        if cand.file_path == rel_path:
                            target_candidate = cand
                            break

                    if not target_candidate:
                        # Check candidates in imported modules
                        for cand in callee_candidates:
                            edge_check = f"{repository_id}:file:{rel_path}:IMPORTS:{repository_id}:file:{cand.file_path}"
                            if edge_check in edge_keys_seen:
                                target_candidate = cand
                                break

                    # Fallback to single candidate match (heuristic)
                    if not target_candidate and len(callee_candidates) == 1:
                        target_candidate = callee_candidates[0]

                    if target_candidate:
                        conf = 1.0 if target_candidate.file_path == rel_path else 0.85
                        add_edge(
                            source_id=caller_id,
                            target_id=target_candidate.id,
                            edge_type="CALLS",
                            file_path=rel_path,
                            line_no=call.line_number,
                            confidence=conf,
                            evidence=call.raw_call,
                        )

                        # If caller is a TEST node, create explicit TESTS relationship
                        caller_node = nodes_by_id.get(caller_id)
                        if caller_node and caller_node.node_type == "TEST":
                            add_edge(
                                source_id=caller_id,
                                target_id=target_candidate.id,
                                edge_type="TESTS",
                                file_path=rel_path,
                                line_no=call.line_number,
                                confidence=0.95,
                                evidence=f"Test invokes {target_candidate.name}",
                            )

            self.db.bulk_save_objects(edges_to_insert)
            self.db.commit()

            # 5. Compile Statistics
            duration = time.time() - start_time
            node_type_counts: Dict[str, int] = {}
            for n in nodes_to_insert:
                node_type_counts[n.node_type] = node_type_counts.get(n.node_type, 0) + 1

            stats = {
                "total_files": len(all_parse_results),
                "total_lines": sum(line_counts.values()),
                "languages": language_counts,
                "node_types": node_type_counts,
                "total_nodes": len(nodes_to_insert),
                "total_edges": len(edges_to_insert),
                "duration_seconds": round(duration, 3),
                "warnings": syntax_warnings[:20],
            }

            repo.indexing_status = "ready"
            repo.last_indexed_at = datetime.utcnow()
            repo.stats = stats
            repo.error_message = None
            self.db.commit()

            logger.info(
                f"Successfully indexed {repo.name}: {len(nodes_to_insert)} nodes, "
                f"{len(edges_to_insert)} edges in {duration:.2f}s"
            )
            return repo

        except Exception as e:
            self.db.rollback()
            repo.indexing_status = "failed"
            repo.error_message = str(e)
            self.db.commit()
            logger.error(f"Failed indexing repository {repository_id}: {e}", exc_info=True)
            raise

    def _resolve_import_path(
        self,
        base_file: str,
        import_path: str,
        known_files: Set[str],
        base_language: Optional[str] = None,
    ) -> Optional[str]:
        """Resolves relative and package imports to actual files in the repository."""
        base_dir = os.path.dirname(base_file)

        # Detect language family if not provided
        if not base_language:
            ext = os.path.splitext(base_file)[1].lower()
            if ext == ".py":
                base_language = "python"
            elif ext in (".ts", ".tsx"):
                base_language = "typescript"
            elif ext in (".js", ".jsx", ".mjs", ".cjs"):
                base_language = "javascript"

        is_python = base_language == "python"

        # Handle relative imports: ./util, ../math
        if import_path.startswith("."):
            joined = os.path.normpath(os.path.join(base_dir, import_path)).replace("\\", "/")
            if is_python:
                candidates = [
                    joined,
                    f"{joined}.py",
                    f"{joined}/__init__.py",
                ]
            else:
                candidates = [
                    joined,
                    f"{joined}.ts",
                    f"{joined}.tsx",
                    f"{joined}.js",
                    f"{joined}.jsx",
                    f"{joined}/index.ts",
                    f"{joined}/index.tsx",
                    f"{joined}/index.js",
                    f"{joined}/index.jsx",
                ]
            for cand in candidates:
                if cand in known_files:
                    return cand

        # Handle Python module dot notation (e.g. core.math -> core/math.py)
        # Strictly limited to Python files so TS/JS non-relative imports (e.g. express)
        # do not match unrelated Python files or directories.
        if is_python:
            dot_as_path = import_path.replace(".", "/")
            candidates = [
                f"{dot_as_path}.py",
                f"{dot_as_path}/__init__.py",
            ]
            if base_dir and base_dir != ".":
                joined_rel = os.path.normpath(os.path.join(base_dir, dot_as_path)).replace("\\", "/")
                candidates.extend([
                    f"{joined_rel}.py",
                    f"{joined_rel}/__init__.py",
                ])
            for cand in candidates:
                if cand in known_files:
                    return cand

        return None
