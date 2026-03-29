# 📦 Memory Storage Anatomy — Where Your Data Lives

> **TL;DR:** Click **http://localhost:8765** to see all memory data in a beautiful dashboard. Or use command-line tools below.

---

## 🏗️ Storage Architecture Overview

Your PS-01 system has **4 memory tiers**, each physically stored in a different location:

```
┌─────────────────────────────────────────────────────────────┐
│                      YOUR SYSTEM                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  TIER 1: Write-Ahead Log (WAL)                            │
│  Location: /tmp/ps01_wal.jsonl                            │
│  Format: JSONL (one fact per line)                        │
│  Size: ~1.2 MB (55 entries so far)                        │
│  Role: "Source of Truth" - every fact recorded here FIRST │
│                                                             │
│  ↓ (copied asynchronously)                                 │
│                                                             │
│  TIER 2: ChromaDB (Vector Search)                         │
│  Location: ./chroma_db/default/chroma.sqlite3             │
│  Format: SQLite with vector embeddings                    │
│  Size: ~144 KB                                             │
│  Role: Semantic search, "what facts are similar?"         │
│                                                             │
│  ↓ (indexed metadata)                                      │
│                                                             │
│  TIER 3: Mem0 SQLite (Metadata)                          │
│  Location: ./mem0_history/default/default.db             │
│  Format: SQLite with structured metadata                  │
│  Size: ~12 KB                                              │
│  Role: Fast lookup by type/verified/customer             │
│                                                             │
│  ↓ (cached frequently used)                               │
│                                                             │
│  TIER 4: Redis Cache                                      │
│  Location: In-memory                                       │
│  Format: JSON blobs with TTL                              │
│  Size: ~varies (100s of MB possible)                      │
│  Role: <50ms retrieval, session state                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 📍 Folder Structure — Where Everything Lives

### **Absolute Paths**

```
/home/parth/ccode/wam0/                    (Project Root)
├── chroma_db/                             (TIER 2: Vector Store)
│   └── default/
│       ├── chroma.sqlite3                 (Main vector database)
│       └── a2de690a-ec4d-4959-ad77-dfd73ccae9b2/  (Collection data)
│
├── mem0_history/                          (TIER 3: Metadata Store)
│   └── default/
│       └── default.db                     (Mem0 SQLite database)
│
└── /tmp/ps01_wal.jsonl                    (TIER 1: Write-Ahead Log)
```

---

## 🎯 How to View Each Storage Tier

### **Option 1: Web Dashboard (Recommended) ✨**

Open your browser to see all data visually:

```bash
# Dashboard auto-opens on startup, or manually visit:
http://localhost:8765
```

**What you see:**
- 📊 Summary cards (file sizes, record counts)
- 📝 WAL entries (customer facts, timestamps)
- 🔍 ChromaDB collections (vector metadata)
- 📚 Mem0 tables (structured data)
- 🎨 Interactive tabs, real-time JSON viewers

---

### **Option 2: Command-Line Inspector**

Run the inspection script to view data in your terminal:

```bash
# View everything
python3 scripts/inspect_memory_storage.py --all

# View just one tier
python3 scripts/inspect_memory_storage.py --chromadb    # ChromaDB data
python3 scripts/inspect_memory_storage.py --sqlite      # Mem0 SQLite data
python3 scripts/inspect_memory_storage.py --wal         # WAL entries
python3 scripts/inspect_memory_storage.py --summary     # Just file sizes

# Show storage locations
python3 scripts/inspect_memory_storage.py --summary
```

---

### **Option 3: Direct Database Inspection**

#### **View ChromaDB**

```bash
# Connect to ChromaDB SQLite
sqlite3 /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3

# Inside sqlite3:
.tables                              # Show all tables
SELECT name FROM collections;        # Show collections
SELECT * FROM embeddings LIMIT 5;    # View sample embeddings
```

#### **View Mem0 SQLite**

```bash
# Connect to Mem0 database
sqlite3 /home/parth/ccode/wam0/mem0_history/default/default.db

# Inside sqlite3:
.schema                              # Show all tables + columns
SELECT * FROM history;               # View stored memories
.mode column                          # Pretty-print results
.headers on                           # Show column headers
```

#### **View WAL (JSON Lines Format)**

```bash
# View all entries
cat /tmp/ps01_wal.jsonl | jq .

# View just last 5 entries
tail -5 /tmp/ps01_wal.jsonl | jq .

