# PS-01 Memory Layer — Complete Documentation

## 🧠 What is the Memory Layer?

The **Memory Layer** is how PS-01 remembers customer facts across sessions and agents. It's a **4-tier storage system** that:

1. **Never loses data** (WAL append-only log)
2. **Searches facts instantly** (Redis cache + ChromaDB vectors)
3. **Tracks relationships** (Mem0 SQLite relationships)
4. **Survives crashes** (WAL replay)

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    MEMORY LAYER                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  TIER 1: WAL (Source of Truth)                              │
│  File: /tmp/ps01_wal.jsonl                                  │
│  Role: Append-only log, crash recovery                      │
│                          ↓                                   │
│  TIER 2: ChromaDB (Vector Store)                            │
│  Path: ./chroma_db/{bank_id}/                               │
│  Role: Semantic search, relevance scoring                   │
│                          ↓                                   │
│  TIER 3: Mem0 SQLite (Metadata Index)                       │
│  Path: ./mem0_history/{bank_id}/{bank_id}.db                │
│  Role: Fast fact lookup by type, relationships              │
│                          ↓                                   │
│  TIER 4: Redis Cache (Speed Layer)                          │
│  Host: localhost:6379                                       │
│  Role: <50ms retrieval, session state                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 📝 TIER 1: WAL (Write-Ahead Log)

### **Purpose**
- Source of truth — never loses data
- Crash recovery — replay from last checkpoint
- Audit trail — every change recorded
- Immutable history

### **Structure**
```
File: /tmp/ps01_wal.jsonl

One fact per line (JSONL format):
```

### **Example Entry**
```json
{
  "entry_id": "E_001",
  "session_id": "sess_38f4ae4734e6",
  "customer_id": "C001",
  "agent_id": "AGENT_A",
  "timestamp": "2026-03-27T10:15:23Z",
  "facts": [
    {
      "fact_id": "F_001_income",
      "type": "income",
      "value": "55000_INR_MONTHLY",
      "source": "customer_verbal",
      "verified": false,
      "confidence": 0.95
    }
  ]
}
```

### **Key Properties**
| Property | Meaning |
|----------|---------|
| `entry_id` | Unique ID for this WAL entry |
| `session_id` | Which session added this fact |
| `customer_id` | Which customer owns this fact |
| `agent_id` | Which agent was involved |
| `timestamp` | When this fact was added |
| `facts` | Array of facts in this entry |

### **Minimal Code Example**
```python
# src/core/wal.py
class WALLogger:
    def append(self, session_id: str, customer_id: str, 
               agent_id: str, facts: list):
        """Write fact to WAL (always first, before Mem0)"""
        entry = {
            "session_id": session_id,
            "customer_id": customer_id,
            "agent_id": agent_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "facts": facts
        }
        # Append to /tmp/ps01_wal.jsonl (one line)
        with open("/tmp/ps01_wal.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
```

### **Rule: ALWAYS write WAL first**
```python
# ✅ CORRECT
wal_logger.append(session_id, customer_id, agent_id, facts)
mem0_bridge.add_with_wal(facts)

# ❌ WRONG (breaks recovery)
mem0_bridge.add_with_wal(facts)
wal_logger.append(session_id, customer_id, agent_id, facts)
```

---

## 🔍 TIER 2: ChromaDB (Vector Store)

### **Purpose**
- Semantic search: find "62000" when asked "income?"
- Relevance ranking: most important facts first
- Vectorized representation: customer facts as embeddings

### **How It Works**
```
Fact: "Customer income is 55000 rupees per month"
           ↓
Embedder (nomic-embed-text):
  Converts text → 1536-dimensional vector
           ↓
Vector: [0.234, -0.456, 0.789, ..., 0.123]
           ↓
Stored in ChromaDB with metadata
```

### **Directory Structure**
```
./chroma_db/
├── default/
│   ├── chroma.sqlite3
│   └── {collection_uuid}/
│       ├── data_level0.bin      ← vector data
│       ├── header.bin           ← metadata
│       ├── length.bin           ← vector lengths
│       └── link_lists.bin       ← indexing
└── cooperative_bank_01/
    └── [same structure]
```

### **Minimal Code Example**
```python
# src/infra/mem0_init.py
from chromadb.config import Settings

def init_mem0():
    """Initialize Mem0 with ChromaDB"""
    memory = Memory.from_config({
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": f"ps01_{bank_id}",
                "path": f"./chroma_db/{bank_id}"
            }
        },
        "embedder": {
            "provider": "ollama",
            "model": "nomic-embed-text"
        }
    })
    return memory
```

### **Metadata Stored with Vectors**
```python
# When fact is added to ChromaDB:
metadata = {
    "type": "income",              # Fact type
    "value": "55000",              # Fact value
    "verified": False,             # Trust level
    "customer_id": "C001",         # Owner
    "timestamp": "2026-03-27...",  # When added
    "session_id": "sess_..."       # Which session
}
```

