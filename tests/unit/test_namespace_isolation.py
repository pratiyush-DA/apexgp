"""Unit tests for namespace isolation, cross-language boundary protection, and graph integrity.

Verifies:
1. Same-name symbol collisions between languages do not produce false cross-language edges.
2. Legitimate same-language call resolution is preserved.
3. JavaScript / TypeScript bidirectional interoperability is preserved.
4. External Python imports create correctly formatted external stubs (ext:py:*).
5. External TypeScript/JavaScript imports create correctly formatted external stubs (ext:ts:* / ext:js:*)
   and do not collide with identically named internal Python modules.
6. Graph integrity: zero dangling internal edges, valid external stubs.
7. Query engine contracts (callers, callees, dependencies, shortest path, impact, tests) remain intact.
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


@pytest.fixture
def db_session():
    """Provides an isolated in-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def polyglot_repo_path(tmp_path):
    """Creates a controlled polyglot repository fixture with Python, TypeScript, and JavaScript."""
    # 1. Python package
    py_dir = tmp_path / "py_pkg"
    py_dir.mkdir()

    (py_dir / "utils.py").write_text(
        'def next(items):\n'
        '    """Python-specific next function."""\n'
        '    return items[0]\n'
        '\n'
        'def compute_sum(a: int, b: int) -> int:\n'
        '    """Adds two numbers."""\n'
        '    return a + b\n',
        encoding="utf-8",
    )

    (py_dir / "service.py").write_text(
        'import requests\n'
        'from py_pkg.utils import next, compute_sum\n'
        '\n'
        'def run_py_service():\n'
        '    total = compute_sum(10, 20)\n'
        '    return next([total])\n'
        '\n'
        'def py_calls_unimplemented_ts():\n'
        '    return ts_only_symbol()\n',
        encoding="utf-8",
    )

    (py_dir / "test_service.py").write_text(
        'from py_pkg.service import run_py_service\n'
        '\n'
        'def test_service_execution():\n'
        '    assert run_py_service() == 30\n',
        encoding="utf-8",
    )

    # 2. TypeScript / JavaScript package
    ts_dir = tmp_path / "ts_pkg"
    ts_dir.mkdir()

    (ts_dir / "helper.ts").write_text(
        'export function next(callback: () => void): void {\n'
        '  callback();\n'
        '}\n'
        '\n'
        'export function formatValue(val: string): string {\n'
        '  return val.toUpperCase();\n'
        '}\n'
        '\n'
        'export function ts_only_symbol(): string {\n'
        '  return "ts_only";\n'
        '}\n',
        encoding="utf-8",
    )

    (ts_dir / "client.ts").write_text(
        'import { next, formatValue } from "./helper";\n'
        '\n'
        'export function runClient(): string {\n'
        '  const formatted = formatValue("data");\n'
        '  next(() => {});\n'
        '  return formatted;\n'
        '}\n'
        '\n'
        'export function tsCallsUnimplementedPy(): void {\n'
        '  compute_sum(1, 2);\n'
        '}\n',
        encoding="utf-8",
    )

    (ts_dir / "legacy.js").write_text(
        'import { formatValue } from "./helper";\n'
        '\n'
        'export function legacyFormat(msg) {\n'
        '  return formatValue(msg);\n'
        '}\n',
        encoding="utf-8",
    )

    (ts_dir / "server.ts").write_text(
        'import express from "express";\n'
        'import { legacyFormat } from "./legacy";\n'
        '\n'
        'export function startServer(): string {\n'
        '  const app = express();\n'
        '  return legacyFormat("server started");\n'
        '}\n',
        encoding="utf-8",
    )

    # 3. Unrelated Python file named express.py at repository root
    (tmp_path / "express.py").write_text(
        'def express():\n'
        '    return "internal python express"\n',
        encoding="utf-8",
    )

    return str(tmp_path)


@pytest.fixture
def indexed_polyglot_repo(db_session, polyglot_repo_path):
    """Indexes the polyglot repository and yields (repo_model, query_engine)."""
    ws = WorkspaceModel(name="Polyglot Workspace")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="Polyglot Repo",
        source_type="local",
        source_path=polyglot_repo_path,
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexed_repo = indexer.index_repository(repo.id)
    assert indexed_repo.indexing_status == "ready"

    query_engine = GraphQueryEngine(db_session)
    return indexed_repo, query_engine, db_session


