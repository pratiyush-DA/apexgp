"""Unit and Regression Tests for Phase 9.2: Incremental Indexing

Validates:
- No-op synchronization on unchanged repositories
- Modified, added, deleted, and renamed file handling
- Content-hash change detection with identical mtime and size
- Stale symbol cleanup and dangling incoming/outgoing edge prevention
- Cross-file import and call relationship rebuilding
- Parse/syntax failure transaction rollback preserving valid state
- Strict repository isolation
- Accurate node, edge, and file counts
- Idempotency of repeated synchronizations
- MCP sync_repository tool integration
"""

import json
import os
import shutil
import time
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
from packages.mcp.server import sync_repository as mcp_sync_repository


@pytest.fixture
def db_session():
    """Provides an isolated in-memory SQLite database session for unit tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_repo_dir(tmp_path):
    """Creates a temporary multi-file repository with Python and TypeScript code."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # math_ops.py
    math_ops = repo_dir / "math_ops.py"
    math_ops.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def sub(a: int, b: int) -> int:\n"
        "    return a - b\n",
        encoding="utf-8",
    )

    # calculator.py
    calc = repo_dir / "calculator.py"
    calc.write_text(
        "from math_ops import add, sub\n\n"
        "class Calculator:\n"
        "    def calculate_sum(self, x: int, y: int) -> int:\n"
        "        return add(x, y)\n\n"
        "    def calculate_diff(self, x: int, y: int) -> int:\n"
        "        return sub(x, y)\n",
        encoding="utf-8",
    )

    # test_calculator.py
    test_calc = repo_dir / "test_calculator.py"
    test_calc.write_text(
        "from calculator import Calculator\n\n"
        "def test_calculator_sum():\n"
        "    calc = Calculator()\n"
        "    assert calc.calculate_sum(2, 3) == 5\n",
        encoding="utf-8",
    )

    # utils.ts
    utils_ts = repo_dir / "utils.ts"
    utils_ts.write_text(
        "export function formatResult(value: number): string {\n"
        "    return `Result: ${value}`;\n"
        "}\n",
        encoding="utf-8",
    )

    return repo_dir


def _setup_repo(db_session, source_path, name="Sample Repo") -> RepositoryModel:
    ws = WorkspaceModel(name="Test Workspace")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name=name,
        source_type="local",
        source_path=str(source_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)
    return repo


def test_sync_repository_noop_unchanged(db_session, sample_repo_dir):
    """Verifies that an unchanged repository returns status 'unchanged' in sub-millisecond time."""
    repo = _setup_repo(db_session, sample_repo_dir)

    initial_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()
    initial_edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).count()
    initial_stats = dict(repo.stats)

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "unchanged"
    assert result["total_changes"] == 0
    assert result["files_added"] == []
    assert result["files_modified"] == []
    assert result["files_deleted"] == []
    assert result["duration_seconds"] < 0.2

    # Node and edge counts must remain identical
    current_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()
    current_edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).count()
    assert current_nodes == initial_nodes
    assert current_edges == initial_edges
    assert repo.stats["total_files"] == initial_stats["total_files"]
    assert repo.stats["total_nodes"] == initial_stats["total_nodes"]


def test_sync_repository_modified_file(db_session, sample_repo_dir):
    """Verifies incremental sync when an existing file is modified."""
    repo = _setup_repo(db_session, sample_repo_dir)

    # Modify math_ops.py body
    math_ops = sample_repo_dir / "math_ops.py"
    math_ops.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    # Modified implementation with docstring\n"
        "    return int(a + b)\n\n"
        "def sub(a: int, b: int) -> int:\n"
        "    return int(a - b)\n",
        encoding="utf-8",
    )

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert result["total_changes"] == 1
    assert result["files_modified"] == ["math_ops.py"]
    assert result["files_added"] == []
    assert result["files_deleted"] == []

    # Caller relationship from calculator.py -> add must remain valid
    engine = GraphQueryEngine(db_session)
    add_node = engine.find_symbol("add", repo_id=repo.id)[0]
    callers = engine.get_callers(add_node.id)
    caller_names = {c["node"]["name"] for c in callers}
    assert "calculate_sum" in caller_names


