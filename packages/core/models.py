"""ApexGraph Database Models and Pydantic Schemas

Defines relational entities for PostgreSQL / SQLite graph persistence.
"""

from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ============================================================================
# SQLAlchemy ORM Models
# ============================================================================

class WorkspaceModel(Base):
    __tablename__ = "workspaces"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    repositories = relationship("RepositoryModel", back_populates="workspace", cascade="all, delete-orphan")


class RepositoryModel(Base):
    __tablename__ = "repositories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    source_type = Column(String(50), default="local", nullable=False)  # local | git
    source_path = Column(Text, nullable=False)
    current_revision = Column(String(100), default="HEAD", nullable=False)
    indexing_status = Column(String(50), default="pending", nullable=False)  # pending | indexing | ready | failed
    last_indexed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    stats = Column(JSON, default=dict, nullable=False)  # file counts, languages, nodes, edges

    workspace = relationship("WorkspaceModel", back_populates="repositories")
    nodes = relationship("GraphNodeModel", back_populates="repository", cascade="all, delete-orphan")
    edges = relationship("GraphEdgeModel", back_populates="repository", cascade="all, delete-orphan")


class GraphNodeModel(Base):
    __tablename__ = "graph_nodes"

    id = Column(String(512), primary_key=True)  # {repo_id}:{runtime}:{file_path}:{name}
    repository_id = Column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    node_type = Column(String(50), nullable=False)  # FILE, MODULE, CLASS, INTERFACE, FUNCTION, METHOD, VARIABLE, TYPE, TEST
    name = Column(String(255), nullable=False)
    qualified_name = Column(String(512), nullable=False)
    file_path = Column(String(1024), nullable=False)
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    start_col = Column(Integer, default=0, nullable=False)
    end_col = Column(Integer, default=0, nullable=False)
    language = Column(String(50), nullable=False)
    signature = Column(Text, default="", nullable=False)
    docstring = Column(Text, default="", nullable=False)
    node_metadata = Column(JSON, default=dict, nullable=False)

    repository = relationship("RepositoryModel", back_populates="nodes")

    __table_args__ = (
        Index("idx_nodes_repo_name", "repository_id", "name"),
        Index("idx_nodes_repo_type", "repository_id", "node_type"),
        Index("idx_nodes_repo_file", "repository_id", "file_path"),
        Index("idx_nodes_qualname", "repository_id", "qualified_name"),
    )


class GraphEdgeModel(Base):
    __tablename__ = "graph_edges"

    id = Column(String(1024), primary_key=True)  # {source_id}:{edge_type}:{target_id}
    repository_id = Column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(String(512), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(String(512), nullable=False)  # Can link to internal node or namespaced external node
    edge_type = Column(String(50), nullable=False)  # DEFINES, CONTAINS, IMPORTS, CALLS, EXTENDS, IMPLEMENTS, REFERENCES, TESTS
    confidence = Column(Float, default=1.0, nullable=False)  # 1.0 = verified AST, 0.75 = heuristic
    file_path = Column(String(1024), nullable=False)
    line_number = Column(Integer, default=1, nullable=False)
    evidence = Column(Text, default="", nullable=False)

    repository = relationship("RepositoryModel", back_populates="edges")

    __table_args__ = (
        Index("idx_edges_repo_source", "repository_id", "source_id"),
        Index("idx_edges_repo_target", "repository_id", "target_id"),
        Index("idx_edges_repo_type", "repository_id", "edge_type"),
    )


# ============================================================================
# Pydantic Schemas for API & MCP Validation
# ============================================================================

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime


class RepositoryRegister(BaseModel):
    workspace_id: str
    name: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(default="local", pattern="^(local|git)$")
    source_path: str = Field(..., min_length=1)


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    name: str
    source_type: str
    source_path: str
    current_revision: str
    indexing_status: str
    last_indexed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    stats: Dict[str, Any] = Field(default_factory=dict)


class GraphNodeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repository_id: str
    node_type: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    start_col: int = 0
    end_col: int = 0
    language: str
    signature: str = ""
    docstring: str = ""
    node_metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphEdgeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repository_id: str
    source_id: str
    target_id: str
    edge_type: str
    confidence: float = 1.0
    file_path: str
    line_number: int
    evidence: str = ""


class SearchResult(BaseModel):
    node: GraphNodeSchema
    match_score: float = 1.0
    snippet: Optional[str] = None


class ImpactAnalysisResult(BaseModel):
    target: str
    repository_id: str
    affected_nodes: List[GraphNodeSchema]
    affected_files: List[str]
    linked_tests: List[GraphNodeSchema]
    traversal_depth: int
    total_affected: int
    evidence_paths: List[Dict[str, Any]]
