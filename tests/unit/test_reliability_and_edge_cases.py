"""Unit Tests for Reliability, Regression Hardening, and Edge Cases (Step 7)

Verifies:
1. Multi-repository isolation: analyze_change_impact does not cross repo boundaries.
2. trace_relationships rejects ghost nodes, cross-repo source/target, and external stub bridges.
3. find_related_tests and get_graph_neighborhood strictly scope to target repository.
4. Empty repository indexing and querying behavior.
5. Zero-byte files handling in indexer and query engine.
6. Re-indexing cleanup after file deletion (stale nodes and dangling edges purged).
7. Input handling: find_symbol whitespace resilience, search_code zero/negative limit safety.
"""

from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.core.indexer import RepositoryIndexer
from packages.core.models import (
    Base,
    GraphEdgeModel,
    GraphNodeModel,
    RepositoryModel,
    WorkspaceModel,
)
from packages.core.query_engine import GraphQueryEngine


@pytest.fixture
def isolated_db():
    """Provides an isolated in-memory SQLite database session for reliability tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _create_node(
    db,
    repo_id: str,
    name: str,
    file_path: str,
    node_type: str = "FUNCTION",
    start_line: int = 1,
    docstring: str = "",
) -> GraphNodeModel:
    qual_name = f"{file_path}:{name}"
    node_id = f"{repo_id}:py:{file_path}:{name}"
    node = GraphNodeModel(
        id=node_id,
        repository_id=repo_id,
        node_type=node_type,
        name=name,
        qualified_name=qual_name,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + 5,
        start_col=0,
        end_col=0,
        language="python",
        signature=f"def {name}()",
        docstring=docstring,
        node_metadata={},
    )
    db.add(node)
    db.commit()
    return node


# ============================================================================
# 1. Multi-Repository Isolation: analyze_change_impact
# ============================================================================

def test_analyze_change_impact_cross_repo_isolation(isolated_db):
    """P1: analyze_change_impact rejects target nodes belonging to a different repository."""
    ws = WorkspaceModel(name="MultiRepo WS")
    isolated_db.add(ws)
    isolated_db.commit()

    repo_a = RepositoryModel(workspace_id=ws.id, name="Repo A", source_path="/tmp/repo_a")
    repo_b = RepositoryModel(workspace_id=ws.id, name="Repo B", source_path="/tmp/repo_b")
    isolated_db.add_all([repo_a, repo_b])
    isolated_db.commit()

    # Node in Repo A
    node_a = _create_node(isolated_db, repo_a.id, "func_a", "pkg_a/service.py")
    # Node in Repo B
    node_b = _create_node(isolated_db, repo_b.id, "func_b", "pkg_b/service.py")

    engine = GraphQueryEngine(isolated_db)

    # 1. Querying Repo A for node_a must succeed
    impact_a = engine.analyze_change_impact(node_a.id, repo_id=repo_a.id)
    assert impact_a.target == "func_a"
    assert impact_a.repository_id == repo_a.id
    assert impact_a.affected_files == ["pkg_a/service.py"]

    # 2. Querying Repo A with node_b's ID must raise ValueError (target not found in repo_a)
    with pytest.raises(ValueError, match=f"not found in repository {repo_a.id}"):
        engine.analyze_change_impact(node_b.id, repo_id=repo_a.id)

    # 3. Querying Repo A with node_b's file path must raise ValueError
    with pytest.raises(ValueError, match=f"not found in repository {repo_a.id}"):
        engine.analyze_change_impact("pkg_b/service.py", repo_id=repo_a.id)

    # 4. Querying Repo A with node_b's symbol name must raise ValueError
    with pytest.raises(ValueError, match=f"not found in repository {repo_a.id}"):
        engine.analyze_change_impact("func_b", repo_id=repo_a.id)


# ============================================================================
# 2. Relationship Tracing: trace_relationships
# ============================================================================

def test_trace_relationships_ghost_nodes_and_cross_repo_isolation(isolated_db):
    """P1: trace_relationships rejects non-existent ghost nodes, cross-repo pairs, and ext stub bridges."""
    ws = WorkspaceModel(name="Trace WS")
    isolated_db.add(ws)
    isolated_db.commit()

    repo_a = RepositoryModel(workspace_id=ws.id, name="Repo A", source_path="/tmp/repo_a")
    repo_b = RepositoryModel(workspace_id=ws.id, name="Repo B", source_path="/tmp/repo_b")
    isolated_db.add_all([repo_a, repo_b])
    isolated_db.commit()

    node_a = _create_node(isolated_db, repo_a.id, "service_a", "service_a.py")
    node_b = _create_node(isolated_db, repo_b.id, "service_b", "service_b.py")

    engine = GraphQueryEngine(isolated_db)

    # A. Non-existent identical ID must return None (not a fake 1-node path)
    assert engine.trace_relationships("non_existent_ghost", "non_existent_ghost") is None

    # B. Non-existent source with valid target must return None
    assert engine.trace_relationships("non_existent_ghost", node_a.id) is None

    # C. Valid source with non-existent target must return None
    assert engine.trace_relationships(node_a.id, "non_existent_ghost") is None

    # D. Both nodes valid, but belonging to different repositories: must return None
    assert engine.trace_relationships(node_a.id, node_b.id) is None

    # E. Self-trace on an existing node returns the single-node path
    self_path = engine.trace_relationships(node_a.id, node_a.id)
    assert self_path == [{"node_id": node_a.id, "name": "service_a", "edge": None}]

    # F. Shared external stub bridging prevention:
    # Both Repo A and Repo B import ext:py:requests
    edge_a = GraphEdgeModel(
        id=f"{node_a.id}:IMPORTS:ext:py:requests",
        repository_id=repo_a.id,
        source_id=node_a.id,
        target_id="ext:py:requests",
        edge_type="IMPORTS",
        file_path="service_a.py",
        confidence=1.0,
    )
    edge_b = GraphEdgeModel(
        id=f"{node_b.id}:IMPORTS:ext:py:requests",
        repository_id=repo_b.id,
        source_id=node_b.id,
        target_id="ext:py:requests",
        edge_type="IMPORTS",
        file_path="service_b.py",
        confidence=1.0,
    )
    isolated_db.add_all([edge_a, edge_b])
    isolated_db.commit()

    # Traversal from node_a to node_b must NOT bridge via ext:py:requests
    assert engine.trace_relationships(node_a.id, node_b.id) is None


# ============================================================================
# 3. Repository Scoping in find_related_tests & get_graph_neighborhood
# ============================================================================

def test_find_related_tests_and_neighborhood_repository_scoping(isolated_db):
    """P2: find_related_tests and get_graph_neighborhood scope strictly to target repo."""
    ws = WorkspaceModel(name="Scoping WS")
    isolated_db.add(ws)
    isolated_db.commit()

    repo_a = RepositoryModel(workspace_id=ws.id, name="Repo A", source_path="/tmp/repo_a")
    repo_b = RepositoryModel(workspace_id=ws.id, name="Repo B", source_path="/tmp/repo_b")
    isolated_db.add_all([repo_a, repo_b])
    isolated_db.commit()

    func_a = _create_node(isolated_db, repo_a.id, "func_a", "math.py")
    test_a = _create_node(isolated_db, repo_a.id, "test_func_a", "test_math.py", node_type="TEST")

    # Add TESTS edge in Repo A
    edge_a = GraphEdgeModel(
        id=f"{test_a.id}:TESTS:{func_a.id}",
        repository_id=repo_a.id,
        source_id=test_a.id,
        target_id=func_a.id,
        edge_type="TESTS",
        file_path="test_math.py",
        confidence=1.0,
    )
    isolated_db.add(edge_a)
    isolated_db.commit()

    engine = GraphQueryEngine(isolated_db)

    # Tests for func_a
    tests_found = engine.find_related_tests(func_a.id)
    assert len(tests_found) == 1
    assert tests_found[0].id == test_a.id
    assert tests_found[0].repository_id == repo_a.id

    # Non-existent node returns empty list
    assert engine.find_related_tests("non_existent_node") == []

    # Graph neighborhood for func_a
    neighborhood = engine.get_graph_neighborhood(func_a.id, radius=1)
    assert len(neighborhood["nodes"]) == 2  # func_a and test_a
    assert all(n["id"].startswith(repo_a.id) for n in neighborhood["nodes"])
    assert len(neighborhood["edges"]) == 1
    assert neighborhood["edges"][0]["source"] == test_a.id

    # Non-existent node returns empty neighborhood structure
    empty_neigh = engine.get_graph_neighborhood("non_existent_node")
    assert empty_neigh == {"nodes": [], "edges": []}


# ============================================================================
# 4. Empty Repository Indexing & Queries
# ============================================================================

def test_empty_repository_indexing_and_queries(isolated_db, tmp_path: Path):
    """P2: Indexing an empty directory succeeds and all query methods handle it cleanly."""
    empty_dir = tmp_path / "empty_repo"
    empty_dir.mkdir()

    ws = WorkspaceModel(name="Empty WS")
    isolated_db.add(ws)
    isolated_db.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="Empty Repo",
        source_type="local",
        source_path=str(empty_dir),
    )
    isolated_db.add(repo)
    isolated_db.commit()

    indexer = RepositoryIndexer(isolated_db)
    indexed_repo = indexer.index_repository(repo.id)

    # Status must be ready
    assert indexed_repo.indexing_status == "ready"
    assert indexed_repo.stats["total_files"] == 0
    assert indexed_repo.stats["total_nodes"] == 0
    assert indexed_repo.stats["total_edges"] == 0
    assert indexed_repo.stats["total_lines"] == 0
    assert indexed_repo.error_message is None

    # Query engine on empty repository
    engine = GraphQueryEngine(isolated_db)

    assert engine.search_code("anything", repo_id=indexed_repo.id) == []
    assert engine.find_symbol("anything", repo_id=indexed_repo.id) == []
    assert engine.detect_import_cycles(repo_id=indexed_repo.id) == []
    assert engine.find_related_tests(f"{indexed_repo.id}:any") == []
    assert engine.get_symbol_details(f"{indexed_repo.id}:any") is None
    assert engine.get_callers(f"{indexed_repo.id}:any") == []
    assert engine.get_callees(f"{indexed_repo.id}:any") == []
    assert engine.get_dependencies(f"{indexed_repo.id}:any") == {
        "internal_dependencies": [],
        "external_dependencies": [],
    }
    assert engine.get_graph_neighborhood(f"{indexed_repo.id}:any") == {"nodes": [], "edges": []}

    with pytest.raises(ValueError, match=f"not found in repository {indexed_repo.id}"):
        engine.analyze_change_impact("anything", repo_id=indexed_repo.id)


# ============================================================================
# 5. Zero-Byte Files & Non-UTF8 Toleration
# ============================================================================

def test_zero_byte_and_empty_file_indexing(isolated_db, tmp_path: Path):
    """P2: Zero-byte files (.py, .ts) are indexed as FILE nodes with valid line counts and 0 size."""
    repo_dir = tmp_path / "zero_byte_repo"
    repo_dir.mkdir()

    # Empty files
    (repo_dir / "empty.py").write_text("", encoding="utf-8")
    (repo_dir / "empty.ts").write_text("", encoding="utf-8")
    # File with content
    (repo_dir / "active.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    ws = WorkspaceModel(name="ZeroByte WS")
    isolated_db.add(ws)
    isolated_db.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="ZeroByte Repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    isolated_db.add(repo)
    isolated_db.commit()

    indexer = RepositoryIndexer(isolated_db)
    indexed_repo = indexer.index_repository(repo.id)

    assert indexed_repo.indexing_status == "ready"
    assert indexed_repo.stats["total_files"] == 3

    engine = GraphQueryEngine(isolated_db)

    # Empty file nodes must exist with start_line=1 and end_line=1
    py_empty = engine.search_code("empty.py", repo_id=indexed_repo.id)
    assert len(py_empty) >= 1
    assert py_empty[0].node.node_type == "FILE"
    assert py_empty[0].node.start_line == 1
    assert py_empty[0].node.end_line == 1
    assert py_empty[0].node.node_metadata.get("size_bytes") == 0

    ts_empty = engine.search_code("empty.ts", repo_id=indexed_repo.id)
    assert len(ts_empty) >= 1
    assert ts_empty[0].node.node_type == "FILE"
    assert ts_empty[0].node.start_line == 1
    assert ts_empty[0].node.end_line == 1

    # Active file has function hello
    active_search = engine.search_code("hello", repo_id=indexed_repo.id)
    assert len(active_search) >= 1
    assert active_search[0].node.name == "hello"


# ============================================================================
# 6. Re-indexing Cleanup of Stale Deleted Files
# ============================================================================

def test_reindex_purges_stale_deleted_file_nodes_and_edges(isolated_db, tmp_path: Path):
    """P2: Deleting a file from disk and re-indexing completely purges old nodes and edges."""
    repo_dir = tmp_path / "reindex_repo"
    repo_dir.mkdir()

    file_b = repo_dir / "helper.py"
    file_b.write_text("def help_func():\n    return 'help'\n", encoding="utf-8")

    file_a = repo_dir / "main.py"
    file_a.write_text(
        "from helper import help_func\n"
        "def main_func():\n"
        "    return help_func()\n",
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Reindex WS")
    isolated_db.add(ws)
    isolated_db.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="Reindex Repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    isolated_db.add(repo)
    isolated_db.commit()

    indexer = RepositoryIndexer(isolated_db)
    # 1. First index
    indexed_repo = indexer.index_repository(repo.id)
    assert indexed_repo.stats["total_files"] == 2

    # Verify both files and nodes exist
    nodes_v1 = isolated_db.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    node_names_v1 = {n.name for n in nodes_v1}
    assert "main.py" in node_names_v1
    assert "helper.py" in node_names_v1
    assert "help_func" in node_names_v1
    assert "main_func" in node_names_v1

    # 2. Delete helper.py from disk and rewrite main.py without the import
    file_b.unlink()
    file_a.write_text("def main_func():\n    return 'standalone'\n", encoding="utf-8")

    # 3. Re-index repository
    indexed_repo_v2 = indexer.index_repository(repo.id)
    assert indexed_repo_v2.indexing_status == "ready"
    assert indexed_repo_v2.stats["total_files"] == 1

    # Verify helper.py and help_func were completely removed
    nodes_v2 = isolated_db.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    node_names_v2 = {n.name for n in nodes_v2}
    assert "main.py" in node_names_v2
    assert "main_func" in node_names_v2
    assert "helper.py" not in node_names_v2
    assert "help_func" not in node_names_v2

    # Verify zero dangling edges exist pointing to helper.py or help_func
    edges_v2 = isolated_db.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    all_node_ids_v2 = {n.id for n in nodes_v2}
    for e in edges_v2:
        assert e.source_id in all_node_ids_v2
        if not e.target_id.startswith("ext:"):
            assert e.target_id in all_node_ids_v2


# ============================================================================
# 7. Input Handling: Whitespace Stripping & Deterministic Limits
# ============================================================================

def test_find_symbol_input_whitespace_resilience(isolated_db):
    """P3: find_symbol strips leading and trailing whitespace from query names."""
    ws = WorkspaceModel(name="WS")
    isolated_db.add(ws)
    isolated_db.commit()
    repo = RepositoryModel(workspace_id=ws.id, name="R", source_path="/tmp")
    isolated_db.add(repo)
    isolated_db.commit()

    _create_node(isolated_db, repo.id, "calculate_tax", "tax.py")

    engine = GraphQueryEngine(isolated_db)

    # Whitespace-padded input must find the symbol
    assert len(engine.find_symbol("  calculate_tax  ", repo_id=repo.id)) == 1
    assert len(engine.find_symbol("\tcalculate_tax\n", repo_id=repo.id)) == 1

    # Empty or whitespace-only inputs must return []
    assert engine.find_symbol("", repo_id=repo.id) == []
    assert engine.find_symbol("   ", repo_id=repo.id) == []


def test_search_code_deterministic_zero_and_negative_limits(isolated_db):
    """P3: search_code handles zero and negative limits deterministically by returning empty list."""
    ws = WorkspaceModel(name="WS")
    isolated_db.add(ws)
    isolated_db.commit()
    repo = RepositoryModel(workspace_id=ws.id, name="R", source_path="/tmp")
    isolated_db.add(repo)
    isolated_db.commit()

    _create_node(isolated_db, repo.id, "parse_csv", "parser.py")
    _create_node(isolated_db, repo.id, "parse_csv_header", "parser.py")

    engine = GraphQueryEngine(isolated_db)

    # Limit of 0 must return 0 results
    assert engine.search_code("parse_csv", repo_id=repo.id, limit=0) == []

    # Negative limits must return empty list (not negative-slice indexing)
    assert engine.search_code("parse_csv", repo_id=repo.id, limit=-1) == []
    assert engine.search_code("parse_csv", repo_id=repo.id, limit=-10) == []