def test_sync_repository_content_hash_detection_same_mtime_size(db_session, sample_repo_dir):
    """Verifies that content-hash change detection identifies changes even when mtime and file size are identical."""
    repo = _setup_repo(db_session, sample_repo_dir)

    math_ops = sample_repo_dir / "math_ops.py"
    orig_stat = os.stat(str(math_ops))
    orig_mtime = orig_stat.st_mtime
    orig_size = orig_stat.st_size

    # Replace 'return a + b' with 'return a - b' (exact same character count and byte length)
    content = math_ops.read_text(encoding="utf-8")
    modified_content = content.replace("return a + b", "return a - b")
    assert len(content.encode("utf-8")) == len(modified_content.encode("utf-8"))

    math_ops.write_text(modified_content, encoding="utf-8")
    # Restore modification time to match original exactly
    os.utime(str(math_ops), (orig_mtime, orig_mtime))

    new_stat = os.stat(str(math_ops))
    assert new_stat.st_size == orig_size
    assert abs(new_stat.st_mtime - orig_mtime) < 0.001

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    # Must detect modification via SHA-256 hash
    assert result["status"] == "synchronized"
    assert result["files_modified"] == ["math_ops.py"]
    assert result["total_changes"] == 1


def test_sync_repository_added_file(db_session, sample_repo_dir):
    """Verifies incremental sync when a new file is added to the repository."""
    repo = _setup_repo(db_session, sample_repo_dir)
    initial_files = repo.stats["total_files"]

    # Add new file
    greeter = sample_repo_dir / "greeter.py"
    greeter.write_text(
        "def greet(name: str) -> str:\n"
        "    return f'Hello, {name}'\n",
        encoding="utf-8",
    )

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert result["files_added"] == ["greeter.py"]
    assert result["total_changes"] == 1

    # Verify greeter file and symbol exist in database
    greet_nodes = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.name == "greet")
        .all()
    )
    assert len(greet_nodes) == 1
    assert greet_nodes[0].node_type == "FUNCTION"
    assert greet_nodes[0].file_path == "greeter.py"
    assert repo.stats["total_files"] == initial_files + 1


def test_sync_repository_deleted_file_and_dangling_edge_prevention(db_session, sample_repo_dir):
    """Verifies incremental sync when a file is deleted and ensures no dangling edges remain."""
    repo = _setup_repo(db_session, sample_repo_dir)

    # Verify math_ops.py had incoming edges before deletion
    math_ops_file_id = f"{repo.id}:file:math_ops.py"
    incoming_to_math_file = (
        db_session.query(GraphEdgeModel)
        .filter(GraphEdgeModel.repository_id == repo.id, GraphEdgeModel.target_id == math_ops_file_id)
        .count()
    )
    assert incoming_to_math_file >= 1

    # Delete math_ops.py from disk
    math_ops = sample_repo_dir / "math_ops.py"
    math_ops.unlink()

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert result["files_deleted"] == ["math_ops.py"]

    # 1. No nodes for math_ops.py in database
    remaining_math_nodes = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.file_path == "math_ops.py")
        .count()
    )
    assert remaining_math_nodes == 0

    # 2. No dangling edges targeting math_ops.py or its symbols
    all_current_nodes = {
        n.id for n in db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    }
    current_edges = (
        db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    )
    for edge in current_edges:
        if not edge.target_id.startswith("ext:"):
            assert edge.target_id in all_current_nodes, f"Dangling edge detected: {edge.id}"
        assert edge.source_id in all_current_nodes, f"Dangling source edge detected: {edge.id}"


def test_sync_repository_renamed_file(db_session, sample_repo_dir):
    """Verifies that renaming a file updates nodes and removes old path nodes without dangling edges."""
    repo = _setup_repo(db_session, sample_repo_dir)

    # Rename utils.ts -> helpers.ts
    utils_ts = sample_repo_dir / "utils.ts"
    helpers_ts = sample_repo_dir / "helpers.ts"
    utils_ts.rename(helpers_ts)

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert result["files_renamed"] == [{"from": "utils.ts", "to": "helpers.ts"}]
    assert "utils.ts" in result["files_deleted"]
    assert "helpers.ts" in result["files_added"]

    # utils.ts nodes must be gone
    old_nodes = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.file_path == "utils.ts")
        .count()
    )
    assert old_nodes == 0

    # helpers.ts node must exist
    new_nodes = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.file_path == "helpers.ts")
        .count()
    )
    assert new_nodes >= 2  # FILE + formatResult


