# ApexGraph Troubleshooting & Diagnostics Guide

This document lists common edge cases, errors, and verified resolution procedures.

---

## 1. Port 8000 Already in Use

### Symptom:
`[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000): only one usage of each socket address is normally permitted`

### Resolution:
Change the default listening port via environment variable:
```powershell
$env:APEX_PORT="8080"
python -m apps.api.main
```
Or identify and terminate the occupying process on Windows:
```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object OwningProcess
Stop-Process -Id <OwningProcessId> -Force
```

---

## 2. SQLite Database Locked

### Symptom:
`sqlite3.OperationalError: database is locked`

### Resolution:
ApexGraph automatically configures SQLite WAL (Write-Ahead Logging) mode on connection. If external tools hold a file lock:
1. Ensure background indexing jobs have completed.
2. Verify WAL mode is active:
   ```powershell
   python -c "from packages.core.database import get_engine; print(get_engine())"
   ```
3. In multi-user or high-concurrency environments, switch to PostgreSQL via `DATABASE_URL` in `.env`:
   ```
   DATABASE_URL=postgresql+psycopg2://apex:password@localhost:5432/apexgraph
   ```

---

## 3. Tree-sitter Grammar Parse Warnings

### Symptom:
`Syntax error at line X:Y in file.py` or skipped malformed source file.

### Diagnostic:
1. ApexGraph strictly treats parse errors as non-fatal warnings without crashing the indexer.
2. Inspect `stats["warnings"]` in the Overview screen or query:
   ```powershell
   python -c "import json; from packages.mcp.server import get_repository_status; print(get_repository_status('<repo_id>'))"
   ```
3. Files exceeding `MAX_FILE_SIZE_BYTES` (default 2 MB) are safely skipped. Increase this threshold in `.env` if legitimate large source files require indexing.

---

## 4. MCP Agent Fails to Connect over Stdio

### Symptom:
Coding assistant reports `Connection refused` or `Process exited with code 1`.

### Resolution:
1. Ensure the Python environment path and `PYTHONPATH` are correctly specified in `mcp_config.json`:
   ```json
   {
     "mcpServers": {
       "apexgraph": {
         "command": "python",
         "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
         "cwd": "C:/path/to/RepopeekV3",
         "env": {
           "PYTHONPATH": "C:/path/to/RepopeekV3"
         }
       }
     }
   }
   ```
2. Run the self-test flag to confirm all 13 tools register without error:
   ```powershell
   python -m packages.mcp.server --test
   ```

---

## 5. Playwright Browser Executable Missing

### Symptom:
`BrowserType.launch: Executable doesn't exist at ms-playwright\...`

### Resolution:
Run the official Playwright browser downloader:
```powershell
playwright install chromium
```
Or for headless verification without local browser binaries, use the REST API integration test suite:
```powershell
pytest -v tests/integration/test_api_endpoints.py
```
