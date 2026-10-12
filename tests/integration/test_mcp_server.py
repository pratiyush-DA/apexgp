"""Integration Tests for ApexGraph Model Context Protocol (MCP) Server
"""

import json
import os
import pytest

from packages.core.database import Base, get_db_session, get_engine
from packages.core.indexer import RepositoryIndexer
from packages.core.models import RepositoryModel, WorkspaceModel
from packages.mcp.server import (
    analyze_change_impact,
    find_related_tests,
    find_symbol,
    get_callees,
    get_callers,
    get_dependencies,
    get_graph_neighborhood,
    get_repository_status,
    get_symbol_details,
    list_repositories,
    list_workspaces,
    search_code,
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_repo")


@pytest.fixture(scope="module")
def setup_test_data():
    # Use real test database
    engine = get_engine()
    Base.metadata.create_all(bind=engine)

    with get_db_session() as db:
        ws = WorkspaceModel(name="MCP Integration Workspace")
        db.add(ws)
        db.commit()

        repo = RepositoryModel(
            workspace_id=ws.id,
            name="MCP Sample Repo",
            source_type="local",
            source_path=FIXTURES_DIR,
        )
        db.add(repo)
        db.commit()

        indexer = RepositoryIndexer(db)
        indexer.index_repository(repo.id)

        ws_id = ws.id
        repo_id = repo.id

    return {"workspace_id": ws_id, "repository_id": repo_id}


def test_mcp_workspace_and_repo_tools(setup_test_data):
    repo_id = setup_test_data["repository_id"]

    # 1. list_workspaces
    ws_res = json.loads(list_workspaces())
    assert isinstance(ws_res, list)
    assert len(ws_res) >= 1

    # 2. list_repositories
    repos_res = json.loads(list_repositories())
    assert any(r["id"] == repo_id for r in repos_res)

    # 3. get_repository_status
    status_res = json.loads(get_repository_status(repo_id))
    assert status_res["indexing_status"] == "ready"
    assert status_res["stats"]["total_files"] == 5


def test_mcp_code_intelligence_tools(setup_test_data):
    repo_id = setup_test_data["repository_id"]

    # 4. search_code
    search_res = json.loads(search_code(query="Calculator", repo_id=repo_id))
    assert len(search_res) >= 1
    calc_node_id = search_res[0]["node"]["id"]

    # 5. find_symbol
    sym_res = json.loads(find_symbol(name="add", repo_id=repo_id))
    assert len(sym_res) >= 1
    add_node_id = sym_res[0]["id"]
    assert sym_res[0]["file_path"] == "math_ops.py"

    # 6. get_symbol_details
    details_res = json.loads(get_symbol_details(add_node_id))
    assert "node" in details_res
    assert details_res["node"]["name"] == "add"
    assert len(details_res["incoming_edges"]) >= 1

    # 7. get_callers
    callers_res = json.loads(get_callers(add_node_id))
    assert len(callers_res) >= 1
    assert any(c["node"]["name"] == "calculate_sum" for c in callers_res)

    # 8. get_callees
    sum_node = [c["node"] for c in callers_res if c["node"]["name"] == "calculate_sum"][0]
    callees_res = json.loads(get_callees(sum_node["id"]))
    assert any(c["node"]["name"] == "add" for c in callees_res)

    # 9. get_dependencies
    deps_res = json.loads(get_dependencies(calc_node_id))
    assert "internal_dependencies" in deps_res

    # 10. analyze_change_impact
    impact_res = json.loads(analyze_change_impact(target_node_or_file="add", repo_id=repo_id))
    assert impact_res["total_affected"] >= 1
    assert "calculator.py" in impact_res["affected_files"]

    # 11. find_related_tests
    tests_res = json.loads(find_related_tests(calc_node_id))
    assert isinstance(tests_res, list)

    # 12. get_graph_neighborhood
    neigh_res = json.loads(get_graph_neighborhood(calc_node_id, radius=1))
    assert len(neigh_res["nodes"]) >= 2
    assert len(neigh_res["edges"]) >= 1
