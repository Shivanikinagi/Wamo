#!/usr/bin/env python3
"""
Memory Layer Dashboard — Interactive web-based memory inspector
Run: python3 scripts/memory_dashboard.py
Then open: http://localhost:8765
"""

import os
import json
import sqlite3
import time
from uuid import uuid4
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse
import urllib.request
import urllib.error

try:
    import redis
except Exception:
    redis = None

PROJECT_ROOT = Path(__file__).parent.parent


def _pick_active_path(candidates: list[Path], expected_file: str) -> Path:
    existing = [p for p in candidates if (p / expected_file).exists()]
    if not existing:
        return candidates[0]

    # Prefer the file with latest mtime, then larger size.
    def score(p: Path):
        fp = p / expected_file
        st = fp.stat()
        return (st.st_mtime, st.st_size)

    existing.sort(key=score, reverse=True)
    return existing[0]


CHROMA_DB_PATH = _pick_active_path(
    [PROJECT_ROOT / "PS01" / "chroma_db" / "default", PROJECT_ROOT / "chroma_db" / "default"],
    "chroma.sqlite3",
)
MEM0_HISTORY_PATH = _pick_active_path(
    [PROJECT_ROOT / "PS01" / "mem0_history" / "default", PROJECT_ROOT / "mem0_history" / "default"],
    "default.db",
)
WAL_PATH = _pick_active_path(
    [PROJECT_ROOT / "PS01" / "data" / "wal", PROJECT_ROOT],
    "ps01_wal.jsonl",
) / "ps01_wal.jsonl"
API_BASE = os.getenv("PS01_API_URL", "http://localhost:8000")
API_TEST_TIMEOUT = int(os.getenv("STORAGE_TEST_TIMEOUT", "15"))

class MemoryDashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(get_html_dashboard().encode())
        
        elif path == "/api/summary":
            self.send_json(get_storage_summary())
        
        elif path == "/api/chromadb":
            self.send_json(get_chromadb_data())
        
        elif path == "/api/mem0":
            self.send_json(get_mem0_data())
        
        elif path == "/api/wal":
            self.send_json(get_wal_data())

        elif path == "/api/test-storage":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            prompt = query.get("prompt", ["Meri monthly income 72000 hai aur co-applicant Sunita hai"])[0]
            customer_id = query.get("customer_id", [f"dashboard_test_{int(time.time())}"])[0]
            result = run_storage_test(prompt_text=prompt, customer_id=customer_id)
            self.send_json(result)
        
        else:
            self.send_error(404)
    
    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, default=str).encode())


def get_storage_summary():
    """Get storage locations and file sizes"""
    summary = {}
    
    for name, path, desc in [
        ("ChromaDB", CHROMA_DB_PATH, "Vector embeddings of facts"),
        ("Mem0 SQLite", MEM0_HISTORY_PATH, "Metadata & relationships"),
        ("WAL", WAL_PATH.parent, "Source of truth"),
    ]:
        exists = path.exists()
        size = 0
        if exists:
            if path.is_file():
                size = path.stat().st_size
            else:
                size = sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
        
        summary[name] = {
            "exists": exists,
            "path": str(path),
            "size_bytes": size,
            "size_kb": round(size / 1024, 1),
            "description": desc
        }
    
    return summary


def get_chromadb_data():
    """Extract ChromaDB contents"""
    chroma_sqlite = CHROMA_DB_PATH / "chroma.sqlite3"
    
    if not chroma_sqlite.exists():
        return {"error": "ChromaDB not found", "collections": [], "embeddings_count": 0}
    
    try:
        conn = sqlite3.connect(str(chroma_sqlite))
        cursor = conn.cursor()
        
        # Get collections
        cursor.execute("SELECT id, name, dimension FROM collections;")
        collections = [
            {
                "id": row[0],
                "name": row[1],
                "dimension": row[2]
            }
            for row in cursor.fetchall()
        ]
        
        # Count embeddings
        cursor.execute("SELECT COUNT(*) FROM embeddings;")
        embeddings_count = cursor.fetchone()[0]
        
        # Get table counts
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {}
        for table in cursor.fetchall():
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
            tables[table_name] = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "path": str(chroma_sqlite),
            "size_kb": chroma_sqlite.stat().st_size / 1024,
            "collections": collections,
            "embeddings_count": embeddings_count,
            "tables": tables
        }
    
    except Exception as e:
        return {"error": str(e)}


