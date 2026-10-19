"""Phase 9.13 Phase C: Retrieval Ranking and Structural Prior Tests

Verifies compound identifier decomposition, inflection stemming, contextual structural boost (+0.14)
for CLASS and INTERFACE containers, and deterministic tie-breaking.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.core.indexer import RepositoryIndexer
from packages.core.models import Base, GraphNodeModel, RepositoryModel, WorkspaceModel
from packages.core.query_engine import GraphQueryEngine, _terms_match, _tokenize_query_terms


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


def test_terms_match_inflections_and_prefixes():
    """Verifies that _terms_match correctly handles bidirectional prefixes and inflection stems."""
    # Exact match
    assert _terms_match("validator", "validator") is True
    # Prefix overlap (longer vs shorter)
    assert _terms_match("calc", "calculate") is True
    assert _terms_match("calculate", "calc") is True
    # Inflection stems (common root >= 5 chars or min_len - 1)
    assert _terms_match("registration", "registry") is True
    assert _terms_match("registry", "registration") is True
    assert _terms_match("registration", "register") is True
    assert _terms_match("validation", "validator") is True
    assert _terms_match("calculation", "calculate") is True
    # Unrelated terms must NOT match
    assert _terms_match("engine", "validator") is False
    assert _terms_match("query", "result") is False
    assert _terms_match("nonexistent", "calculator") is False


def test_structural_ranking_validator_registry(tmp_path, db_session):
    """Verifies that ValidatorRegistry ranks at #1 over helper decorator register_validator on structural query."""
    src_dir = tmp_path / "core"
    src_dir.mkdir(parents=True)

    code = (
        'class Registry:\n'
        '    """Base registry for components."""\n'
        '    pass\n\n'
        'class ValidatorRegistry(Registry):\n'
        '    """Registry for validator and engine classes."""\n'
        '    pass\n\n'
        'def register_validator(name: str):\n'
        '    """Decorator to register a validator engine."""\n'
        '    return name\n\n'
        'def get_validator_registry():\n'
        '    """Get global validator registry instance."""\n'
        '    return ValidatorRegistry()\n'
    )
    (src_dir / "registry.py").write_text(code, encoding="utf-8")

    ws = WorkspaceModel(name="Ranking WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="ranking-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)
    results = query_engine.search_code(
        "validator registry engine lookup and registration",
        repo_id=repo.id,
        limit=5,
    )

    assert len(results) >= 2
    top_result = results[0]
    # Primary target ValidatorRegistry must rank #1
    assert top_result.node.name == "ValidatorRegistry"
    assert top_result.node.node_type == "CLASS"
    assert top_result.match_score >= 0.65

    # register_validator must be outranked
    result_names = [r.node.name for r in results]
    assert result_names.index("ValidatorRegistry") < result_names.index("register_validator")


def test_leaf_method_search_not_distorted(tmp_path, db_session):
    """Verifies that leaf method searches (e.g. calculate sum) still rank the exact METHOD above CLASS."""
    src_dir = tmp_path / "math"
    src_dir.mkdir(parents=True)

    code = (
        'class Calculator:\n'
        '    """Calculator engine for basic math operations."""\n'
        '    def calculate_sum(self, a: int, b: int) -> int:\n'
        '        """Calculates sum of two integers."""\n'
        '        return a + b\n'
    )
    (src_dir / "calc.py").write_text(code, encoding="utf-8")

    ws = WorkspaceModel(name="Leaf Search WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="leaf-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)
    results = query_engine.search_code("calculate sum", repo_id=repo.id, limit=5)

    assert len(results) >= 1
    # calculate_sum method must rank #1
    assert results[0].node.name == "calculate_sum"
    assert results[0].node.node_type == "METHOD"


def test_negative_rejection_preserved(tmp_path, db_session):
    """Verifies that non-existent queries return 0 results and do not suffer false positive boosts."""
    src_dir = tmp_path / "app"
    src_dir.mkdir(parents=True)
    (src_dir / "main.py").write_text('class AppManager:\n    pass\n', encoding="utf-8")

    ws = WorkspaceModel(name="Neg Rejection WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="neg-repo",
        source_type="local",
        source_path=str(tmp_path),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)
    results = query_engine.search_code("nonexistent_random_token_99999", repo_id=repo.id, limit=5)
    assert len(results) == 0