# Count total entries
wc -l /tmp/ps01_wal.jsonl

# View specific customer's facts
cat /tmp/ps01_wal.jsonl | jq 'select(.customer_id == "C001")'

# Show fact distribution
cat /tmp/ps01_wal.jsonl | jq '.facts[].type' | sort | uniq -c
```

---

## 📊 What's Currently Stored

As of now (March 27, 2026):

### **Tier 1: WAL** 📝
- **File:** `/tmp/ps01_wal.jsonl`
- **Size:** 25.7 KB
- **Entries:** 55 total
- **Customers:** Multiple (C001, cust_rajesh_001, etc.)
- **Fact Types:** income, emi_outgoing, co_applicant_income, land_record, derived_eligibility
- **Sample Entry:**
  ```json
  {
    "session_id": "S001",
    "timestamp": "2026-03-27T09:29:17.814748+00:00Z",
    "customer_id": "C001",
    "agent_id": "AGT_C",
    "bank_id": "central",
    "facts": [
      {
        "fact_id": "F006",
        "type": "land_record",
        "value": "Nashik_plot_1200sqm_encumbrance_clean",
        "verified": true,
        "source": "document_parsed",
        "relationship": "new"
      }
    ]
  }
  ```

### **Tier 2: ChromaDB** 🔍
- **File:** `/home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3`
- **Size:** 144 KB
- **Collections:** 2
  - `mem0_default` (current memory collection)
  - `mem0migrations` (system metadata, 1536-dim vectors)
- **Embeddings:** 1 indexed
- **Tables:** 17 (migrations, segments, collections, embeddings, etc.)

### **Tier 3: Mem0 SQLite** 📚
- **File:** `/home/parth/ccode/wam0/mem0_history/default/default.db`
- **Size:** 12 KB
- **Tables:** 1 (history table, empty)
- **Purpose:** Will store structured relationships once facts are synced

### **Tier 4: Redis Cache** 🚀
- **Status:** Not persisting to disk (in-memory only)
- **Keys Stored:**
  - `briefing:{customer_id}` → Last briefing (TTL: 1 hour)
  - `summary:{customer_id}` → Session summary (TTL: 4 hours)
  - `session:{session_id}` → Session state (TTL: 2 hours)
  - `conversation_history:{session_id}` → Chat history

---

## 🔄 Data Flow Through Tiers

Here's how a fact moves through the 4-tier system:

```
1. Customer speaks (Voice Input)
         ↓
2. AI4Bharat transcribes + tokenizes PII
         ↓
3. Fact extracted (e.g., "income: 62000")
         ↓
4. ✅ WRITE TO TIER 1: WAL ← Must happen FIRST
         ↓ (background process)
5. Generate vector embedding (nomic-embed-text)
         ↓
6. ✅ WRITE TO TIER 2: ChromaDB ← Store vector + metadata
         ↓ (background process)
7. Index fact metadata (type, customer_id, verified, etc.)
         ↓
8. ✅ WRITE TO TIER 3: Mem0 SQLite ← Store relationships
         ↓ (background process)
9. Cache briefing dict (contains top 5 facts)
         ↓
10. ✅ WRITE TO TIER 4: Redis ← <50ms retrieval ready

NEXT SESSION:
         ↓
11. Agent D queries "Give me briefing for C001"
    → Redis cache HIT? (<50ms) ✅
    → No? Query ChromaDB vectors (semantic search)
    → No? Query Mem0 SQLite (fast lookup)
    → Emergency? Replay WAL from backup
```

---

## 🛠️ Practical Operations

### **Task: See how much data is stored**

```bash
# Summary
du -sh /home/parth/ccode/wam0/chroma_db
du -sh /home/parth/ccode/wam0/mem0_history
ls -lh /tmp/ps01_wal.jsonl

# Output:
# 6.3M    /home/parth/ccode/wam0/chroma_db
# 12K     /home/parth/ccode/wam0/mem0_history
# 25K     /tmp/ps01_wal.jsonl
```

### **Task: Export all facts for backup**

```bash
# Copy WAL to safe location
cp /tmp/ps01_wal.jsonl ~/backup_facts_$(date +%s).jsonl

# Pretty-print to JSON file
cat /tmp/ps01_wal.jsonl | jq . > ~/facts_readable.json
```

### **Task: Search for specific customer's facts**

```bash
# Find all facts for Rajesh
cat /tmp/ps01_wal.jsonl | jq '.[] | select(.customer_id | contains("rajesh"))'