def test_same_name_symbol_collision_prevention(indexed_polyglot_repo):
    """Scenario 1: Python and TypeScript each define 'next'.

    An ambiguous call must not produce a false cross-language edge, even when
    the target name is unique in the other language.
    """
    repo, query_engine, session = indexed_polyglot_repo

    # Find both definitions of 'next'
    next_symbols = query_engine.find_symbol("next", repo_id=repo.id)
    assert len(next_symbols) == 2

    py_next = next(s for s in next_symbols if s.language == "python")
    ts_next = next(s for s in next_symbols if s.language == "typescript")

    # 1. Inspect callers of py_next
    py_callers = query_engine.get_callers(py_next.id)
    py_caller_names = {c["node"]["name"] for c in py_callers}
    py_caller_langs = {c["node"]["language"] for c in py_callers}
    assert "run_py_service" in py_caller_names
    assert "runClient" not in py_caller_names
    assert py_caller_langs == {"python"}

    # 2. Inspect callers of ts_next
    ts_callers = query_engine.get_callers(ts_next.id)
    ts_caller_names = {c["node"]["name"] for c in ts_callers}
    ts_caller_langs = {c["node"]["language"] for c in ts_callers}
    assert "runClient" in ts_caller_names
    assert "run_py_service" not in ts_caller_names
    assert ts_caller_langs == {"typescript"}

    # 3. Single candidate in another language must NOT link:
    # tsCallsUnimplementedPy calls compute_sum (only defined in Python)
    # py_calls_unimplemented_ts calls ts_only_symbol (only defined in TypeScript)
    compute_sum_node = query_engine.find_symbol("compute_sum", repo_id=repo.id)[0]
    compute_sum_callers = query_engine.get_callers(compute_sum_node.id)
    compute_caller_names = {c["node"]["name"] for c in compute_sum_callers}
    assert "tsCallsUnimplementedPy" not in compute_caller_names
    assert "run_py_service" in compute_caller_names

    ts_only_node = query_engine.find_symbol("ts_only_symbol", repo_id=repo.id)[0]
    ts_only_callers = query_engine.get_callers(ts_only_node.id)
    ts_only_caller_names = {c["node"]["name"] for c in ts_only_callers}
    assert "py_calls_unimplemented_ts" not in ts_only_caller_names


def test_legitimate_same_language_call_resolution(indexed_polyglot_repo):
    """Scenario 2: Valid Python-to-Python and TypeScript-to-TypeScript calls resolve cleanly."""
    repo, query_engine, session = indexed_polyglot_repo

    # Python -> Python
    compute_sum_node = query_engine.find_symbol("compute_sum", repo_id=repo.id)[0]
    py_callers = query_engine.get_callers(compute_sum_node.id)
    assert any(c["node"]["name"] == "run_py_service" for c in py_callers)

    # TypeScript -> TypeScript
    format_val_node = query_engine.find_symbol("formatValue", repo_id=repo.id)[0]
    ts_callers = query_engine.get_callers(format_val_node.id)
    ts_caller_names = {c["node"]["name"] for c in ts_callers}
    assert "runClient" in ts_caller_names


def test_javascript_typescript_interoperability(indexed_polyglot_repo):
    """Scenario 3: Supported imports and calls between JavaScript and TypeScript are preserved."""
    repo, query_engine, session = indexed_polyglot_repo

    # 1. JS calling TS function: legacy.js calling formatValue in helper.ts
    format_val_node = query_engine.find_symbol("formatValue", repo_id=repo.id)[0]
    format_callers = query_engine.get_callers(format_val_node.id)
    caller_names = {c["node"]["name"] for c in format_callers}
    assert "legacyFormat" in caller_names

    # 2. TS calling JS function: server.ts calling legacyFormat in legacy.js
    legacy_node = query_engine.find_symbol("legacyFormat", repo_id=repo.id)[0]
    legacy_callers = query_engine.get_callers(legacy_node.id)
    caller_names = {c["node"]["name"] for c in legacy_callers}
    assert "startServer" in caller_names

    # 3. REFERENCES edge across JS/TS boundary
    ref_edges = (
        session.query(GraphEdgeModel)
        .filter(
            GraphEdgeModel.repository_id == repo.id,
            GraphEdgeModel.edge_type == "REFERENCES",
            GraphEdgeModel.file_path == "ts_pkg/legacy.js",
        )
        .all()
    )
    assert len(ref_edges) >= 1
    assert any(e.target_id.endswith(":ts_pkg/helper.ts:formatValue") for e in ref_edges)


