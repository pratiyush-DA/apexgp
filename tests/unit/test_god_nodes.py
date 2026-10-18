"""Unit Tests for God-Node Centrality Analysis and MCP Tool
"""

import json
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.core.models import (
    Base,
    GraphEdgeModel,
    GraphNodeModel,
    RepositoryModel,
    WorkspaceModel,
)
from packages.core.query_engine import GraphQueryEngine
from packages.mcp.server import god_nodes, mcp_server


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


def _create_node(
    db,
    repo_id: str,
    name: str,
    node_type: str = "FUNCTION",
    file_path: str = "main.py",
    start_line: int = 1,
    end_line: int = 10,
    node_id: str = None,
) -> GraphNodeModel:
    nid = node_id or f"{repo_id}:{file_path}:{name}:{uuid.uuid4().hex[:6]}"
    node = GraphNodeModel(
        id=nid,
        repository_id=repo_id,
        node_type=node_type,
        name=name,
        qualified_name=f"{file_path}:{name}",
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        start_col=0,
        end_col=0,
        language="python",
        signature=f"def {name}()",
        docstring="",
        node_metadata={},
    )
    db.add(node)
    db.commit()
    return node


def _create_edge(
    db,
    repo_id: str,
    source_id: str,
    target_id: str,
    edge_type: str = "CALLS",
    line_number: int = 1,
    evidence: str = "",
    edge_id: str = None,
) -> GraphEdgeModel:
    eid = edge_id or f"{source_id}:{edge_type}:{target_id}:{uuid.uuid4().hex[:6]}"
    edge = GraphEdgeModel(
        id=eid,
        repository_id=repo_id,
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        confidence=1.0,
        file_path="main.py",
        line_number=line_number,
        evidence=evidence,
    )
    db.add(edge)
    db.commit()
    return edge


def test_god_nodes_score_calculation_known_graph(db_session):
    """Verifies exact formula calculation on a mathematically consistent known graph.

    Graph topology:
    - file_hub defines fn_hub, fn_helper
    - fn_caller1 calls fn_hub
    - fn_caller2 calls fn_hub
    - fn_hub calls fn_helper

    Expected degrees & scores:
    - fn_hub: in_degree=3 (file_hub [DEFINES], fn_caller1 [CALLS], fn_caller2 [CALLS])
              incoming_calls=2 (fn_caller1, fn_caller2)
              out_degree=1 (fn_helper [CALLS])
              score = 2*3 + 3*2 + 1*1 = 6 + 6 + 1 = 13.0
    - fn_helper: in_degree=2 (file_hub [DEFINES], fn_hub [CALLS])
                 incoming_calls=1 (fn_hub)
                 out_degree=0
                 score = 2*2 + 3*1 + 1*0 = 4 + 3 + 0 = 7.0
    - fn_caller1: in_degree=0, incoming_calls=0, out_degree=1 (fn_hub)
                  score = 2*0 + 3*0 + 1*1 = 1.0
    - fn_caller2: in_degree=0, incoming_calls=0, out_degree=1 (fn_hub)
                  score = 2*0 + 3*0 + 1*1 = 1.0
    - file_hub: in_degree=0, incoming_calls=0, out_degree=2 (fn_hub, fn_helper)
                score = 2*0 + 3*0 + 1*2 = 2.0
    """
    repo = _create_repo(db_session, "Known Graph Repo")
    file_hub = _create_node(db_session, repo.id, "hub.py", node_type="FILE", file_path="hub.py")
    fn_hub = _create_node(db_session, repo.id, "fn_hub", node_type="FUNCTION", file_path="hub.py", start_line=5)
    fn_helper = _create_node(db_session, repo.id, "fn_helper", node_type="FUNCTION", file_path="hub.py", start_line=20)
    fn_caller1 = _create_node(db_session, repo.id, "fn_caller1", node_type="FUNCTION", file_path="caller.py", start_line=10)
    fn_caller2 = _create_node(db_session, repo.id, "fn_caller2", node_type="FUNCTION", file_path="caller.py", start_line=25)

    _create_edge(db_session, repo.id, file_hub.id, fn_hub.id, "DEFINES")
    _create_edge(db_session, repo.id, file_hub.id, fn_helper.id, "DEFINES")
    _create_edge(db_session, repo.id, fn_caller1.id, fn_hub.id, "CALLS")
    _create_edge(db_session, repo.id, fn_caller2.id, fn_hub.id, "CALLS")
    _create_edge(db_session, repo.id, fn_hub.id, fn_helper.id, "CALLS")

    engine = GraphQueryEngine(db_session)
    results = engine.get_god_nodes(repo.id, limit=10, scope="symbol")

    assert len(results) == 4
    top = results[0]
    assert top.node.name == "fn_hub"
    assert top.in_degree == 3
    assert top.incoming_calls == 2
    assert top.out_degree == 1
    assert top.god_score == 13.0

    second = results[1]
    assert second.node.name == "fn_helper"
    assert second.in_degree == 2
    assert second.incoming_calls == 1
    assert second.out_degree == 0
    assert second.god_score == 7.0

    third = results[2]
    fourth = results[3]
    assert third.god_score == 1.0
    assert fourth.god_score == 1.0
    assert {third.node.name, fourth.node.name} == {"fn_caller1", "fn_caller2"}


