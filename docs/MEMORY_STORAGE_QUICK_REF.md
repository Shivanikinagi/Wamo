# 🎯 Memory Storage Quick Reference — Copy/Paste Commands

## 🌐 Web Dashboard (Easiest)

```bash
# Already running! Just open in browser:
http://localhost:8765
```

Shows: file sizes, entry counts, fact types, recent entries, collection metadata

---

## 🖥️ Command-Line Tools

### **Inspect Summary (Files + Sizes)**

```bash
# Run the Python inspector
cd /home/parth/ccode/wam0
python3 scripts/inspect_memory_storage.py --summary
```

Output:
```
✅ ChromaDB             → ./chroma_db/default (6284.7KB total)
✅ Mem0 SQLite          → ./mem0_history/default (12.0KB total)
✅ WAL                  → /tmp (1181.5KB total)
```

---

### **View Tier 1: Write-Ahead Log (WAL)**

**Quick preview:**
```bash
# See how many facts stored
wc -l /tmp/ps01_wal.jsonl

# View last 3 entries (most recent facts)
tail -3 /tmp/ps01_wal.jsonl | jq .

# View first 3 entries (oldest facts)
head -3 /tmp/ps01_wal.jsonl | jq .
```

**Analytical queries:**
```bash
# All facts for customer C001
cat /tmp/ps01_wal.jsonl | jq '.[] | select(.customer_id == "C001")'

# Count facts by type
cat /tmp/ps01_wal.jsonl | jq -r '.facts[].type' | sort | uniq -c

# List all unique customers
cat /tmp/ps01_wal.jsonl | jq -r '.customer_id' | sort | uniq

# Find all verified facts
cat /tmp/ps01_wal.jsonl | jq '.[] | select(.facts[].verified == true)'

# Show fact value distribution (income only)
cat /tmp/ps01_wal.jsonl | jq '.[] | select(.facts[].type == "income") | .facts[].value'
```

**Full read:**
```bash
# Pretty-print entire WAL to console
cat /tmp/ps01_wal.jsonl | jq .

# Pretty-print and save to file
cat /tmp/ps01_wal.jsonl | jq . > /tmp/facts_readable.json
```

**Backup/Export:**
```bash
# Backup WAL with timestamp
cp /tmp/ps01_wal.jsonl ~/backup/ps01_wal_$(date +%Y%m%d_%H%M%S).jsonl

# Export to CSV (for Excel analysis)
cat /tmp/ps01_wal.jsonl | jq -r '[.timestamp, .customer_id, .session_id, (.facts | length)] | @csv' > ~/facts.csv
```

---

### **View Tier 2: ChromaDB Vectors**

**Via SQLite CLI:**
```bash
# Connect to ChromaDB
sqlite3 /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3

# Then inside sqlite3, run these:
.mode column
.headers on

-- Show all collections
SELECT id, name, dimension FROM collections;

-- Count embeddings per collection
SELECT COUNT(*) as total_embeddings FROM embeddings;

-- Show collection metadata
SELECT * FROM collection_metadata;

-- List all tables
.tables

-- Exit
.quit
```

**Via command line (one-liners):**
```bash
# Count embeddings
sqlite3 ./chroma_db/default/chroma.sqlite3 "SELECT COUNT(*) FROM embeddings;"

# List collections
sqlite3 ./chroma_db/default/chroma.sqlite3 "SELECT name FROM collections;"

# Show table sizes
sqlite3 ./chroma_db/default/chroma.sqlite3 "
  SELECT name FROM sqlite_master WHERE type='table'
  UNION ALL
  SELECT (SELECT COUNT(*) FROM sqlite_master WHERE type='table') as count;"
```

---

### **View Tier 3: Mem0 SQLite**

**Via SQLite CLI:**
```bash
# Connect to Mem0 history database
sqlite3 /home/parth/ccode/wam0/mem0_history/default/default.db

# Then inside sqlite3, run these:
.schema                      -- Show all tables/columns
.tables                      -- List table names
SELECT * FROM history;       -- View stored memories
SELECT COUNT(*) FROM history; -- Count entries

.quit
```

**Via command line (one-liners):**
```bash
# Count history entries
sqlite3 ./mem0_history/default/default.db "SELECT COUNT(*) FROM history;"

# List all tables
sqlite3 ./mem0_history/default/default.db ".tables"

# Show schema
sqlite3 ./mem0_history/default/default.db ".schema"
```

