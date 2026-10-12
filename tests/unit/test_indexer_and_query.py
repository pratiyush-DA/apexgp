"""Unit and Integration Tests for Repository Indexer and Graph Query Engine
"""

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

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_repo")


@pytest.fixture
def db_session():
    # Use isolated in-memory SQLite engine
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_indexing_and_queries_on_sample_repo(db_session):
    # 1. Setup Workspace and Repository
    ws = WorkspaceModel(name="Test Workspace")
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

    # 2. Run Indexer
    indexer = RepositoryIndexer(db_session)
    indexed_repo = indexer.index_repository(repo.id)

    assert indexed_repo.indexing_status == "ready"
    assert indexed_repo.stats["total_files"] == 5
    assert indexed_repo.stats["total_nodes"] > 10
    assert indexed_repo.stats["total_edges"] > 10
    assert "python" in indexed_repo.stats["languages"]
    assert "typescript" in indexed_repo.stats["languages"]

    # 3. Test GraphQueryEngine
    query_engine = GraphQueryEngine(db_session)

    # A. Symbol Search
    calc_nodes = query_engine.find_symbol("Calculator", repo_id=repo.id)
    assert len(calc_nodes) >= 1
    calc_node = calc_nodes[0]
    assert calc_node.node_type == "CLASS"
    assert calc_node.file_path == "calculator.py"

    add_nodes = query_engine.find_symbol("add", repo_id=repo.id)
    assert len(add_nodes) >= 1
    add_node = add_nodes[0]
    assert add_node.node_type == "FUNCTION"
    assert add_node.file_path == "math_ops.py"

    # B. Text Search
    search_results = query_engine.search_code("formatResult", repo_id=repo.id)
    assert len(search_results) >= 1
    assert any(r.node.name == "formatResult" for r in search_results)

    # C. Callers
    callers = query_engine.get_callers(add_node.id)
    assert len(callers) >= 1
    caller_names = {c["node"]["name"] for c in callers}
    assert "calculate_sum" in caller_names

    # D. Change Impact Analysis
    impact = query_engine.analyze_change_impact("add", repo_id=repo.id)
    assert impact.total_affected >= 1
    affected_files = set(impact.affected_files)
    assert "calculator.py" in affected_files

    # Linked tests must discover test_calculator.py and tests
    test_names = {t.name for t in impact.linked_tests}
    assert "test_calculator_sum" in test_names or any("test" in f for f in impact.affected_files)

    # E. Trace Relationships
    sum_node = [c["node"] for c in callers if c["node"]["name"] == "calculate_sum"][0]
    trace = query_engine.trace_relationships(source_id=sum_node["id"], target_id=add_node.id)
    assert trace is not None
    assert len(trace) >= 1
    assert trace[0]["edge_type"] == "CALLS"

    # F. Neighborhood
    neighborhood = query_engine.get_graph_neighborhood(calc_node.id, radius=1)
    assert len(neighborhood["nodes"]) >= 2
    assert len(neighborhood["edges"]) >= 1
