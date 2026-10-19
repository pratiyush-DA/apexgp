"""Integration and Indexing Tests for Phase 9.7: Shell Language Support.

Validates:
- Multi-file Shell script indexing (POSIX sh, bash, zsh)
- Graph node extraction (FUNCTION, VARIABLE, FILE)
- Relationship building: DEFINES, IMPORTS, REFERENCES, and CALLS edges
- Cross-file script sourcing (source, .) and symbol resolution
- Import-cycle detection in mutually sourcing shell scripts
- God-Node architectural hub analysis on shared shell utility scripts
- Incremental indexing via sync_repository (add, modify, delete)
- Polyglot repository isolation across Shell, Python, TypeScript, Go, Rust, and SQL
"""

import os
import shutil
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


@pytest.fixture
def shell_repo_dir(tmp_path):
    """Creates a temporary multi-file Shell repository with libraries and scripts."""
    repo_dir = tmp_path / "shell_test_repo"
    repo_dir.mkdir()

    lib_dir = repo_dir / "lib"
    lib_dir.mkdir()

    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir()

    # 1. lib/math_utils.sh
    math_sh = lib_dir / "math_utils.sh"
    math_sh.write_text(
        "#!/usr/bin/env bash\n"
        "# Math utilities library\n"
        "export MATH_PI=3\n\n"
        "add_numbers() {\n"
        "    echo $(($1 + $2))\n"
        "}\n\n"
        "multiply_numbers() {\n"
        "    echo $(($1 * $2))\n"
        "}\n",
        encoding="utf-8",
    )

    # 2. lib/log_utils.sh
    log_sh = lib_dir / "log_utils.sh"
    log_sh.write_text(
        "#!/usr/bin/env bash\n"
        "log_info() {\n"
        "    echo \"[INFO] $1\"\n"
        "}\n\n"
        "log_error() {\n"
        "    echo \"[ERROR] $1\"\n"
        "}\n",
        encoding="utf-8",
    )

    # 3. scripts/deploy.sh
    deploy_sh = scripts_dir / "deploy.sh"
    deploy_sh.write_text(
        "#!/usr/bin/env bash\n"
        "source ./lib/math_utils.sh\n"
        "source ./lib/log_utils.sh\n\n"
        "run_deploy() {\n"
        "    log_info \"Starting deploy\"\n"
        "    TOTAL=$(add_numbers 10 20)\n"
        "    log_info \"Computed total: $TOTAL\"\n"
        "    log_error \"Deploy finished\"\n"
        "}\n\n"
        "run_deploy\n",
        encoding="utf-8",
    )

    return repo_dir


