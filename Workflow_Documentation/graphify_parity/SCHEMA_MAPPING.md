# Schema Mapping: RepoPeekV3 (APEXGP) × Graphify

## 1. Executive Summary
This document provides a field-by-field and semantic mapping between **Graphify's NetworkX / JSON graph model** and **RepoPeekV3's relational SQL / Pydantic property graph**.

The unified architecture maintains **RepoPeekV3's relational SQL schema as the authoritative, durable source of truth** while adding bidirectional adapters to serialize and deserialize Graphify's canonical `graph.json` format and instantiate in-memory NetworkX graphs for topological algorithms.

---

## 2. Node Schema Mapping

### 2.1 Field Comparison & Conversion Rules

| Field Concept | Graphify Attribute (`graph.json`) | RepoPeekV3 Column (`graph_nodes`) | Mapping & Transformation Strategy |
| :--- | :--- | :--- | :--- |
| **Node Identity** | `id: str`<br>(e.g. `path/file.py::ClassName`) | `id: str`<br>(e.g. `{repo_id}:{runtime}:{file_path}:{name}`) | **Incompatible syntax, identical intent.**<br>RepoPeek adds repository UUID and runtime prefix for strict multi-repo multi-tenant isolation. Adapter converts Graphify IDs to `{repo_id}:{runtime}:{id}` on ingest, and strips repository prefixes on export to standalone `graph.json`. |
| **Display Name** | `label: str`<br>(e.g. `ClassName`) | `name: str`<br>(e.g. `ClassName`) | **Direct 1:1 match.** Both represent the bare unqualified identifier name. |
| **Qualified Name** | Implicit in `id` | `qualified_name: str`<br>(e.g. `path/file.py:ClassName`) | **RepoPeek is richer.** RepoPeek explicitly separates display name from qualified module path. |
| **Category / File Type** | `file_type: str`<br>(`code`, `document`, `concept`) | Stored in `node_metadata["file_type"]` | **Adapter maps to metadata.** RepoPeek stores language (`python`, `typescript`) in top-level `language` column; `file_type` persists in `node_metadata`. |
| **Entity Type** | `type: str`<br>(`class`, `function`, `method`, `interface`, `module`, `external`) | `node_type: str`<br>(`CLASS`, `FUNCTION`, `METHOD`, `INTERFACE`, `FILE`, `TEST`) | **Normalized Case Mapping:**<br>`class` ◄► `CLASS`<br>`function` ◄► `FUNCTION`<br>`method` ◄► `METHOD`<br>`interface` ◄► `INTERFACE`<br>`module` ◄► `MODULE`<br>`external` ◄► `EXTERNAL_STUB`<br>`doc`/`heading` ◄► `DOCUMENT` / `SECTION`. |
| **Source File** | `source_file: str`<br>(e.g. `packages/core/indexer.py`) | `file_path: str`<br>(e.g. `packages/core/indexer.py`) | **Direct 1:1 match.** Both enforce repo-relative POSIX forward-slash paths. Windows backslashes are sanitized. |
| **Line Numbers** | `source_location: str`<br>(e.g. `"L42"` or `"L42-L68"`) | `start_line: int`, `end_line: int`<br>`start_col: int`, `end_col: int` | **RepoPeek is richer.** RepoPeek stores numeric bounds. Adapter converts `start_line` -> `"L{start_line}"` on export and parses regex `^L(\d+)` on ingest. |
| **Signature & Types** | Stored in unstructured label or omitted | `signature: str`<br>(e.g. `def add(a: int, b: int) -> int:`) | **RepoPeek is richer.** Preserved in RepoPeek. Exported to Graphify `metadata["signature"]`. |
| **Docstrings** | Omitted or in `metadata` | `docstring: str` | **RepoPeek is richer.** Preserved in RepoPeek. Exported to Graphify `metadata["docstring"]`. |
| **Community Cluster** | `community: int` | Add `community_id: Optional[int]` to `graph_nodes` (or `node_metadata["community"]`) | **Absorption Requirement.** Add indexed `community_id` column to `graph_nodes` to store Leiden/Louvain cluster partitions natively. |
| **External Stub** | `external: bool` | Target ID prefix `ext:{runtime}:{module}` | **Syntactic variation.** Both prevent dangling edges to external packages. Adapter sets `external: True` when target ID starts with `ext:`. |
| **Extraction Origin** | `_origin: str` (`ast` or `semantic`) | `node_metadata["origin"]` (default `"ast"`) | **Direct match.** Store in RepoPeek's `node_metadata` JSON column. |

---

## 3. Edge Schema Mapping

### 3.1 Field Comparison & Conversion Rules