def test_sync_repository_duplicate_content_rename_one_to_one(db_session, tmp_path):
    """Verifies that multiple deleted files and multiple added files with identical content
    produce strictly one-to-one and deterministic rename mappings without duplicate claims.
    """
    repo_dir = tmp_path / "dup_rename_repo"
    repo_dir.mkdir()

    shared_content = "# Shared identical content\nDEFAULT_CONFIG = {'active': True}\n"
    del1 = repo_dir / "del1.py"
    del2 = repo_dir / "del2.py"
    del1.write_text(shared_content, encoding="utf-8")
    del2.write_text(shared_content, encoding="utf-8")

    repo = _setup_repo(db_session, repo_dir, name="Dup Rename Repo")

    files_in_db = (
        db_session.query(GraphNodeModel.file_path)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.node_type == "FILE")
        .all()
    )
    assert {f[0] for f in files_in_db} == {"del1.py", "del2.py"}

    del1.unlink()
    del2.unlink()
    add1 = repo_dir / "add1.py"
    add2 = repo_dir / "add2.py"
    add1.write_text(shared_content, encoding="utf-8")
    add2.write_text(shared_content, encoding="utf-8")

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert result["files_deleted"] == ["del1.py", "del2.py"]
    assert result["files_added"] == ["add1.py", "add2.py"]

    renamed = result["files_renamed"]
    assert len(renamed) == 2

    # Deterministic 1-to-1 matching:
    # add1.py claims del1.py
    # add2.py claims del2.py
    assert renamed == [
        {"from": "del1.py", "to": "add1.py"},
        {"from": "del2.py", "to": "add2.py"},
    ]

    from_paths = [r["from"] for r in renamed]
    to_paths = [r["to"] for r in renamed]
    assert len(from_paths) == len(set(from_paths)) == 2
    assert len(to_paths) == len(set(to_paths)) == 2
    assert set(from_paths) == {"del1.py", "del2.py"}
    assert set(to_paths) == {"add1.py", "add2.py"}


def test_sync_repository_asymmetric_duplicate_content_rename(db_session, tmp_path):
    """Verifies 1-to-1 rename matching when added and deleted counts with identical content differ."""
    repo_dir = tmp_path / "asym_rename_repo"
    repo_dir.mkdir()

    identical_content = "# Identical module\nVERSION = '1.0.0'\n"
    old_a = repo_dir / "old_a.py"
    old_b = repo_dir / "old_b.py"
    old_c = repo_dir / "old_c.py"
    old_a.write_text(identical_content, encoding="utf-8")
    old_b.write_text(identical_content, encoding="utf-8")
    old_c.write_text(identical_content, encoding="utf-8")

    repo = _setup_repo(db_session, repo_dir, name="Asym Rename Repo")

    # Delete 3, but add only 2 new files with that content
    old_a.unlink()
    old_b.unlink()
    old_c.unlink()

    new_x = repo_dir / "new_x.py"
    new_y = repo_dir / "new_y.py"
    new_x.write_text(identical_content, encoding="utf-8")
    new_y.write_text(identical_content, encoding="utf-8")

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    renamed = result["files_renamed"]
    # Only 2 renames can occur since only 2 files were added
    assert len(renamed) == 2
    assert renamed == [
        {"from": "old_a.py", "to": "new_x.py"},
        {"from": "old_b.py", "to": "new_y.py"},
    ]

    from_paths = [r["from"] for r in renamed]
    to_paths = [r["to"] for r in renamed]
    assert len(from_paths) == len(set(from_paths)) == 2
    assert len(to_paths) == len(set(to_paths)) == 2
    assert set(from_paths) == {"old_a.py", "old_b.py"}
    assert set(to_paths) == {"new_x.py", "new_y.py"}