# Count facts by customer
cat /tmp/ps01_wal.jsonl | jq '.customer_id' | sort | uniq -c
```

### **Task: Check if data is syncing to ChromaDB**

```bash
# Look at actual vector data stored
sqlite3 /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 \
  "SELECT COUNT(*) as embedding_count FROM embeddings;"
```

### **Task: Clear memory (careful!)**

```bash
# Don't use these lightly, but if needed:

# Clear WAL (source of truth - backup first!)
cp /tmp/ps01_wal.jsonl ~/backup_wal.jsonl
rm /tmp/ps01_wal.jsonl
touch /tmp/ps01_wal.jsonl

# Clear ChromaDB collections
rm -rf /home/parth/ccode/wam0/chroma_db/default/*

# Clear Mem0 history
rm /home/parth/ccode/wam0/mem0_history/default/default.db
```

---

## 📈 Understanding File Growth

- **WAL grows fastest:** ~400 bytes per fact (new entries appended)
- **ChromaDB grows moderate:** ~1.5 KB per fact (vector embedding)
- **Mem0 SQLite grows slowest:** ~100 bytes per fact (metadata only)
- **Redis volatile:** Cleared on restart, size depends on active sessions

---

## ✅ Storage Health Checklist

Before validation tests:

```bash
# 1. Does WAL exist and have data?
[ -s /tmp/ps01_wal.jsonl ] && echo "✅ WAL OK" || echo "❌ WAL missing"

# 2. Is ChromaDB initialized?
[ -f /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 ] && \
  echo "✅ ChromaDB OK" || echo "❌ ChromaDB missing"

# 3. Is Mem0 SQLite present?
[ -f /home/parth/ccode/wam0/mem0_history/default/default.db ] && \
  echo "✅ Mem0 OK" || echo "❌ Mem0 missing"

# 4. Can we query each database?
sqlite3 /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 ".tables" > /dev/null && \
  echo "✅ ChromaDB queryable" || echo "❌ ChromaDB corrupted"

# 5. Is WAL actually capturing facts?
[ $(wc -l < /tmp/ps01_wal.jsonl) -gt 0 ] && \
  echo "✅ WAL has $(wc -l < /tmp/ps01_wal.jsonl) entries" || \
  echo "❌ WAL empty"
```

---

## 🎓 Key Concepts

### **Write-Ahead Log (WAL) = Source of Truth**
- Append-only file: `/tmp/ps01_wal.jsonl`
- Each line is immutable
- Can replay to recover from crashes
- Used by Mem0 for synchronization

### **ChromaDB = Smart Search**
- Converts facts → vector embeddings
- Allows semantic queries: "what income facts exist?"
- Powers the "Get briefing" feature
- Fast retrieval when indexed

### **Mem0 SQLite = Metadata Index**
- Structured storage of fact properties
- Enables: "find all verified income facts for customer C001"
- Tracks relationships between facts
- Supplements ChromaDB with fast SQL queries

### **Redis Cache = Speed Layer**
- Stores pre-computed briefings
- TTL-based auto-expiry
- <50ms retrieval (vs 300ms+ for database)
- Clears on restart (acceptable for 1-hour sessions)

---

## 🚨 Troubleshooting

**Q: Dashboard shows 0 embeddings but I have 55 WAL entries?**
- A: WAL and embeddings are separate. ChromaDB is initialized but facts haven't been synced yet. Run `python3 scripts/sync_to_vector.py` to trigger sync.

**Q: File sizes seem small - is data being saved?**
- A: Yes! 25KB for 55 facts is normal. JSON is compact, vectors are binary.

**Q: Can I delete old data?**
- A: Yes, but backup WAL first. Deleting WAL loses source of truth. SafestOption: archive old WAL to backup directory.

**Q: Why separate files instead of one database?**
- A: Performance + safety. WAL is source of truth (never corrupted), ChromaDB is fast search (can be rebuilt), Redis is cache (can be cleared).

---

## 📞 Next Steps

1. **View the dashboard:** http://localhost:8765
2. **Run inspector:** `python3 scripts/inspect_memory_storage.py --all`
3. **Query ChromaDB:** `sqlite3 ./chroma_db/default/chroma.sqlite3`
4. **View facts:** `cat /tmp/ps01_wal.jsonl | jq .`
5. **Monitor growth:** Watch file sizes over time as facts accumulate