---

## 📚 TIER 3: Mem0 SQLite (Metadata Index)

### **Purpose**
- Fast fact lookup by type, customer, verified status
- Relationship tracking (income → eligibility)
- Audit trail (created_at, updated_at)
- Soft deletes (is_active flag)

### **Database Schema**
```sql
TABLE memory_facts
├── id                  (PRIMARY KEY)
├── fact_id            (UNIQUE, links to WAL)
├── customer_id        (indexed)
├── type               (income, co_applicant, property, etc.)
├── value              (actual fact value)
├── source             (customer_verbal, form16, agent_feedback)
├── verified           (0=false, 1=true)
├── confidence         (0.0-1.0, trust score)
├── embedding_id       (links to ChromaDB vector)
├── created_at         (timestamp)
├── updated_at         (timestamp)
├── is_active          (0=deleted, 1=active)
└── session_id         (which session added this)

TABLE fact_relationships
├── from_fact_id       (source fact)
├── to_fact_id         (related fact)
├── relationship_type  (updates, conflicts_with, derived_from)
└── metadata           (JSON with details)
```

### **Example Queries**
```python
# Get all active facts for customer (fast!)
SELECT * FROM memory_facts 
WHERE customer_id = 'C001' 
  AND is_active = 1
ORDER BY updated_at DESC
LIMIT 5

# Get income facts only
SELECT * FROM memory_facts 
WHERE customer_id = 'C001' 
  AND type = 'income'
  AND is_active = 1

# Get verified facts
SELECT * FROM memory_facts 
WHERE customer_id = 'C001' 
  AND verified = 1
  AND is_active = 1

# Track relationships (income → eligibility)
SELECT fact_relationships.to_fact_id 
FROM fact_relationships
WHERE from_fact_id = 'F_001_income'
  AND relationship_type = 'derived_from'
```

### **Minimal Code Example**
```python
# src/core/mem0_bridge.py
class Mem0Bridge:
    def add_with_wal(self, facts: list):
        """Add facts to Mem0 (after WAL)"""
        for fact in facts:
            # Add to Mem0 (embeds + indexes)
            self.memory.add(
                fact_id=fact["fact_id"],
                content=fact["value"],
                metadata={
                    "type": fact["type"],
                    "verified": fact["verified"],
                    "source": fact["source"],
                    "customer_id": fact["customer_id"]
                }
            )
            
            # If fact updates another fact, track relationship
            if fact.get("relationship"):
                self._add_relationship(
                    from_fact=fact,
                    to_fact=fact["relationship"]["target"],
                    rel_type=fact["relationship"]["type"]
                )
```

---

## ⚡ TIER 4: Redis Cache (Speed Layer)

### **Purpose**
- Sub-100ms fact retrieval
- Session state storage
- Distributed locks (prevent concurrent updates)
- Temporary data (briefings, summaries)

### **Key Patterns**

#### **Pattern 1: Briefing Cache**
```
Key: briefing:{customer_id}
TTL: 3600 seconds (1 hour)

Value (JSON):
{
  "customer_id": "C001",
  "customer_name": "Rajesh Kumar",
  "session_count": 3,
  "facts": [...],
  "has_prior_context": true,
  "cached_at": "2026-03-27T10:45:00Z"
}
```

#### **Pattern 2: Summary Cache**
```
Key: summary:{customer_id}
TTL: 14400 seconds (4 hours)

Value: Compacted JSON from Phi4Compactor
{
  "total_facts": 8,
  "verified_facts": 3,
  "income": "62000",
  "co_applicant": "Sunita",
  "property": "Nashik",
  "eligibility": "₹48L",
  "next_steps": ["Collect documents"]
}
```

#### **Pattern 3: Session State**
```
Key: session:{session_id}
TTL: 7200 seconds (2 hours)

Value:
{
  "customer_id": "C001",
  "agent_id": "AGENT_D",
  "status": "active",
  "started_at": "2026-03-27T11:00:00Z",
  "last_activity": "2026-03-27T11:05:30Z"
}
```

#### **Pattern 4: Distributed Lock**
```
Key: lock:{customer_id}
TTL: 10 seconds (short!)

Mechanism: Redis SET NX (atomic)
Purpose: Only one agent updates same customer at a time

Use case:
Agent A: SET lock:C001 value=A NX ← SUCCESS (gets lock)
Agent B: SET lock:C001 value=B NX ← FAILED (lock held by A)
```

