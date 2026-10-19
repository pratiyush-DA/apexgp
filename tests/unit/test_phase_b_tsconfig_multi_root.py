"""Phase 9.13 Phase B: Multi-Root Nested TypeScript Configuration Tests

Verifies discovery of nested tsconfig.json files in subprojects (monorepos),
path alias resolution scoped to closest ancestor project root, 1-level extends inheritance,
strict cross-app isolation, and language isolation (no leakage to Python/YAML/SQL).
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from packages.core.indexer import RepositoryIndexer
from packages.core.models import Base, GraphEdgeModel, RepositoryModel, WorkspaceModel


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


def test_nested_tsconfigs_isolated_sibling_apps(tmp_path, db_session):
    """Verifies that sibling apps with identical alias '@/*' resolve to their own files and do not leak."""
    # 1. Setup root tsconfig.json (shared aliases only)
    root_tsconfig = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@shared/*": ["packages/shared/*"],
            },
        }
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(root_tsconfig), encoding="utf-8")

    # 2. Setup shared package
    shared_dir = tmp_path / "packages" / "shared"
    shared_dir.mkdir(parents=True)
    (shared_dir / "types.ts").write_text('export interface User { id: string; }\n', encoding="utf-8")

    # 3. Setup App A: apps/web
    web_dir = tmp_path / "apps" / "web"
    web_src = web_dir / "src"
    web_src.mkdir(parents=True)
    web_tsconfig = {
        "extends": "../../tsconfig.json",
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@/*": ["src/*"],
            },
        },
    }
    (web_dir / "tsconfig.json").write_text(json.dumps(web_tsconfig), encoding="utf-8")
    (web_src / "button.tsx").write_text('export function Button() { return "WebButton"; }\n', encoding="utf-8")
    (web_src / "main.ts").write_text(
        'import { Button } from "@/button";\n'
        'import { User } from "@shared/types";\n'
        'export function render() { return Button(); }\n',
        encoding="utf-8",
    )

    # 4. Setup App B: apps/mobile
    mobile_dir = tmp_path / "apps" / "mobile"
    mobile_src = mobile_dir / "src"
    mobile_src.mkdir(parents=True)
    mobile_tsconfig = {
        "extends": "../../tsconfig.json",
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@/*": ["src/*"],
            },
        },
    }
    (mobile_dir / "tsconfig.json").write_text(json.dumps(mobile_tsconfig), encoding="utf-8")
    (mobile_src / "button.tsx").write_text('export function Button() { return "MobileButton"; }\n', encoding="utf-8")
    (mobile_src / "index.ts").write_text(
        'import { Button } from "@/button";\n'
        'export function start() { return Button(); }\n',
        encoding="utf-8",
    )

    # 5. Index repository
    ws = WorkspaceModel(name="Monorepo WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="monorepo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    calls = [e for e in edges if e.edge_type == "CALLS"]

    # In apps/web/src/main.ts:
    # Button should resolve to apps/web/src/button.tsx
    web_calls = [e for e in calls if "apps/web/src/main.ts" in e.source_id or e.file_path == "apps/web/src/main.ts"]
    web_targets = {e.target_id for e in web_calls}
    assert f"{repo.id}:ts:apps/web/src/button.tsx:Button" in web_targets
    # Must NOT call mobile button
    assert f"{repo.id}:ts:apps/mobile/src/button.tsx:Button" not in web_targets

    # In apps/mobile/src/index.ts:
    # Button should resolve to apps/mobile/src/button.tsx
    mobile_calls = [e for e in calls if "apps/mobile/src/index.ts" in e.source_id or e.file_path == "apps/mobile/src/index.ts"]
    mobile_targets = {e.target_id for e in mobile_calls}
    assert f"{repo.id}:ts:apps/mobile/src/button.tsx:Button" in mobile_targets
    assert f"{repo.id}:ts:apps/web/src/button.tsx:Button" not in mobile_targets

    # Inherited root alias: @shared/types must resolve in web
    import_edges = [e for e in edges if e.edge_type == "IMPORTS" and e.file_path == "apps/web/src/main.ts"]
    import_targets = {e.target_id for e in import_edges}
    assert f"{repo.id}:file:packages/shared/types.ts" in import_targets


def test_language_isolation_no_leakage_to_python(tmp_path, db_session):
    """Verifies that nested TypeScript aliases do NOT resolve from Python or other languages."""
    app_dir = tmp_path / "frontend"
    app_src = app_dir / "src"
    app_src.mkdir(parents=True)
    tsconfig = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@auth/*": ["src/auth/*"],
            },
        }
    }
    (app_dir / "tsconfig.json").write_text(json.dumps(tsconfig), encoding="utf-8")
    (app_src / "auth" / "service.ts").parent.mkdir(parents=True)
    (app_src / "auth" / "service.ts").write_text('export function login() { return true; }\n', encoding="utf-8")

    # Python file trying to import something with same name
    py_dir = tmp_path / "backend"
    py_dir.mkdir(parents=True)
    (py_dir / "app.py").write_text('from src.auth.service import login\ndef run():\n    login()\n', encoding="utf-8")

    ws = WorkspaceModel(name="Polyglot Isolation WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="polyglot-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    py_edges = [e for e in edges if e.file_path == "backend/app.py"]
    # Python file must never link to TypeScript service.ts
    for e in py_edges:
        assert "frontend/src/auth/service.ts" not in e.target_id


def test_invalid_and_missing_tsconfigs_graceful_handling(tmp_path, db_session):
    """Verifies that invalid JSON in a nested tsconfig is logged and does not crash indexing."""
    bad_dir = tmp_path / "broken_project"
    bad_dir.mkdir(parents=True)
    (bad_dir / "tsconfig.json").write_text("{ this is not valid JSON !!!", encoding="utf-8")
    (bad_dir / "index.ts").write_text('export const x = 1;\n', encoding="utf-8")

    ws = WorkspaceModel(name="Broken WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="broken-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexed = indexer.index_repository(repo.id)
    assert indexed.indexing_status == "ready"


def test_ts_aliases_not_leaked_to_yaml_and_sql(tmp_path, db_session):
    """Verifies that TypeScript aliases do not leak into YAML or SQL files."""
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir(parents=True)
    tsconfig = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@config/*": ["config/*"],
            },
        }
    }
    (frontend_dir / "tsconfig.json").write_text(json.dumps(tsconfig), encoding="utf-8")
    (frontend_dir / "config" / "settings.ts").parent.mkdir(parents=True)
    (frontend_dir / "config" / "settings.ts").write_text('export const port = 3000;\n', encoding="utf-8")

    # YAML file referencing something named config
    (tmp_path / "schema.yaml").write_text('service:\n  settings: "@config/settings"\n', encoding="utf-8")
    # SQL file
    (tmp_path / "query.sql").write_text('SELECT * FROM "@config/settings";\n', encoding="utf-8")

    ws = WorkspaceModel(name="Polyglot WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="polyglot-repo-2",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    yaml_sql_edges = [e for e in edges if e.file_path in ("schema.yaml", "query.sql")]
    for e in yaml_sql_edges:
        assert "frontend/config/settings.ts" not in e.target_id


def test_nested_tsconfigs_incremental_update(tmp_path, db_session):
    """Verifies that incremental indexing refreshes discovered tsconfigs when a tsconfig.json changes."""
    app_dir = tmp_path / "apps" / "portal"
    app_src = app_dir / "src"
    app_src.mkdir(parents=True)

    tsconfig_v1 = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@v1/*": ["src/*"],
            },
        }
    }
    tsconfig_file = app_dir / "tsconfig.json"
    tsconfig_file.write_text(json.dumps(tsconfig_v1), encoding="utf-8")

    (app_src / "widget.tsx").write_text('export function Widget() { return "v1"; }\n', encoding="utf-8")
    (app_src / "main.ts").write_text('import { Widget } from "@v1/widget";\nexport function run() { return Widget(); }\n', encoding="utf-8")

    ws = WorkspaceModel(name="Incremental WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="inc-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    edges_v1 = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id, file_path="apps/portal/src/main.ts").all()
    call_targets_v1 = {e.target_id for e in edges_v1 if e.edge_type == "CALLS"}
    assert f"{repo.id}:ts:apps/portal/src/widget.tsx:Widget" in call_targets_v1

    # Now update tsconfig to v2 alias
    tsconfig_v2 = {
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "@v2/*": ["src/*"],
            },
        }
    }
    tsconfig_file.write_text(json.dumps(tsconfig_v2), encoding="utf-8")

    # Update main.ts to import from @v2
    (app_src / "main.ts").write_text('import { Widget } from "@v2/widget";\nexport function run() { return Widget(); }\n', encoding="utf-8")

    # Incremental update of tsconfig.json then main.ts
    indexer.update_file(repo.id, "apps/portal/tsconfig.json")
    indexer.update_file(repo.id, "apps/portal/src/main.ts")

    edges_v2 = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id, file_path="apps/portal/src/main.ts").all()
    call_targets_v2 = {e.target_id for e in edges_v2 if e.edge_type == "CALLS"}
    assert f"{repo.id}:ts:apps/portal/src/widget.tsx:Widget" in call_targets_v2

