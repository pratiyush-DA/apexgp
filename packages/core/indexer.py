"""ApexGraph Indexing Pipeline

Traverses repository files, parses supported ASTs, resolves cross-file
symbol calls and imports, and constructs the persistent property graph.
"""

import os
import re
import json
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
        self.tsconfig_base_url = "."
        self.tsconfig_paths: Dict[str, List[str]] = {}

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

        self.tsconfig_base_url, self.tsconfig_paths = self._load_tsconfig(repo_root)

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

            # B. IMPORTS edges (including re-exports)
            file_imported_symbols: Dict[str, Dict[str, GraphNodeModel]] = {}
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
                                tgt_node = nodes_by_id[target_sym_id]
                                if rel_path not in file_imported_symbols:
                                    file_imported_symbols[rel_path] = {}
                                file_imported_symbols[rel_path][sym] = tgt_node
                                if imp.alias:
                                    file_imported_symbols[rel_path][imp.alias] = tgt_node
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
                                # Check if target_file_path re-exports sym (barrel re-export)
                                resolved_origin = self._resolve_symbol_origin(
                                    current_file=target_file_path,
                                    symbol_name=sym,
                                    all_parse_results=all_parse_results,
                                    nodes_by_id=nodes_by_id,
                                    repository_id=repository_id,
                                )
                                if resolved_origin:
                                    origin_file, origin_sym = resolved_origin
                                    origin_res = all_parse_results.get(origin_file)
                                    origin_prefix = (
                                        "py"
                                        if origin_res and origin_res.language == "python"
                                        else ("ts" if origin_res and origin_res.language == "typescript" else "js")
                                    )
                                    origin_node_id = f"{repository_id}:{origin_prefix}:{origin_file}:{origin_sym}"
                                    if origin_node_id in nodes_by_id:
                                        tgt_node = nodes_by_id[origin_node_id]
                                        if rel_path not in file_imported_symbols:
                                            file_imported_symbols[rel_path] = {}
                                        file_imported_symbols[rel_path][sym] = tgt_node
                                        if imp.alias:
                                            file_imported_symbols[rel_path][imp.alias] = tgt_node
                                        add_edge(
                                            source_id=file_id,
                                            target_id=origin_node_id,
                                            edge_type="REFERENCES",
                                            file_path=rel_path,
                                            line_no=imp.line_number,
                                            confidence=1.0,
                                            evidence=f"import {sym} from {imp.module_path} (re-exported from {origin_file})",
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

                # Re-exports: export ... from "..."
                for re_exp in parse_res.re_exports:
                    target_file_path = self._resolve_import_path(
                        base_file=rel_path,
                        import_path=re_exp.module_path,
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
                            line_no=re_exp.line_number,
                            confidence=1.0,
                            evidence=re_exp.raw_statement,
                        )
                        # Link re-exported specific symbols
                        target_parse_res = all_parse_results.get(target_file_path)
                        target_runtime_prefix = (
                            "py"
                            if target_parse_res and target_parse_res.language == "python"
                            else ("ts" if target_parse_res and target_parse_res.language == "typescript" else "js")
                        )
                        for exp_alias, src_name in re_exp.exported_symbols.items():
                            target_sym_id = f"{repository_id}:{target_runtime_prefix}:{target_file_path}:{src_name}"
                            if target_sym_id in nodes_by_id:
                                add_edge(
                                    source_id=file_id,
                                    target_id=target_sym_id,
                                    edge_type="REFERENCES",
                                    file_path=rel_path,
                                    line_no=re_exp.line_number,
                                    confidence=1.0,
                                    evidence=f"re-export {exp_alias} from {re_exp.module_path}",
                                )
                    else:
                        external_id = f"ext:{runtime_prefix}:{re_exp.module_path}"
                        add_edge(
                            source_id=file_id,
                            target_id=external_id,
                            edge_type="IMPORTS",
                            file_path=rel_path,
                            line_no=re_exp.line_number,
                            confidence=1.0,
                            evidence=re_exp.raw_statement,
                        )

            # Precompute reachable import closure for each file (including re-export barrels)
            reachable_imports: Dict[str, Set[str]] = {}
            known_all_files = set(all_parse_results.keys())
            for r_path, p_res in all_parse_results.items():
                visited_f: Set[str] = set()
                queue: List[str] = [r_path]
                while queue:
                    curr_f = queue.pop(0)
                    curr_res = all_parse_results.get(curr_f)
                    if not curr_res:
                        continue
                    deps_to_check = [i.module_path for i in curr_res.imports] + [r.module_path for r in curr_res.re_exports]
                    for mod_p in deps_to_check:
                        t_path = self._resolve_import_path(
                            base_file=curr_f,
                            import_path=mod_p,
                            known_files=known_all_files,
                            base_language=curr_res.language,
                        )
                        if t_path and t_path not in visited_f and t_path != r_path:
                            visited_f.add(t_path)
                            queue.append(t_path)
                reachable_imports[r_path] = visited_f

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

                    # Look for matching callee in symbol table or imported aliases
                    target_candidate = None
                    imported_syms = file_imported_symbols.get(rel_path, {})
                    if call.callee_name in imported_syms:
                        target_candidate = imported_syms[call.callee_name]
                    else:
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

                        # Prioritize candidate from same file or imported file (direct or via barrel)
                        for cand in callee_candidates:
                            if cand.file_path == rel_path:
                                target_candidate = cand
                                break

                        if not target_candidate:
                            file_reachable = reachable_imports.get(rel_path, set())
                            for cand in callee_candidates:
                                if cand.file_path in file_reachable:
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

            # D. Decorator REFERENCES edges
            for rel_path, parse_res in all_parse_results.items():
                if parse_res.language != "python":
                    continue
                runtime_prefix = "py"
                file_reachable = reachable_imports.get(rel_path, set())
                for ex_node in parse_res.nodes:
                    dec_names = ex_node.metadata.get("decorator_names", [])
                    if not dec_names:
                        continue
                    source_node_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{ex_node.qualified_name}"
                    for d_name in dec_names:
                        simple_name = d_name.split(".")[-1]
                        candidates = symbol_table.get(simple_name, [])
                        matching_cand = None
                        for c in candidates:
                            if c.language == "python" and (
                                c.file_path == rel_path or c.file_path in file_reachable
                            ):
                                matching_cand = c
                                break
                        if not matching_cand and len(candidates) == 1 and candidates[0].language == "python":
                            matching_cand = candidates[0]

                        if matching_cand:
                            add_edge(
                                source_id=source_node_id,
                                target_id=matching_cand.id,
                                edge_type="REFERENCES",
                                file_path=rel_path,
                                line_no=ex_node.start_line,
                                confidence=1.0,
                                evidence=f"Decorated by @{d_name}",
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

    def _load_tsconfig(self, repo_root: str) -> Tuple[str, Dict[str, List[str]]]:
        """Loads and parses tsconfig.json compilerOptions baseUrl and paths mappings."""
        tsconfig_path = os.path.join(repo_root, "tsconfig.json")
        if not os.path.exists(tsconfig_path) or not os.path.isfile(tsconfig_path):
            return ".", {}

        try:
            with open(tsconfig_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_content = f.read()

            # Remove single-line // comments and multi-line /* */ comments
            cleaned = re.sub(r"//.*$", "", raw_content, flags=re.MULTILINE)
            cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
            # Remove trailing commas before } or ]
            cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

            data = json.loads(cleaned)
            compiler_opts = data.get("compilerOptions", {})
            base_url = compiler_opts.get("baseUrl", ".")
            paths = compiler_opts.get("paths", {})

            normalized_paths: Dict[str, List[str]] = {}
            for k, v in paths.items():
                if isinstance(v, list):
                    normalized_paths[k] = [str(item) for item in v]
                elif isinstance(v, str):
                    normalized_paths[k] = [v]

            # Sort path patterns according to TypeScript's resolution precedence:
            # 1. Exact matches (no '*') take precedence over wildcard patterns ('*').
            # 2. For patterns with '*', longer prefix (characters before '*') takes precedence.
            # 3. For equal prefix length, longer suffix (characters after '*') takes precedence.
            def _pattern_precedence_key(pattern: str):
                if "*" not in pattern:
                    return (0, -len(pattern), 0)
                prefix, *rest = pattern.split("*", 1)
                suffix = rest[0] if rest else ""
                return (1, -len(prefix), -len(suffix))

            sorted_paths = dict(
                sorted(normalized_paths.items(), key=lambda item: _pattern_precedence_key(item[0]))
            )
            return base_url, sorted_paths
        except Exception as e:
            logger.warning(f"Error reading tsconfig.json: {e}")
            return ".", {}

    def _resolve_symbol_origin(
        self,
        current_file: str,
        symbol_name: str,
        all_parse_results: Dict[str, ParseResult],
        nodes_by_id: Dict[str, GraphNodeModel],
        repository_id: str,
        visited_files: Optional[Set[str]] = None,
    ) -> Optional[Tuple[str, str]]:
        """Follows re-export chains to locate the defining file and symbol name."""
        if visited_files is None:
            visited_files = set()

        if current_file in visited_files:
            return None  # Cycle prevention
        visited_files.add(current_file)

        parse_res = all_parse_results.get(current_file)
        if not parse_res:
            return None

        runtime_prefix = "py" if parse_res.language == "python" else ("ts" if parse_res.language == "typescript" else "js")
        direct_node_id = f"{repository_id}:{runtime_prefix}:{current_file}:{symbol_name}"
        if direct_node_id in nodes_by_id:
            return (current_file, symbol_name)

        # Check re-exports in current_file
        known_files = set(all_parse_results.keys())
        for re_exp in parse_res.re_exports:
            target_path = self._resolve_import_path(
                base_file=current_file,
                import_path=re_exp.module_path,
                known_files=known_files,
                base_language=parse_res.language,
            )
            if not target_path or target_path not in all_parse_results:
                continue

            # Case A: Named re-export
            if symbol_name in re_exp.exported_symbols:
                src_sym = re_exp.exported_symbols[symbol_name]
                found = self._resolve_symbol_origin(
                    current_file=target_path,
                    symbol_name=src_sym,
                    all_parse_results=all_parse_results,
                    nodes_by_id=nodes_by_id,
                    repository_id=repository_id,
                    visited_files=visited_files,
                )
                if found:
                    return found

            # Case B: Wildcard re-export
            if re_exp.is_wildcard:
                found = self._resolve_symbol_origin(
                    current_file=target_path,
                    symbol_name=symbol_name,
                    all_parse_results=all_parse_results,
                    nodes_by_id=nodes_by_id,
                    repository_id=repository_id,
                    visited_files=visited_files,
                )
                if found:
                    return found

        return None

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

        # Check TypeScript path aliases (baseUrl and paths from tsconfig.json)
        # ONLY apply to TypeScript/JavaScript files.
        if not is_python:
            if self.tsconfig_paths:
                for pattern, target_list in self.tsconfig_paths.items():
                    matched = False
                    suffix = ""
                    if "*" in pattern:
                        prefix, *rest = pattern.split("*", 1)
                        rest_suffix = rest[0] if rest else ""
                        if import_path.startswith(prefix) and import_path.endswith(rest_suffix):
                            if len(import_path) >= len(prefix) + len(rest_suffix):
                                matched = True
                                end_idx = len(import_path) - len(rest_suffix) if rest_suffix else len(import_path)
                                suffix = import_path[len(prefix):end_idx]
                    elif import_path == pattern:
                        matched = True
                        suffix = ""

                    if matched:
                        for target_pattern in target_list:
                            if "*" in target_pattern:
                                target_base = target_pattern.replace("*", suffix, 1)
                            else:
                                target_base = target_pattern

                            # Combine with base_url
                            resolved_candidate = os.path.normpath(
                                os.path.join(self.tsconfig_base_url, target_base)
                            ).replace("\\", "/")

                            alias_candidates = [
                                resolved_candidate,
                                f"{resolved_candidate}.ts",
                                f"{resolved_candidate}.tsx",
                                f"{resolved_candidate}.js",
                                f"{resolved_candidate}.jsx",
                                f"{resolved_candidate}/index.ts",
                                f"{resolved_candidate}/index.tsx",
                                f"{resolved_candidate}/index.js",
                                f"{resolved_candidate}/index.jsx",
                            ]
                            for cand in alias_candidates:
                                if cand in known_files:
                                    return cand

            # If baseUrl is specified and not '.', also try resolving relative to baseUrl
            if self.tsconfig_base_url and self.tsconfig_base_url != ".":
                base_candidate = os.path.normpath(
                    os.path.join(self.tsconfig_base_url, import_path)
                ).replace("\\", "/")
                alias_candidates = [
                    base_candidate,
                    f"{base_candidate}.ts",
                    f"{base_candidate}.tsx",
                    f"{base_candidate}.js",
                    f"{base_candidate}.jsx",
                    f"{base_candidate}/index.ts",
                    f"{base_candidate}/index.tsx",
                    f"{base_candidate}/index.js",
                    f"{base_candidate}/index.jsx",
                ]
                for cand in alias_candidates:
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
