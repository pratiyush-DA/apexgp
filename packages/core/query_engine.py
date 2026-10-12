"""ApexGraph Query Engine

Provides fast graph queries, recursive traversals, change-impact
blast-radius calculations, and test discovery with evidence.
"""

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from packages.core.models import (
    GraphEdgeModel,
    GraphNodeModel,
    GraphNodeSchema,
    ImpactAnalysisResult,
    SearchResult,
)


class GraphQueryEngine:
    def __init__(self, db_session: Session):
        self.db = db_session

    def find_symbol(
        self,
        name: str,
        repo_id: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> List[GraphNodeModel]:
        """Locates exact or qualified symbol definitions."""
        query = self.db.query(GraphNodeModel).filter(
            or_(
                GraphNodeModel.name == name,
                GraphNodeModel.qualified_name.endswith(f":{name}"),
                GraphNodeModel.qualified_name == name,
            )
        )
        if repo_id:
            query = query.filter(GraphNodeModel.repository_id == repo_id)
        if file_path:
            query = query.filter(GraphNodeModel.file_path == file_path)

        return query.limit(50).all()

    def search_code(
        self,
        query_text: str,
        repo_id: Optional[str] = None,
        node_type: Optional[str] = None,
        file_path_filter: Optional[str] = None,
        limit: int = 50,
    ) -> List[SearchResult]:
        """Performs lexical and structural code search across symbols and files."""
        q = query_text.strip().lower()
        query = self.db.query(GraphNodeModel)

        if repo_id:
            query = query.filter(GraphNodeModel.repository_id == repo_id)
        if node_type:
            query = query.filter(GraphNodeModel.node_type == node_type.upper())
        if file_path_filter:
            query = query.filter(GraphNodeModel.file_path.ilike(f"%{file_path_filter}%"))

        # Match by name, qualified_name, or docstring
        query = query.filter(
            or_(
                GraphNodeModel.name.ilike(f"%{q}%"),
                GraphNodeModel.qualified_name.ilike(f"%{q}%"),
                GraphNodeModel.file_path.ilike(f"%{q}%"),
                GraphNodeModel.docstring.ilike(f"%{q}%"),
            )
        )

        nodes = query.limit(limit).all()
        results: List[SearchResult] = []
        for n in nodes:
            # Score relevance
            name_lower = n.name.lower()
            if name_lower == q:
                score = 1.0
            elif name_lower.startswith(q):
                score = 0.85
            elif q in name_lower:
                score = 0.7
            else:
                score = 0.5

            snippet = n.signature or n.docstring or f"{n.node_type} {n.name} ({n.file_path}:{n.start_line})"
            results.append(
                SearchResult(
                    node=GraphNodeSchema.model_validate(n),
                    match_score=score,
                    snippet=snippet,
                )
            )

        # Sort highest score first
        results.sort(key=lambda r: r.match_score, reverse=True)
        return results

    def get_symbol_details(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Returns full symbol profile with incoming and outgoing relationships."""
        node = self.db.query(GraphNodeModel).filter_by(id=node_id).first()
        if not node:
            return None

        # Incoming edges (who calls, tests, imports this node)
        incoming_edges = (
            self.db.query(GraphEdgeModel).filter_by(target_id=node_id).limit(100).all()
        )
        # Outgoing edges (who this node calls, imports, defines)
        outgoing_edges = (
            self.db.query(GraphEdgeModel).filter_by(source_id=node_id).limit(100).all()
        )

        return {
            "node": GraphNodeSchema.model_validate(node).model_dump(),
            "incoming_edges": [
                {
                    "source_id": e.source_id,
                    "edge_type": e.edge_type,
                    "confidence": e.confidence,
                    "file_path": e.file_path,
                    "line_number": e.line_number,
                    "evidence": e.evidence,
                }
                for e in incoming_edges
            ],
            "outgoing_edges": [
                {
                    "target_id": e.target_id,
                    "edge_type": e.edge_type,
                    "confidence": e.confidence,
                    "file_path": e.file_path,
                    "line_number": e.line_number,
                    "evidence": e.evidence,
                }
                for e in outgoing_edges
            ],
        }

    def get_callers(
        self, node_id: str, max_depth: int = 5
    ) -> List[Dict[str, Any]]:
        """Finds callers traversing incoming CALLS edges up to max_depth."""
        visited: Set[str] = {node_id}
        queue = deque([(node_id, 0, [])])
        callers: List[Dict[str, Any]] = []

        while queue:
            curr_id, depth, path = queue.popleft()
            if depth >= max_depth:
                continue

            incoming = (
                self.db.query(GraphEdgeModel)
                .filter(
                    GraphEdgeModel.target_id == curr_id,
                    GraphEdgeModel.edge_type == "CALLS",
                )
                .all()
            )

            for edge in incoming:
                caller_node = self.db.query(GraphNodeModel).filter_by(id=edge.source_id).first()
                if not caller_node:
                    continue

                new_path = path + [
                    {
                        "caller": caller_node.name,
                        "file": caller_node.file_path,
                        "line": edge.line_number,
                        "evidence": edge.evidence,
                    }
                ]

                callers.append(
                    {
                        "node": GraphNodeSchema.model_validate(caller_node).model_dump(),
                        "depth": depth + 1,
                        "evidence": edge.evidence,
                        "line_number": edge.line_number,
                        "call_path": new_path,
                    }
                )

                if edge.source_id not in visited:
                    visited.add(edge.source_id)
                    queue.append((edge.source_id, depth + 1, new_path))

        return callers

    def get_callees(
        self, node_id: str, max_depth: int = 5
    ) -> List[Dict[str, Any]]:
        """Finds callees traversing outgoing CALLS edges up to max_depth."""
        visited: Set[str] = {node_id}
        queue = deque([(node_id, 0, [])])
        callees: List[Dict[str, Any]] = []

        while queue:
            curr_id, depth, path = queue.popleft()
            if depth >= max_depth:
                continue

            outgoing = (
                self.db.query(GraphEdgeModel)
                .filter(
                    GraphEdgeModel.source_id == curr_id,
                    GraphEdgeModel.edge_type == "CALLS",
                )
                .all()
            )

            for edge in outgoing:
                callee_node = self.db.query(GraphNodeModel).filter_by(id=edge.target_id).first()
                if not callee_node:
                    continue

                new_path = path + [
                    {
                        "callee": callee_node.name,
                        "file": callee_node.file_path,
                        "line": edge.line_number,
                        "evidence": edge.evidence,
                    }
                ]

                callees.append(
                    {
                        "node": GraphNodeSchema.model_validate(callee_node).model_dump(),
                        "depth": depth + 1,
                        "evidence": edge.evidence,
                        "line_number": edge.line_number,
                        "call_path": new_path,
                    }
                )

                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    queue.append((edge.target_id, depth + 1, new_path))

        return callees

    def get_dependencies(self, node_id: str) -> Dict[str, Any]:
        """Returns outgoing IMPORTS and REFERENCES edges for a node or file."""
        edges = (
            self.db.query(GraphEdgeModel)
            .filter(
                GraphEdgeModel.source_id == node_id,
                GraphEdgeModel.edge_type.in_(["IMPORTS", "REFERENCES"]),
            )
            .all()
        )

        dep_nodes = []
        external_deps = []

        for e in edges:
            if e.target_id.startswith("ext:"):
                external_deps.append({"id": e.target_id, "evidence": e.evidence})
            else:
                target_node = self.db.query(GraphNodeModel).filter_by(id=e.target_id).first()
                if target_node:
                    dep_nodes.append(
                        {
                            "node": GraphNodeSchema.model_validate(target_node).model_dump(),
                            "edge_type": e.edge_type,
                            "evidence": e.evidence,
                            "line": e.line_number,
                        }
                    )

        return {
            "internal_dependencies": dep_nodes,
            "external_dependencies": external_deps,
        }

    def trace_relationships(
        self, source_id: str, target_id: str, max_depth: int = 6
    ) -> Optional[List[Dict[str, Any]]]:
        """Finds shortest path between two symbols using bidirectional BFS."""
        if source_id == target_id:
            node = self.db.query(GraphNodeModel).filter_by(id=source_id).first()
            return [{"node_id": source_id, "name": node.name if node else source_id, "edge": None}]

        visited: Set[str] = {source_id}
        queue = deque([(source_id, [])])

        while queue:
            curr_id, path = queue.popleft()
            if len(path) >= max_depth:
                continue

            # Check outgoing and incoming edges for traversal
            edges = (
                self.db.query(GraphEdgeModel)
                .filter(or_(GraphEdgeModel.source_id == curr_id, GraphEdgeModel.target_id == curr_id))
                .all()
            )

            for edge in edges:
                next_id = edge.target_id if edge.source_id == curr_id else edge.source_id
                direction = "outgoing" if edge.source_id == curr_id else "incoming"

                step_info = {
                    "from_id": curr_id,
                    "to_id": next_id,
                    "edge_type": edge.edge_type,
                    "direction": direction,
                    "evidence": edge.evidence,
                    "line": edge.line_number,
                }
                new_path = path + [step_info]

                if next_id == target_id:
                    return new_path

                if next_id not in visited and not next_id.startswith("ext:"):
                    visited.add(next_id)
                    queue.append((next_id, new_path))

        return None

    def analyze_change_impact(
        self,
        target_node_or_file: str,
        repo_id: str,
        max_depth: int = 5,
    ) -> ImpactAnalysisResult:
        """Calculates blast radius: affected symbols, files, and linked tests."""
        # Check if target is a node_id, file_path, or symbol name
        target_node = self.db.query(GraphNodeModel).filter_by(id=target_node_or_file).first()

        if not target_node:
            # Try file path lookup
            target_node = (
                self.db.query(GraphNodeModel)
                .filter(
                    GraphNodeModel.repository_id == repo_id,
                    GraphNodeModel.file_path == target_node_or_file,
                    GraphNodeModel.node_type == "FILE",
                )
                .first()
            )

        if not target_node:
            # Try symbol name lookup
            target_node = (
                self.db.query(GraphNodeModel)
                .filter(
                    GraphNodeModel.repository_id == repo_id,
                    GraphNodeModel.name == target_node_or_file,
                )
                .first()
            )

        if not target_node:
            raise ValueError(f"Target '{target_node_or_file}' not found in repository {repo_id}")

        visited_nodes: Set[str] = {target_node.id}
        affected_nodes_list: List[GraphNodeModel] = []
        affected_files_set: Set[str] = {target_node.file_path}
        evidence_paths: List[Dict[str, Any]] = []

        # Traverse backward: WHO calls, imports, or contains this target?
        queue = deque([(target_node.id, 0, [])])

        while queue:
            curr_id, depth, trace = queue.popleft()
            if depth >= max_depth:
                continue

            incoming_edges = (
                self.db.query(GraphEdgeModel)
                .filter(
                    GraphEdgeModel.target_id == curr_id,
                    GraphEdgeModel.edge_type.in_(["CALLS", "IMPORTS", "REFERENCES", "CONTAINS"]),
                )
                .all()
            )

            for edge in incoming_edges:
                dep_node = (
                    self.db.query(GraphNodeModel)
                    .filter_by(id=edge.source_id, repository_id=repo_id)
                    .first()
                )
                if not dep_node:
                    continue

                step = {
                    "source": dep_node.name,
                    "source_id": dep_node.id,
                    "target_id": curr_id,
                    "edge_type": edge.edge_type,
                    "file": dep_node.file_path,
                    "line": edge.line_number,
                    "evidence": edge.evidence,
                    "depth": depth + 1,
                }
                evidence_paths.append(step)

                if dep_node.id not in visited_nodes:
                    visited_nodes.add(dep_node.id)
                    affected_nodes_list.append(dep_node)
                    affected_files_set.add(dep_node.file_path)
                    queue.append((dep_node.id, depth + 1, trace + [step]))

        # Identify linked tests
        linked_tests: List[GraphNodeModel] = []
        test_ids_seen: Set[str] = set()

        for aff_id in visited_nodes:
            # Find tests directly testing or calling affected nodes
            test_edges = (
                self.db.query(GraphEdgeModel)
                .filter(
                    GraphEdgeModel.target_id == aff_id,
                    GraphEdgeModel.edge_type.in_(["TESTS", "CALLS"]),
                )
                .all()
            )
            for te in test_edges:
                t_node = (
                    self.db.query(GraphNodeModel)
                    .filter_by(id=te.source_id, repository_id=repo_id)
                    .first()
                )
                if t_node and (t_node.node_type == "TEST" or "test" in t_node.file_path.lower()):
                    if t_node.id not in test_ids_seen:
                        test_ids_seen.add(t_node.id)
                        linked_tests.append(t_node)

        return ImpactAnalysisResult(
            target=target_node.name,
            repository_id=repo_id,
            affected_nodes=[GraphNodeSchema.model_validate(n) for n in affected_nodes_list],
            affected_files=sorted(list(affected_files_set)),
            linked_tests=[GraphNodeSchema.model_validate(t) for t in linked_tests],
            traversal_depth=max_depth,
            total_affected=len(affected_nodes_list),
            evidence_paths=evidence_paths[:100],
        )

    def find_related_tests(self, node_id: str) -> List[GraphNodeModel]:
        """Finds all unit and integration tests covering the target node."""
        target_node = self.db.query(GraphNodeModel).filter_by(id=node_id).first()
        if not target_node:
            return []

        # 1. Direct TESTS or CALLS edges
        direct_tests = (
            self.db.query(GraphNodeModel)
            .join(GraphEdgeModel, GraphEdgeModel.source_id == GraphNodeModel.id)
            .filter(
                GraphEdgeModel.target_id == node_id,
                or_(
                    GraphEdgeModel.edge_type == "TESTS",
                    GraphNodeModel.node_type == "TEST",
                ),
            )
            .all()
        )
        if direct_tests:
            return direct_tests

        # 2. File-level tests (test files importing this file)
        file_id = f"{target_node.repository_id}:file:{target_node.file_path}"
        importing_test_nodes = (
            self.db.query(GraphNodeModel)
            .join(GraphEdgeModel, GraphEdgeModel.source_id == GraphNodeModel.id)
            .filter(
                GraphEdgeModel.target_id == file_id,
                or_(
                    GraphNodeModel.node_type == "TEST",
                    GraphNodeModel.file_path.ilike("%test%"),
                ),
            )
            .all()
        )
        return importing_test_nodes

    def get_graph_neighborhood(
        self, node_id: str, radius: int = 1, limit: int = 100
    ) -> Dict[str, Any]:
        """Returns immediate N-hop graph neighborhood for visualization."""
        center_node = self.db.query(GraphNodeModel).filter_by(id=node_id).first()
        if not center_node:
            return {"nodes": [], "edges": []}

        visited_nodes: Set[str] = {node_id}
        node_models: Dict[str, GraphNodeModel] = {node_id: center_node}
        edges_list: List[GraphEdgeModel] = []
        edge_keys: Set[str] = set()

        queue = deque([(node_id, 0)])

        while queue:
            curr_id, depth = queue.popleft()
            if depth >= radius:
                continue

            # Outgoing & Incoming
            edges = (
                self.db.query(GraphEdgeModel)
                .filter(or_(GraphEdgeModel.source_id == curr_id, GraphEdgeModel.target_id == curr_id))
                .limit(limit)
                .all()
            )

            for e in edges:
                if e.id not in edge_keys:
                    edge_keys.add(e.id)
                    edges_list.append(e)

                next_id = e.target_id if e.source_id == curr_id else e.source_id
                if next_id not in visited_nodes and not next_id.startswith("ext:"):
                    visited_nodes.add(next_id)
                    next_node = self.db.query(GraphNodeModel).filter_by(id=next_id).first()
                    if next_node:
                        node_models[next_id] = next_node
                        queue.append((next_id, depth + 1))

        return {
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "node_type": n.node_type,
                    "file_path": n.file_path,
                    "line": n.start_line,
                    "language": n.language,
                }
                for n in node_models.values()
            ],
            "edges": [
                {
                    "id": e.id,
                    "source": e.source_id,
                    "target": e.target_id,
                    "type": e.edge_type,
                    "confidence": e.confidence,
                    "evidence": e.evidence,
                }
                for e in edges_list
            ],
        }