def test_god_nodes_incoming_calls_weighting(db_session):
    """Verifies that incoming CALLS edges receive 5 total points (2 from in_degree + 3 bonus).

    func_calls receives 2 incoming CALLS -> in_degree=2, incoming_calls=2, score=2*2 + 3*2 = 10.0
    func_refs receives 2 incoming REFERENCES -> in_degree=2, incoming_calls=0, score=2*2 + 0 = 4.0
    """
    repo = _create_repo(db_session, "Call Weighting Repo")
    target_calls = _create_node(db_session, repo.id, "target_calls", node_type="FUNCTION", file_path="mod.py")
    target_refs = _create_node(db_session, repo.id, "target_refs", node_type="FUNCTION", file_path="mod.py")
    caller_a = _create_node(db_session, repo.id, "caller_a", node_type="FUNCTION", file_path="c.py")
    caller_b = _create_node(db_session, repo.id, "caller_b", node_type="FUNCTION", file_path="c.py")

    _create_edge(db_session, repo.id, caller_a.id, target_calls.id, "CALLS")
    _create_edge(db_session, repo.id, caller_b.id, target_calls.id, "CALLS")
    _create_edge(db_session, repo.id, caller_a.id, target_refs.id, "REFERENCES")
    _create_edge(db_session, repo.id, caller_b.id, target_refs.id, "REFERENCES")

    engine = GraphQueryEngine(db_session)
    results = engine.get_god_nodes(repo.id, limit=10, scope="symbol")

    # target_calls (10.0) must strictly outrank target_refs (4.0)
    assert len(results) >= 2
    assert results[0].node.name == "target_calls"
    assert results[0].god_score == 10.0
    assert results[0].incoming_calls == 2
    assert results[0].in_degree == 2

    assert results[1].node.name == "target_refs"
    assert results[1].god_score == 4.0
    assert results[1].incoming_calls == 0
    assert results[1].in_degree == 2


def test_god_nodes_scope_filtering(db_session):
    """Verifies scope filtering for 'all', 'file', and 'symbol'."""
    repo = _create_repo(db_session, "Scope Repo")
    file_a = _create_node(db_session, repo.id, "a.py", node_type="FILE", file_path="a.py")
    file_b = _create_node(db_session, repo.id, "b.py", node_type="FILE", file_path="b.py")
    fn_a = _create_node(db_session, repo.id, "func_a", node_type="FUNCTION", file_path="a.py")
    fn_b = _create_node(db_session, repo.id, "func_b", node_type="FUNCTION", file_path="b.py")

    _create_edge(db_session, repo.id, file_a.id, file_b.id, "IMPORTS")
    _create_edge(db_session, repo.id, fn_a.id, fn_b.id, "CALLS")

    engine = GraphQueryEngine(db_session)

    # 1. Scope 'file'
    file_results = engine.get_god_nodes(repo.id, limit=10, scope="file")
    assert len(file_results) == 2
    assert all(r.node.node_type == "FILE" for r in file_results)
    assert file_results[0].node.name == "b.py"  # in_degree=1, score=2.0
    assert file_results[1].node.name == "a.py"  # out_degree=1, score=1.0

    # 2. Scope 'symbol'
    sym_results = engine.get_god_nodes(repo.id, limit=10, scope="symbol")
    assert len(sym_results) == 2
    assert all(r.node.node_type != "FILE" for r in sym_results)
    assert sym_results[0].node.name == "func_b"  # in_degree=1, incoming_calls=1, score=5.0
    assert sym_results[1].node.name == "func_a"  # out_degree=1, score=1.0

    # 3. Scope 'all'
    all_results = engine.get_god_nodes(repo.id, limit=10, scope="all")
    assert len(all_results) == 4
    names = [r.node.name for r in all_results]
    assert names[0] == "func_b"  # 5.0
    assert names[1] == "b.py"    # 2.0


