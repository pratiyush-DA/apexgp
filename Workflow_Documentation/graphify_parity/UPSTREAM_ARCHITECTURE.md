# Upstream Architecture: Graphify (Release 0.9.82)

## 1. Executive Identification & Baseline
- **Reference Project:** Graphify (`Graphify-Labs/graphify`)
- **Evaluated Commit SHA:** `5b74d7d74911cf435c8f1636b6f96ea202cc6246`
- **Tracked Branch:** `v8`
- **Release Version:** `0.9.82`
- **Primary Design Paradigm:** In-memory property graph engine built on NetworkX, fed by Tree-sitter AST extractors and serialized to a canonical `graph.json` artifact.

---

## 2. Actual System Pipeline & Execution Flow

Graphify executes as a linear processing pipeline operating over plain Python dictionaries and NetworkX `nx.Graph` instances:

```
detect() ───► extract() ───► build() ───► cluster() ───► analyze.* ───► report ───► export.*
```

```
[Target Directory]
       │
       ▼
 1. detect.py ────────► Scans file tree, detects language mix, applies exclusion rules
       │
       ▼
 2. extract.py ───────► AST parsing via Tree-sitter (0.25+) -> nodes, edges, imports, calls
       │
       ▼
 3. build.py ─────────► Ingests extractions into NetworkX Graph, mints external stubs,
                        enforces cross-language family boundaries, drops dangling edges
       │
       ▼
 4. cluster.py ───────► Leiden algorithm (graspologic-native) or Louvain (networkx),
                        computes modularity, splits oversized clusters (>100 nodes)
       │
       ▼
 5. analyze.py ───────► God nodes, surprising connections, import cycles, suggested questions
       │
       ▼
 6. report.py ────────► Synthesizes GRAPH_REPORT.md with markdown metrics
       │
       ▼
 7. export.py ────────► Writes graph.json, standalone graph.html, wiki, and graph formats
```

### Stage-by-Stage Implementation Details

#### Stage 1: File Detection (`detect.py`)
- **Entry point:** `detect(root: Path) -> dict`
- **Behavior:** Traverses `root` recursively, ignoring standard directories (`.git`, `node_modules`, `venv`, `__pycache__`, `dist`, `build`). Categorizes files into `code`, `document`, and `other`.
- **Output:** Returns inventory dictionary containing `files` grouped by category, `total_files`, `total_words`, `scan_root`, and warnings.