---

### **View Tier 4: Redis Cache (In-Memory)**

**Check if Redis is running:**
```bash
redis-cli PING
# Should return: PONG
```

**View cached data:**
```bash
# Connect to Redis CLI
redis-cli

# Then run these commands:
KEYS *                           -- Show all cached keys
KEYS briefing:*                  -- Show all briefing caches
KEYS session:*                   -- Show all session caches
GET briefing:C001                -- Get briefing for customer C001
TTL briefing:C001                -- Check cache expiry time (seconds)
DBSIZE                           -- Show total number of keys
FLUSHDB                          -- CLEAR all cache (careful!)

exit
```

---

## 🔍 Real-World Examples

### **Example 1: Find all income revisions for customer Rajesh**

```bash
cat /tmp/ps01_wal.jsonl | jq '.[] | 
  select(.customer_id | contains("rajesh")) | 
  select(.facts[].type == "income") | 
  {timestamp: .timestamp, old_income: .facts[0].value}'
```

### **Example 2: See which agent extracted which facts**

```bash
cat /tmp/ps01_wal.jsonl | jq -r '[.agent_id, .facts[].type, .facts[].value] | @csv' | column -t -s,
```

### **Example 3: Check if verified facts are being stored**

```bash
cat /tmp/ps01_wal.jsonl | jq '
  [.[] | 
   {customer: .customer_id, 
    verified_count: ([.facts[] | select(.verified == true)] | length), 
    total_facts: (.facts | length)}] | 
  group_by(.customer) |
  map({customer: .[0].customer, total_verified: (map(.verified_count) | add), total: (map(.total_facts) | add)})'
```

### **Example 4: Backup data across all 3 tiers**

```bash
# Create backup directory
mkdir -p ~/ps01_backup_$(date +%Y%m%d)

# Backup WAL
cp /tmp/ps01_wal.jsonl ~/ps01_backup_$(date +%Y%m%d)/

# Backup ChromaDB
cp -r /home/parth/ccode/wam0/chroma_db ~/ps01_backup_$(date +%Y%m%d)/

# Backup Mem0
cp -r /home/parth/ccode/wam0/mem0_history ~/ps01_backup_$(date +%Y%m%d)/

# Verify
ls -lh ~/ps01_backup_$(date +%Y%m%d)/
```

---

## 📊 Monitoring Commands (Run Periodically)

### **Daily Health Check**

```bash
#!/bin/bash
echo "=== PS-01 Memory Health Check ==="
echo ""
echo "📝 WAL Status:"
echo "  Entries: $(wc -l < /tmp/ps01_wal.jsonl)"
echo "  Size: $(du -h /tmp/ps01_wal.jsonl | cut -f1)"
echo ""
echo "🔍 ChromaDB Status:"
sqlite3 /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 \
  "SELECT 'Collections: ' || COUNT(*) FROM collections;" 2>/dev/null || echo "  Error reading ChromaDB"
echo ""
echo "📚 Mem0 Status:"
sqlite3 /home/parth/ccode/wam0/mem0_history/default/default.db \
  "SELECT 'Tables: ' || COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null
echo ""
echo "🚀 Redis Status:"
redis-cli PING 2>/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"
```

### **Watch File Sizes (Real-Time)**

```bash
watch -n 5 'ls -lh /tmp/ps01_wal.jsonl /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 /home/parth/ccode/wam0/mem0_history/default/default.db'
```

---

## 🛠️ Maintenance Operations

### **Compact WAL (Optimization)**

```bash
# Sort and remove duplicates (caution: changes timestamps)
cat /tmp/ps01_wal.jsonl | jq -s 'sort_by(.idempotency_key) | unique_by(.idempotency_key) | .[]' > /tmp/ps01_wal_compact.jsonl

# Verify
wc -l /tmp/ps01_wal.jsonl /tmp/ps01_wal_compact.jsonl

# If smaller, replace
if [ $(wc -c < /tmp/ps01_wal_compact.jsonl) -lt $(wc -c < /tmp/ps01_wal.jsonl) ]; then
  mv /tmp/ps01_wal_compact.jsonl /tmp/ps01_wal.jsonl
  echo "✅ WAL compacted"
fi
```

### **Rebuild ChromaDB from WAL**

```bash
# If ChromaDB gets corrupted, rebuild from source:
rm -rf /home/parth/ccode/wam0/chroma_db/default/*
python3 src/scripts/rebuild_chromadb.py
```