def test_god_nodes_repository_isolation(db_session):
    """Verifies that edges and nodes never leak across repository boundaries,
    even when cross-repository or foreign-repository edges are present in the table.
    """
    repo1 = _create_repo(db_session, "Repo 1")
    repo2 = _create_repo(db_session, "Repo 2")

    node_1a = _create_node(db_session, repo1.id, "node_1a", node_type="FUNCTION", file_path="mod1.py")
    node_1b = _create_node(db_session, repo1.id, "node_1b", node_type="FUNCTION", file_path="mod1.py")
    _create_edge(db_session, repo1.id, node_1a.id, node_1b.id, "CALLS")

    node_2a = _create_node(db_session, repo2.id, "node_2a", node_type="FUNCTION", file_path="mod2.py")
    node_2b = _create_node(db_session, repo2.id, "node_2b", node_type="FUNCTION", file_path="mod2.py")
    _create_edge(db_session, repo2.id, node_2a.id, node_2b.id, "CALLS")

    # Malformed cross-repository edges
    # Edge with repo1 ID but targeting a node in repo2
    _create_edge(db_session, repo1.id, node_1a.id, node_2a.id, "CALLS")
    # Edge with repo2 ID targeting a node in repo1
    _create_edge(db_session, repo2.id, node_2a.id, node_1b.id, "CALLS")

    engine = GraphQueryEngine(db_session)
    results_1 = engine.get_god_nodes(repo1.id, limit=10, scope="all")

    # Results for Repo 1 must contain only Repo 1 nodes
    assert all(r.node.repository_id == repo1.id for r in results_1)
    res_1b = [r for r in results_1 if r.node.name == "node_1b"][0]
    res_1a = [r for r in results_1 if r.node.name == "node_1a"][0]

    # node_1b in_degree must be exactly 1 (cross-repo edge from repo2 is ignored)
    assert res_1b.in_degree == 1
    assert res_1b.incoming_calls == 1
    assert res_1b.god_score == 5.0

    # node_1a out_degree must be exactly 1 (cross-repo edge to node_2a is ignored)
    assert res_1a.out_degree == 1
    assert res_1a.god_score == 1.0


def test_god_nodes_external_stub_exclusion(db_session):
    """Verifies that external dependency stubs (target_id starting with 'ext:')
    are completely excluded from candidate nodes and do not inflate internal out-degree.
    """
    repo = _create_repo(db_session, "External Stubs Repo")
    node_internal_a = _create_node(db_session, repo.id, "caller_fn", node_type="FUNCTION", file_path="app.py")
    node_internal_b = _create_node(db_session, repo.id, "callee_fn", node_type="FUNCTION", file_path="app.py")

    # Internal edge
    _create_edge(db_session, repo.id, node_internal_a.id, node_internal_b.id, "CALLS")

    # Outgoing edges to external dependency stubs
    _create_edge(db_session, repo.id, node_internal_a.id, "ext:python:json:loads", "CALLS")
    _create_edge(db_session, repo.id, node_internal_a.id, "ext:npm:react", "IMPORTS")

    engine = GraphQueryEngine(db_session)
    results = engine.get_god_nodes(repo.id, limit=10, scope="all")

    # External stubs must not appear
    assert not any(r.node.id.startswith("ext:") for r in results)
    # caller_fn out_degree must be 1 (only internal edge to callee_fn), NOT 3
    caller_item = [r for r in results if r.node.name == "caller_fn"][0]
    assert caller_item.out_degree == 1
    assert caller_item.god_score == 1.0