### **Minimal Code Example**
```python
# src/api/dependencies.py
async def get_redis_cache():
    """Get Redis connection"""
    redis = await aioredis.create_redis_pool("redis://localhost")
    return redis

# Usage in endpoints
async def build_briefing(customer_id: str, redis):
    # Check cache first (fast!)
    cached = await redis.get(f"briefing:{customer_id}")
    if cached:
        return json.loads(cached)
    
    # Cache miss: expensive search
    briefing = await briefing_builder.build(customer_id)
    
    # Store in cache for next time
    await redis.set(
        f"briefing:{customer_id}",
        json.dumps(briefing),
        ex=3600  # Expire in 1 hour
    )
    
    return briefing
```

---

## 🔄 Complete Flow: How Tiers Work Together

### **Step 1: Customer Fact Arrives**
```
Input: "Meri income ab 62000 ho gayi"
         ↓
Tokenize: PII masking
         ↓
Output: "Meri income ab {TOKEN_62000} ho gayi"
```

### **Step 2: Write to WAL (ALWAYS FIRST)**
```
WAL.append({
  "session_id": "sess_4aa...",
  "customer_id": "C001",
  "facts": [
    {
      "fact_id": "F_001_income_rev",
      "type": "income",
      "value": "62000_INR_MONTHLY",
      "verified": false
    }
  ]
})

File: /tmp/ps01_wal.jsonl ← ONE LINE APPENDED
```

### **Step 3: Sync to Mem0 (Async)**
```
Mem0Bridge.add_with_wal(facts)
  ├─ Embed fact text: "income 62000" → [vectors...]
  ├─ Store in ChromaDB: vectors + metadata
  ├─ Insert into SQLite: fact_id, type, verified
  └─ Create relationship: income_rev → updates → income_prev

Results:
  ./chroma_db/{bank_id}/... ← vector files
  ./mem0_history/{bank_id}/... ← SQLite with relationships
```

### **Step 4: Invalidate Cache**
```
Redis.delete(f"briefing:C001")
↓
Next session will rebuild fresh briefing:
  - Search WAL (recent facts)
  - Search ChromaDB (semantic match)
  - Search SQLite (metadata match)
  - Merge results
  - Cache for 1 hour
```

### **Step 5: Async Processing (Non-blocking)**
```
ConflictDetector: 55000 vs 62000 → CONFLICT!
           ↓
AdversarialGuard: +13% income → fraud_score=0.2 (low)
           ↓
DerivesWorker: Recalculate eligibility
           ↓
Mem0Bridge.update(): Store results back
           ↓
Customer doesn't wait for any of this ✅
```

---

## 🎯 Key Concepts

### **Concept 1: Cache Invalidation**
When a fact changes, **invalidate cache** so next request rebuilds fresh:

```python
# After income revision detected
await redis.delete(f"briefing:C001")

# Next session:
# 1. Cache miss for briefing:C001
# 2. Rebuild briefing with NEW income
# 3. Cache for 1 hour
# 4. Agent D sees updated income ✅
```

### **Concept 2: TTL (Time To Live)**
Different data has different lifespans:

```
Briefing (facts):          TTL = 3600s  (facts change often)
Summary (compacted):       TTL = 14400s (changes less often)
Session state:             TTL = 7200s  (session duration)
Branch registry:           TTL = 3600s  (config changes)
CBS pre-seed:              TTL = 86400s (daily refresh)
Tenant mapping:            TTL = null   (permanent)
Distributed lock:          TTL = 10s    (quick operations)
```

### **Concept 3: Soft Deletes**
In SQLite, facts are never really deleted — they're deactivated:

```sql
-- Delete a fact
UPDATE memory_facts 
SET is_active = 0 
WHERE fact_id = 'F_001_income'

-- Later: audit sees full history
SELECT * FROM memory_facts 
WHERE customer_id = 'C001'
ORDER BY created_at  -- Shows all versions!
```

### **Concept 4: Relationships**
Track how facts relate to each other:

```
income_55000 ─── updates ──→ income_62000
                 (one replaced the other)

income_62000 ─── derived_from ──→ eligibility_48L
                 (eligibility calculated from income)

documents_pending ─── conflicts_with ──→ documents_ready
                      (can't both be true)
```

---

## 🔒 PII Protection Across Layers

### **Never Stored Raw**
```
Input:  "Aapka Aadhaar 123456789012 aur PAN ABCDE1234F"
        ↓
Tokenize: "Aapka Aadhaar {AADHAAR_T1} aur PAN {PAN_T2}"
        ↓
WAL stores: "Aapka Aadhaar {AADHAAR_T1} aur PAN {PAN_T2}"
            ↓
ChromaDB stores: "Aapka Aadhaar {AADHAAR_T1}..." (no raw PII)
                 ↓
SQLite stores: "Aapka Aadhaar {AADHAAR_T1}..." (no raw PII)
               ↓
Redis caches: "Aapka Aadhaar {AADHAAR_T1}..." (no raw PII)

Token mapping (Aadhaar123... → {AADHAAR_T1}) stored SEPARATELY
in ConsentDB with restricted access ✅
```