| Field Concept | Graphify Attribute (`graph.json`) | RepoPeekV3 Column (`graph_edges`) | Mapping & Transformation Strategy |
| :--- | :--- | :--- | :--- |
| **Edge Identity** | Implicit from endpoints and relation | `id: str`<br>(`{source_id}:{edge_type}:{target_id}`) | **RepoPeek enforces deterministic hashing.** Prevents duplicate parallel edges in SQL database. |
| **Source Endpoint** | `source: str` (alias: `from`, `_src`) | `source_id: str` | **Direct 1:1 match.** Converted via node ID adapter. |
| **Target Endpoint** | `target: str` (alias: `to`, `_tgt`) | `target_id: str` | **Direct 1:1 match.** Converted via node ID adapter. External modules map to `ext:*`. |
| **Relation Type** | `relation: str`<br>(`calls`, `defines`, `contains`, `imports`, `imports_from`, `re_exports`, `inherits`, `uses`, `references`, `mentions`) | `edge_type: str`<br>(`CALLS`, `DEFINES`, `CONTAINS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `REFERENCES`, `TESTS`) | **Case-Insensitive Translation:**<br>`calls` ◄► `CALLS`<br>`defines` ◄► `DEFINES`<br>`contains` ◄► `CONTAINS`<br>`imports` / `imports_from` ◄► `IMPORTS`<br>`inherits` ◄► `EXTENDS`<br>`implements` ◄► `IMPLEMENTS`<br>`references` / `uses` / `mentions` ◄► `REFERENCES`<br>`tests` ◄► `TESTS`. |
| **Confidence Level** | `confidence: str`<br>(`EXTRACTED`, `INFERRED`, `AMBIGUOUS`) | `confidence: float`<br>(`1.0`, `0.85`, `0.75`, `0.50`) | **Bidirectional Metric Mapping:**<br>`EXTRACTED` ◄► `1.0`<br>`INFERRED` (AST resolution) ◄► `0.85`<br>`INFERRED` (heuristic/fallback) ◄► `0.75`<br>`AMBIGUOUS` ◄► `0.50`. |
| **Evidence & Line** | None (or nested in custom metadata) | `line_number: int`<br>`evidence: str` (code snippet) | **RepoPeek is richer.** RepoPeek provides exact line numbers and code snippets showing why edge exists. Preserved in RepoPeek. |
| **Edge Weight** | `weight: float` (default 1.0) | Stored in `edge_type` or calculated dynamically | Adapter passes `1.0` (or `confidence`) as NetworkX edge weight for clustering. |
| **Directional Arc** | `"directed": false` with `_src`/`_tgt` | Directed by definition (`source_id` -> `target_id`) | **RepoPeek is strictly directed.** When exporting to Graphify `graph.json`, stamp `_src: source_id` and `_tgt: target_id`. |

---

## 4. Hyperedges & Concept Representation

- **Graphify Concept:** Graphify supports `hyperedges: list[dict]` representing N-ary relationships (e.g., a shared architectural pattern connecting 5 classes across modules: `{"id": "he_1", "relation": "pattern", "nodes": ["id1", "id2", "id3"]}`).
- **RepoPeek Representation:** Relational property graphs do not natively support hyperedges without join tables.
- **Unified Solution:**
  1. Store hyperedges in a new lightweight table: `graph_hyperedges`:
     - `id: VARCHAR(512) PK`
     - `repository_id: VARCHAR(36) FK`
     - `relation: VARCHAR(50)`
     - `member_node_ids: JSON` (List of member strings)
     - `metadata: JSON`
  2. For standard binary traversals, project hyperedges as a clique of `REFERENCES` edges or query them directly through `GraphQueryEngine.get_hyperedges()`.

---

## 5. Storage & Serialization Compatibility

### 5.1 Storage Matrix

| Dimension | RepoPeekV3 (APEXGP) | Graphify Upstream | Unified System Strategy |
| :--- | :--- | :--- | :--- |
| **Primary Storage** | PostgreSQL / SQLite (SQLAlchemy 2.0) | File-based `graph.json` | **Retain SQLite / PostgreSQL as primary database.** Provides transactions, indexes, and concurrency. |
| **Graph Cache** | SQLite database file (`apexgraph.db`) | In-memory NetworkX + AST cache file | Store AST parse caches in `.apexgraph/cache/` to accelerate incremental indexing. |
| **Interchange Format** | JSON REST API payloads | Canonical `graph.json` (node-link format) | Implement `packages/core/adapters/graphify_adapter.py` to import and export standard `graph.json`. |
| **Visualizer Artifact** | Dynamic Web Canvas (`app.js`) | Standalone offline `graph.html` (Vis.js) | Support both: keep interactive REST dashboard AND add CLI command to export self-contained `graph.html`. |

---

## 6. Migration Requirements & Backward Compatibility

1. **Zero Database Breaking Changes:**
   Existing RepoPeek database schemas (`workspaces`, `repositories`, `graph_nodes`, `graph_edges`) will receive non-breaking nullable additions:
   - `graph_nodes.community_id` (`Integer`, nullable, indexed).
   - `graph_edges.evidence` length limit preserved; new relation types mapped to existing string column.
2. **Deterministic Re-Indexing:**
   Re-running indexing on existing repositories with newly registered parsers (Rust, Go, C#) will cleanly update the repository without manual migration scripts.
3. **API & MCP Contract Preservation:**
   All 13 existing RepoPeek MCP tools retain their exact parameter signatures and JSON response formats. New tools are strictly additive.