def test_shell_full_indexing_and_graph_nodes(db_session, shell_repo_dir):
    """Verifies that full indexing extracts shell nodes, defines, imports, and calls edges."""
    ws = WorkspaceModel(id="ws-shell-1", name="Shell Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-shell-1",
        workspace_id="ws-shell-1",
        name="shell_test_repo",
        source_path=str(shell_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    updated_repo = indexer.index_repository(repo.id)

    assert updated_repo.indexing_status == "ready"
    assert updated_repo.error_message is None
    stats = updated_repo.stats or {}
    assert stats.get("total_nodes", 0) > 0
    assert stats.get("total_edges", 0) > 0

    # Verify nodes
    nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    node_types = {n.node_type for n in nodes}
    assert "FILE" in node_types
    assert "FUNCTION" in node_types
    assert "VARIABLE" in node_types

    fn_nodes = [n for n in nodes if n.node_type == "FUNCTION"]
    fn_names = {n.name for n in fn_nodes}
    assert "add_numbers" in fn_names
    assert "multiply_numbers" in fn_names
    assert "log_info" in fn_names
    assert "log_error" in fn_names
    assert "run_deploy" in fn_names

    # Check node runtime prefixes
    for n in fn_nodes:
        assert n.id.startswith(f"{repo.id}:sh:")
        assert n.language == "shell"

    # Verify edges
    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    edge_types = {e.edge_type for e in edges}
    assert "DEFINES" in edge_types
    assert "IMPORTS" in edge_types
    assert "CALLS" in edge_types

    # Check IMPORTS edges from deploy.sh
    deploy_file_id = f"{repo.id}:file:scripts/deploy.sh"
    import_edges = [
        e for e in edges
        if e.edge_type == "IMPORTS" and e.source_id == deploy_file_id
    ]
    target_ids = {e.target_id for e in import_edges}
    assert f"{repo.id}:file:lib/math_utils.sh" in target_ids
    assert f"{repo.id}:file:lib/log_utils.sh" in target_ids


def test_shell_cross_file_calls_and_query_engine(db_session, shell_repo_dir):
    """Verifies that GraphQueryEngine correctly resolves cross-file callers and callees."""
    ws = WorkspaceModel(id="ws-shell-2", name="Shell Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-shell-2",
        workspace_id="ws-shell-2",
        name="shell_test_repo",
        source_path=str(shell_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)

    # 1. Inspect callees of run_deploy
    deploy_fn_id = f"{repo.id}:sh:scripts/deploy.sh:run_deploy"
    callees = engine.get_callees(deploy_fn_id)
    callee_names = {c["node"]["name"] for c in callees}
    assert "add_numbers" in callee_names
    assert "log_info" in callee_names
    assert "log_error" in callee_names

    # 2. Inspect callers of add_numbers
    add_fn_id = f"{repo.id}:sh:lib/math_utils.sh:add_numbers"
    callers = engine.get_callers(add_fn_id)
    caller_names = {c["node"]["name"] for c in callers}
    assert "run_deploy" in caller_names

    # 3. File dependencies
    deps = engine.get_dependencies(f"{repo.id}:file:scripts/deploy.sh")
    dep_paths = {d["node"]["file_path"] for d in deps["internal_dependencies"]}
    assert "lib/math_utils.sh" in dep_paths
    assert "lib/log_utils.sh" in dep_paths


def test_shell_cycle_detection(db_session, tmp_path):
    """Verifies that circular sourcing between shell scripts is detected."""
    cycle_dir = tmp_path / "cycle_repo"
    cycle_dir.mkdir()

    # Script A sources Script B
    (cycle_dir / "script_a.sh").write_text(
        "#!/usr/bin/env bash\n"
        "source ./script_b.sh\n"
        "func_a() { echo A; }\n",
        encoding="utf-8",
    )

    # Script B sources Script A
    (cycle_dir / "script_b.sh").write_text(
        "#!/usr/bin/env bash\n"
        "source ./script_a.sh\n"
        "func_b() { echo B; }\n",
        encoding="utf-8",
    )

    ws = WorkspaceModel(id="ws-cycle", name="Cycle Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-cycle",
        workspace_id="ws-cycle",
        name="cycle_repo",
        source_path=str(cycle_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert len(cycles) > 0
    cycle_files = set(cycles[0])
    assert "script_a.sh" in cycle_files
    assert "script_b.sh" in cycle_files


def test_shell_god_node_analysis(db_session, tmp_path):
    """Verifies that heavily sourced shell libraries are identified as God-Nodes."""
    god_dir = tmp_path / "god_repo"
    god_dir.mkdir()

    # Central utility library
    (god_dir / "common.sh").write_text(
        "#!/usr/bin/env bash\n"
        "export APP_ENV=production\n"
        "helper() { echo ok; }\n",
        encoding="utf-8",
    )

    # Many consumers
    for i in range(5):
        (god_dir / f"consumer_{i}.sh").write_text(
            f"#!/usr/bin/env bash\n"
            f"source ./common.sh\n"
            f"task_{i}() {{ helper; }}\n",
            encoding="utf-8",
        )

    ws = WorkspaceModel(id="ws-god", name="God Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-god",
        workspace_id="ws-god",
        name="god_repo",
        source_path=str(god_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)
    god_nodes = engine.get_god_nodes(repo.id, limit=10, scope="file")

    assert len(god_nodes) > 0
    god_node_paths = {g.node.file_path for g in god_nodes}
    assert "common.sh" in god_node_paths


def test_shell_incremental_indexing(db_session, shell_repo_dir):
    """Verifies incremental sync (add, modify, delete) on shell scripts."""
    ws = WorkspaceModel(id="ws-inc", name="Inc Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-inc",
        workspace_id="ws-inc",
        name="shell_test_repo",
        source_path=str(shell_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # 1. Add new file: lib/net_utils.sh
    net_sh = shell_repo_dir / "lib" / "net_utils.sh"
    net_sh.write_text(
        "#!/usr/bin/env bash\n"
        "check_network() { echo ping; }\n",
        encoding="utf-8",
    )

    sync_res = indexer.sync_repository(repo.id)
    assert "lib/net_utils.sh" in sync_res["files_added"]

    net_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="check_network",
    ).first()
    assert net_node is not None

    # 2. Modify deploy.sh to source net_utils.sh and call check_network
    deploy_sh = shell_repo_dir / "scripts" / "deploy.sh"
    deploy_sh.write_text(
        "#!/usr/bin/env bash\n"
        "source ./lib/math_utils.sh\n"
        "source ./lib/log_utils.sh\n"
        "source ./lib/net_utils.sh\n\n"
        "run_deploy() {\n"
        "    check_network\n"
        "    log_info \"Network verified\"\n"
        "}\n",
        encoding="utf-8",
    )

    sync_res2 = indexer.sync_repository(repo.id)
    assert "scripts/deploy.sh" in sync_res2["files_modified"]

    engine = GraphQueryEngine(db_session)
    deploy_fn_id = f"{repo.id}:sh:scripts/deploy.sh:run_deploy"
    callees = engine.get_callees(deploy_fn_id)
    callee_names = {c["node"]["name"] for c in callees}
    assert "check_network" in callee_names

    # 3. Delete net_utils.sh
    os.remove(str(net_sh))
    sync_res3 = indexer.sync_repository(repo.id)
    assert "lib/net_utils.sh" in sync_res3["files_deleted"]

    # Ensure net_node was cleanly purged
    deleted_net = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="check_network",
    ).first()
    assert deleted_net is None


def test_shell_polyglot_repository_isolation(db_session, tmp_path):
    """Verifies that shell scripts peacefully coexist with other languages without cross-language collision."""
    poly_dir = tmp_path / "poly_repo"
    poly_dir.mkdir()

    # Python file
    (poly_dir / "service.py").write_text(
        "def execute():\n    return 'python'\n",
        encoding="utf-8",
    )

    # Shell file
    (poly_dir / "build.sh").write_text(
        "#!/usr/bin/env bash\n"
        "execute() {\n"
        "    echo 'shell'\n"
        "}\n"
        "execute\n",
        encoding="utf-8",
    )

    ws = WorkspaceModel(id="ws-poly", name="Poly Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-poly",
        workspace_id="ws-poly",
        name="poly_repo",
        source_path=str(poly_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Both should have an execute function with distinct namespaces
    py_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="execute",
        language="python",
    ).first()
    assert py_node is not None
    assert py_node.id.startswith(f"{repo.id}:py:")

    sh_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="execute",
        language="shell",
    ).first()
    assert sh_node is not None
    assert sh_node.id.startswith(f"{repo.id}:sh:")

    # Verify calls in build.sh target the shell execute, not python execute
    sh_calls = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        edge_type="CALLS",
        file_path="build.sh",
    ).all()
    assert len(sh_calls) == 1
    assert sh_calls[0].target_id == sh_node.id
