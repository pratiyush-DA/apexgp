# ApexGraph Deployment & Operations Guide

## 1. Deployment Architecture
ApexGraph can run in two primary modes:
1. **Local Developer Workstation (Standalone):** Zero external server requirements. Uses embedded SQLite property graph storage, in-process Tree-sitter parsers, and local HTTP / Stdio MCP servers.
2. **Containerized Production (Docker Compose):** Multi-container setup with FastAPI application service, dedicated indexing background worker, and PostgreSQL 16 database.

---

## 2. Docker Compose Deployment

The provided `docker-compose.yml` configures:
* `apex-db`: PostgreSQL 16 Alpine container with persistent volume storage.
* `apex-api`: FastAPI REST API server running on port 8000.
* `apex-worker`: Background worker handling repository ingestion and indexing queues.
* `apex-web`: Lightweight Nginx container serving the interactive frontend dashboard.

### Starting with Docker Compose:
```bash
docker compose up -d --build
```

### Applying Migrations in Docker:
```bash
docker compose exec apex-api python -m packages.core.migrations
```

---

## 3. Local Standalone Quickstart (No Docker Required)

1. **Activate Virtual Environment:**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
2. **Apply Database Migrations (Initializes SQLite tables):**
   ```powershell
   python -m packages.core.migrations
   ```
3. **Start API and Web Console:**
   ```powershell
   python -m apps.api.main
   ```
   Access the dashboard at `http://127.0.0.1:8000`.

4. **Connect MCP Server to AI Coding Agents:**
   Direct agent to:
   ```powershell
   python -m packages.mcp.server --transport stdio
   ```
