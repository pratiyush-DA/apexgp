"""Comprehensive Unit and Integration Tests for Phase 9.5: Rust Indexer Integration.

Validates:
- Multi-file, multi-module Rust repository indexing (crate::, super::, mod declarations)
- Struct and method extraction with CONTAINS edges
- Trait declarations, trait implementations, and selector/path calls
- Test function extraction with TESTS relationship generation
- External crate and std imports (ext:rs:std::collections)
- Incremental indexing (added, modified, deleted, renamed Rust files)
- Cyclic import detection in Rust modules
- God-Node architectural analysis on Rust graphs
- Repository isolation and polyglot namespace protection
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
def rust_repo_dir(tmp_path):
    """Creates a temporary multi-module Rust repository with Cargo layout."""
    repo_dir = tmp_path / "rust_test_repo"
    repo_dir.mkdir()

    # Cargo.toml
    cargo_toml = repo_dir / "Cargo.toml"
    cargo_toml.write_text(
        '[package]\nname = "test_app"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )

    src_dir = repo_dir / "src"
    src_dir.mkdir()

    # src/math.rs
    math_rs = src_dir / "math.rs"
    math_rs.write_text(
        "pub fn add(a: i32, b: i32) -> i32 {\n"
        "    a + b\n"
        "}\n\n"
        "pub fn multiply(a: i32, b: i32) -> i32 {\n"
        "    a * b\n"
        "}\n",
        encoding="utf-8",
    )

    # src/models/mod.rs
    models_dir = src_dir / "models"
    models_dir.mkdir()
    models_mod = models_dir / "mod.rs"
    models_mod.write_text(
        "pub mod user;\n\n"
        "pub use user::User;\n",
        encoding="utf-8",
    )

    # src/models/user.rs
    user_rs = models_dir / "user.rs"
    user_rs.write_text(
        "pub struct User {\n"
        "    pub name: String,\n"
        "    pub age: u32,\n"
        "}\n\n"
        "impl User {\n"
        "    pub fn new(name: String, age: u32) -> Self {\n"
        "        User { name, age }\n"
        "    }\n\n"
        "    pub fn get_age(&self) -> u32 {\n"
        "        self.age\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # src/main.rs
    main_rs = src_dir / "main.rs"
    main_rs.write_text(
        "mod math;\n"
        "mod models;\n\n"
        "use math::add;\n"
        "use models::User;\n"
        "use std::collections::HashMap;\n\n"
        "pub fn run() -> i32 {\n"
        '    let u = User::new("Alice".to_string(), 30);\n'
        "    let res = add(10, 20);\n"
        "    res\n"
        "}\n\n"
        "fn main() {\n"
        "    run();\n"
        "}\n\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    use super::*;\n\n"
        "    #[test]\n"
        "    fn test_run() {\n"
        "        assert_eq!(run(), 30);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    return repo_dir


def test_rust_full_indexing_multi_module(db_session, rust_repo_dir):
    """Verifies complete full-indexing of a multi-file Rust project."""
    workspace = WorkspaceModel(id="ws-rust-1", name="Rust Workspace")
    db_session.add(workspace)
    db_session.flush()

    repo = RepositoryModel(
        id="repo-rust-1",
        workspace_id=workspace.id,
        name="test_repo",
        source_path=str(rust_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    db_session.refresh(repo)
    assert repo.indexing_status == "ready"
    assert repo.stats["total_files"] == 4  # main.rs, math.rs, models/mod.rs, models/user.rs
    assert repo.stats["languages"].get("rust", 0) > 0

    # 1. Verify Nodes
    nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    node_names = {n.name for n in nodes}
    assert "add" in node_names
    assert "multiply" in node_names
    assert "User" in node_names
    assert "new" in node_names
    assert "get_age" in node_names
    assert "run" in node_names
    assert "main" in node_names

    # Check node ID runtime prefix
    user_node = next(n for n in nodes if n.name == "User" and n.node_type == "CLASS")
    assert user_node.id.startswith(f"{repo.id}:rs:src/models/user.rs:User")
    assert user_node.language == "rust"

    # 2. Verify CONTAINS edges (User contains new, get_age)
    contains_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, edge_type="CONTAINS")
        .all()
    )
    contains_pairs = {(e.source_id, e.target_id) for e in contains_edges}

    new_node = next(n for n in nodes if n.name == "new" and n.qualified_name.endswith("User::new"))
    assert (user_node.id, new_node.id) in contains_pairs

    # 3. Verify IMPORTS edges (main.rs imports math, models, std::collections)
    main_file_id = f"{repo.id}:file:src/main.rs"
    math_file_id = f"{repo.id}:file:src/math.rs"
    models_file_id = f"{repo.id}:file:src/models/mod.rs"

    import_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, edge_type="IMPORTS")
        .all()
    )
    import_pairs = {(e.source_id, e.target_id) for e in import_edges}
    assert (main_file_id, math_file_id) in import_pairs
    assert (main_file_id, models_file_id) in import_pairs

    # Check external import
    ext_imports = [e.target_id for e in import_edges if e.target_id.startswith("ext:rs:")]
    assert any("std::collections" in ext for ext in ext_imports)

    # 4. Verify CALLS edges
    call_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, edge_type="CALLS")
        .all()
    )
    call_pairs = {(e.source_id, e.target_id) for e in call_edges}

    run_node = next(n for n in nodes if n.name == "run")
    add_node = next(n for n in nodes if n.name == "add")
    main_node = next(n for n in nodes if n.name == "main")

    # main calls run
    assert (main_node.id, run_node.id) in call_pairs
    # run calls add
    assert (run_node.id, add_node.id) in call_pairs
    # run calls User::new
    assert (run_node.id, new_node.id) in call_pairs

    # 5. Verify TESTS relationship
    test_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, edge_type="TESTS")
        .all()
    )
    assert len(test_edges) >= 1
    test_targets = {e.target_id for e in test_edges}
    assert run_node.id in test_targets


def test_rust_traits_and_implementations(db_session, tmp_path):
    """Verifies trait declarations, struct trait implementations, and cross-module calls."""
    repo_dir = tmp_path / "rust_traits_repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()

    # src/shapes.rs
    shapes_rs = src_dir / "shapes.rs"
    shapes_rs.write_text(
        "pub trait Shape {\n"
        "    fn area(&self) -> f64;\n"
        "}\n\n"
        "pub struct Circle {\n"
        "    pub radius: f64,\n"
        "}\n\n"
        "impl Shape for Circle {\n"
        "    fn area(&self) -> f64 {\n"
        "        3.14159 * self.radius * self.radius\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # src/lib.rs
    lib_rs = src_dir / "lib.rs"
    lib_rs.write_text(
        "pub mod shapes;\n"
        "use shapes::{Circle, Shape};\n\n"
        "pub fn calculate_total_area(c: &Circle) -> f64 {\n"
        "    c.area()\n"
        "}\n",
        encoding="utf-8",
    )

    workspace = WorkspaceModel(id="ws-rust-traits", name="traits_ws")
    db_session.add(workspace)
    db_session.flush()

    repo = RepositoryModel(id="repo-rust-traits", workspace_id=workspace.id, name="traits_repo", source_path=str(repo_dir))
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Verify Shape trait and Circle struct
    nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    shape_node = next(n for n in nodes if n.name == "Shape" and n.node_type == "INTERFACE")
    circle_node = next(n for n in nodes if n.name == "Circle" and n.node_type == "CLASS")
    area_node = next(n for n in nodes if n.name == "area" and n.qualified_name.endswith("Circle::area"))

    assert area_node.node_metadata["trait"] == "Shape"
    assert area_node.node_metadata["enclosing"] == "Circle"

    # Verify CALLS edge from calculate_total_area to Circle::area
    calc_node = next(n for n in nodes if n.name == "calculate_total_area")
    calls = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=calc_node.id, edge_type="CALLS")
        .all()
    )
    called_ids = {c.target_id for c in calls}
    assert area_node.id in called_ids


def test_rust_incremental_indexing(db_session, rust_repo_dir):
    """Verifies incremental addition, modification, deletion, and rename of Rust files."""
    workspace = WorkspaceModel(id="ws-rust-inc", name="inc_ws")
    db_session.add(workspace)
    db_session.flush()

    repo = RepositoryModel(id="repo-rust-inc", workspace_id=workspace.id, name="inc_repo", source_path=str(rust_repo_dir))
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # 1. Add new file src/stats.rs
    stats_rs = rust_repo_dir / "src" / "stats.rs"
    stats_rs.write_text(
        "pub fn mean(data: &[f64]) -> f64 {\n"
        "    data.iter().sum::<f64>() / (data.len() as f64)\n"
        "}\n",
        encoding="utf-8",
    )

    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["files_added"] == ["src/stats.rs"]

    mean_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="mean")
        .first()
    )
    assert mean_node is not None
    assert mean_node.language == "rust"

    # 2. Modify src/stats.rs
    stats_rs.write_text(
        "pub fn mean(data: &[f64]) -> f64 {\n"
        "    data.iter().sum::<f64>() / (data.len() as f64)\n"
        "}\n\n"
        "pub fn variance(data: &[f64]) -> f64 {\n"
        "    0.0\n"
        "}\n",
        encoding="utf-8",
    )
    # Ensure mtime advance
    os.utime(str(stats_rs), (os.path.getatime(str(stats_rs)) + 2, os.path.getmtime(str(stats_rs)) + 2))

    sync_res2 = indexer.sync_repository(repo.id)
    assert sync_res2["files_modified"] == ["src/stats.rs"]

    var_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="variance")
        .first()
    )
    assert var_node is not None

    # 3. Rename src/stats.rs -> src/metrics.rs
    metrics_rs = rust_repo_dir / "src" / "metrics.rs"
    shutil.move(str(stats_rs), str(metrics_rs))

    sync_res3 = indexer.sync_repository(repo.id)
    assert len(sync_res3["files_renamed"]) == 1
    assert sync_res3["files_renamed"][0] == {"from": "src/stats.rs", "to": "src/metrics.rs"}

    old_stats_nodes = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, file_path="src/stats.rs")
        .all()
    )
    assert len(old_stats_nodes) == 0

    new_metrics_nodes = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, file_path="src/metrics.rs")
        .all()
    )
    assert len(new_metrics_nodes) >= 2

    # 4. Delete src/metrics.rs
    os.remove(str(metrics_rs))
    sync_res4 = indexer.sync_repository(repo.id)
    assert sync_res4["files_deleted"] == ["src/metrics.rs"]

    remaining = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, file_path="src/metrics.rs")
        .all()
    )
    assert len(remaining) == 0


def test_rust_import_cycle_detection(db_session, tmp_path):
    """Verifies that circular imports between Rust modules are accurately detected."""
    repo_dir = tmp_path / "rust_cycle_repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()

    # mod_a.rs imports mod_b
    a_rs = src_dir / "mod_a.rs"
    a_rs.write_text(
        "use crate::mod_b::func_b;\n"
        "pub fn func_a() { func_b(); }\n",
        encoding="utf-8",
    )

    # mod_b.rs imports mod_a
    b_rs = src_dir / "mod_b.rs"
    b_rs.write_text(
        "use crate::mod_a::func_a;\n"
        "pub fn func_b() { func_a(); }\n",
        encoding="utf-8",
    )

    workspace = WorkspaceModel(id="ws-rust-cycle", name="cycle_ws")
    db_session.add(workspace)
    db_session.flush()

    repo = RepositoryModel(id="repo-rust-cycle", workspace_id=workspace.id, name="cycle_repo", source_path=str(repo_dir))
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)
    cycles = query_engine.detect_import_cycles(repo.id)

    assert len(cycles) > 0
    cycle_files = set(cycles[0])
    assert "src/mod_a.rs" in cycle_files
    assert "src/mod_b.rs" in cycle_files


def test_rust_god_node_analysis(db_session, tmp_path):
    """Verifies God-Node architectural analysis on a central Rust utility node."""
    repo_dir = tmp_path / "rust_god_repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()

    # Central utility
    core_rs = src_dir / "core.rs"
    core_rs.write_text(
        "pub fn core_engine() -> i32 { 100 }\n",
        encoding="utf-8",
    )

    # Multiple callers
    for i in range(5):
        caller_rs = src_dir / f"service_{i}.rs"
        caller_rs.write_text(
            f"use crate::core::core_engine;\n"
            f"pub fn handle_{i}() {{\n"
            f"    core_engine();\n"
            f"}}\n",
            encoding="utf-8",
        )

    workspace = WorkspaceModel(id="ws-rust-god", name="god_ws")
    db_session.add(workspace)
    db_session.flush()

    repo = RepositoryModel(id="repo-rust-god", workspace_id=workspace.id, name="god_repo", source_path=str(repo_dir))
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)
    god_nodes = query_engine.get_god_nodes(repo.id)

    assert len(god_nodes) > 0
    top_node = god_nodes[0]
    # The central core_engine or core.rs file should rank high in in-degree
    assert "core" in (top_node.node.name or "") or "core" in (top_node.node.file_path or "")


def test_polyglot_and_repository_isolation(db_session, tmp_path):
    """Verifies complete isolation between Rust repositories and other languages."""
    # Repo 1: Rust
    rust_dir = tmp_path / "repo_rust"
    rust_dir.mkdir()
    rust_src = rust_dir / "src"
    rust_src.mkdir()
    (rust_src / "lib.rs").write_text("pub fn calculate() -> i32 { 10 }\n", encoding="utf-8")

    # Repo 2: Python
    py_dir = tmp_path / "repo_py"
    py_dir.mkdir()
    (py_dir / "lib.py").write_text("def calculate():\n    return 20\n", encoding="utf-8")

    # Repo 3: Go
    go_dir = tmp_path / "repo_go"
    go_dir.mkdir()
    (go_dir / "go.mod").write_text("module example.com/calc\n\ngo 1.22\n", encoding="utf-8")
    (go_dir / "lib.go").write_text("package calc\n\nfunc Calculate() int {\n    return 30\n}\n", encoding="utf-8")

    workspace = WorkspaceModel(id="ws-rust-iso", name="iso_ws")
    db_session.add(workspace)
    db_session.flush()

    repo_rs = RepositoryModel(id="repo-iso-rs", workspace_id=workspace.id, name="rust_repo", source_path=str(rust_dir))
    repo_py = RepositoryModel(id="repo-iso-py", workspace_id=workspace.id, name="py_repo", source_path=str(py_dir))
    repo_go = RepositoryModel(id="repo-iso-go", workspace_id=workspace.id, name="go_repo", source_path=str(go_dir))
    db_session.add_all([repo_rs, repo_py, repo_go])
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo_rs.id)
    indexer.index_repository(repo_py.id)
    indexer.index_repository(repo_go.id)

    # Check isolation in repo_rs
    rs_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo_rs.id).all()
    assert all(n.language == "rust" for n in rs_nodes if n.node_type != "FILE")
    assert all(":rs:" in n.id for n in rs_nodes if n.node_type != "FILE")

    # Check isolation in repo_py
    py_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo_py.id).all()
    assert all(n.language == "python" for n in py_nodes if n.node_type != "FILE")
    assert all(":py:" in n.id for n in py_nodes if n.node_type != "FILE")

    # Check isolation in repo_go
    go_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo_go.id).all()
    assert all(n.language == "go" for n in go_nodes if n.node_type != "FILE")
    assert all(":go:" in n.id for n in go_nodes if n.node_type != "FILE")