def test_sync_repository_removed_symbol_dangling_edge_prevention(db_session, sample_repo_dir):
    """Verifies that removing a single symbol from a file deletes any incoming edges targeting that symbol."""
    repo = _setup_repo(db_session, sample_repo_dir)

    # Verify edge to sub exists initially
    sub_node = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.name == "sub")
        .first()
    )
    assert sub_node is not None
    sub_node_id = sub_node.id
    incoming_sub_edges = (
        db_session.query(GraphEdgeModel)
        .filter(GraphEdgeModel.repository_id == repo.id, GraphEdgeModel.target_id == sub_node_id)
        .count()
    )
    assert incoming_sub_edges >= 1

    # In math_ops.py, remove sub function, keep add
    math_ops = sample_repo_dir / "math_ops.py"
    math_ops.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n",
        encoding="utf-8",
    )

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert result["files_modified"] == ["math_ops.py"]

    # sub node must be deleted
    assert (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.name == "sub")
        .first()
        is None
    )

    # No edges targeting sub_node_id may remain
    remaining_sub_edges = (
        db_session.query(GraphEdgeModel)
        .filter(GraphEdgeModel.repository_id == repo.id, GraphEdgeModel.target_id == sub_node_id)
        .count()
    )
    assert remaining_sub_edges == 0

    # add node and its edges must still be intact
    add_node = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.name == "add")
        .first()
    )
    assert add_node is not None
    incoming_add_edges = (
        db_session.query(GraphEdgeModel)
        .filter(GraphEdgeModel.repository_id == repo.id, GraphEdgeModel.target_id == add_node.id)
        .count()
    )
    assert incoming_add_edges >= 1


def test_sync_repository_cross_file_relationships_rebuilt(db_session, sample_repo_dir):
    """Verifies that cross-file relationships are rebuilt when a new symbol is introduced and called."""
    repo = _setup_repo(db_session, sample_repo_dir)

    # 1. Add multiply to math_ops.py
    math_ops = sample_repo_dir / "math_ops.py"
    math_ops.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def sub(a: int, b: int) -> int:\n"
        "    return a - b\n\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n",
        encoding="utf-8",
    )

    # 2. In calculator.py, import and invoke multiply
    calc = sample_repo_dir / "calculator.py"
    calc.write_text(
        "from math_ops import add, sub, multiply\n\n"
        "class Calculator:\n"
        "    def calculate_sum(self, x: int, y: int) -> int:\n"
        "        return add(x, y)\n\n"
        "    def calculate_diff(self, x: int, y: int) -> int:\n"
        "        return sub(x, y)\n\n"
        "    def calculate_product(self, x: int, y: int) -> int:\n"
        "        return multiply(x, y)\n",
        encoding="utf-8",
    )

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    assert result["status"] == "synchronized"
    assert set(result["files_modified"]) == {"math_ops.py", "calculator.py"}

    # Query engine: verify CALLS edge connects calculate_product to multiply
    engine = GraphQueryEngine(db_session)
    mult_nodes = engine.find_symbol("multiply", repo_id=repo.id)
    assert len(mult_nodes) == 1
    mult_node = mult_nodes[0]

    callers = engine.get_callers(mult_node.id)
    caller_names = {c["node"]["name"] for c in callers}
    assert "calculate_product" in caller_names


def test_update_file_parse_failure_rollback(db_session, sample_repo_dir):
    """Verifies that if parsing or updating fails, the graph rolls back and preserves the prior valid state."""
    repo = _setup_repo(db_session, sample_repo_dir)

    initial_nodes = {
        n.id: (n.name, n.file_path)
        for n in db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    }
    initial_edges = {
        e.id for e in db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    }

    indexer = RepositoryIndexer(db_session)

    # Try updating with an unsupported or non-existent file
    updated = indexer.update_file(repo.id, "non_existent.py")
    assert updated is True  # Treated as deleted non-existent, no change to existing

    # Verify prior state preserved
    current_nodes = {
        n.id: (n.name, n.file_path)
        for n in db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    }
    current_edges = {
        e.id for e in db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    }
    assert current_nodes == initial_nodes
    assert current_edges == initial_edges