def test_god_nodes_duplicate_edge_handling(db_session):
    """Verifies that duplicate stored edges of the same type are deduplicated,
    while distinct relationship types between the same endpoints are preserved.
    """
    repo = _create_repo(db_session, "Deduplication Repo")
    node_a = _create_node(db_session, repo.id, "source_fn", node_type="FUNCTION", file_path="lib.py")
    node_b = _create_node(db_session, repo.id, "target_fn", node_type="FUNCTION", file_path="lib.py")

    # Two duplicate CALLS edges between node_a and node_b
    _create_edge(db_session, repo.id, node_a.id, node_b.id, "CALLS", line_number=10)
    _create_edge(db_session, repo.id, node_a.id, node_b.id, "CALLS", line_number=20)

    # One REFERENCES edge between node_a and node_b (distinct relationship type)
    _create_edge(db_session, repo.id, node_a.id, node_b.id, "REFERENCES", line_number=5)

    engine = GraphQueryEngine(db_session)
    results = engine.get_god_nodes(repo.id, limit=10, scope="all")

    target_item = [r for r in results if r.node.name == "target_fn"][0]
    # in_degree = 2 (1 distinct CALLS + 1 distinct REFERENCES)
    assert target_item.in_degree == 2
    # incoming_calls = 1 (the 2 CALLS edges collapsed to 1 distinct call relationship)
    assert target_item.incoming_calls == 1
    assert target_item.out_degree == 0
    # score = 2*2 + 3*1 + 0 = 7.0
    assert target_item.god_score == 7.0

    source_item = [r for r in results if r.node.name == "source_fn"][0]
    # out_degree = 2 (1 CALLS + 1 REFERENCES)
    assert source_item.out_degree == 2
    assert source_item.god_score == 2.0


def test_god_nodes_empty_repository_and_isolated_nodes(db_session):
    """Verifies that empty repositories and repositories with only isolated nodes
    return empty lists without error.
    """
    # 1. Completely empty repository
    empty_repo = _create_repo(db_session, "Completely Empty Repo")
    engine = GraphQueryEngine(db_session)
    assert engine.get_god_nodes(empty_repo.id, limit=10, scope="all") == []

    # 2. Repository with nodes but zero edges (all isolated)
    isolated_repo = _create_repo(db_session, "Isolated Nodes Repo")
    _create_node(db_session, isolated_repo.id, "iso1", node_type="FUNCTION", file_path="iso.py")
    _create_node(db_session, isolated_repo.id, "iso2", node_type="CLASS", file_path="iso.py")
    _create_node(db_session, isolated_repo.id, "iso.py", node_type="FILE", file_path="iso.py")

    assert engine.get_god_nodes(isolated_repo.id, limit=10, scope="all") == []


def test_god_nodes_limits(db_session):
    """Verifies limit handling: positive limits, zero limits, and negative limits."""
    repo = _create_repo(db_session, "Limits Repo")
    nodes = [_create_node(db_session, repo.id, f"fn_{i}", node_type="FUNCTION", file_path="mod.py") for i in range(5)]
    for i in range(4):
        _create_edge(db_session, repo.id, nodes[i].id, nodes[i + 1].id, "CALLS")

    engine = GraphQueryEngine(db_session)

    # Positive limit smaller than count
    res_2 = engine.get_god_nodes(repo.id, limit=2, scope="all")
    assert len(res_2) == 2

    # Zero limit
    assert engine.get_god_nodes(repo.id, limit=0, scope="all") == []

    # Negative limits
    assert engine.get_god_nodes(repo.id, limit=-1, scope="all") == []
    assert engine.get_god_nodes(repo.id, limit=-100, scope="all") == []