#### Stage 2: Structural Extraction (`extract.py` & `extractors/`)
- **Entry point:** `extract(paths: list[Path], *, root: Path = None, ...) -> dict`
- **Mechanism:** Dispatches files to language-specific extractors based on extension.
- **Tree-Sitter Integration:** Relies on modern Tree-Sitter runtime pinned to `>=0.25,<0.26` (ABI 15 compatibility) across 30+ supported grammar packages.
- **Multi-pass Resolution:**
  1. *First Pass (Syntax Walk):* Extracts syntax nodes (classes, methods, functions, interfaces, modules) and explicit edges (`defines`, `contains`, `imports`, `inherits`).
  2. *Second Pass (Language Resolvers):* Executes language-specific resolution logic registered in `resolver_registry.py` (e.g., TSConfig alias mapping, Python star imports, C# interface dispatch, Go package re-pointing).
- **Caching Layer (`cache.py`):** Caches extracted `{nodes, edges}` per file using SHA-256 content hashing and file modification times (`mtime`) with cache schema versioning (Schema 7).

#### Stage 3: Graph Construction & Sanitization (`build.py`)
- **Entry point:** `build(extractions: list[dict]) -> nx.Graph` and `build_from_json(extraction: dict) -> nx.Graph`
- **Three-Layer Deduplication:**
  1. *Within-file (AST):* Extractor-level `seen_ids` prevents duplicate definitions within the same file.
  2. *Between-files (build):* Graph node insertion is idempotent; duplicate IDs overwrite with latter attributes.
  3. *Semantic merge:* Merges cached and newly extracted items using explicit identity keys.
- **Dangling Edge & Phantom Prevention:**
  - *External Stub Minting:* Mints typed external stub nodes (`type="external"`, `file_type="concept"`, `source_file=""`) for import-family links (`imports`, `imports_from`, `re_exports`) targeting unindexed packages (stdlib, npm modules).
  - *Dangling Edge Dropping:* Drops call edges targeting undeclared nodes that were never imported.
  - *Cross-Language Phantom Edge Guard:* Inspects `_EDGE_LANG_FAMILY` table. Drops cross-runtime calls (e.g. Python calling TypeScript) unless they belong to verified interop families (C/C++, JVM, JS/TS).
- **Hyperedge Normalization:** Coerces `hyperedges` member references to hashable scalar IDs, folding legacy aliases (`members`, `node_ids` -> `nodes`).

#### Stage 4: Community Clustering (`cluster.py`)
- **Entry point:** `cluster(G: nx.Graph) -> dict[int, list[str]]`
- **Primary Algorithm:** Leiden community detection via `graspologic_native.leiden()` (Rust native extension) using modularity optimization (`seed=42`, `iterations=1`, `resolution=1.0`).
- **Fallback Algorithm:** If `graspologic_native` is not installed, falls back seamlessly to NetworkX's pure Python Louvain implementation (`networkx.algorithms.community.louvain_communities`).
- **Post-processing:** Recursively splits oversized communities (>100 nodes) down to a manageable size, computing modularity cohesion scores per cluster.

#### Stage 5: Architectural Analytics (`analyze.py`)
- **No monolithic entry point:** Implements sibling analysis functions:
  - `god_nodes(G, top_n=10)`: Computes node degrees, filtering built-in noise labels (e.g., `str`, `List`, `None`, `Mock`) and file hub nodes to identify core architectural bottlenecks.
  - `surprising_connections(G, communities, top_n=10)`: Detects unexpected edges bridging distant functional communities.
  - `find_import_cycles(G)`: Identifies circular dependency loops using directed simple cycles.
  - `suggest_questions(G, communities, community_labels, limit=7)`: Generates architectural triage questions for developers and agents.
  - `graph_diff(G_old, G_new)`: Detects added, removed, and retargeted nodes and edges across graph versions.

#### Stage 6 & 7: Reports & Exports (`report.py`, `export.py`, `exporters/html.py`, `wiki.py`)
- **Markdown Report (`GRAPH_REPORT.md`):** Generates architectural overview with community tables, god nodes, and cycle warnings.
- **Interactive Visualizer (`graph.html`):** Renders single-file HTML containing embedded Vis.js network visualization (`vis-network@9.1.6`) with ForceAtlas2 physics simulation, legend, search bar, and inspection side panel.
- **Wiki Generation (`wiki.py`):** Produces a directory of interlinked Markdown files per community with a root `index.md`.
- **Exporters:** Serializes to `graph.json` (canonical sorted JSON), Cypher (`to_cypher` for Neo4j / FalkorDB), GraphML (`to_graphml`), SVG (`to_svg` via Matplotlib), and Canvas (`to_canvas` for Obsidian Canvas).

---

## 3. Data Structures & Schemas

### 3.1 Node Data Schema
In-memory representation as NetworkX node attributes and serialized in `graph.json`:

```json
{
  "id": "packages/core/indexer.py::RepositoryIndexer",
  "label": "RepositoryIndexer",
  "file_type": "code",
  "type": "class",
  "source_file": "packages/core/indexer.py",
  "source_location": "L44",
  "community": 3,
  "external": false,
  "_origin": "ast"
}
```

- `id`: Normalized string identifier.
- `label`: Human-readable symbol name.
- `file_type`: Enum string (`code`, `document`, `concept`).
- `type`: Granular entity type (`class`, `function`, `method`, `interface`, `module`, `external`).
- `source_file`: Repo-relative POSIX path.
- `source_location`: Line indicator string (`L44`).
- `community`: Integer cluster ID assigned by Leiden/Louvain.
- `external`: Boolean flag indicating third-party or stdlib stub.
- `_origin`: Extraction tier (`ast` or `semantic`).

### 3.2 Edge Data Schema
In-memory NetworkX edge attributes:

```json
{
  "source": "packages/core/indexer.py::RepositoryIndexer",
  "target": "packages/core/models.py::GraphNodeModel",
  "relation": "calls",
  "confidence": "EXTRACTED",
  "confidence_score": 1.0,
  "weight": 1.0,
  "_src": "packages/core/indexer.py::RepositoryIndexer",
  "_tgt": "packages/core/models.py::GraphNodeModel"
}
```

- `relation`: Relationship verb (`calls`, `defines`, `contains`, `imports`, `inherits`, `uses`, `references`).
- `confidence`: Tri-state enum (`EXTRACTED`, `INFERRED`, `AMBIGUOUS`).
- `confidence_score`: Float between 0.0 and 1.0.
- `_src` / `_tgt`: Explicit directed arc markers ensuring directionality is preserved when serialized to undirected JSON graph format.

---

## 4. Public Interfaces & Runtime Contracts

### 4.1 CLI Commands (`graphify/cli.py`)
Graphify provides a rich terminal CLI:
- `graphify extract <paths>`: Runs extraction on target paths, writes output to `graphify-out/`.
- `graphify update <path>`: Incremental re-index of changed files using AST cache.
- `graphify query "<question>"`: Query codebase relationships and subgraphs.
- `graphify path "<nodeA>" "<nodeB>"`: Returns shortest path between two symbols.
- `graphify explain "<concept>"`: Summarizes targeted concept and its immediate neighborhood.
- `graphify affected <file>`: Computes blast radius of modified files.
- `graphify export --format <json|html|cypher|wiki|obsidian>`: Generates export artifacts.
- `graphify watch <path>`: Runs background daemon watching for file changes.
- `graphify benchmark`: Compares subgraph token efficiency against whole-file reading.
- `graphify install`: Installs agent skills and rules for Claude Code, Antigravity, Gemini, Cursor.

### 4.2 Agent MCP Server (`graphify/serve.py`)
Exposes tools via JSON-RPC 2.0 over standard I/O and HTTP (Starlette):
- `query_graph(query: str, limit: int = 10)`: Semantic and keyword graph query.
- `get_node(id_or_label: str)`: Returns symbol details, source location, and community.
- `get_neighbors(id_or_label: str)`: Returns incoming and outgoing typed edges.
- `get_community(community_id: int)`: Returns all nodes in a cluster.
- `god_nodes(top_n: int = 10)`: Returns most connected architectural hubs.
- `graph_stats()`: Summary of nodes, edges, density, and language counts.
- `shortest_path(source: str, target: str, undirected: bool = True)`: Relational trace.
- `list_prs()`, `get_pr_impact()`, `triage_prs()`: Git pull request impact tools.

---

## 5. Dependency Analysis & Runtime Footprint

### 5.1 Mandatory Core Dependencies
- `tree-sitter>=0.25,<0.26`: Core parsing engine.
- 25+ language grammar packages (`tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-rust`, `tree-sitter-go`, etc.).
- `networkx>=3.4`: In-memory graph algorithms and graph manipulation.
- `numpy>=1.21`: Numerical array calculations for layout and clustering.
- `rapidfuzz>=3.0`: Fast fuzzy string matching for node lookup and search.

### 5.2 Optional Extensions
- `graspologic-native>=1.3.1`: Fast Rust-compiled Leiden community detection.
- `mcp>=1,<3`, `starlette>=1.3.1,<2`: MCP server communication.
- `watchdog`: Filesystem events for `watch.py`.
- `pypdf`, `markdownify`: PDF document parsing.
- `python-docx`, `openpyxl`: Office document ingestion.
- `psycopg[binary]`, `tree-sitter-sql`: PostgreSQL database DDL introspection.

---

## 6. Distinguishing Verified Functionality vs. Intentions

| Subsystem | Verified Code in Reference Repo | Documented Intentions / Unverified / Optional |
| :--- | :--- | :--- |
| **AST Extraction** | Verified locally across Python, JS, TS, Rust, Go, C#, C++, Swift via Tree-Sitter grammars. | Niche languages (Pascal, Fortran, COBOL) require specific grammars often missing wheels on certain OSes. |
| **Community Clustering** | Verified NetworkX Louvain fallback and native Leiden. | Leiden requires `graspologic-native` binary wheels. |
| **Static HTML Visualizer** | Verified self-contained HTML generation with Vis.js. | Graph rendering degrades if nodes exceed 5,000. |
| **Incremental Updates** | Verified SHA-256 AST caching and file modification checks. | In-memory graph rewrite is not atomic if process is killed mid-write. |
| **Cloud LLM Extraction** | Python client calls `openai`, `anthropic`, or `boto3`. | External cloud dependency. Not open-source local capability. Not acceptable for secure enterprise codebases. |
| **Audio / Video Transcribe** | Wrapper scripts around `faster-whisper` and `yt-dlp`. | Heavy multi-gigabyte models; extraneous to code intelligence core. |
