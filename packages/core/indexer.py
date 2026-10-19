"""ApexGraph Indexing Pipeline

Traverses repository files, parses supported ASTs, resolves cross-file
symbol calls and imports, and constructs the persistent property graph.
"""

import os
import re
import json
import time
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from packages.core.models import (
    GraphEdgeModel,
    GraphNodeModel,
    RepositoryModel,
)
from packages.core.parsers import (
    are_languages_call_compatible,
    default_registry,
    get_runtime_prefix,
    is_supported_file,
    parse_source_file,
)
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

LANGUAGE_FAMILIES: Dict[str, str] = default_registry.get_language_families()


class RepositoryIndexer:
    def __init__(self, db_session: Session):
        self.db = db_session
        self.tsconfig_base_url = "."
        self.tsconfig_paths: Dict[str, List[str]] = {}
        self.go_module_name: Optional[str] = None

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
        self.go_module_name = self._load_go_mod(repo_root)

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
            file_mtimes: Dict[str, float] = {}
            file_hashes: Dict[str, str] = {}
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
                        file_mtimes[rel_path] = os.path.getmtime(full_path)
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()

                        file_hashes[rel_path] = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
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
                    node_metadata={
                        "size_bytes": file_sizes.get(rel_path, 0),
                        "lines": line_counts.get(rel_path, 0),
                        "content_hash": file_hashes.get(rel_path, ""),
                        "mtime": file_mtimes.get(rel_path, 0.0),
                    },
                )
                nodes_to_insert.append(file_node)
                nodes_by_id[file_id] = file_node
                file_nodes_by_path[rel_path] = file_node

                # B. Create symbol nodes inside file
                runtime_prefix = get_runtime_prefix(parse_res.language)
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
                runtime_prefix = get_runtime_prefix(parse_res.language)
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
                        elif default_registry.is_package_scoped(parse_res.language):
                            # In package-scoped languages, struct may be in another file in the same package directory
                            rel_dir = os.path.dirname(rel_path)
                            for cand in symbol_table.get(enclosing, []):
                                if default_registry.is_package_scoped(cand.language) and cand.node_type == "CLASS" and os.path.dirname(cand.file_path) == rel_dir:
                                    add_edge(
                                        source_id=cand.id,
                                        target_id=node_id,
                                        edge_type="CONTAINS",
                                        file_path=rel_path,
                                        line_no=ex_node.start_line,
                                        confidence=1.0,
                                        evidence=f"{enclosing} contains {ex_node.name}",
                                    )
                                    break

            # B. IMPORTS edges (including re-exports)
            file_imported_symbols: Dict[str, Dict[str, GraphNodeModel]] = {}
            for rel_path, parse_res in all_parse_results.items():
                file_id = f"{repository_id}:file:{rel_path}"
                runtime_prefix = get_runtime_prefix(parse_res.language)

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
                        target_runtime_prefix = get_runtime_prefix(target_parse_res.language if target_parse_res else "")

                        if default_registry.is_package_scoped(parse_res.language):
                            # In package-scoped languages, importing a package exposes its exported symbols
                            target_dir = os.path.dirname(target_file_path)
                            target_files = [
                                f for f in all_parse_results.keys()
                                if (os.path.dirname(f) == target_dir or (target_dir == "" and os.path.dirname(f) == ""))
                                and f.endswith(".go")
                                and not f.endswith("_test.go")
                            ]
                            pkg_alias = imp.alias or (os.path.basename(target_dir) if target_dir else "main")
                            if rel_path not in file_imported_symbols:
                                file_imported_symbols[rel_path] = {}
                            for t_file in target_files:
                                t_res = all_parse_results.get(t_file)
                                if not t_res:
                                    continue
                                for ex_n in t_res.nodes:
                                    if ex_n.metadata.get("exported") or (ex_n.name and ex_n.name[0].isupper()):
                                        t_sym_id = f"{repository_id}:go:{t_file}:{ex_n.qualified_name}"
                                        if t_sym_id in nodes_by_id:
                                            tgt_node = nodes_by_id[t_sym_id]
                                            file_imported_symbols[rel_path][f"{pkg_alias}.{ex_n.name}"] = tgt_node
                                            if imp.alias == ".":
                                                file_imported_symbols[rel_path][ex_n.name] = tgt_node
                                            add_edge(
                                                source_id=file_id,
                                                target_id=t_sym_id,
                                                edge_type="REFERENCES",
                                                file_path=rel_path,
                                                line_no=imp.line_number,
                                                confidence=1.0,
                                                evidence=f"import {pkg_alias}.{ex_n.name} from {imp.module_path}",
                                            )
                        else:
                            for sym in imp.imported_symbols:
                                if sym == "*":
                                    if target_parse_res:
                                        if rel_path not in file_imported_symbols:
                                            file_imported_symbols[rel_path] = {}
                                        for ex_n in target_parse_res.nodes:
                                            if ex_n.node_type in ("FUNCTION", "VARIABLE", "CLASS", "TYPE"):
                                                t_sym_id = f"{repository_id}:{target_runtime_prefix}:{target_file_path}:{ex_n.qualified_name}"
                                                if t_sym_id in nodes_by_id:
                                                    tgt_node = nodes_by_id[t_sym_id]
                                                    if ex_n.name not in file_imported_symbols[rel_path]:
                                                        file_imported_symbols[rel_path][ex_n.name] = tgt_node
                                                    add_edge(
                                                        source_id=file_id,
                                                        target_id=t_sym_id,
                                                        edge_type="REFERENCES",
                                                        file_path=rel_path,
                                                        line_no=imp.line_number,
                                                        confidence=0.8,
                                                        evidence=f"{imp.raw_statement} (wildcard import)",
                                                    )
                                    continue
                                target_sym_id = f"{repository_id}:{target_runtime_prefix}:{target_file_path}:{sym}"
                                if target_sym_id not in nodes_by_id and target_parse_res:
                                    for ex_n in target_parse_res.nodes:
                                        if ex_n.name == sym:
                                            cand_id = f"{repository_id}:{target_runtime_prefix}:{target_file_path}:{ex_n.qualified_name}"
                                            if cand_id in nodes_by_id:
                                                target_sym_id = cand_id
                                                break
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
                                        origin_prefix = get_runtime_prefix(origin_res.language if origin_res else "")
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
                runtime_prefix = get_runtime_prefix(parse_res.language)
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
                        callee_candidates = [
                            cand
                            for cand in raw_candidates
                            if are_languages_call_compatible(parse_res.language, cand.language)
                        ]

                        # Prioritize candidate from same file or imported file (direct or via barrel)
                        for cand in callee_candidates:
                            if cand.file_path == rel_path:
                                target_candidate = cand
                                break

                        # In package-scoped languages, files in the same directory share package scope
                        if not target_candidate and default_registry.is_package_scoped(parse_res.language):
                            rel_dir = os.path.dirname(rel_path)
                            for cand in callee_candidates:
                                if os.path.dirname(cand.file_path) == rel_dir:
                                    target_candidate = cand
                                    break

                        if not target_candidate:
                            file_reachable = reachable_imports.get(rel_path, set())
                            for cand in callee_candidates:
                                if cand.file_path in file_reachable:
                                    target_candidate = cand
                                    break

                        # Handle method selector and path calls like "c.AddToTotal", "calc.Add", "math::add", "Person::new"
                        if not target_candidate and ("." in call.callee_name or "::" in call.callee_name):
                            simple_name = call.callee_name.split("::")[-1].split(".")[-1]
                            method_candidates = [
                                cand
                                for cand in symbol_table.get(simple_name, [])
                                if are_languages_call_compatible(parse_res.language, cand.language)
                            ]
                            for cand in method_candidates:
                                if cand.file_path == rel_path:
                                    target_candidate = cand
                                    break
                            if not target_candidate and default_registry.is_package_scoped(parse_res.language):
                                rel_dir = os.path.dirname(rel_path)
                                for cand in method_candidates:
                                    if os.path.dirname(cand.file_path) == rel_dir or cand.file_path in reachable_imports.get(rel_path, set()):
                                        target_candidate = cand
                                        break
                            if not target_candidate:
                                file_reachable = reachable_imports.get(rel_path, set())
                                for cand in method_candidates:
                                    if cand.file_path in file_reachable:
                                        if cand.node_metadata and cand.node_metadata.get("trait"):
                                            target_candidate = cand
                                            break
                                        if not target_candidate:
                                            target_candidate = cand
                            if not target_candidate and len(method_candidates) == 1:
                                target_candidate = method_candidates[0]

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

                        if parse_res.language == "sql" and target_candidate.node_type in ("CLASS", "TYPE", "VARIABLE"):
                            add_edge(
                                source_id=caller_id,
                                target_id=target_candidate.id,
                                edge_type="REFERENCES",
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

    def _load_go_mod(self, repo_root: str) -> Optional[str]:
        """Loads and extracts the module path from go.mod if present."""
        go_mod_path = os.path.join(repo_root, "go.mod")
        if not os.path.exists(go_mod_path) or not os.path.isfile(go_mod_path):
            return None
        try:
            with open(go_mod_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("module "):
                        parts = line.split(None, 1)
                        if len(parts) > 1:
                            return parts[1].strip().strip('"\'')
        except Exception as e:
            logger.warning(f"Error reading go.mod: {e}")
        return None

    def _resolve_symbol_origin(
        self,
        current_file: str,
        symbol_name: str,
        all_parse_results: Dict[str, ParseResult],
        nodes_by_id: Optional[Dict[str, GraphNodeModel]],
        repository_id: str,
        visited_files: Optional[Set[str]] = None,
        repo_root: Optional[str] = None,
    ) -> Optional[Tuple[str, str]]:
        """Follows re-export chains to locate the defining file and symbol name."""
        if visited_files is None:
            visited_files = set()

        if current_file in visited_files:
            return None  # Cycle prevention
        visited_files.add(current_file)

        parse_res = all_parse_results.get(current_file)
        if not parse_res and repo_root:
            full_p = os.path.join(repo_root, current_file)
            if os.path.exists(full_p) and is_supported_file(current_file):
                try:
                    with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                        c = f.read()
                    parse_res = parse_source_file(current_file, c)
                    if parse_res:
                        all_parse_results[current_file] = parse_res
                except Exception:
                    parse_res = None

        if not parse_res:
            return None

        runtime_prefix = get_runtime_prefix(parse_res.language)
        direct_node_id = f"{repository_id}:{runtime_prefix}:{current_file}:{symbol_name}"
        if nodes_by_id and direct_node_id in nodes_by_id:
            return (current_file, symbol_name)
        db_sym = self.db.query(GraphNodeModel.id).filter_by(id=direct_node_id, repository_id=repository_id).first()
        if db_sym:
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
            if not target_path:
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
                    repo_root=repo_root,
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
                    repo_root=repo_root,
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
            elif ext == ".go":
                base_language = "go"
            elif ext == ".rs":
                base_language = "rust"
            elif ext == ".sql":
                base_language = "sql"
            elif ext in (".sh", ".bash", ".zsh"):
                base_language = "shell"
            elif ext == ".json":
                base_language = "json"
            elif ext in (".yaml", ".yml"):
                base_language = "yaml"

        is_python = base_language == "python"
        is_go = base_language == "go"
        is_rust = base_language == "rust"
        is_sql = base_language == "sql"
        is_shell = base_language in ("shell", "bash", "sh")
        is_json = base_language == "json"
        is_yaml = base_language in ("yaml", "yml")

        if is_yaml:
            clean_path = import_path.strip().strip("'\"")
            if "#" in clean_path:
                clean_path = clean_path.split("#")[0].strip()

            rel_candidate = os.path.normpath(os.path.join(base_dir, clean_path)).replace("\\", "/")
            if rel_candidate in known_files:
                return rel_candidate

            root_candidate = os.path.normpath(clean_path).replace("\\", "/")
            if root_candidate in known_files:
                return root_candidate

            if not any(clean_path.endswith(ext) for ext in (".yaml", ".yml", ".json")):
                for ext in (".yaml", ".yml", ".json"):
                    cand1 = f"{rel_candidate}{ext}"
                    if cand1 in known_files:
                        return cand1
                    cand2 = f"{root_candidate}{ext}"
                    if cand2 in known_files:
                        return cand2

            base_name = os.path.basename(clean_path)
            for kf in known_files:
                if any(kf.endswith(ext) for ext in (".yaml", ".yml", ".json")):
                    if os.path.basename(kf) == base_name or os.path.splitext(os.path.basename(kf))[0] == base_name:
                        return kf
            return None

        if is_json:
            clean_path = import_path.strip().strip("'\"")
            if "#" in clean_path:
                clean_path = clean_path.split("#")[0].strip()

            rel_candidate = os.path.normpath(os.path.join(base_dir, clean_path)).replace("\\", "/")
            if rel_candidate in known_files:
                return rel_candidate

            root_candidate = os.path.normpath(clean_path).replace("\\", "/")
            if root_candidate in known_files:
                return root_candidate

            if not clean_path.endswith(".json"):
                cand1 = f"{rel_candidate}.json"
                if cand1 in known_files:
                    return cand1
                cand2 = f"{root_candidate}.json"
                if cand2 in known_files:
                    return cand2

            base_name = os.path.basename(clean_path)
            for kf in known_files:
                if os.path.basename(kf) == base_name and kf.endswith(".json"):
                    return kf
            return None

        if is_shell:
            clean_path = import_path.strip().strip("'\"")
            # If path contains variable like "$DIR/lib.sh" or "${SCRIPT_DIR}/lib.sh", strip variable prefix
            if "$" in clean_path:
                cleaned_var = clean_path.split("/")[-1]
            else:
                cleaned_var = clean_path

            # 1. Direct relative candidate from base_dir
            rel_candidate = os.path.normpath(os.path.join(base_dir, clean_path)).replace("\\", "/")
            if rel_candidate in known_files:
                return rel_candidate

            # 2. Repo-root candidate
            root_candidate = os.path.normpath(clean_path).replace("\\", "/")
            if root_candidate in known_files:
                return root_candidate

            # 3. Cleaned variable candidate from base_dir
            if cleaned_var != clean_path:
                rel_var_cand = os.path.normpath(os.path.join(base_dir, cleaned_var)).replace("\\", "/")
                if rel_var_cand in known_files:
                    return rel_var_cand

            # 4. Check extension candidates if omitted
            for e in (".sh", ".bash", ".zsh"):
                cand1 = f"{rel_candidate}{e}"
                if cand1 in known_files:
                    return cand1
                cand2 = f"{root_candidate}{e}"
                if cand2 in known_files:
                    return cand2

            # 5. Basename fallback among known shell scripts
            base_name = os.path.basename(cleaned_var)
            for kf in known_files:
                if os.path.basename(kf) == base_name and any(kf.endswith(sh_ext) for sh_ext in (".sh", ".bash", ".zsh")):
                    return kf
            return None

        if is_sql:
            clean_path = import_path.strip().strip("'\"")
            if not clean_path.endswith(".sql"):
                clean_path = f"{clean_path}.sql"
            rel_candidate = os.path.normpath(os.path.join(base_dir, clean_path)).replace("\\", "/")
            if rel_candidate in known_files:
                return rel_candidate
            root_candidate = os.path.normpath(clean_path).replace("\\", "/")
            if root_candidate in known_files:
                return root_candidate
            base_name = os.path.basename(clean_path)
            for kf in known_files:
                if os.path.basename(kf) == base_name and kf.endswith(".sql"):
                    return kf
            return None

        if is_rust:
            return self._resolve_rust_import_path(base_file, import_path, known_files)

        if is_go:
            # 1. Handle relative imports: ./util, ../math
            if import_path.startswith("."):
                joined = os.path.normpath(os.path.join(base_dir, import_path)).replace("\\", "/")
                matching = [
                    f for f in known_files
                    if os.path.dirname(f) == joined and f.endswith(".go") and not f.endswith("_test.go")
                ]
                if matching:
                    return sorted(matching)[0]

            # 2. Handle module path imports via go.mod: e.g. "github.com/myorg/myrepo/pkg/math"
            target_dir = None
            if self.go_module_name:
                if import_path == self.go_module_name:
                    target_dir = "."
                elif import_path.startswith(self.go_module_name + "/"):
                    target_dir = import_path[len(self.go_module_name) + 1:].strip("/")

            # 3. Direct subdirectory match (e.g. "pkg/math") if no go.mod prefix matched
            if target_dir is None and not import_path.startswith("."):
                norm_p = os.path.normpath(import_path).replace("\\", "/")
                matching = [
                    f for f in known_files
                    if (os.path.dirname(f) == norm_p or (norm_p == "." and os.path.dirname(f) == ""))
                    and f.endswith(".go")
                    and not f.endswith("_test.go")
                ]
                if matching:
                    return sorted(matching)[0]

            if target_dir is not None:
                norm_target = os.path.normpath(target_dir).replace("\\", "/")
                matching = [
                    f for f in known_files
                    if (os.path.dirname(f) == norm_target or (norm_target == "." and os.path.dirname(f) == ""))
                    and f.endswith(".go")
                    and not f.endswith("_test.go")
                ]
                if matching:
                    pkg_base = os.path.basename(norm_target) if norm_target != "." else "main"
                    for m in matching:
                        if os.path.basename(m) == f"{pkg_base}.go":
                            return m
                    return sorted(matching)[0]

            return None

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
        if not is_python and not is_go and not is_rust and not is_sql and not is_shell and not is_json:
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

    def _resolve_rust_import_path(
        self,
        base_file: str,
        import_path: str,
        known_files: Set[str],
    ) -> Optional[str]:
        """Resolves Rust crate::, self::, super::, and module imports to file paths."""
        norm_base = base_file.replace("\\", "/")
        base_dir = os.path.dirname(norm_base)

        if norm_base.startswith("src/") or norm_base == "src":
            crate_root = "src"
        elif any(f.startswith("src/") for f in known_files):
            crate_root = "src"
        else:
            crate_root = ""

        clean_path = import_path.replace("/", "::")
        parts = [p for p in clean_path.split("::") if p]
        if not parts:
            return None

        first = parts[0]
        subparts = parts[1:]

        candidate_bases: List[str] = []

        if first == "crate":
            root = crate_root
            if not subparts:
                for cand in [f"{root}/lib.rs", f"{root}/main.rs", "lib.rs", "main.rs"]:
                    c = cand.strip("/")
                    if c in known_files:
                        return c
                return None
            subpath = "/".join(subparts)
            candidate_bases.append(f"{root}/{subpath}".strip("/"))
        elif first == "self":
            subpath = "/".join(subparts)
            candidate_bases.append(
                os.path.normpath(os.path.join(base_dir, subpath)).replace("\\", "/")
            )
        elif first == "super":
            curr = base_dir
            idx = 0
            while idx < len(parts) and parts[idx] == "super":
                curr = os.path.dirname(curr)
                idx += 1
            rem = "/".join(parts[idx:])
            candidate_bases.append(
                os.path.normpath(os.path.join(curr, rem)).replace("\\", "/")
            )
        else:
            full_sub = "/".join(parts)
            candidate_bases.append(
                os.path.normpath(os.path.join(base_dir, full_sub)).replace("\\", "/")
            )
            if crate_root:
                candidate_bases.append(f"{crate_root}/{full_sub}".strip("/"))
            base_stem = os.path.splitext(os.path.basename(norm_base))[0]
            if base_stem not in ("mod", "main", "lib"):
                candidate_bases.append(
                    os.path.normpath(os.path.join(base_dir, base_stem, full_sub)).replace("\\", "/")
                )

        for cand_base in candidate_bases:
            cand_base = cand_base.strip("/")
            for cand in [
                cand_base,
                f"{cand_base}.rs",
                f"{cand_base}/mod.rs",
            ]:
                if cand in known_files:
                    return cand

            parent_cand = os.path.dirname(cand_base)
            for cand in [f"{parent_cand}.rs", f"{parent_cand}/mod.rs"]:
                cand = cand.strip("/")
                if cand in known_files and cand != norm_base:
                    return cand

        return None

    def _purge_file_nodes_and_edges(self, repository_id: str, rel_path: str) -> Set[str]:
        """Purges all nodes and edges associated with a file, including incoming dangling edges."""
        existing_nodes = (
            self.db.query(GraphNodeModel)
            .filter(
                GraphNodeModel.repository_id == repository_id,
                GraphNodeModel.file_path == rel_path,
            )
            .all()
        )
        if not existing_nodes:
            return set()

        node_ids = {n.id for n in existing_nodes}
        node_ids_list = list(node_ids)

        # 1. Purge incoming edges targeting any of these nodes (prevents dangling edges)
        # Chunk into slices of 500 for SQLite parameter limits
        for i in range(0, len(node_ids_list), 500):
            chunk = node_ids_list[i : i + 500]
            self.db.query(GraphEdgeModel).filter(
                GraphEdgeModel.repository_id == repository_id,
                GraphEdgeModel.target_id.in_(chunk),
            ).delete(synchronize_session=False)

        # 2. Purge outgoing edges originating from this file
        self.db.query(GraphEdgeModel).filter(
            GraphEdgeModel.repository_id == repository_id,
            GraphEdgeModel.file_path == rel_path,
        ).delete(synchronize_session=False)

        # 3. Purge nodes themselves
        self.db.query(GraphNodeModel).filter(
            GraphNodeModel.repository_id == repository_id,
            GraphNodeModel.file_path == rel_path,
        ).delete(synchronize_session=False)

        return node_ids

    def _recompute_repository_stats(self, repository_id: str, repo: RepositoryModel) -> Dict[str, Any]:
        """Recomputes aggregate repository statistics directly from the database graph."""
        total_files = (
            self.db.query(func.count(GraphNodeModel.id))
            .filter(
                GraphNodeModel.repository_id == repository_id,
                GraphNodeModel.node_type == "FILE",
            )
            .scalar()
            or 0
        )

        total_nodes = (
            self.db.query(func.count(GraphNodeModel.id))
            .filter(GraphNodeModel.repository_id == repository_id)
            .scalar()
            or 0
        )

        total_edges = (
            self.db.query(func.count(GraphEdgeModel.id))
            .filter(GraphEdgeModel.repository_id == repository_id)
            .scalar()
            or 0
        )

        node_type_rows = (
            self.db.query(GraphNodeModel.node_type, func.count(GraphNodeModel.id))
            .filter(GraphNodeModel.repository_id == repository_id)
            .group_by(GraphNodeModel.node_type)
            .all()
        )
        node_types = {r[0]: r[1] for r in node_type_rows}

        lang_rows = (
            self.db.query(GraphNodeModel.language, func.count(GraphNodeModel.id))
            .filter(
                GraphNodeModel.repository_id == repository_id,
                GraphNodeModel.node_type == "FILE",
            )
            .group_by(GraphNodeModel.language)
            .all()
        )
        languages = {r[0]: r[1] for r in lang_rows}

        file_nodes = (
            self.db.query(GraphNodeModel.node_metadata)
            .filter(
                GraphNodeModel.repository_id == repository_id,
                GraphNodeModel.node_type == "FILE",
            )
            .all()
        )
        total_lines = 0
        for fn in file_nodes:
            meta = fn[0] if fn and isinstance(fn[0], dict) else {}
            total_lines += meta.get("lines", 0)

        prev_stats = repo.stats if isinstance(repo.stats, dict) else {}
        stats = {
            "total_files": total_files,
            "total_lines": total_lines,
            "languages": languages,
            "node_types": node_types,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "duration_seconds": prev_stats.get("duration_seconds", 0.0),
            "warnings": prev_stats.get("warnings", []),
        }
        repo.stats = stats
        return stats

    def _construct_edges_for_file(
        self,
        repository_id: str,
        rel_path: str,
        parse_res: ParseResult,
        known_files: Set[str],
        repo_root: Optional[str] = None,
    ) -> List[GraphEdgeModel]:
        """Constructs all outgoing edges for a single parsed file against the repository graph."""
        file_id = f"{repository_id}:file:{rel_path}"
        runtime_prefix = get_runtime_prefix(parse_res.language)

        local_nodes = (
            self.db.query(GraphNodeModel)
            .filter(
                GraphNodeModel.repository_id == repository_id,
                GraphNodeModel.file_path == rel_path,
            )
            .all()
        )
        local_nodes_by_id = {n.id: n for n in local_nodes}
        local_nodes_by_name: Dict[str, List[GraphNodeModel]] = {}
        for n in local_nodes:
            if n.node_type != "FILE":
                local_nodes_by_name.setdefault(n.name, []).append(n)

        file_node = local_nodes_by_id.get(file_id)
        file_name = file_node.name if file_node else os.path.basename(rel_path)

        edges_to_insert: List[GraphEdgeModel] = []
        edge_keys_seen: Set[str] = set()

        def add_edge(
            source_id: str,
            target_id: str,
            edge_type: str,
            line_no: int,
            confidence: float,
            evidence: str,
        ):
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
                        file_path=rel_path,
                        line_number=line_no,
                        evidence=evidence[:500],
                    )
                )

        # 1. DEFINES and CONTAINS edges
        for ex_node in parse_res.nodes:
            node_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{ex_node.qualified_name}"
            add_edge(
                source_id=file_id,
                target_id=node_id,
                edge_type="DEFINES",
                line_no=ex_node.start_line,
                confidence=1.0,
                evidence=f"{file_name} defines {ex_node.name}",
            )

            enclosing = ex_node.metadata.get("enclosing")
            if enclosing:
                parent_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{enclosing}"
                if parent_id in local_nodes_by_id:
                    add_edge(
                        source_id=parent_id,
                        target_id=node_id,
                        edge_type="CONTAINS",
                        line_no=ex_node.start_line,
                        confidence=1.0,
                        evidence=f"{enclosing} contains {ex_node.name}",
                    )
                elif default_registry.is_package_scoped(parse_res.language):
                    rel_dir = os.path.dirname(rel_path)
                    cand_structs = (
                        self.db.query(GraphNodeModel)
                        .filter(
                            GraphNodeModel.repository_id == repository_id,
                            GraphNodeModel.name == enclosing,
                            GraphNodeModel.language == parse_res.language,
                            GraphNodeModel.node_type == "CLASS",
                        )
                        .all()
                    )
                    for cand in cand_structs:
                        if os.path.dirname(cand.file_path) == rel_dir:
                            add_edge(
                                source_id=cand.id,
                                target_id=node_id,
                                edge_type="CONTAINS",
                                line_no=ex_node.start_line,
                                confidence=1.0,
                                evidence=f"{enclosing} contains {ex_node.name}",
                            )
                            break

        # 2. IMPORTS and REFERENCES edges
        file_imported_symbols: Dict[str, GraphNodeModel] = {}
        target_nodes_cache: Dict[str, Dict[str, GraphNodeModel]] = {}

        def _get_target_file_nodes(tgt_f: str) -> Dict[str, GraphNodeModel]:
            if tgt_f not in target_nodes_cache:
                t_nodes = (
                    self.db.query(GraphNodeModel)
                    .filter(
                        GraphNodeModel.repository_id == repository_id,
                        GraphNodeModel.file_path == tgt_f,
                    )
                    .all()
                )
                m = {}
                for n in t_nodes:
                    if n.node_type != "FILE":
                        m[n.name] = n
                        if ":" in n.qualified_name:
                            m[n.qualified_name.split(":", 1)[-1]] = n
                target_nodes_cache[tgt_f] = m
            return target_nodes_cache[tgt_f]

        for imp in parse_res.imports:
            target_file_path = self._resolve_import_path(
                base_file=rel_path,
                import_path=imp.module_path,
                known_files=known_files,
                base_language=parse_res.language,
            )
            if target_file_path:
                target_file_id = f"{repository_id}:file:{target_file_path}"
                add_edge(
                    source_id=file_id,
                    target_id=target_file_id,
                    edge_type="IMPORTS",
                    line_no=imp.line_number,
                    confidence=1.0,
                    evidence=imp.raw_statement,
                )

                if default_registry.is_package_scoped(parse_res.language):
                    target_dir = os.path.dirname(target_file_path)
                    target_files = [
                        f for f in known_files
                        if (os.path.dirname(f) == target_dir or (target_dir == "" and os.path.dirname(f) == ""))
                        and f.endswith(".go")
                        and not f.endswith("_test.go")
                    ]
                    pkg_alias = imp.alias or (os.path.basename(target_dir) if target_dir else "main")
                    for t_file in target_files:
                        t_syms = _get_target_file_nodes(t_file)
                        for s_name, tgt_node in t_syms.items():
                            meta = tgt_node.node_metadata if isinstance(tgt_node.node_metadata, dict) else {}
                            if meta.get("exported") or (tgt_node.name and tgt_node.name[0].isupper()):
                                file_imported_symbols[f"{pkg_alias}.{tgt_node.name}"] = tgt_node
                                if imp.alias == ".":
                                    file_imported_symbols[tgt_node.name] = tgt_node
                                add_edge(
                                    source_id=file_id,
                                    target_id=tgt_node.id,
                                    edge_type="REFERENCES",
                                    line_no=imp.line_number,
                                    confidence=1.0,
                                    evidence=f"import {pkg_alias}.{tgt_node.name} from {imp.module_path}",
                                )
                else:
                    t_syms = _get_target_file_nodes(target_file_path)
                    for sym in imp.imported_symbols:
                        if sym == "*":
                            for s_name, tgt_node in t_syms.items():
                                if s_name not in file_imported_symbols:
                                    file_imported_symbols[s_name] = tgt_node
                                add_edge(
                                    source_id=file_id,
                                    target_id=tgt_node.id,
                                    edge_type="REFERENCES",
                                    line_no=imp.line_number,
                                    confidence=0.8,
                                    evidence=f"{imp.raw_statement} (wildcard import)",
                                )
                            continue
                        if sym in t_syms:
                            tgt_node = t_syms[sym]
                            file_imported_symbols[sym] = tgt_node
                            if imp.alias:
                                file_imported_symbols[imp.alias] = tgt_node
                            add_edge(
                                source_id=file_id,
                                target_id=tgt_node.id,
                                edge_type="REFERENCES",
                                line_no=imp.line_number,
                                confidence=1.0,
                                evidence=f"import {sym} from {imp.module_path}",
                            )
                        else:
                            resolved_origin = self._resolve_symbol_origin(
                                current_file=target_file_path,
                                symbol_name=sym,
                                all_parse_results={rel_path: parse_res},
                                nodes_by_id=None,
                                repository_id=repository_id,
                                repo_root=repo_root,
                            )
                            if resolved_origin:
                                origin_file, origin_sym = resolved_origin
                                o_syms = _get_target_file_nodes(origin_file)
                                if origin_sym in o_syms:
                                    origin_node = o_syms[origin_sym]
                                    file_imported_symbols[sym] = origin_node
                                    if imp.alias:
                                        file_imported_symbols[imp.alias] = origin_node
                                    add_edge(
                                        source_id=file_id,
                                        target_id=origin_node.id,
                                        edge_type="REFERENCES",
                                        line_no=imp.line_number,
                                        confidence=1.0,
                                        evidence=f"import {sym} from {imp.module_path} (re-exported from {origin_file})",
                                    )
            else:
                external_id = f"ext:{runtime_prefix}:{imp.module_path}"
                add_edge(
                    source_id=file_id,
                    target_id=external_id,
                    edge_type="IMPORTS",
                    line_no=imp.line_number,
                    confidence=1.0,
                    evidence=imp.raw_statement,
                )

        for re_exp in parse_res.re_exports:
            target_file_path = self._resolve_import_path(
                base_file=rel_path,
                import_path=re_exp.module_path,
                known_files=known_files,
                base_language=parse_res.language,
            )
            if target_file_path:
                target_file_id = f"{repository_id}:file:{target_file_path}"
                add_edge(
                    source_id=file_id,
                    target_id=target_file_id,
                    edge_type="IMPORTS",
                    line_no=re_exp.line_number,
                    confidence=1.0,
                    evidence=re_exp.raw_statement,
                )
                t_syms = _get_target_file_nodes(target_file_path)
                for exp_alias, src_name in re_exp.exported_symbols.items():
                    if src_name in t_syms:
                        tgt_node = t_syms[src_name]
                        add_edge(
                            source_id=file_id,
                            target_id=tgt_node.id,
                            edge_type="REFERENCES",
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
                    line_no=re_exp.line_number,
                    confidence=1.0,
                    evidence=re_exp.raw_statement,
                )

        # 3. Precompute reachable import closure for rel_path
        reachable_imports: Set[str] = set()
        queue: List[str] = [rel_path]
        visited: Set[str] = {rel_path}
        file_prefix = f"{repository_id}:file:"

        while queue:
            curr_file = queue.pop(0)
            if curr_file == rel_path:
                deps = [imp.module_path for imp in parse_res.imports] + [r.module_path for r in parse_res.re_exports]
                for mod_p in deps:
                    t_path = self._resolve_import_path(
                        base_file=curr_file,
                        import_path=mod_p,
                        known_files=known_files,
                        base_language=parse_res.language,
                    )
                    if t_path and t_path not in visited:
                        visited.add(t_path)
                        reachable_imports.add(t_path)
                        queue.append(t_path)
            else:
                imp_edge_rows = (
                    self.db.query(GraphEdgeModel.target_id)
                    .filter(
                        GraphEdgeModel.repository_id == repository_id,
                        GraphEdgeModel.source_id == f"{file_prefix}{curr_file}",
                        GraphEdgeModel.edge_type == "IMPORTS",
                    )
                    .all()
                )
                for row in imp_edge_rows:
                    tgt = row[0]
                    if tgt.startswith(file_prefix):
                        dep_f = tgt[len(file_prefix):]
                        if dep_f not in visited:
                            visited.add(dep_f)
                            reachable_imports.add(dep_f)
                            queue.append(dep_f)

        # 4. CALLS and TESTS edges
        for call in parse_res.calls:
            caller_id = (
                f"{repository_id}:{runtime_prefix}:{rel_path}:{call.caller_name}"
                if call.caller_name != "<module>"
                else file_id
            )
            if caller_id not in local_nodes_by_id:
                caller_id = file_id

            target_candidate = None
            if call.callee_name in file_imported_symbols:
                target_candidate = file_imported_symbols[call.callee_name]
            elif call.callee_name in local_nodes_by_name:
                target_candidate = local_nodes_by_name[call.callee_name][0]
            else:
                raw_cands = (
                    self.db.query(GraphNodeModel)
                    .filter(
                        GraphNodeModel.repository_id == repository_id,
                        GraphNodeModel.name == call.callee_name,
                        GraphNodeModel.node_type != "FILE",
                    )
                    .all()
                )
                callee_candidates = [
                    cand for cand in raw_cands
                    if are_languages_call_compatible(parse_res.language, cand.language)
                ]
                if callee_candidates:
                    # In Go, files in the same directory share package scope
                    if parse_res.language == "go":
                        rel_dir = os.path.dirname(rel_path)
                        for cand in callee_candidates:
                            if os.path.dirname(cand.file_path) == rel_dir:
                                target_candidate = cand
                                break
                    if not target_candidate:
                        for cand in callee_candidates:
                            if cand.file_path in reachable_imports:
                                target_candidate = cand
                                break
                    if not target_candidate and len(callee_candidates) == 1:
                        target_candidate = callee_candidates[0]

                # Handle method selector and path calls like "c.AddToTotal", "calc.Add", "math::add", "Person::new"
                if not target_candidate and ("." in call.callee_name or "::" in call.callee_name):
                    simple_name = call.callee_name.split("::")[-1].split(".")[-1]
                    raw_method_cands = (
                        self.db.query(GraphNodeModel)
                        .filter(
                            GraphNodeModel.repository_id == repository_id,
                            GraphNodeModel.name == simple_name,
                            GraphNodeModel.node_type != "FILE",
                        )
                        .all()
                    )
                    method_candidates = [
                        cand for cand in raw_method_cands
                        if are_languages_call_compatible(parse_res.language, cand.language)
                    ]
                    for cand in method_candidates:
                        if cand.file_path == rel_path:
                            target_candidate = cand
                            break
                    if not target_candidate and default_registry.is_package_scoped(parse_res.language):
                        rel_dir = os.path.dirname(rel_path)
                        for cand in method_candidates:
                            if os.path.dirname(cand.file_path) == rel_dir or cand.file_path in reachable_imports:
                                target_candidate = cand
                                break
                    if not target_candidate:
                        for cand in method_candidates:
                            if cand.file_path in reachable_imports:
                                target_candidate = cand
                                break
                    if not target_candidate and len(method_candidates) == 1:
                        target_candidate = method_candidates[0]

            if target_candidate:
                conf = 1.0 if target_candidate.file_path == rel_path else 0.85
                add_edge(
                    source_id=caller_id,
                    target_id=target_candidate.id,
                    edge_type="CALLS",
                    line_no=call.line_number,
                    confidence=conf,
                    evidence=call.raw_call,
                )

                if parse_res.language == "sql" and target_candidate.node_type in ("CLASS", "TYPE", "VARIABLE"):
                    add_edge(
                        source_id=caller_id,
                        target_id=target_candidate.id,
                        edge_type="REFERENCES",
                        line_no=call.line_number,
                        confidence=conf,
                        evidence=call.raw_call,
                    )

                caller_node = local_nodes_by_id.get(caller_id) or self.db.query(GraphNodeModel).filter_by(id=caller_id, repository_id=repository_id).first()
                if caller_node and caller_node.node_type == "TEST":
                    add_edge(
                        source_id=caller_id,
                        target_id=target_candidate.id,
                        edge_type="TESTS",
                        line_no=call.line_number,
                        confidence=0.95,
                        evidence=f"Test invokes {target_candidate.name}",
                    )

        # 5. Decorator REFERENCES edges (Python)
        if parse_res.language == "python":
            for ex_node in parse_res.nodes:
                dec_names = ex_node.metadata.get("decorator_names", [])
                if not dec_names:
                    continue
                source_node_id = f"{repository_id}:{runtime_prefix}:{rel_path}:{ex_node.qualified_name}"
                for d_name in dec_names:
                    simple_name = d_name.split(".")[-1]
                    candidates = (
                        self.db.query(GraphNodeModel)
                        .filter(
                            GraphNodeModel.repository_id == repository_id,
                            GraphNodeModel.name == simple_name,
                            GraphNodeModel.language == "python",
                            GraphNodeModel.node_type != "FILE",
                        )
                        .all()
                    )
                    matching_cand = None
                    for c in candidates:
                        if c.file_path == rel_path or c.file_path in reachable_imports:
                            matching_cand = c
                            break
                    if not matching_cand and len(candidates) == 1:
                        matching_cand = candidates[0]

                    if matching_cand:
                        add_edge(
                            source_id=source_node_id,
                            target_id=matching_cand.id,
                            edge_type="REFERENCES",
                            line_no=ex_node.start_line,
                            confidence=1.0,
                            evidence=f"Decorated by @{d_name}",
                        )

        return edges_to_insert

    def _refresh_importers_edges(
        self,
        repository_id: str,
        repo_root: str,
        direct_importers: Set[str],
        known_files: Optional[Set[str]] = None,
    ):
        """Rebuilds outgoing edges for files that directly import modified/deleted files."""
        if not direct_importers:
            return

        if known_files is None:
            known_files = {
                r[0]
                for r in self.db.query(GraphNodeModel.file_path)
                .filter(
                    GraphNodeModel.repository_id == repository_id,
                    GraphNodeModel.node_type == "FILE",
                )
                .all()
            }

        for imp_path in direct_importers:
            imp_full_path = os.path.join(repo_root, imp_path)
            if not os.path.exists(imp_full_path) or not is_supported_file(imp_path):
                continue

            try:
                with open(imp_full_path, "r", encoding="utf-8", errors="ignore") as f:
                    imp_content = f.read()
                imp_parse_res = parse_source_file(imp_path, imp_content)
                if not imp_parse_res:
                    continue

                self.db.query(GraphEdgeModel).filter(
                    GraphEdgeModel.repository_id == repository_id,
                    GraphEdgeModel.file_path == imp_path,
                ).delete(synchronize_session=False)

                new_edges = self._construct_edges_for_file(
                    repository_id=repository_id,
                    rel_path=imp_path,
                    parse_res=imp_parse_res,
                    known_files=known_files,
                    repo_root=repo_root,
                )
                if new_edges:
                    self.db.bulk_save_objects(new_edges)
                    self.db.flush()
            except Exception as err:
                logger.warning(f"Could not refresh edges for importer {imp_path}: {err}")

    def update_file(self, repository_id: str, rel_path: str) -> bool:
        """Incrementally updates or removes a single file in the repository index.
        
        Args:
            repository_id: The UUID of the repository.
            rel_path: Relative path of the file to update.
            
        Returns:
            bool: True if updated or purged successfully, False otherwise.
        """
        repo = self.db.query(RepositoryModel).filter_by(id=repository_id).first()
        if not repo:
            raise ValueError(f"Repository {repository_id} not found")

        repo_root = os.path.realpath(repo.source_path)
        rel_path = rel_path.replace("\\", "/")
        full_path = os.path.join(repo_root, rel_path)

        if not self.tsconfig_paths and os.path.exists(repo_root):
            self.tsconfig_base_url, self.tsconfig_paths = self._load_tsconfig(repo_root)
        if self.go_module_name is None and os.path.exists(repo_root):
            self.go_module_name = self._load_go_mod(repo_root)

        # Pre-query direct importers
        importer_rows = (
            self.db.query(GraphEdgeModel.file_path)
            .filter(
                GraphEdgeModel.repository_id == repository_id,
                GraphEdgeModel.target_id == f"{repository_id}:file:{rel_path}",
                GraphEdgeModel.edge_type == "IMPORTS",
            )
            .all()
        )
        direct_importers = {r[0] for r in importer_rows if r[0] != rel_path}
        if default_registry.is_package_scoped(rel_path):
            rel_dir = os.path.dirname(rel_path)
            co_pkg_rows = (
                self.db.query(GraphNodeModel.file_path)
                .filter(
                    GraphNodeModel.repository_id == repository_id,
                    GraphNodeModel.node_type == "FILE",
                    GraphNodeModel.language == "go",
                )
                .all()
            )
            for r in co_pkg_rows:
                f_path = r[0]
                if f_path != rel_path and os.path.dirname(f_path) == rel_dir:
                    direct_importers.add(f_path)

        # Case A: File deleted on disk
        if not os.path.exists(full_path):
            try:
                self._purge_file_nodes_and_edges(repository_id, rel_path)
                self._refresh_importers_edges(repository_id, repo_root, direct_importers)
                self._recompute_repository_stats(repository_id, repo)
                repo.last_indexed_at = datetime.utcnow()
                repo.indexing_status = "ready"
                self.db.commit()
                return True
            except Exception as e:
                self.db.rollback()
                logger.error(f"Error purging file {rel_path} in repo {repository_id}: {e}", exc_info=True)
                raise

        # Case B: File exists on disk
        if not is_supported_file(rel_path):
            return False

        sz = os.path.getsize(full_path)
        if sz > MAX_FILE_SIZE:
            logger.warning(f"File {rel_path} exceeds size limit ({sz} bytes). Skipped.")
            return False

        # Read and parse file BEFORE touching database (Requirement 7)
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
        mtime = os.path.getmtime(full_path)
        lines = content.splitlines()
        line_count = len(lines)

        parse_res = parse_source_file(rel_path, content)
        if not parse_res:
            return False

        try:
            # 1. Purge old nodes and edges for this file
            self._purge_file_nodes_and_edges(repository_id, rel_path)

            # 2. Insert new FILE node and symbol nodes
            file_id = f"{repository_id}:file:{rel_path}"
            file_node = GraphNodeModel(
                id=file_id,
                repository_id=repository_id,
                node_type="FILE",
                name=os.path.basename(rel_path),
                qualified_name=rel_path,
                file_path=rel_path,
                start_line=1,
                end_line=max(line_count, 1),
                start_col=0,
                end_col=0,
                language=parse_res.language,
                signature=f"file {os.path.basename(rel_path)}",
                docstring="",
                node_metadata={
                    "size_bytes": sz,
                    "lines": line_count,
                    "content_hash": content_hash,
                    "mtime": mtime,
                },
            )
            nodes_to_insert = [file_node]
            runtime_prefix = get_runtime_prefix(parse_res.language)
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

            self.db.bulk_save_objects(nodes_to_insert)
            self.db.flush()

            # 3. Known files in repo
            known_files = {
                r[0]
                for r in self.db.query(GraphNodeModel.file_path)
                .filter(
                    GraphNodeModel.repository_id == repository_id,
                    GraphNodeModel.node_type == "FILE",
                )
                .all()
            }
            known_files.add(rel_path)

            # 4. Construct edges for this file
            edges = self._construct_edges_for_file(
                repository_id=repository_id,
                rel_path=rel_path,
                parse_res=parse_res,
                known_files=known_files,
                repo_root=repo_root,
            )
            if edges:
                self.db.bulk_save_objects(edges)
                self.db.flush()

            # 5. Refresh direct importers
            self._refresh_importers_edges(repository_id, repo_root, direct_importers, known_files=known_files)

            # 6. Recompute stats & commit
            self._recompute_repository_stats(repository_id, repo)
            repo.last_indexed_at = datetime.utcnow()
            repo.indexing_status = "ready"
            self.db.commit()
            return True

        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed updating file {rel_path} in repo {repository_id}: {e}", exc_info=True)
            raise

    def sync_repository(self, repository_id: str) -> Dict[str, Any]:
        """Incrementally synchronizes a repository with its source directory on disk.
        
        Detects added, modified, deleted, and renamed files using content hashes and mtime.
        Updates only affected files and their cross-file relationships.
        
        Args:
            repository_id: The UUID of the repository.
            
        Returns:
            Dict[str, Any] summary of changes, duration, and updated repository stats.
        """
        start_time = time.time()
        repo = self.db.query(RepositoryModel).filter_by(id=repository_id).first()
        if not repo:
            raise ValueError(f"Repository {repository_id} not found")

        repo_root = os.path.realpath(repo.source_path)
        if not os.path.exists(repo_root) or not os.path.isdir(repo_root):
            raise ValueError(f"Repository path '{repo.source_path}' does not exist or is not a directory.")

        if not self.tsconfig_paths:
            self.tsconfig_base_url, self.tsconfig_paths = self._load_tsconfig(repo_root)
        if self.go_module_name is None:
            self.go_module_name = self._load_go_mod(repo_root)

        # 1. Scan disk for supported files
        disk_files: Dict[str, str] = {}
        for root, dirs, files in os.walk(repo_root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, repo_root).replace("\\", "/")
                if not is_supported_file(rel_path):
                    continue
                sz = os.path.getsize(full_path)
                if sz > MAX_FILE_SIZE:
                    continue
                disk_files[rel_path] = full_path

        # 2. Query existing FILE nodes from DB
        existing_file_nodes = (
            self.db.query(GraphNodeModel)
            .filter(
                GraphNodeModel.repository_id == repository_id,
                GraphNodeModel.node_type == "FILE",
            )
            .all()
        )
        db_file_map = {n.file_path: n for n in existing_file_nodes}

        # 3. Detect changes
        disk_paths = set(disk_files.keys())
        db_paths = set(db_file_map.keys())

        added_paths = disk_paths - db_paths
        deleted_paths = db_paths - disk_paths
        common_paths = disk_paths & db_paths

        modified_paths: Set[str] = set()
        disk_hashes: Dict[str, str] = {}
        disk_mtimes: Dict[str, float] = {}
        disk_sizes: Dict[str, int] = {}
        disk_contents: Dict[str, str] = {}

        for rel_path in common_paths:
            full_path = disk_files[rel_path]
            node = db_file_map[rel_path]
            meta = node.node_metadata if isinstance(node.node_metadata, dict) else {}
            db_size = meta.get("size_bytes")
            db_hash = meta.get("content_hash")

            disk_size = os.path.getsize(full_path)
            disk_mtime = os.path.getmtime(full_path)
            disk_sizes[rel_path] = disk_size
            disk_mtimes[rel_path] = disk_mtime

            # Read content and compute hash to verify (covers requirement 8: identical mtime and size)
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            disk_contents[rel_path] = content
            chash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
            disk_hashes[rel_path] = chash

            if db_hash is None or chash != db_hash or (db_size is not None and disk_size != db_size):
                modified_paths.add(rel_path)

        for rel_path in added_paths:
            full_path = disk_files[rel_path]
            disk_sizes[rel_path] = os.path.getsize(full_path)
            disk_mtimes[rel_path] = os.path.getmtime(full_path)
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            disk_contents[rel_path] = content
            disk_hashes[rel_path] = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()

        # Detect renamed files: deleted file having identical hash to added file
        renamed: List[Dict[str, str]] = []
        claimed_deleted: Set[str] = set()
        deleted_hashes = {}
        for d_path in sorted(deleted_paths):
            d_meta = db_file_map[d_path].node_metadata if isinstance(db_file_map[d_path].node_metadata, dict) else {}
            d_hash = d_meta.get("content_hash")
            if d_hash:
                deleted_hashes[d_path] = d_hash

        for a_path in sorted(added_paths):
            a_hash = disk_hashes.get(a_path)
            if not a_hash:
                continue
            for d_path in sorted(deleted_paths):
                if d_path not in claimed_deleted and deleted_hashes.get(d_path) == a_hash:
                    renamed.append({"from": d_path, "to": a_path})
                    claimed_deleted.add(d_path)
                    break

        total_changes = len(added_paths) + len(modified_paths) + len(deleted_paths)

        # 4. Fast No-Op Exit
        if total_changes == 0:
            duration = time.time() - start_time
            return {
                "repository_id": repository_id,
                "status": "unchanged",
                "files_added": [],
                "files_modified": [],
                "files_deleted": [],
                "files_renamed": [],
                "total_changes": 0,
                "duration_seconds": round(duration, 4),
                "stats": repo.stats or self._recompute_repository_stats(repository_id, repo),
            }

        # 5. Pre-parse ALL added and modified files BEFORE modifying DB (Requirement 7)
        parsed_files: Dict[str, ParseResult] = {}
        files_to_parse = added_paths | modified_paths
        for rel_path in files_to_parse:
            content = disk_contents[rel_path]
            res = parse_source_file(rel_path, content)
            if res is None:
                raise ValueError(f"Failed to parse source file: {rel_path}")
            parsed_files[rel_path] = res

        # 6. Find all direct importers of any changed files
        all_changed_files = added_paths | modified_paths | deleted_paths
        target_ids = [f"{repository_id}:file:{p}" for p in all_changed_files]
        dependent_file_query = (
            self.db.query(GraphEdgeModel.file_path)
            .filter(
                GraphEdgeModel.repository_id == repository_id,
                GraphEdgeModel.edge_type == "IMPORTS",
                GraphEdgeModel.target_id.in_(target_ids),
            )
            .all()
        )
        direct_importers = {r[0] for r in dependent_file_query if r[0] not in all_changed_files}
        for p in all_changed_files:
            if default_registry.is_package_scoped(p):
                p_dir = os.path.dirname(p)
                for f_path in (disk_paths | db_paths):
                    if default_registry.is_package_scoped(f_path) and f_path not in all_changed_files and os.path.dirname(f_path) == p_dir:
                        direct_importers.add(f_path)

        # 7. Apply DB mutations atomically
        try:
            for rel_path in deleted_paths:
                self._purge_file_nodes_and_edges(repository_id, rel_path)

            for rel_path in modified_paths:
                self._purge_file_nodes_and_edges(repository_id, rel_path)

            new_nodes: List[GraphNodeModel] = []
            for rel_path in files_to_parse:
                parse_res = parsed_files[rel_path]
                content = disk_contents[rel_path]
                lines = content.splitlines()
                file_id = f"{repository_id}:file:{rel_path}"
                file_node = GraphNodeModel(
                    id=file_id,
                    repository_id=repository_id,
                    node_type="FILE",
                    name=os.path.basename(rel_path),
                    qualified_name=rel_path,
                    file_path=rel_path,
                    start_line=1,
                    end_line=max(len(lines), 1),
                    start_col=0,
                    end_col=0,
                    language=parse_res.language,
                    signature=f"file {os.path.basename(rel_path)}",
                    docstring="",
                    node_metadata={
                        "size_bytes": disk_sizes[rel_path],
                        "lines": len(lines),
                        "content_hash": disk_hashes[rel_path],
                        "mtime": disk_mtimes[rel_path],
                    },
                )
                new_nodes.append(file_node)

                runtime_prefix = get_runtime_prefix(parse_res.language)
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
                    new_nodes.append(db_node)

            self.db.bulk_save_objects(new_nodes)
            self.db.flush()

            known_files = (db_paths - deleted_paths) | added_paths

            new_edges: List[GraphEdgeModel] = []
            for rel_path in files_to_parse:
                file_edges = self._construct_edges_for_file(
                    repository_id=repository_id,
                    rel_path=rel_path,
                    parse_res=parsed_files[rel_path],
                    known_files=known_files,
                    repo_root=repo_root,
                )
                new_edges.extend(file_edges)

            if new_edges:
                self.db.bulk_save_objects(new_edges)
                self.db.flush()

            self._refresh_importers_edges(
                repository_id=repository_id,
                repo_root=repo_root,
                direct_importers=direct_importers,
                known_files=known_files,
            )

            stats = self._recompute_repository_stats(repository_id, repo)
            repo.last_indexed_at = datetime.utcnow()
            repo.indexing_status = "ready"
            repo.error_message = None
            self.db.commit()

            duration = time.time() - start_time
            return {
                "repository_id": repository_id,
                "status": "synchronized",
                "files_added": sorted(list(added_paths)),
                "files_modified": sorted(list(modified_paths)),
                "files_deleted": sorted(list(deleted_paths)),
                "files_renamed": renamed,
                "total_changes": total_changes,
                "duration_seconds": round(duration, 4),
                "stats": stats,
            }

        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed incremental synchronization for repo {repository_id}: {e}", exc_info=True)
            raise