### **Clear Session Cache**

```bash
# Clear specific session
redis-cli DEL session:S001 session:S002

# Clear all caches (caution!)
redis-cli FLUSHDB
```

---

## 📈 Storage Sizing Guide

| Tier | Per Fact | Per 1000 Facts | Per 1M Facts |
|------|----------|---|---|
| WAL | ~400 bytes | 400 KB | 400 MB |
| ChromaDB | ~1.5 KB | 1.5 MB | 1.5 GB |
| Mem0 SQLite | ~100 bytes | 100 KB | 100 MB |
| **TOTAL** | **~2 KB** | **~2 MB** | **~2 GB** |

*Storage for 1M facts: ~2 GB (reasonable for production)*

---

## 🎓 Understanding Output Formats

### **WAL Format (JSONL)**
```json
{
  "session_id": "S001",
  "timestamp": "2026-03-27T09:29:17.814748+00:00Z",
  "customer_id": "C001",
  "agent_id": "AGT_A",
  "bank_id": "central",
  "facts": [
    {
      "fact_id": "F001",
      "type": "income",
      "value": "55000_INR_MONTHLY",
      "verified": false,
      "source": "customer_verbal",
      "relationship": "new"
    }
  ],
  "idempotency_key": "uuid-here",
  "shipped": false
}
```

### **ChromaDB Collections**
```
Collection: mem0_default
  ID: 7cb94fd2-bd71-4017-8234-8ca5ed64e2ef
  Dimension: (dynamic, based on embeddings)
  Purpose: Store all fact vectors

Collection: mem0migrations
  ID: 1d625029-9272-414b-9055-4e4748d152b1
  Dimension: 1536 (text-embedding-007)
  Purpose: System metadata
```

### **Mem0 Tables**
```
Table: history
  Columns: [id, memory_text, metadata, created_at, updated_at, ...]
  Purpose: Store structured memory facts
```

---

## ✅ Verification Checklist

Before running demo:

```bash
# 1. WAL has data
[ $(wc -l < /tmp/ps01_wal.jsonl) -gt 10 ] && echo "✅ WAL populated" || echo "❌ WAL empty"

# 2. ChromaDB is queryable
sqlite3 /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 ".tables" > /dev/null && echo "✅ ChromaDB OK" || echo "❌ ChromaDB corrupted"

# 3. Mem0 SQLite exists
[ -s /home/parth/ccode/wam0/mem0_history/default/default.db ] && echo "✅ Mem0 exists" || echo "❌ Mem0 missing"

# 4. Redis responsive
redis-cli PING | grep -q PONG && echo "✅ Redis OK" || echo "⚠️  Redis not running"

# 5. All files readable
[ -r /tmp/ps01_wal.jsonl ] && [ -r /home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3 ] && [ -r /home/parth/ccode/wam0/mem0_history/default/default.db ] && echo "✅ All files readable" || echo "❌ Permission issues"
```

---

## 🚨 Troubleshooting

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| **Dashboard shows "No data"** | WAL entries may not be synced yet | Run `python3 scripts/sync_wal_to_chromadb.py` |
| **ChromaDB feels slow** | Too many embeddings without pruning | Archive old facts to backup WAL |
| **"Database locked" error** | Another process accessing DB | Close other SQLite connections or restart |
| **Redis "Connection refused"** | Redis not running | `redis-server` or check docker |
| **WAL file corrupted** | JSON malformed | Restore from backup WAL |

---

## 🎯 Shortcuts

```bash
# Ultra-quick check (all in one command)
wc -l /tmp/ps01_wal.jsonl && sqlite3 ./chroma_db/default/chroma.sqlite3 'SELECT COUNT(*) FROM collections;' && redis-cli PING

# View latest fact added
tail -1 /tmp/ps01_wal.jsonl | jq .

# Count facts by customer (top 5)
cat /tmp/ps01_wal.jsonl | jq -r '.customer_id' | sort | uniq -c | sort -rn | head -5

# Export facts as CSV
cat /tmp/ps01_wal.jsonl | jq -r '[.session_id, .customer_id, .agent_id, (.facts | length)] | @csv' > ~/facts.csv && echo "✅ Exported to ~/facts.csv"
```

---

**Last Updated:** March 27, 2026  
**System Ready:** ✅ All tiers operational