def test_external_python_import_stub(indexed_polyglot_repo):
    """Scenario 4: An import such as 'requests' produces a correctly typed external stub."""
    repo, query_engine, session = indexed_polyglot_repo

    edges = (
        session.query(GraphEdgeModel)
        .filter(
            GraphEdgeModel.repository_id == repo.id,
            GraphEdgeModel.edge_type == "IMPORTS",
            GraphEdgeModel.file_path == "py_pkg/service.py",
        )
        .all()
    )

    ext_edges = [e for e in edges if e.target_id.startswith("ext:")]
    assert len(ext_edges) == 1
    ext_edge = ext_edges[0]
    assert ext_edge.target_id == "ext:py:requests"
    assert ext_edge.confidence == 1.0
    assert "requests" in ext_edge.evidence


def test_external_javascript_import_stub(indexed_polyglot_repo):
    """Scenario 5: An import such as 'express' produces a correctly typed external stub

    and does NOT resolve to an unrelated internal python module express.py.
    """
    repo, query_engine, session = indexed_polyglot_repo

    edges = (
        session.query(GraphEdgeModel)
        .filter(
            GraphEdgeModel.repository_id == repo.id,
            GraphEdgeModel.edge_type == "IMPORTS",
            GraphEdgeModel.file_path == "ts_pkg/server.ts",
        )
        .all()
    )

    target_ids = {e.target_id for e in edges}
    # Must have external stub
    assert "ext:ts:express" in target_ids

    # Must NOT have an import edge to express.py
    express_py_node = session.query(GraphNodeModel).filter_by(
        repository_id=repo.id, file_path="express.py", node_type="FILE"
    ).first()
    assert express_py_node is not None
    assert express_py_node.id not in target_ids


def test_graph_integrity_and_external_representation(indexed_polyglot_repo):
    """Scenario 6: Graph integrity.

    - Every edge source_id must exist in graph_nodes.
    - Every internal edge target_id must exist in graph_nodes (zero dangling internal edges).
    - Every external stub must follow documented representation (ext:<runtime>:<package>).
    """
    repo, query_engine, session = indexed_polyglot_repo

    all_edges = session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    assert len(all_edges) > 0

    all_node_ids = {n.id for n in session.query(GraphNodeModel.id).filter_by(repository_id=repo.id).all()}

    dangling_internal_edges = []
    invalid_external_stubs = []

    for e in all_edges:
        # Source must always exist
        assert e.source_id in all_node_ids, f"Edge {e.id} has invalid source {e.source_id}"

        if e.target_id.startswith("ext:"):
            parts = e.target_id.split(":", 2)
            if len(parts) != 3 or parts[1] not in ("py", "ts", "js") or not parts[2]:
                invalid_external_stubs.append(e.target_id)
            # External stub must NOT exist as an internal node ID
            assert e.target_id not in all_node_ids
        else:
            if e.target_id not in all_node_ids:
                dangling_internal_edges.append((e.id, e.target_id))

    assert dangling_internal_edges == [], f"Dangling internal edges detected: {dangling_internal_edges}"
    assert invalid_external_stubs == [], f"Invalid external stubs detected: {invalid_external_stubs}"


def test_regression_preservation_across_query_engine(indexed_polyglot_repo):
    """Scenario 7: Regression preservation across caller, callee, dependencies, shortest path, impact, and tests."""
    repo, query_engine, session = indexed_polyglot_repo

    # 1. Dependency query
    service_file_id = f"{repo.id}:file:py_pkg/service.py"
    deps = query_engine.get_dependencies(service_file_id)
    assert len(deps["external_dependencies"]) >= 1
    assert any(d["id"] == "ext:py:requests" for d in deps["external_dependencies"])

    server_file_id = f"{repo.id}:file:ts_pkg/server.ts"
    server_deps = query_engine.get_dependencies(server_file_id)
    assert len(server_deps["external_dependencies"]) >= 1
    assert any(d["id"] == "ext:ts:express" for d in server_deps["external_dependencies"])

    # 2. Shortest-path trace relationships
    run_service_node = query_engine.find_symbol("run_py_service", repo_id=repo.id)[0]
    compute_sum_node = query_engine.find_symbol("compute_sum", repo_id=repo.id)[0]
    trace = query_engine.trace_relationships(run_service_node.id, compute_sum_node.id)
    assert trace is not None
    assert len(trace) >= 1
    assert trace[0]["edge_type"] == "CALLS"

    # 3. Change impact analysis and test discovery
    impact = query_engine.analyze_change_impact("compute_sum", repo_id=repo.id)
    assert impact.total_affected >= 1
    assert "py_pkg/service.py" in impact.affected_files
    test_names = {t.name for t in impact.linked_tests}
    assert "test_service_execution" in test_names or any("test" in f for f in impact.affected_files)