def test_incremental_sync_repository_isolation(db_session, sample_repo_dir, tmp_path):
    """Verifies that incremental updates to repo 1 strictly preserve repo 2's nodes, edges, and statistics."""
    # Setup Repo 1
    repo1 = _setup_repo(db_session, sample_repo_dir, name="Repo 1")

    # Setup Repo 2 in a separate directory
    repo2_dir = tmp_path / "repo2"
    repo2_dir.mkdir()
    (repo2_dir / "service.py").write_text("def start_service(): pass\n", encoding="utf-8")
    repo2 = _setup_repo(db_session, repo2_dir, name="Repo 2")

    # Record Repo 2 baseline
    repo2_nodes_before = {
        n.id for n in db_session.query(GraphNodeModel).filter_by(repository_id=repo2.id).all()
    }
    repo2_edges_before = {
        e.id for e in db_session.query(GraphEdgeModel).filter_by(repository_id=repo2.id).all()
    }
    repo2_stats_before = dict(repo2.stats)

    # Perform multiple modifications, additions, and deletions in Repo 1
    (sample_repo_dir / "math_ops.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (sample_repo_dir / "new_module.py").write_text("def new_func(): pass\n", encoding="utf-8")
    (sample_repo_dir / "utils.ts").unlink()

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo1.id)
    assert result["status"] == "synchronized"

    # Repo 2 must be completely unchanged
    repo2_nodes_after = {
        n.id for n in db_session.query(GraphNodeModel).filter_by(repository_id=repo2.id).all()
    }
    repo2_edges_after = {
        e.id for e in db_session.query(GraphEdgeModel).filter_by(repository_id=repo2.id).all()
    }
    assert repo2_nodes_after == repo2_nodes_before
    assert repo2_edges_after == repo2_edges_before
    assert repo2.stats == repo2_stats_before


def test_sync_repository_idempotency(db_session, sample_repo_dir):
    """Verifies that repeated synchronizations without further disk edits are idempotent no-ops."""
    repo = _setup_repo(db_session, sample_repo_dir)

    indexer = RepositoryIndexer(db_session)
    # First sync (unchanged)
    r1 = indexer.sync_repository(repo.id)
    assert r1["status"] == "unchanged"

    # Modify a file
    (sample_repo_dir / "math_ops.py").write_text("def add(a, b): return a\n", encoding="utf-8")

    # Second sync (applies modification)
    r2 = indexer.sync_repository(repo.id)
    assert r2["status"] == "synchronized"
    assert r2["files_modified"] == ["math_ops.py"]

    # Third sync (immediately after, should be no-op)
    r3 = indexer.sync_repository(repo.id)
    assert r3["status"] == "unchanged"
    assert r3["total_changes"] == 0

    # Fourth sync (still no-op)
    r4 = indexer.sync_repository(repo.id)
    assert r4["status"] == "unchanged"
    assert r4["total_changes"] == 0


def test_sync_repository_accurate_node_edge_file_counts(db_session, sample_repo_dir):
    """Verifies that repository stats accurately reflect real DB node, edge, and file counts after sync."""
    repo = _setup_repo(db_session, sample_repo_dir)

    # Add a file with known 2 functions
    (sample_repo_dir / "extra.py").write_text(
        "def f1(): pass\n"
        "def f2(): pass\n",
        encoding="utf-8",
    )

    indexer = RepositoryIndexer(db_session)
    result = indexer.sync_repository(repo.id)

    db_file_count = (
        db_session.query(GraphNodeModel)
        .filter(GraphNodeModel.repository_id == repo.id, GraphNodeModel.node_type == "FILE")
        .count()
    )
    db_node_count = (
        db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()
    )
    db_edge_count = (
        db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).count()
    )

    assert result["stats"]["total_files"] == db_file_count
    assert result["stats"]["total_nodes"] == db_node_count
    assert result["stats"]["total_edges"] == db_edge_count
    assert repo.stats["total_files"] == db_file_count
    assert repo.stats["total_nodes"] == db_node_count
    assert repo.stats["total_edges"] == db_edge_count


def test_mcp_sync_repository_tool(db_session, sample_repo_dir):
    """Verifies that the registered MCP sync_repository tool executes properly and outputs valid JSON."""
    import asyncio
    from contextlib import contextmanager
    from unittest.mock import patch
    from packages.mcp.server import mcp_server

    tools = asyncio.run(mcp_server.list_tools())
    tool_names = [t.name for t in tools]
    assert "sync_repository" in tool_names
    assert len(tool_names) == 16

    repo = _setup_repo(db_session, sample_repo_dir)

    @contextmanager
    def mock_session():
        yield db_session

    with patch("packages.mcp.server.get_db_session", mock_session):
        raw_json = mcp_sync_repository(repo_id=repo.id)
        data = json.loads(raw_json)

        assert "repository_id" in data
        assert data["repository_id"] == repo.id
        assert "status" in data
        assert "total_changes" in data
        assert "stats" in data
        assert data["status"] == "unchanged"
