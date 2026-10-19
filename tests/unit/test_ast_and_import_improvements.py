"""Unit tests for Step 2: AST and Import-Resolution Improvements.

Verifies:
Workstream A: TypeScript path aliases (baseUrl, paths, wildcards, unresolved, external, isolation from .py)
Workstream B: Python decorator metadata and relationships (@app.get, @router.post, multiple decorators, REFERENCES edges, no synthetic CALLS)
Workstream C: JS/TS barrel re-exports (named, aliased, wildcard, default, external, circular cycle prevention)
Step 1 Regressions: Collision isolation, external stubs, zero dangling edges, query engine intact.
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
from packages.core.parsers.python_parser import PythonParser
from packages.core.parsers.ts_js_parser import TypeScriptJavaScriptParser
from packages.core.query_engine import GraphQueryEngine
from tests.unit.test_namespace_isolation import polyglot_repo_path


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# -------------------------------------------------------------------------
# Workstream A: TypeScript Path Aliases Tests
# -------------------------------------------------------------------------


def test_ts_path_aliases_wildcard_and_specific(tmp_path, db_session):
    """Verifies that tsconfig.json baseUrl and paths resolve wildcards and specific mappings."""
    # Create tsconfig.json
    tsconfig = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@/*": ["src/*"],
                "@components/*": ["src/components/*"],
                "shared-utils": ["src/lib/utils.ts"],
            },
        }
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig), encoding="utf-8")

    # Create directories and files
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    comp_dir = src_dir / "components"
    comp_dir.mkdir()
    lib_dir = src_dir / "lib"
    lib_dir.mkdir()

    (lib_dir / "utils.ts").write_text(
        'export function formatDate(d: string): string {\n'
        '  return d.trim();\n'
        '}\n',
        encoding="utf-8",
    )

    (comp_dir / "Button.tsx").write_text(
        'export function Button(): string {\n'
        '  return "<button />";\n'
        '}\n',
        encoding="utf-8",
    )

    (src_dir / "auth.ts").write_text(
        'export function authenticate(token: string): boolean {\n'
        '  return token.length > 0;\n'
        '}\n',
        encoding="utf-8",
    )

    # Consumer importing via aliases
    (src_dir / "app.ts").write_text(
        'import { authenticate } from "@/auth";\n'
        'import { Button } from "@components/Button";\n'
        'import { formatDate } from "shared-utils";\n'
        'import express from "express";\n'
        'import { missing } from "@/does_not_exist";\n'
        '\n'
        'export function main(): void {\n'
        '  authenticate("secret");\n'
        '  Button();\n'
        '  formatDate("2026-10-18");\n'
        '}\n',
        encoding="utf-8",
    )

    # Add a python file with same name to test language isolation
    py_dir = tmp_path / "src" / "auth.py"
    py_dir.write_text('def authenticate(token):\n    return False\n', encoding="utf-8")

    ws = WorkspaceModel(name="TS Alias WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="alias-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Verify nodes
    app_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, file_path="src/app.ts").all()
    assert len(app_nodes) > 0

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()

    # 1. Check IMPORTS edges
    import_targets = {e.target_id for e in edges if e.edge_type == "IMPORTS"}
    assert f"{repo.id}:file:src/auth.ts" in import_targets
    assert f"{repo.id}:file:src/components/Button.tsx" in import_targets
    assert f"{repo.id}:file:src/lib/utils.ts" in import_targets

    # 2. Check external stubs for genuine external package (express) and unresolved alias
    assert "ext:ts:express" in import_targets
    assert "ext:ts:@/does_not_exist" in import_targets

    # 3. Check REFERENCES edges for imported symbols
    ref_targets = {e.target_id for e in edges if e.edge_type == "REFERENCES"}
    assert f"{repo.id}:ts:src/auth.ts:authenticate" in ref_targets
    assert f"{repo.id}:ts:src/components/Button.tsx:Button" in ref_targets
    assert f"{repo.id}:ts:src/lib/utils.ts:formatDate" in ref_targets

    # 4. Check CALLS edges from main
    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    calls_targets = {e.target_id for e in calls_edges}
    assert f"{repo.id}:ts:src/auth.ts:authenticate" in calls_targets
    assert f"{repo.id}:ts:src/components/Button.tsx:Button" in calls_targets
    assert f"{repo.id}:ts:src/lib/utils.ts:formatDate" in calls_targets

    # 5. Isolation: TS alias "@/auth" must NOT resolve to "src/auth.py"
    for e in edges:
        if e.source_id == f"{repo.id}:file:src/app.ts":
            assert "auth.py" not in e.target_id


def test_ts_base_url_direct_resolution(tmp_path, db_session):
    """Verifies that baseUrl without paths resolves non-relative imports to internal files."""
    tsconfig = {
        "compilerOptions": {
            "baseUrl": "src",
        }
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig), encoding="utf-8")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    core_dir = src_dir / "core"
    core_dir.mkdir()

    (core_dir / "logger.ts").write_text(
        'export function log(msg: string): void {\n'
        '  console.log(msg);\n'
        '}\n',
        encoding="utf-8",
    )

    (src_dir / "index.ts").write_text(
        'import { log } from "core/logger";\n'
        'export function run(): void {\n'
        '  log("starting");\n'
        '}\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="BaseUrl WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="base-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    import_targets = {e.target_id for e in edges if e.edge_type == "IMPORTS"}
    assert f"{repo.id}:file:src/core/logger.ts" in import_targets


# -------------------------------------------------------------------------
# Workstream B: Python Decorator Metadata and Relationships Tests
# -------------------------------------------------------------------------


def test_python_decorator_parsing():
    """Unit tests PythonParser extraction of decorators and framework routes."""
    parser = PythonParser()
    code = (
        'def login_required(func):\n'
        '    return func\n'
        '\n'
        '@login_required\n'
        'def protected_view():\n'
        '    return "ok"\n'
        '\n'
        '@app.get("/items")\n'
        'def list_items():\n'
        '    return []\n'
        '\n'
        '@router.post("/calc")\n'
        '@audit_log(event="calculation")\n'
        'def calculate_total():\n'
        '    return 42\n'
        '\n'
        '@dataclass\n'
        'class UserRecord:\n'
        '    name: str\n'
        '\n'
        'def plain_function():\n'
        '    return "no decorators"\n'
    )

    res = parser.parse_code(code, file_path="api.py")
    assert len(res.syntax_errors) == 0
    nodes_by_name = {n.name: n for n in res.nodes}

    # 1. protected_view
    pv = nodes_by_name["protected_view"]
    assert "login_required" in pv.metadata.get("decorator_names", [])
    assert any("@login_required" in d for d in pv.metadata.get("decorators", []))
    assert pv.metadata.get("route") is None

    # 2. list_items: Route extraction
    li = nodes_by_name["list_items"]
    assert "app.get" in li.metadata.get("decorator_names", [])
    route_li = li.metadata.get("route")
    assert route_li is not None
    assert route_li["path"] == "/items"
    assert route_li["method"] == "GET"
    assert route_li["methods"] == ["GET"]
    assert route_li["decorator"] == "app.get"

    # 3. calculate_total: Multiple decorators and route
    ct = nodes_by_name["calculate_total"]
    assert "router.post" in ct.metadata.get("decorator_names", [])
    assert "audit_log" in ct.metadata.get("decorator_names", [])
    route_ct = ct.metadata.get("route")
    assert route_ct is not None
    assert route_ct["path"] == "/calc"
    assert route_ct["method"] == "POST"
    assert route_ct["methods"] == ["POST"]
    assert route_ct["decorator"] == "router.post"

    # 4. UserRecord: Class decorator
    ur = nodes_by_name["UserRecord"]
    assert "dataclass" in ur.metadata.get("decorator_names", [])

    # 5. plain_function: No decorators
    pf = nodes_by_name["plain_function"]
    assert "decorator_names" not in pf.metadata or len(pf.metadata["decorator_names"]) == 0

    # 6. Verify NO fake calls for route decorators
    callees = {c.callee_name for c in res.calls if c.caller_name == "list_items"}
    assert "get" not in callees
    assert "items" not in callees


def test_python_decorator_graph_relationships(tmp_path, db_session):
    """Verifies that decorator definitions in the repo produce REFERENCES edges."""
    api_py = tmp_path / "api.py"
    api_py.write_text(
        'def require_admin(func):\n'
        '    def wrapper(*args, **kwargs):\n'
        '        return func(*args, **kwargs)\n'
        '    return wrapper\n'
        '\n'
        '@require_admin\n'
        'def delete_user(user_id: int):\n'
        '    return True\n'
        '\n'
        '@app.get("/status")\n'
        'def get_status():\n'
        '    return {"status": "ok"}\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Deco WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="deco-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()

    # Check REFERENCES edge from delete_user to require_admin
    deco_edges = [
        e for e in edges
        if e.source_id == f"{repo.id}:py:api.py:delete_user"
        and e.target_id == f"{repo.id}:py:api.py:require_admin"
        and e.edge_type == "REFERENCES"
    ]
    assert len(deco_edges) == 1
    assert "Decorated by @require_admin" in deco_edges[0].evidence

    # Verify no synthetic CALLS edge from get_status to /status or get
    status_calls = [
        e for e in edges
        if e.source_id == f"{repo.id}:py:api.py:get_status" and e.edge_type == "CALLS"
    ]
    assert len(status_calls) == 0


# -------------------------------------------------------------------------
# Workstream C: JS/TS Barrel Re-Exports Tests
# -------------------------------------------------------------------------


def test_ts_js_barrel_reexports_and_resolution(tmp_path, db_session):
    """Verifies direct, aliased, chained, wildcard, external, and circular re-exports."""
    # 1. math.ts
    (tmp_path / "math.ts").write_text(
        'export function add(a: number, b: number): number {\n'
        '  return a + b;\n'
        '}\n'
        '\n'
        'export function multiply(a: number, b: number): number {\n'
        '  return a * b;\n'
        '}\n',
        encoding="utf-8",
    )

    # 2. types.ts
    (tmp_path / "types.ts").write_text(
        'export interface UserConfig {\n'
        '  id: string;\n'
        '  theme: string;\n'
        '}\n',
        encoding="utf-8",
    )

    # 3. app.ts
    (tmp_path / "app.ts").write_text(
        'export default function App(): string {\n'
        '  return "app";\n'
        '}\n',
        encoding="utf-8",
    )

    # 4. barrel: index.ts (named, aliased, wildcard, default, external)
    (tmp_path / "index.ts").write_text(
        'export { add } from "./math";\n'
        'export { multiply as mult } from "./math";\n'
        'export * from "./types";\n'
        'export { default as MainApp } from "./app";\n'
        'export { Router } from "express";\n',
        encoding="utf-8",
    )

    # 5. chained re-export: api.ts re-exports from index.ts
    (tmp_path / "api.ts").write_text(
        'export { add, mult } from "./index";\n',
        encoding="utf-8",
    )

    # 6. consumer: main.ts importing through the chain
    (tmp_path / "main.ts").write_text(
        'import { add, mult } from "./api";\n'
        'export function execute(): number {\n'
        '  return add(2, 3) + mult(4, 5);\n'
        '}\n',
        encoding="utf-8",
    )

    # 7. Circular re-export pair (must not cause infinite recursion)
    (tmp_path / "circle_a.ts").write_text(
        'export * from "./circle_b";\n'
        'export function fromA(): string { return "A"; }\n',
        encoding="utf-8",
    )
    (tmp_path / "circle_b.ts").write_text(
        'export * from "./circle_a";\n'
        'export function fromB(): string { return "B"; }\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Barrel WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="barrel-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()

    # Verify barrel index.ts has IMPORTS to math.ts, types.ts, app.ts
    barrel_imports = {
        e.target_id for e in edges
        if e.source_id == f"{repo.id}:file:index.ts" and e.edge_type == "IMPORTS"
    }
    assert f"{repo.id}:file:math.ts" in barrel_imports
    assert f"{repo.id}:file:types.ts" in barrel_imports
    assert f"{repo.id}:file:app.ts" in barrel_imports
    assert "ext:ts:express" in barrel_imports

    # Verify re-export REFERENCES edges from index.ts to original symbols
    barrel_refs = {
        e.target_id for e in edges
        if e.source_id == f"{repo.id}:file:index.ts" and e.edge_type == "REFERENCES"
    }
    assert f"{repo.id}:ts:math.ts:add" in barrel_refs
    assert f"{repo.id}:ts:math.ts:multiply" in barrel_refs

    # Verify main.ts resolved add and mult through api.ts -> index.ts -> math.ts
    main_refs = {
        e.target_id for e in edges
        if e.source_id == f"{repo.id}:file:main.ts" and e.edge_type == "REFERENCES"
    }
    assert f"{repo.id}:ts:math.ts:add" in main_refs

    # Verify CALLS from main.ts:execute to math.ts:add and math.ts:multiply
    call_targets = {
        e.target_id for e in edges
        if e.source_id == f"{repo.id}:ts:main.ts:execute" and e.edge_type == "CALLS"
    }
    assert f"{repo.id}:ts:math.ts:add" in call_targets
    assert f"{repo.id}:ts:math.ts:multiply" in call_targets

    # Verify circular re-exports did not crash and circular files have nodes
    circle_nodes = db_session.query(GraphNodeModel).filter(
        GraphNodeModel.repository_id == repo.id,
        GraphNodeModel.file_path.in_(["circle_a.ts", "circle_b.ts"]),
    ).all()
    assert len(circle_nodes) >= 4  # 2 files + 2 functions


# -------------------------------------------------------------------------
# Step 1 Regression Protection Tests
# -------------------------------------------------------------------------


def test_step1_regression_guarantees_preserved(polyglot_repo_path, db_session):
    """Verifies that all Step 1 namespace isolation and query engine guarantees hold."""
    ws = WorkspaceModel(name="Polyglot Workspace")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="polyglot-repo",
        source_type="local",
        source_path=str(polyglot_repo_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()

    # 1. Zero cross-language CALLS edges
    py_caller_id = f"{repo.id}:py:py_pkg/service.py:run_py_service"
    py_calls = [e for e in edges if e.source_id == py_caller_id and e.edge_type == "CALLS"]
    for c in py_calls:
        assert not c.target_id.startswith(f"{repo.id}:ts:")
        assert not c.target_id.startswith(f"{repo.id}:js:")

    # 2. Correct same-language call resolution
    py_callee_targets = {c.target_id for c in py_calls}
    assert f"{repo.id}:py:py_pkg/utils.py:next" in py_callee_targets
    assert f"{repo.id}:py:py_pkg/utils.py:compute_sum" in py_callee_targets

    # 3. External stub preservation
    external_targets = {e.target_id for e in edges if e.target_id.startswith("ext:")}
    assert "ext:py:requests" in external_targets

    # 4. Zero dangling internal edges
    all_node_ids = {n.id for n in db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()}
    for e in edges:
        assert e.source_id in all_node_ids
        if not e.target_id.startswith("ext:"):
            assert e.target_id in all_node_ids

    # 5. Query engine contracts
    qe = GraphQueryEngine(db_session)
    callers = qe.get_callers(f"{repo.id}:py:py_pkg/utils.py:next")
    assert len(callers) >= 1
    assert any(c["node"]["id"] == py_caller_id for c in callers)


def test_ts_path_aliases_ambiguous_and_priority(tmp_path, db_session):
    """Verifies that more specific alias mappings and exact mappings resolve correctly
    even when broader wildcard aliases are defined first in tsconfig.json (adversarial order)."""
    tsconfig = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@/*": ["src/*"],  # Broad wildcard defined first in JSON
                "@components/*": ["src/components/*"],  # More specific wildcard
                "@components/Special": ["src/components/CustomModal.tsx"],  # Exact match
            },
        }
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig), encoding="utf-8")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    comp_dir = src_dir / "components"
    comp_dir.mkdir()
    utils_dir = src_dir / "utils"
    utils_dir.mkdir()

    (comp_dir / "CustomModal.tsx").write_text(
        'export function SpecialModal(): string { return "custom"; }\n',
        encoding="utf-8",
    )
    (comp_dir / "Modal.tsx").write_text(
        'export function Modal(): string { return "modal"; }\n',
        encoding="utf-8",
    )
    (utils_dir / "helper.ts").write_text(
        'export function help(): string { return "help"; }\n',
        encoding="utf-8",
    )

    (src_dir / "consumer.ts").write_text(
        'import { SpecialModal } from "@components/Special";\n'
        'import { Modal } from "@components/Modal";\n'
        'import { help } from "@/utils/helper";\n'
        'export function show(): string {\n'
        '  return SpecialModal() + Modal() + help();\n'
        '}\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Ambiguous WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="ambiguous-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    import_targets = {e.target_id for e in edges if e.edge_type == "IMPORTS"}
    assert f"{repo.id}:file:src/components/CustomModal.tsx" in import_targets
    assert f"{repo.id}:file:src/components/Modal.tsx" in import_targets
    assert f"{repo.id}:file:src/utils/helper.ts" in import_targets

    call_targets = {
        e.target_id for e in edges
        if e.source_id == f"{repo.id}:ts:src/consumer.ts:show" and e.edge_type == "CALLS"
    }
    assert f"{repo.id}:ts:src/components/CustomModal.tsx:SpecialModal" in call_targets
    assert f"{repo.id}:ts:src/components/Modal.tsx:Modal" in call_targets
    assert f"{repo.id}:ts:src/utils/helper.ts:help" in call_targets


def test_python_decorator_methods_and_route_variants():
    """Verifies class methods with decorators, keyword path args, and multi-method route variants."""
    parser = PythonParser()
    code = (
        'class UserController:\n'
        '    @staticmethod\n'
        '    def helper():\n'
        '        return True\n'
        '\n'
        '    @router.get(path="/users/{user_id}")\n'
        '    def get_user(self, user_id: int):\n'
        '        return {"id": user_id}\n'
        '\n'
        '    @api.route(rule="/create", methods=["POST", "PUT"])\n'
        '    def create_user(self):\n'
        '        return "created"\n'
        '\n'
        '    @app.post(path="/items")\n'
        '    def post_item():\n'
        '        return "item"\n'
    )

    res = parser.parse_code(code, file_path="controllers.py")
    assert len(res.syntax_errors) == 0

    nodes_by_name = {n.name: n for n in res.nodes}

    # 1. staticmethod
    helper = nodes_by_name["helper"]
    assert helper.node_type == "METHOD"
    assert "staticmethod" in helper.metadata.get("decorator_names", [])

    # 2. get_user: keyword argument path="/users/{user_id}"
    gu = nodes_by_name["get_user"]
    assert gu.node_type == "METHOD"
    route_gu = gu.metadata.get("route")
    assert route_gu is not None
    assert route_gu["path"] == "/users/{user_id}"
    assert route_gu["method"] == "GET"
    assert route_gu["methods"] == ["GET"]
    assert route_gu["decorator"] == "router.get"

    # 3. create_user: keyword argument rule="/create", methods=["POST", "PUT"]
    cu = nodes_by_name["create_user"]
    assert cu.node_type == "METHOD"
    route_cu = cu.metadata.get("route")
    assert route_cu is not None
    assert route_cu["path"] == "/create"
    assert route_cu["method"] == "POST"
    assert route_cu["methods"] == ["POST", "PUT"]
    assert route_cu["decorator"] == "api.route"

    # 4. post_item: keyword argument path="/items"
    pi = nodes_by_name["post_item"]
    route_pi = pi.metadata.get("route")
    assert route_pi is not None
    assert route_pi["path"] == "/items"
    assert route_pi["method"] == "POST"
    assert route_pi["methods"] == ["POST"]

    # 5. Zero fake calls from route annotations
    callees = {c.callee_name for c in res.calls if c.caller_name == "UserController.get_user"}
    assert "get" not in callees
    assert "path" not in callees
    assert "users" not in callees


def test_ts_js_barrel_reexport_edge_cases(tmp_path, db_session):
    """Verifies namespace re-exports, multi-hop chains, and non-existent re-exports."""
    (tmp_path / "lib.ts").write_text(
        'export function coreFn(): string { return "core"; }\n',
        encoding="utf-8",
    )

    (tmp_path / "barrel1.ts").write_text(
        'export { coreFn } from "./lib";\n'
        'export { nonExistent } from "./lib";\n',
        encoding="utf-8",
    )

    (tmp_path / "barrel2.ts").write_text(
        'export { coreFn } from "./barrel1";\n'
        'export * as libNs from "./lib";\n',
        encoding="utf-8",
    )

    (tmp_path / "app.ts").write_text(
        'import { coreFn } from "./barrel2";\n'
        'export function run(): string {\n'
        '  return coreFn();\n'
        '}\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Edge WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="edge-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()

    # app.ts:run calls coreFn in lib.ts
    call_targets = {
        e.target_id for e in edges
        if e.source_id == f"{repo.id}:ts:app.ts:run" and e.edge_type == "CALLS"
    }
    assert f"{repo.id}:ts:lib.ts:coreFn" in call_targets
