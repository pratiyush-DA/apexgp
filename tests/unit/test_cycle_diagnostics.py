"""Unit Tests for GraphQueryEngine Import Cycle Diagnostics and MCP Tool
"""

import json
import os
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
from packages.mcp.server import detect_import_cycles, mcp_server

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_repo")


@pytest.fixture
def db_session():
    """Provides an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _create_repo(db, name="Test Repo") -> RepositoryModel:
    ws = WorkspaceModel(name=f"Workspace for {name}")
    db.add(ws)
    db.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name=name,
        source_type="local",
        source_path="/tmp/fake_path",
    )
    db.add(repo)
    db.commit()
    return repo


def _add_file_node(db, repo_id: str, rel_path: str):
    file_id = f"{repo_id}:file:{rel_path}"
    node = GraphNodeModel(
        id=file_id,
        repository_id=repo_id,
        node_type="FILE",
        name=os.path.basename(rel_path),
        qualified_name=rel_path,
        file_path=rel_path,
        start_line=1,
        end_line=50,
        start_col=0,
        end_col=0,
        language="python",
        signature=f"file {os.path.basename(rel_path)}",
        docstring="",
        node_metadata={},
    )
    db.add(node)
    db.commit()
    return node


def _add_import_edge(db, repo_id: str, src_path: str, tgt_path: str, edge_id: str = None):
    src_id = f"{repo_id}:file:{src_path}"
    tgt_id = tgt_path if tgt_path.startswith("ext:") else f"{repo_id}:file:{tgt_path}"
    eid = edge_id or f"{src_id}:IMPORTS:{tgt_id}"
    edge = GraphEdgeModel(
        id=eid,
        repository_id=repo_id,
        source_id=src_id,
        target_id=tgt_id,
        edge_type="IMPORTS",
        confidence=1.0,
        file_path=src_path,
        line_number=1,
        evidence=f"import {tgt_path}",
    )
    db.add(edge)
    db.commit()
    return edge


def test_detect_cycle_simple_two_node(db_session):
    """1. Two-file cycle: A -> B and B -> A."""
    repo = _create_repo(db_session)
    _add_file_node(db_session, repo.id, "module_a.py")
    _add_file_node(db_session, repo.id, "module_b.py")

    _add_import_edge(db_session, repo.id, "module_a.py", "module_b.py")
    _add_import_edge(db_session, repo.id, "module_b.py", "module_a.py")

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert len(cycles) == 1
    assert cycles[0] == ["module_a.py", "module_b.py"]


def test_detect_cycle_multi_node_three_files(db_session):
    """2. Three-file cycle: A -> B -> C -> A."""
    repo = _create_repo(db_session)
    _add_file_node(db_session, repo.id, "a.py")
    _add_file_node(db_session, repo.id, "b.py")
    _add_file_node(db_session, repo.id, "c.py")

    _add_import_edge(db_session, repo.id, "a.py", "b.py")
    _add_import_edge(db_session, repo.id, "b.py", "c.py")
    _add_import_edge(db_session, repo.id, "c.py", "a.py")

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert len(cycles) == 1
    assert cycles[0] == ["a.py", "b.py", "c.py"]


def test_detect_cycle_self_loop(db_session):
    """3. Self-loop: A -> A."""
    repo = _create_repo(db_session)
    _add_file_node(db_session, repo.id, "recursive.py")

    _add_import_edge(db_session, repo.id, "recursive.py", "recursive.py")

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert len(cycles) == 1
    assert cycles[0] == ["recursive.py"]


def test_detect_cycle_acyclic_diamond_dag(db_session):
    """4. Acyclic diamond DAG: A -> B, A -> C, B -> D, C -> D."""
    repo = _create_repo(db_session)
    for name in ["root.py", "left.py", "right.py", "leaf.py"]:
        _add_file_node(db_session, repo.id, name)

    _add_import_edge(db_session, repo.id, "root.py", "left.py")
    _add_import_edge(db_session, repo.id, "root.py", "right.py")
    _add_import_edge(db_session, repo.id, "left.py", "leaf.py")
    _add_import_edge(db_session, repo.id, "right.py", "leaf.py")

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert cycles == []


def test_detect_cycle_external_stubs_excluded(db_session):
    """5. External stubs (ext:*) must be excluded from internal cycle detection."""
    repo = _create_repo(db_session)
    _add_file_node(db_session, repo.id, "app.py")
    _add_file_node(db_session, repo.id, "util.py")

    # Both import external python sys and react
    _add_import_edge(db_session, repo.id, "app.py", "ext:py:sys", edge_id="edge_sys_1")
    _add_import_edge(db_session, repo.id, "util.py", "ext:py:sys", edge_id="edge_sys_2")
    _add_import_edge(db_session, repo.id, "app.py", "ext:npm:react", edge_id="edge_react")

    # app imports util, but util does not import app (acyclic internal)
    _add_import_edge(db_session, repo.id, "app.py", "util.py", edge_id="edge_internal")

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert cycles == []


def test_detect_cycle_duplicate_import_edges(db_session):
    """6. Duplicate import edges between same files must not duplicate results."""
    repo = _create_repo(db_session)
    _add_file_node(db_session, repo.id, "x.py")
    _add_file_node(db_session, repo.id, "y.py")

    # Multiple import statements between x.py and y.py
    _add_import_edge(db_session, repo.id, "x.py", "y.py", edge_id="edge_x_y_1")
    _add_import_edge(db_session, repo.id, "x.py", "y.py", edge_id="edge_x_y_2")
    _add_import_edge(db_session, repo.id, "y.py", "x.py", edge_id="edge_y_x_1")
    _add_import_edge(db_session, repo.id, "y.py", "x.py", edge_id="edge_y_x_2")

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert len(cycles) == 1
    assert cycles[0] == ["x.py", "y.py"]


def test_detect_cycle_repository_isolation(db_session):
    """7. Repository isolation: edges in Repo 2 cannot create a cycle in Repo 1."""
    repo1 = _create_repo(db_session, name="Repo 1")
    repo2 = _create_repo(db_session, name="Repo 2")

    # Repo 1 has A -> B
    _add_file_node(db_session, repo1.id, "shared.py")
    _add_file_node(db_session, repo1.id, "other.py")
    _add_import_edge(db_session, repo1.id, "shared.py", "other.py")

    # Repo 2 has other.py -> shared.py (reverse edge, but in Repo 2)
    _add_file_node(db_session, repo2.id, "shared.py")
    _add_file_node(db_session, repo2.id, "other.py")
    _add_import_edge(db_session, repo2.id, "other.py", "shared.py")

    engine = GraphQueryEngine(db_session)
    # Neither repository on its own contains a cycle
    assert engine.detect_import_cycles(repo1.id) == []
    assert engine.detect_import_cycles(repo2.id) == []


def test_detect_cycle_deterministic_ordering(db_session):
    """8. Deterministic ordering: cyclic components and paths are deterministically sorted."""
    repo = _create_repo(db_session)
    # Create two separate cycles: (zeta <-> alpha) and (beta <-> gamma)
    for f in ["zeta.py", "alpha.py", "gamma.py", "beta.py"]:
        _add_file_node(db_session, repo.id, f)

    # Insert in reverse alphabetical order
    _add_import_edge(db_session, repo.id, "zeta.py", "alpha.py")
    _add_import_edge(db_session, repo.id, "alpha.py", "zeta.py")
    _add_import_edge(db_session, repo.id, "gamma.py", "beta.py")
    _add_import_edge(db_session, repo.id, "beta.py", "gamma.py")

    engine = GraphQueryEngine(db_session)
    cycles_1 = engine.detect_import_cycles(repo.id)
    cycles_2 = engine.detect_import_cycles(repo.id)

    # Both cycles must match identically and be sorted by first element
    expected = [
        ["alpha.py", "zeta.py"],
        ["beta.py", "gamma.py"],
    ]
    assert cycles_1 == expected
    assert cycles_2 == expected


def test_detect_cycle_on_sample_repo(db_session):
    """9. Existing sample repository fixture contains no circular dependencies."""
    ws = WorkspaceModel(name="Sample Workspace")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="Sample Repo",
        source_type="local",
        source_path=FIXTURES_DIR,
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    # The verified ground truth of tests/fixtures/sample_repo is an acyclic DAG
    assert cycles == []


def test_mcp_detect_import_cycles_tool(db_session):
    """10. MCP tool registration and JSON formatting for detect_import_cycles."""
    import asyncio
    from contextlib import contextmanager
    from unittest.mock import patch

    # A. Verify tool is registered on the MCPServer instance
    tools = asyncio.run(mcp_server.list_tools())
    tool_names = [t.name for t in tools]
    assert "detect_import_cycles" in tool_names

    repo = _create_repo(db_session)
    _add_file_node(db_session, repo.id, "mod1.py")
    _add_file_node(db_session, repo.id, "mod2.py")
    _add_import_edge(db_session, repo.id, "mod1.py", "mod2.py")
    _add_import_edge(db_session, repo.id, "mod2.py", "mod1.py")

    @contextmanager
    def mock_session():
        yield db_session

    with patch("packages.mcp.server.get_db_session", mock_session):
        # B. Execute tool on repo with cycle
        res_raw = detect_import_cycles(repo.id)
        res = json.loads(res_raw)

        assert "repository_id" in res
        assert res["repository_id"] == repo.id
        assert res["total_cyclic_components"] == 1
        assert res["cyclic_components"] == [["mod1.py", "mod2.py"]]

        # C. Execute tool on non-existent repo (error response)
        err_res = json.loads(detect_import_cycles("non-existent-uuid"))
        assert "error" in err_res
        assert "not found" in err_res["error"]

        # D. Execute tool on acyclic repo
        clean_repo = _create_repo(db_session, name="Clean Repo")
        clean_res = json.loads(detect_import_cycles(clean_repo.id))
        assert clean_res["repository_id"] == clean_repo.id
        assert clean_res["total_cyclic_components"] == 0
        assert clean_res["cyclic_components"] == []