def get_mem0_data():
    """Extract Mem0 SQLite contents"""
    mem0_db = MEM0_HISTORY_PATH / "default.db"
    
    if not mem0_db.exists():
        return {"error": "Mem0 database not found", "tables": {}}
    
    try:
        conn = sqlite3.connect(str(mem0_db))
        cursor = conn.cursor()
        
        # Get table counts
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {}
        for table in cursor.fetchall():
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
            count = cursor.fetchone()[0]
            tables[table_name] = count
        
        # Get schema info
        schema = {}
        for table_name in tables.keys():
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = [row[1] for row in cursor.fetchall()]
            schema[table_name] = columns
        
        conn.close()
        
        return {
            "path": str(mem0_db),
            "size_kb": mem0_db.stat().st_size / 1024,
            "tables": tables,
            "schema": schema
        }
    
    except Exception as e:
        return {"error": str(e)}


def get_wal_data():
    """Extract WAL contents"""
    if not WAL_PATH.exists():
        return {"error": "WAL not found", "total_entries": 0, "entries": []}
    
    try:
        entries = []
        total = 0
        
        with open(WAL_PATH, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    total += 1
                    entries.append(entry)
                except json.JSONDecodeError:
                    pass
        
        # Keep only last 10 for UI performance
        recent_entries = entries[-10:]
        
        # Summary stats
        customer_ids = set()
        fact_types = {}
        
        for entry in entries:
            customer_ids.add(entry.get('customer_id', ''))
            for fact in entry.get('facts', []):
                fact_type = fact.get('type', 'unknown')
                fact_types[fact_type] = fact_types.get(fact_type, 0) + 1
        
        return {
            "path": str(WAL_PATH),
            "size_kb": WAL_PATH.stat().st_size / 1024,
            "total_entries": total,
            "unique_customers": len(customer_ids),
            "fact_types": fact_types,
            "recent_entries": recent_entries
        }
    
    except Exception as e:
        return {"error": str(e), "total_entries": 0}


def _count_mem0_history_rows() -> int:
    db = MEM0_HISTORY_PATH / "default.db"
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM history")
        return int(cur.fetchone()[0])
    except Exception:
        return 0
    finally:
        conn.close()


def _count_chroma_embeddings() -> int:
    db = CHROMA_DB_PATH / "chroma.sqlite3"
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM embeddings")
        return int(cur.fetchone()[0])
    except Exception:
        return 0
    finally:
        conn.close()


def _count_wal_entries() -> int:
    if not WAL_PATH.exists():
        return 0
    total = 0
    with open(WAL_PATH, "r") as f:
        for line in f:
            if line.strip():
                total += 1
    return total


def _count_redis_keys() -> int:
    if redis is None:
        return -1
    try:
        client = redis.Redis(host="localhost", port=6379, decode_responses=True)
        return len(client.keys("*"))
    except Exception:
        return -1


def snapshot_storage() -> dict:
    return {
        "wal_entries": _count_wal_entries(),
        "mem0_history_rows": _count_mem0_history_rows(),
        "chroma_embeddings": _count_chroma_embeddings(),
        "redis_keys": _count_redis_keys(),
    }


def _http_post_json(path: str, payload: dict, timeout: int = API_TEST_TIMEOUT) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def _http_post(path: str, timeout: int = API_TEST_TIMEOUT) -> dict:
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def run_storage_test(prompt_text: str, customer_id: str | None = None) -> dict:
    """Run real API lifecycle and return before/after storage deltas."""
    customer = customer_id or f"storage_test_{int(time.time())}"
    agent_id = "AGT_STORAGE_TEST"

    before = snapshot_storage()
    steps = []

    session_id = None

    start_payload = {
        "customer_id": customer,
        "session_type": "home_loan_processing",
        "agent_id": agent_id,
        "consent_id": f"consent_{uuid4().hex[:8]}",
    }
    try:
        start_resp = _http_post_json("/session/start", start_payload)
        session_id = start_resp.get("session_id")
        steps.append({"step": "session/start", "ok": bool(session_id), "response": start_resp})
    except Exception as e:
        steps.append({"step": "session/start", "ok": False, "error": str(e)})

    if session_id:
        query = urllib.parse.urlencode({
            "session_id": session_id,
            "customer_id": customer,
            "agent_id": agent_id,
            "fact_type": "co_applicant_name",
            "fact_value": "Sunita",
        })
        try:
            add_resp = _http_post(f"/session/add-fact?{query}")
            steps.append({"step": "session/add-fact", "ok": add_resp.get("wal_written", False), "response": add_resp})
        except Exception as e:
            steps.append({"step": "session/add-fact", "ok": False, "error": str(e)})

        # Add explicit memory/add write path as well for storage validation.
        try:
            mem_payload = {
                "session_id": session_id,
                "customer_id": customer,
                "agent_id": agent_id,
                "facts": [
                    {
                        "type": "income",
                        "value": "73500",
                        "verified": False,
                        "source": "storage_test",
                    }
                ],
            }
            mem_resp = _http_post_json("/memory/add", mem_payload)
            mem_ok = str(mem_resp.get("status", "")).lower() in {"ok", "added"}
            steps.append({"step": "memory/add", "ok": mem_ok, "response": mem_resp})
        except Exception as e:
            steps.append({"step": "memory/add", "ok": False, "error": str(e)})

        # Best-effort conversational path (can be slow depending on model load).
        try:
            conv_payload = {
                "session_id": session_id,
                "customer_id": customer,
                "customer_message": prompt_text,
            }
            conv_resp = _http_post_json("/session/converse", conv_payload)
            steps.append({"step": "session/converse", "ok": bool(conv_resp.get("agent_response")), "response": conv_resp})
        except Exception as e:
            steps.append({"step": "session/converse", "ok": False, "error": str(e)})
    else:
        steps.append({"step": "session/add-fact", "ok": False, "error": "skipped: no session_id"})
        steps.append({"step": "memory/add", "ok": False, "error": "skipped: no session_id"})
        steps.append({"step": "session/converse", "ok": False, "error": "skipped: no session_id"})

    # Allow background tasks (compactor/memory sync) to complete.
    time.sleep(2)

    after = snapshot_storage()
    delta = {k: after[k] - before.get(k, 0) for k in after.keys()}
    required = {"session/start", "session/add-fact", "memory/add"}
    required_ok = all(s.get("ok", False) for s in steps if s.get("step") in required)
    return {
        "ok": required_ok,
        "api_base": API_BASE,
        "customer_id": customer,
        "prompt": prompt_text,
        "before": before,
        "after": after,
        "delta": delta,
        "steps": steps,
    }


def get_html_dashboard():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Memory Layer Dashboard — PS-01</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: #333;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        header {
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
        
        h1 {
            color: #1e3c72;
            font-size: 2.5em;
            margin-bottom: 5px;
        }
        
        .subtitle {
            color: #666;
            font-size: 1.1em;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .card {
            background: white;
            border-radius: 12px;
            padding: 25px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 8px 12px rgba(0, 0, 0, 0.15);
        }
        
        .card h2 {
            color: #1e3c72;
            font-size: 1.3em;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .icon {
            font-size: 1.5em;
        }
        
        .stat {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #eee;
            font-size: 0.95em;
        }
        
        .stat:last-child {
            border-bottom: none;
        }
        
        .stat-label {
            color: #666;
            font-weight: 500;
        }
        
        .stat-value {
            color: #1e3c72;
            font-weight: 700;
            font-family: 'Monaco', 'Courier New', monospace;
        }
        
        .full-width {
            grid-column: 1 / -1;
        }
        
        .table-container {
            overflow-x: auto;
            margin-top: 15px;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9em;
        }
        
        th {
            background: #f5f5f5;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #1e3c72;
            border-bottom: 2px solid #ddd;
        }
        
        td {
            padding: 10px 12px;
            border-bottom: 1px solid #eee;
        }
        
        tr:hover {
            background: #f9f9f9;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }
        
        .badge-success {
            background: #d4edda;
            color: #155724;
        }
        
        .badge-warning {
            background: #fff3cd;
            color: #856404;
        }
        
        .badge-error {
            background: #f8d7da;
            color: #721c24;
        }
        
        .loading {
            text-align: center;
            color: #999;
            padding: 20px;
        }
        
        .error {
            background: #f8d7da;
            color: #721c24;
            padding: 15px;
            border-radius: 6px;
            margin: 10px 0;
        }
        
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
            border-bottom: 2px solid #eee;
        }
        
        .tab {
            padding: 10px 15px;
            cursor: pointer;
            color: #666;
            border: none;
            background: none;
            font-size: 0.95em;
            font-weight: 500;
            border-bottom: 3px solid transparent;
            transition: all 0.2s;
        }
        
        .tab.active {
            color: #1e3c72;
            border-bottom-color: #1e3c72;
        }
        
        .tab-content {
            display: none;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .json-display {
            background: #f5f5f5;
            padding: 15px;
            border-radius: 6px;
            overflow-x: auto;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 0.85em;
            line-height: 1.6;
            color: #333;
        }
        
        .path {
            color: #666;
            font-size: 0.9em;
            margin-top: 10px;
            word-break: break-all;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Memory Layer Dashboard</h1>
            <p class="subtitle">PS-01 Loan Officer Memory System • Real-time Data Inspector</p>
        </header>
        
        <div class="grid" id="summary"></div>
        
        <div class="card full-width">
            <h2><span class="icon">💾</span>Storage Details</h2>
            
            <div class="tabs">
                <button class="tab active" onclick="switchTab('wal-tab')">📝 Write-Ahead Log (WAL)</button>
                <button class="tab" onclick="switchTab('chromadb-tab')">🔍 ChromaDB Vectors</button>
                <button class="tab" onclick="switchTab('sqlite-tab')">📚 Mem0 SQLite</button>
            </div>
            
            <div id="wal-tab" class="tab-content active">
                <div id="wal-content" class="loading">Loading WAL data...</div>
            </div>
            
            <div id="chromadb-tab" class="tab-content">
                <div id="chromadb-content" class="loading">Loading ChromaDB data...</div>
            </div>
            
            <div id="sqlite-tab" class="tab-content">
                <div id="sqlite-content" class="loading">Loading SQLite data...</div>
            </div>
        </div>
    </div>
    
    <script>
        // Load all data on page load
        Promise.all([
            fetch('/api/summary').then(r => r.json()),
            fetch('/api/wal').then(r => r.json()),
            fetch('/api/chromadb').then(r => r.json()),
            fetch('/api/mem0').then(r => r.json())
        ]).then(([summary, wal, chromadb, mem0]) => {
            renderSummary(summary);
            renderWAL(wal);
            renderChromaDB(chromadb);
            renderMem0(mem0);
        }).catch(err => {
            console.error('Error loading data:', err);
            document.getElementById('summary').innerHTML = '<div class="error">Error loading dashboard data</div>';
        });
        
        function switchTab(tabId) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }
        
        function renderSummary(data) {
            let html = '';
            
            for (const [name, info] of Object.entries(data)) {
                const badge = info.exists ? 'badge-success' : 'badge-error';
                const status = info.exists ? '✅ Active' : '❌ Missing';
                
                html += `
                    <div class="card">
                        <h2>${name}</h2>
                        <div class="stat">
                            <span class="stat-label">Status</span>
                            <span class="badge ${badge}">${status}</span>
                        </div>
                        <div class="stat">
                            <span class="stat-label">Size</span>
                            <span class="stat-value">${info.size_kb.toFixed(1)} KB</span>
                        </div>
                        <div class="stat">
                            <span class="stat-label">Location</span>
                        </div>
                        <div class="path">${info.path}</div>
                        <div class="stat" style="margin-top: 10px;">
                            <span class="stat-label">Purpose</span>
                        </div>
                        <div style="padding: 0; margin-top: 8px; color: #666; font-size: 0.9em;">
                            ${info.description}
                        </div>
                    </div>
                `;
            }
            
            document.getElementById('summary').innerHTML = html;
        }
        
        function renderWAL(data) {
            if (data.error) {
                document.getElementById('wal-content').innerHTML = `<div class="error">${data.error}</div>`;
                return;
            }
            
            let html = `
                <div class="stat">
                    <span class="stat-label">Total Entries</span>
                    <span class="stat-value">${data.total_entries}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Unique Customers</span>
                    <span class="stat-value">${data.unique_customers}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">File Size</span>
                    <span class="stat-value">${data.size_kb.toFixed(1)} KB</span>
                </div>
                
                <h3 style="margin-top: 20px; margin-bottom: 10px; color: #1e3c72;">Fact Types Distribution</h3>
                <div class="table-container">
                    <table>
                        <tr>
                            <th>Fact Type</th>
                            <th style="text-align: right;">Count</th>
                        </tr>
            `;
            
            for (const [type, count] of Object.entries(data.fact_types || {})) {
                html += `<tr><td>${type}</td><td style="text-align: right; font-weight: 600;">${count}</td></tr>`;
            }
            
            html += `</table></div>`;
            
            if (data.recent_entries && data.recent_entries.length > 0) {
                html += `
                    <h3 style="margin-top: 20px; margin-bottom: 10px; color: #1e3c72;">Latest Entries</h3>
                    <div class="table-container">
                        <table>
                            <tr>
                                <th>Customer ID</th>
                                <th>Facts</th>
                                <th>Timestamp</th>
                            </tr>
                `;
                
                for (const entry of data.recent_entries.slice(-5)) {
                    const facts = entry.facts || [];
                    const factStr = facts.map(f => f.type).join(', ');
                    const ts = new Date(entry.timestamp).toLocaleString();
                    html += `
                        <tr>
                            <td><code>${entry.customer_id}</code></td>
                            <td>${factStr || '-'}</td>
                            <td style="font-size: 0.85em;">${ts}</td>
                        </tr>
                    `;
                }
                
                html += `</table></div>`;
            }
            
            document.getElementById('wal-content').innerHTML = html;
        }
        
        function renderChromaDB(data) {
            if (data.error) {
                document.getElementById('chromadb-content').innerHTML = `<div class="error">${data.error}</div>`;
                return;
            }
            
            let html = `
                <div class="stat">
                    <span class="stat-label">Collections</span>
                    <span class="stat-value">${data.collections?.length || 0}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Embeddings</span>
                    <span class="stat-value">${data.embeddings_count || 0}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">File Size</span>
                    <span class="stat-value">${data.size_kb.toFixed(1)} KB</span>
                </div>
                
                <h3 style="margin-top: 20px; margin-bottom: 10px; color: #1e3c72;">Collections</h3>
                <div class="table-container">
                    <table>
                        <tr>
                            <th>Name</th>
                            <th>Dimension</th>
                            <th>ID</th>
                        </tr>
            `;
            
            for (const col of data.collections || []) {
                html += `
                    <tr>
                        <td><code>${col.name}</code></td>
                        <td>${col.dimension || 'N/A'}</td>
                        <td><code style="font-size: 0.8em;">${col.id.substring(0, 8)}...</code></td>
                    </tr>
                `;
            }
            
            html += `</table></div>`;
            
            if (data.tables) {
                html += `
                    <h3 style="margin-top: 20px; margin-bottom: 10px; color: #1e3c72;">Tables</h3>
                    <div class="table-container">
                        <table>
                            <tr>
                                <th>Table Name</th>
                                <th style="text-align: right;">Rows</th>
                            </tr>
                `;
                
                for (const [table, count] of Object.entries(data.tables)) {
                    html += `<tr><td>${table}</td><td style="text-align: right;">${count}</td></tr>`;
                }
                
                html += `</table></div>`;
            }
            
            document.getElementById('chromadb-content').innerHTML = html;
        }
        
        function renderMem0(data) {
            if (data.error) {
                document.getElementById('sqlite-content').innerHTML = `<div class="error">${data.error}</div>`;
                return;
            }
            
            let html = `
                <div class="stat">
                    <span class="stat-label">Tables</span>
                    <span class="stat-value">${Object.keys(data.tables || {}).length}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">File Size</span>
                    <span class="stat-value">${data.size_kb.toFixed(1)} KB</span>
                </div>
                
                <h3 style="margin-top: 20px; margin-bottom: 10px; color: #1e3c72;">Tables</h3>
                <div class="table-container">
                    <table>
                        <tr>
                            <th>Table Name</th>
                            <th style="text-align: right;">Rows</th>
                            <th>Columns</th>
                        </tr>
            `;
            
            for (const [table, count] of Object.entries(data.tables || {})) {
                const cols = data.schema?.[table] || [];
                html += `
                    <tr>
                        <td><code>${table}</code></td>
                        <td style="text-align: right;">${count}</td>
                        <td style="font-size: 0.85em; color: #666;">${cols.join(', ') || '-'}</td>
                    </tr>
                `;
            }
            
            html += `</table></div>`;
            
            document.getElementById('sqlite-content').innerHTML = html;
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    import sys
    import socket

    if "--test-storage" in sys.argv:
        idx = sys.argv.index("--test-storage")
        prompt = "Meri monthly income 72000 hai aur co-applicant Sunita hai"
        customer = None
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--"):
            prompt = sys.argv[idx + 1]
        if "--customer" in sys.argv:
            cidx = sys.argv.index("--customer")
            if cidx + 1 < len(sys.argv):
                customer = sys.argv[cidx + 1]
        result = run_storage_test(prompt_text=prompt, customer_id=customer)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("ok") else 1)

    port = 8765
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            pass

    # auto-find a free port if the requested one is busy
    for candidate in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', candidate)) != 0:
                port = candidate
                break
    else:
        print(f"ERROR: no free port found in range {port}–{port+20}")
        sys.exit(1)

    server = HTTPServer(('localhost', port), MemoryDashboardHandler)
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                 MEMORY DASHBOARD STARTED                      ║
╚═══════════════════════════════════════════════════════════════╝

🌐 Open in browser: http://localhost:{port}

📊 Dashboard shows:
   • ChromaDB vector store (collections, embeddings)
   • Mem0 SQLite metadata (tables, relationships)
   • WAL log (all facts, timestamps, customers)
   • Storage locations and file sizes

Press Ctrl+C to stop the server.
""")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n✅ Server stopped.")