def test_god_nodes_invalid_scope_and_unknown_repo(db_session):
    """Verifies validation errors for invalid scope and non-existent repository."""
    repo = _create_repo(db_session, "Validation Repo")
    engine = GraphQueryEngine(db_session)

    with pytest.raises(ValueError, match="Invalid scope 'invalid_scope'"):
        engine.get_god_nodes(repo.id, scope="invalid_scope")

    with pytest.raises(ValueError, match="Repository 'nonexistent-uuid' not found"):
        engine.get_god_nodes("nonexistent-uuid", scope="all")


def test_god_nodes_deterministic_tie_breaking(db_session):
    """Verifies that ties in score, incoming calls, and in-degree are deterministically broken
    by: file path ascending, start line ascending, name ascending, node ID ascending.
    """
    repo = _create_repo(db_session, "Tie Breaking Repo")
    caller = _create_node(db_session, repo.id, "shared_caller", node_type="FUNCTION", file_path="main.py")

    # Three candidate nodes with identical in_degree=1, incoming_calls=1, out_degree=0, score=5.0
    # Node X: file_path="src/b.py", start_line=20, name="process"
    node_x = _create_node(db_session, repo.id, "process", node_type="FUNCTION", file_path="src/b.py", start_line=20)
    # Node Y: file_path="src/a.py", start_line=10, name="format"
    node_y = _create_node(db_session, repo.id, "format", node_type="FUNCTION", file_path="src/a.py", start_line=10)
    # Node Z: file_path="src/a.py", start_line=50, name="calculate"
    node_z = _create_node(db_session, repo.id, "calculate", node_type="FUNCTION", file_path="src/a.py", start_line=50)

    _create_edge(db_session, repo.id, caller.id, node_x.id, "CALLS")
    _create_edge(db_session, repo.id, caller.id, node_y.id, "CALLS")
    _create_edge(db_session, repo.id, caller.id, node_z.id, "CALLS")

    engine = GraphQueryEngine(db_session)
    results = engine.get_god_nodes(repo.id, limit=3, scope="symbol")

    # Expected order:
    # 1. "src/a.py" line 10 ("format") -> node_y
    # 2. "src/a.py" line 50 ("calculate") -> node_z
    # 3. "src/b.py" line 20 ("process") -> node_x
    assert [r.node.name for r in results] == ["format", "calculate", "process"]


def test_mcp_god_nodes_tool(monkeypatch, db_session):
    """Verifies MCP tool registration, valid JSON response, and error handling."""
    # 1. Verify tool registration on mcp_server
    import asyncio
    tools = asyncio.run(mcp_server.list_tools())
    tool_names = [t.name for t in tools]
    assert "god_nodes" in tool_names
    assert len(tool_names) >= 15

    # 2. Verify invocation with valid repo using monkeypatched get_db_session
    repo = _create_repo(db_session, "MCP Repo")
    fn_a = _create_node(db_session, repo.id, "api_handler", node_type="FUNCTION", file_path="api.py")
    fn_b = _create_node(db_session, repo.id, "auth_check", node_type="FUNCTION", file_path="auth.py")
    _create_edge(db_session, repo.id, fn_a.id, fn_b.id, "CALLS")

    class MockContextManager:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("packages.mcp.server.get_db_session", lambda: MockContextManager())

    # Success invocation
    resp_raw = god_nodes(repo_id=repo.id, limit=5, scope="all")
    resp = json.loads(resp_raw)
    assert resp["repository_id"] == repo.id
    assert resp["scope"] == "all"
    assert resp["total_results"] == 2
    assert len(resp["god_nodes"]) == 2
    assert resp["god_nodes"][0]["node"]["name"] == "auth_check"
    assert resp["god_nodes"][0]["god_score"] == 5.0

    # Unknown repo error
    err_resp = json.loads(god_nodes(repo_id="nonexistent-repo-id"))
    assert "error" in err_resp
    assert "Repository 'nonexistent-repo-id' not found" in err_resp["error"]

    # Invalid scope error
    scope_err = json.loads(god_nodes(repo_id=repo.id, scope="invalid"))
    assert "error" in scope_err
    assert "Invalid scope 'invalid'" in scope_err["error"]

    # Limit <= 0 returns clean empty results
    zero_limit = json.loads(god_nodes(repo_id=repo.id, limit=0))
    assert zero_limit["total_results"] == 0
    assert zero_limit["god_nodes"] == []
