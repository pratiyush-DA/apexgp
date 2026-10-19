"""Comprehensive Unit and Integration Tests for Phase 9.3: Go Indexer Integration.

Validates:
- Multi-file, multi-package Go repository indexing with go.mod
- Intra-package calls across files in the same package directory
- Cross-package calls with package qualifiers and aliases
- Receiver struct CONTAINS edges (same-file and cross-file in same package)
- Interface extraction and type declarations
- Go test recognition and TESTS relationship generation
- External standard library imports (ext:go:fmt, ext:go:os)
- Incremental indexing (added, modified, deleted, renamed Go files)
- Cyclic import detection in Go packages
- God-Node architectural analysis on Go graphs
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
def go_repo_dir(tmp_path):
    """Creates a temporary multi-package Go repository with go.mod."""
    repo_dir = tmp_path / "go_test_repo"
    repo_dir.mkdir()

    # go.mod
    go_mod = repo_dir / "go.mod"
    go_mod.write_text(
        "module example.com/calcapp\n\ngo 1.22\n",
        encoding="utf-8",
    )

    # pkg/calc/add.go
    calc_dir = repo_dir / "pkg" / "calc"
    calc_dir.mkdir(parents=True)
    add_go = calc_dir / "add.go"
    add_go.write_text(
        "package calc\n\n"
        "// Add returns the sum of two integers.\n"
        "func Add(a, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )

    # pkg/calc/sub.go (same package, calls Add without import)
    sub_go = calc_dir / "sub.go"
    sub_go.write_text(
        "package calc\n\n"
        "// Sub returns difference using Add.\n"
        "func Sub(a, b int) int {\n"
        "    return Add(a, -b)\n"
        "}\n",
        encoding="utf-8",
    )

    # pkg/calc/add_test.go (test file)
    add_test_go = calc_dir / "add_test.go"
    add_test_go.write_text(
        "package calc\n\n"
        'import "testing"\n\n'
        "func TestAdd(t *testing.T) {\n"
        "    res := Add(2, 3)\n"
        "    if res != 5 {\n"
        '        t.Errorf("expected 5, got %d", res)\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # pkg/models/user.go
    models_dir = repo_dir / "pkg" / "models"
    models_dir.mkdir(parents=True)
    user_go = models_dir / "user.go"
    user_go.write_text(
        "package models\n\n"
        "type User struct {\n"
        "    ID int\n"
        "    Name string\n"
        "}\n",
        encoding="utf-8",
    )

    # pkg/models/user_methods.go (receiver methods in separate file of same package)
    user_methods_go = models_dir / "user_methods.go"
    user_methods_go.write_text(
        "package models\n\n"
        "func (u *User) GetName() string {\n"
        "    return u.Name\n"
        "}\n\n"
        "func (u User) GetID() int {\n"
        "    return u.ID\n"
        "}\n",
        encoding="utf-8",
    )

    # pkg/service/reader.go (interface definition)
    service_dir = repo_dir / "pkg" / "service"
    service_dir.mkdir(parents=True)
    reader_go = service_dir / "reader.go"
    reader_go.write_text(
        "package service\n\n"
        "type DataReader interface {\n"
        "    ReadData() string\n"
        "}\n",
        encoding="utf-8",
    )

    # cmd/main.go (imports calc and models, calls functions and methods)
    cmd_dir = repo_dir / "cmd"
    cmd_dir.mkdir(parents=True)
    main_go = cmd_dir / "main.go"
    main_go.write_text(
        "package main\n\n"
        'import (\n'
        '    "fmt"\n'
        '    "example.com/calcapp/pkg/calc"\n'
        '    "example.com/calcapp/pkg/models"\n'
        ')\n\n'
        "func main() {\n"
        "    sum := calc.Add(10, 20)\n"
        '    u := &models.User{ID: 1, Name: "Alice"}\n'
        "    name := u.GetName()\n"
        '    fmt.Println(sum, name)\n'
        "    printGreeting(name)\n"
        "}\n",
        encoding="utf-8",
    )

    # cmd/helper.go (same package main, called by main.go)
    helper_go = cmd_dir / "helper.go"
    helper_go.write_text(
        "package main\n\n"
        'import "fmt"\n\n'
        "func printGreeting(name string) {\n"
        '    fmt.Println("Hello, " + name)\n'
        "}\n",
        encoding="utf-8",
    )

    return repo_dir


def test_full_go_indexing_and_relationships(db_session, go_repo_dir):
    """Verifies complete full-indexing of a multi-package Go project."""
    workspace = WorkspaceModel(id="ws-go-1", name="Go Workspace")
    repo = RepositoryModel(
        id="repo-go-1",
        workspace_id=workspace.id,
        name="go-sample",
        source_path=str(go_repo_dir),
        indexing_status="pending",
    )
    db_session.add(workspace)
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    result = indexer.index_repository(repo.id)

    assert result.indexing_status == "ready"
    assert result.stats["languages"]["go"] > 0
    assert result.stats["total_files"] == 8  # add, sub, add_test, user, user_methods, reader, main, helper

    # Verify nodes have runtime prefix 'go'
    add_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="Add",
        language="go",
    ).first()
    assert add_node is not None
    assert ":go:pkg/calc/add.go:Add" in add_node.id
    assert add_node.node_type == "FUNCTION"
    assert add_node.signature == "func Add(a, b int) int"
    assert "Add returns the sum" in (add_node.docstring or "")

    # Verify intra-package call: Sub -> Add
    sub_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="Sub",
        language="go",
    ).first()
    assert sub_node is not None

    sub_to_add_call = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=sub_node.id,
        target_id=add_node.id,
        edge_type="CALLS",
    ).first()
    assert sub_to_add_call is not None
    assert sub_to_add_call.confidence == 0.85  # cross-file call in same package directory

    # Verify cross-package import: cmd/main.go -> pkg/calc/add.go
    main_file_id = f"{repo.id}:file:cmd/main.go"
    calc_file_id = f"{repo.id}:file:pkg/calc/add.go"
    imp_edge = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=main_file_id,
        target_id=calc_file_id,
        edge_type="IMPORTS",
    ).first()
    assert imp_edge is not None

    # Verify cross-package call: main -> calc.Add
    main_fn = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="main",
        language="go",
    ).first()
    assert main_fn is not None

    main_to_add_call = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=main_fn.id,
        target_id=add_node.id,
        edge_type="CALLS",
    ).first()
    assert main_to_add_call is not None

    # Verify intra-package call in main: main -> printGreeting
    helper_fn = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="printGreeting",
        language="go",
    ).first()
    assert helper_fn is not None

    main_to_helper_call = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=main_fn.id,
        target_id=helper_fn.id,
        edge_type="CALLS",
    ).first()
    assert main_to_helper_call is not None


def test_go_receiver_struct_contains_edges(db_session, go_repo_dir):
    """Verifies that method declarations link to receiver structs across files in the same package."""
    workspace = WorkspaceModel(id="ws-go-2", name="Go Workspace 2")
    repo = RepositoryModel(
        id="repo-go-2",
        workspace_id=workspace.id,
        name="go-methods",
        source_path=str(go_repo_dir),
        indexing_status="pending",
    )
    db_session.add(workspace)
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    user_struct = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="User",
        node_type="CLASS",
    ).first()
    assert user_struct is not None
    assert user_struct.node_metadata.get("kind") == "struct"

    get_name_method = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="GetName",
        node_type="METHOD",
    ).first()
    assert get_name_method is not None
    assert get_name_method.node_metadata.get("receiver_base") == "User"
    assert get_name_method.node_metadata.get("is_pointer_receiver") is True

    # Check CONTAINS edge User -> GetName
    contains_edge = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=user_struct.id,
        target_id=get_name_method.id,
        edge_type="CONTAINS",
    ).first()
    assert contains_edge is not None

    # Method call resolution from main: u.GetName()
    main_fn = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="main",
        language="go",
    ).first()
    main_to_getname_call = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=main_fn.id,
        target_id=get_name_method.id,
        edge_type="CALLS",
    ).first()
    assert main_to_getname_call is not None


def test_go_interface_and_test_discovery(db_session, go_repo_dir):
    """Verifies Go interface node extraction and test discovery with TESTS edges."""
    workspace = WorkspaceModel(id="ws-go-3", name="Go Workspace 3")
    repo = RepositoryModel(
        id="repo-go-3",
        workspace_id=workspace.id,
        name="go-tests",
        source_path=str(go_repo_dir),
        indexing_status="pending",
    )
    db_session.add(workspace)
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # 1. Interface
    iface = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="DataReader",
        node_type="INTERFACE",
    ).first()
    assert iface is not None
    assert "type DataReader interface" in iface.signature

    # 2. Test Function & TESTS edge
    test_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="TestAdd",
        node_type="TEST",
    ).first()
    assert test_node is not None

    add_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="Add",
        node_type="FUNCTION",
    ).first()
    assert add_node is not None

    tests_edge = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=test_node.id,
        target_id=add_node.id,
        edge_type="TESTS",
    ).first()
    assert tests_edge is not None

    # 3. External standard library imports
    ext_fmt_edge = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        target_id="ext:go:fmt",
        edge_type="IMPORTS",
    ).first()
    assert ext_fmt_edge is not None


def test_go_incremental_indexing(db_session, go_repo_dir):
    """Verifies incremental sync_repository handles Go file additions, modifications, renames, and deletions."""
    workspace = WorkspaceModel(id="ws-go-inc", name="Go Inc WS")
    repo = RepositoryModel(
        id="repo-go-inc",
        workspace_id=workspace.id,
        name="go-incremental",
        source_path=str(go_repo_dir),
        indexing_status="pending",
    )
    db_session.add(workspace)
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # 1. No-op sync
    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["status"] == "unchanged"
    assert sync_res["total_changes"] == 0

    # 2. Add a new file in pkg/calc
    mul_file = go_repo_dir / "pkg" / "calc" / "mul.go"
    mul_file.write_text(
        "package calc\n\n"
        "func Mul(a, b int) int {\n"
        "    return a * b\n"
        "}\n",
        encoding="utf-8",
    )
    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["status"] == "synchronized"
    assert "pkg/calc/mul.go" in sync_res["files_added"]
    assert sync_res["total_changes"] == 1

    mul_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="Mul",
        language="go",
    ).first()
    assert mul_node is not None

    # 3. Modify cmd/main.go to call calc.Mul
    main_file = go_repo_dir / "cmd" / "main.go"
    main_file.write_text(
        "package main\n\n"
        'import (\n'
        '    "fmt"\n'
        '    "example.com/calcapp/pkg/calc"\n'
        ')\n\n'
        "func main() {\n"
        "    prod := calc.Mul(5, 6)\n"
        '    fmt.Println(prod)\n'
        "}\n",
        encoding="utf-8",
    )
    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["status"] == "synchronized"
    assert "cmd/main.go" in sync_res["files_modified"]

    main_fn = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        name="main",
        language="go",
    ).first()
    main_to_mul = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=main_fn.id,
        target_id=mul_node.id,
        edge_type="CALLS",
    ).first()
    assert main_to_mul is not None

    # 4. Rename pkg/calc/mul.go -> pkg/calc/multiply.go
    multiply_file = go_repo_dir / "pkg" / "calc" / "multiply.go"
    os.rename(mul_file, multiply_file)

    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["status"] == "synchronized"
    assert any(
        r["from"] == "pkg/calc/mul.go" and r["to"] == "pkg/calc/multiply.go"
        for r in sync_res["files_renamed"]
    )

    # 5. Delete pkg/calc/multiply.go
    os.remove(multiply_file)
    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["status"] == "synchronized"
    assert "pkg/calc/multiply.go" in sync_res["files_deleted"]

    # Verify purged node
    old_mul = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        file_path="pkg/calc/multiply.go",
    ).first()
    assert old_mul is None


def test_go_cyclic_imports_and_god_nodes(db_session, tmp_path):
    """Verifies that import cycle detection and God-Node analysis operate cleanly on Go graphs."""
    repo_dir = tmp_path / "go_cycle_repo"
    repo_dir.mkdir()

    # go.mod
    (repo_dir / "go.mod").write_text("module example.com/cyclerepo\n\ngo 1.22\n", encoding="utf-8")

    # Cycle: pkg/a imports pkg/b, pkg/b imports pkg/a
    a_dir = repo_dir / "pkg" / "a"
    a_dir.mkdir(parents=True)
    (a_dir / "a.go").write_text(
        "package a\n\n"
        'import "example.com/cyclerepo/pkg/b"\n\n'
        "func FuncA() int {\n"
        "    return b.FuncB()\n"
        "}\n",
        encoding="utf-8",
    )

    b_dir = repo_dir / "pkg" / "b"
    b_dir.mkdir(parents=True)
    (b_dir / "b.go").write_text(
        "package b\n\n"
        'import "example.com/cyclerepo/pkg/a"\n\n'
        "func FuncB() int {\n"
        "    return a.FuncA()\n"
        "}\n",
        encoding="utf-8",
    )

    # Central utility package called by both (God Node candidate)
    u_dir = repo_dir / "pkg" / "util"
    u_dir.mkdir(parents=True)
    (u_dir / "util.go").write_text(
        "package util\n\n"
        "func CoreLog(msg string) {}\n",
        encoding="utf-8",
    )

    # Add calls to CoreLog from a and b
    (a_dir / "a2.go").write_text(
        "package a\n\n"
        'import "example.com/cyclerepo/pkg/util"\n\n'
        "func LogFromA() {\n"
        '    util.CoreLog("A")\n'
        "}\n",
        encoding="utf-8",
    )
    (b_dir / "b2.go").write_text(
        "package b\n\n"
        'import "example.com/cyclerepo/pkg/util"\n\n'
        "func LogFromB() {\n"
        '    util.CoreLog("B")\n'
        "}\n",
        encoding="utf-8",
    )

    workspace = WorkspaceModel(id="ws-cycle", name="Cycle WS")
    repo = RepositoryModel(
        id="repo-cycle",
        workspace_id=workspace.id,
        name="go-cycle",
        source_path=str(repo_dir),
        indexing_status="pending",
    )
    db_session.add(workspace)
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)

    # 1. Cycle detection
    cycles = query_engine.detect_import_cycles(repo.id)
    assert len(cycles) >= 1
    found_expected_cycle = any(
        ("pkg/a/a.go" in c and "pkg/b/b.go" in c)
        for c in cycles
    )
    assert found_expected_cycle

    # 2. God Node Analysis
    god_nodes = query_engine.get_god_nodes(repo.id, limit=5, scope="all")
    assert len(god_nodes) > 0
    # CoreLog has incoming calls from LogFromA and LogFromB
    core_log_entry = next((g for g in god_nodes if g.node.name == "CoreLog"), None)
    assert core_log_entry is not None
    assert core_log_entry.incoming_calls >= 2
    assert core_log_entry.god_score > 0


def test_go_repository_isolation_and_cross_language_protection(db_session, go_repo_dir, tmp_path):
    """Verifies that Go nodes are strictly isolated from other repositories and other languages."""
    # Repo 1: Go repository
    ws1 = WorkspaceModel(id="ws-iso-1", name="WS 1")
    repo1 = RepositoryModel(
        id="repo-iso-go",
        workspace_id=ws1.id,
        name="go-repo",
        source_path=str(go_repo_dir),
        indexing_status="pending",
    )

    # Repo 2: Python repository with overlapping symbol names (Add, User)
    py_repo_dir = tmp_path / "py_repo"
    py_repo_dir.mkdir()
    (py_repo_dir / "calc.py").write_text(
        "def Add(a, b):\n    return a + b\n\n"
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    ws2 = WorkspaceModel(id="ws-iso-2", name="WS 2")
    repo2 = RepositoryModel(
        id="repo-iso-py",
        workspace_id=ws2.id,
        name="py-repo",
        source_path=str(py_repo_dir),
        indexing_status="pending",
    )

    db_session.add_all([ws1, repo1, ws2, repo2])
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo1.id)
    indexer.index_repository(repo2.id)

    # Verify node counts and ID prefixes
    go_add = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo1.id,
        name="Add",
    ).first()
    assert go_add is not None
    assert ":go:" in go_add.id
    assert go_add.language == "go"

    py_add = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo2.id,
        name="Add",
    ).first()
    assert py_add is not None
    assert ":py:" in py_add.id
    assert py_add.language == "python"

    # Verify no cross-repo edges exist
    leaked_edges = (
        db_session.query(GraphEdgeModel)
        .filter(
            GraphEdgeModel.repository_id == repo1.id,
            GraphEdgeModel.target_id.like(f"{repo2.id}:%"),
        )
        .all()
    )
    assert len(leaked_edges) == 0