---

## 📊 Storage Size Example

For **1 customer with 8 facts across 3 sessions**:

| Tier | Size | Notes |
|------|------|-------|
| WAL | 2KB | Per 3 facts (~650 bytes each) |
| ChromaDB | 50KB | 8 vectors × 1536 dims × 4 bytes |
| Mem0 SQLite | 10KB | Metadata + relationships |
| Redis Cache | 5KB | Briefing + summary (when active) |
| **Total** | **70KB** | Grows linearly with facts |

**Scaling to 1000 customers**: ~70MB (negligible)

---

## ⚙️ Operations You Can Do

### **Read: Get Customer Facts**
```python
# Option 1: Fast (from cache)
briefing = await redis.get(f"briefing:C001")

# Option 2: Medium (from Mem0 SQLite)
facts = memory.search("C001", query="income")

# Option 3: Thorough (from WAL)
all_facts = wal.replay(customer_id="C001")
```

### **Write: Add New Fact**
```python
# Always WAL first!
wal_logger.append("sess_...", "C001", "AGENT_A", [fact])

# Then async to Mem0
background_tasks.add_task(mem0_bridge.add_with_wal, [fact])

# Invalidate cache
await redis.delete(f"briefing:C001")
```

### **Update: Revise Fact**
```python
# Create new fact with relationship
new_fact = {
    "fact_id": "F_001_income_rev",
    "type": "income",
    "value": "62000",
    "relationship": {
        "target": "F_001_income",  # Previous fact
        "type": "updates"
    }
}

# WAL + Mem0 (same as write)
wal_logger.append("sess_...", "C001", "AGENT_D", [new_fact])
mem0_bridge.add_with_wal([new_fact])
```

### **Delete: Soft Delete Fact**
```python
# Mark as inactive in SQLite
memory_facts_db.update(
    f"UPDATE memory_facts SET is_active=0 WHERE fact_id=?"
)

# WAL tracks deletion
wal_logger.append("sess_...", "C001", "AGENT_D", [{
    "fact_id": "F_001_income",
    "deleted": True
}])
```

---

## 🚨 Common Mistakes

### ❌ Mistake 1: Skipping WAL
```python
# WRONG - loses crash recovery
mem0_bridge.add_with_wal(facts)
wal_logger.append(...)  # Too late!

# RIGHT - WAL first
wal_logger.append(...)
mem0_bridge.add_with_wal(facts)
```

### ❌ Mistake 2: Blocking on Cache Miss
```python
# WRONG - user waits 500ms on cache miss
briefing = redis.get(f"briefing:C001")
if not briefing:
    briefing = await expensive_search()  # 500ms wait!

# RIGHT - cache async, user gets quick response
briefing = await redis.get(f"briefing:C001}")
if briefing:
    return cached_briefing
else:
    # Rebuilt in background, user doesn't wait
    background_tasks.add_task(rebuild_briefing, customer_id)
    # Return last known briefing or default
```

### ❌ Mistake 3: Storing Raw PII
```python
# WRONG - raw data in WAL
fact = {
    "type": "pan",
    "value": "ABCDE1234F"  # ❌ RAW PII!
}

# RIGHT - tokenized
fact = {
    "type": "pan",
    "value": "{PAN_T1}"  # ✅ TOKENIZED
}
```

### ❌ Mistake 4: Not Invalidating Cache
```python
# WRONG - customer sees stale data
income_changes_from_55k_to_62k()
# Cache still has 55K! ❌

# RIGHT - invalidate cache
income_changes_from_55k_to_62k()
await redis.delete(f"briefing:C001")  # ✅
# Next request rebuilds fresh
```

---

## 🎓 Summary: Which Tier to Use?

| Use Case | Which Tier |
|----------|-----------|
| **Need crash recovery?** | WAL (append-only) |
| **Need semantic search?** | ChromaDB (vectors) |
| **Need fast lookup by type?** | Mem0 SQLite (metadata) |
| **Need instant response <50ms?** | Redis (cache) |
| **Need full history?** | WAL (replay all) |
| **Need relationships?** | SQLite (fact_relationships table) |
| **Need to verify trustworthiness?** | SQLite (verified flag) |

---

## 📚 Further Reading

- **WAL Pattern**: Write-Ahead Logging (database standard)
- **Vector Databases**: ChromaDB docs (chroma.ai)
- **SQLite**: Relational database for metadata
- **Redis**: In-memory cache for speed
- **Mem0**: Memory framework for AI agents

---

**Remember:** The Memory Layer makes PS-01 **fast, reliable, and never forgetful**. 🧠✨
