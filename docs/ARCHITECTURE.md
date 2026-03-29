# PS-01 System Architecture
## The Loan Officer Who Never Forgets

---

## Problem Statement

Rajesh, a home loan applicant, speaks to 4 different loan officers across multiple visits. Each time, he repeats the same information — income, existing EMIs, co-applicant details, previous conversations. The loan officers have no shared memory.

**PS-01 solves this**: a persistent, on-premise memory layer that gives every loan officer complete context about every customer from the first word.

---

## Non-Negotiable Constraints

| Constraint | Requirement |
|---|---|
| **Model** | Phi-4-Mini 3.8B only. No external API calls (no GPT-4, no Claude) |
| **Data Residency** | All data stays on bank server. Mem0 self-hosted only |
| **Language** | Hindi + English via AI4Bharat IndicASR v2 |
| **PII** | spaCy NER tokenization runs BEFORE every `mem0.add()` call |
| **Hardware** | CPU-only, 16GB RAM, 500GB SSD |

---

## High-Level Architecture

```
┌─────────────────────── BRANCH EDGE NODES ──────────────────────┐
│                                                                   │
│  Audio/Text → spaCy NER → Consent Gate → WAL → Mem0 (local)   │
│                                               ↓                  │
│                                         WALShipper              │
│                                               ↓                  │
└───────────────────────────────────────────────┼─────────────────┘
                                                 │
                             Redpanda: {bank_id}.session.events
                                                 │
┌────────────────────── CENTRAL HUB ─────────────┴────────────────┐
│                                                                   │
│  RedpandaConsumer → Mem0Bridge → Mem0 + ChromaDB                │
│                         ↓              ↓                         │
│                   Redis Primary   Phi4 Compactor                 │
└───────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Transcription | AI4Bharat IndicASR v2 (Docker) | Hindi + English speech-to-text |
| PII Masking | spaCy 3.8 NER | Tokenize PAN, Aadhaar, Phone before storage |
| Memory Engine | Mem0 0.0.15 + ChromaDB | Vector + graph memory, per-bank isolation |
| Local LLM | Phi-4-Mini via Ollama | Post-session compaction, no cloud |
| Durability | WAL (`wal.jsonl`) | Crash-safe fact storage |
| Caching | Redis (async) | Distributed lock + 4-hour summary cache |
| Messaging | Redpanda (Kafka-compatible) | Branch → Central WAL shipping |
| API | FastAPI 0.105 | `/session/start`, `/session/end` endpoints |
| Compliance | SQLite `consent.db` | DPDP Act consent gate |
| Multi-tenancy | `X-Bank-ID` header | Per-bank namespace isolation |

---

## Component Deep-Dive

### 1. PII Tokenization (`src/preprocessing/tokenizer.py`)

All text passes through `BankingTokenizer` **before** any storage operation.

```
Raw input: "My PAN is ABCDE1234F, phone is +919876543210"
         ↓
Tokenized: "My PAN is [TOKEN:PAN:a3f2], phone is [TOKEN:PHONE:b9d1]"
```

Patterns detected: PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), Aadhaar (12-digit), Indian phone (`+91...`), income amounts.

**Rule**: Raw PAN/Aadhaar **never** enters Mem0.

---

### 2. Consent Gate (`src/api/middleware.py`)

Every `mem0.add()` call is wrapped with `@require_consent(scope="home_loan_processing")`.

- **Consent record** must exist in `consent.db` for the session + scope + bank
- Missing consent → `HTTP 403 Forbidden`
- DPDP Act compliance: customer explicitly consents before any memory write

```python
# Without consent record → blocked
mem0_bridge.add_with_wal(session_id="S001", ...)  # → 403

# After recording consent → allowed
consent_db.record_consent("S001", "C001", "home_loan_processing", bank_id="cooperative_bank_01")
mem0_bridge.add_with_wal(session_id="S001", ...)  # → proceeds
```

---

### 3. WAL Layer (`src/core/wal.py`) — The Core Innovation

**Rule**: Always write to `wal.jsonl` **BEFORE** calling `mem0.add()`. Never skip.

**Why**: If the server crashes after `mem0.add()` starts but before it completes, facts are lost forever. The WAL ensures they can be replayed.

```
Customer states fact
       ↓
WALLogger.append()   ← written to wal.jsonl (durable)
       ↓
mem0.add()           ← if this crashes, WAL replay recovers it
       ↓
mark_shipped=True    ← only after successful Redpanda publish
```

**WAL Entry Schema:**
```json
{
  "session_id": "S004",
  "timestamp": "2025-03-18T14:22:00Z",
  "customer_id": "hashed_C001",
  "agent_id": "AGT_D",
  "bank_id": "cooperative_bank_01",
  "facts": [{
    "fact_id": "F012",
    "type": "income",
    "value": "62000_INR_MONTHLY",
    "relationship": "updates",
    "supersedes": "F001",
    "verified": false,
    "source": "customer_verbal"
  }],
  "idempotency_key": "uuid-1234",
  "shipped": false
}
```

---

### 4. Mem0 Bridge (`src/core/mem0_bridge.py`)

Orchestrates the full write pipeline:

```
WAL append → Redis lock (optional) → mem0.add() → Redis unlock
```

Uses composite user ID: `{bank_id}::{customer_id}` for multi-tenant Mem0 isolation.

---

### 5. Conflict Detection & Adversarial Guard

**ConflictDetector** (`src/core/conflict_detector.py`): Detects when a new fact contradicts an existing fact of the same type.

**AdversarialGuard** (`src/core/adversarial_guard.py`): Flags suspicious value changes for manual review.

| Fact Type | Suspicious Threshold |
|---|---|
| `income` | >50% change |
| `emi_outgoing` | >30% change |
| `loan_amount` | >100% change |

Example: Customer claimed income of ₹40K in session 1, now claims ₹80K → flagged, `review_required=true`.

---

### 6. Derived Facts (`src/core/derives_worker.py`)

Automatically calculated from stored facts:

```
net_income = income - total_emi_burden
loan_eligibility = net_income × 60  (5-year EMI rule)
```

---

### 7. Phi-4-Mini Compactor (`src/core/phi4_compactor.py`)

Post-session: sends all session facts to Phi-4-Mini (local Ollama) for compression.

- Removes contradictions, keeps latest values
- Flags verified vs derived facts
- Output cached to Redis (4-hour TTL) for instant next-session context injection

---

### 8. Multi-Branch Distribution

**Branch Edge Nodes** (one per bank branch):
- Runs WAL, local Mem0, WALShipper
- Handles offline resilience: works without central hub

**Central Hub** (one per bank):
- Aggregates WAL events from all branches via Redpanda
- Maintains authoritative Mem0 + ChromaDB
- Redis primary for distributed lock coordination

**WALShipper** (`src/core/wal_shipper.py`):
- Background task, polls WAL every 5 seconds
- Publishes unshipped entries to Redpanda topic `{bank_id}.session.events`
- Marks entries `shipped=true` after successful publish

---

### 9. Tenant Isolation (`src/api/tenant.py`)

Every API request must include `X-Bank-ID` header.

```
Request without X-Bank-ID → HTTP 400
Request with X-Bank-ID: cooperative_bank_01 → TenantContext(bank_id="cooperative_bank_01")
```

Tenant context flows through:
- Redis keys: `{bank_id}:summary:{customer_id}`, `{bank_id}:lock:{customer_id}`
- Mem0 user ID: `{bank_id}::{customer_id}`
- WAL entries: `bank_id` field
- ChromaDB path: `./chroma_db/{bank_id}/`
- Redpanda topic: `{bank_id}.session.events`

---

## Data Flow: Full Session

```
1. Officer opens session for customer Rajesh
   → POST /session/start {session_id, customer_id, bank_id}
   → Consent checked (consent.db)
   → Redis cache checked for existing summary
   → Mem0.search() for prior facts
   → System prompt injected with context

2. Conversation happens
   → Audio → IndicASR v2 → text transcript
   → spaCy NER tokenizes PII
   → Facts extracted

3. Fact storage (per fact)
   → ConflictDetector: any contradiction?
   → AdversarialGuard: suspicious change?
   → WALLogger.append() [BEFORE mem0.add()]
   → Consent gate check
   → Mem0Bridge.add_with_wal()
   → Redis lock → mem0.add() → Redis unlock

4. Session ends
   → POST /session/end
   → Phi4Compactor.compact(all_session_facts)
   → Compacted summary cached to Redis (4h TTL)
   → WALShipper ships unshipped entries to Redpanda

5. Central hub (async)
   → RedpandaConsumer receives WAL entries
   → Applies to central Mem0 instance
   → All branches now see updated facts on next session
```

---

## Implementation Phases

| Phase | Description | Status |
|---|---|---|
| **Phase 0** | Documentation discovery, API signatures, WAL format spec | ✅ Complete |
| **Phase 1** | Infrastructure: RedisCache, RedpandaProducer/Consumer, config | ✅ Complete |
| **Phase 2** | Multi-tenant: TenantMiddleware, WAL evolution, ConsentDB bank_id | ✅ Complete |
| **Phase 3** | Central processing pipeline: consumer + Mem0 integration | 🔲 Next |
| **Phase 4** | FastAPI endpoints: `/session/start`, `/session/end` | 🔲 Pending |
| **Phase 5** | End-to-end testing: 4 Rajesh sessions, complete flow validation | 🔲 Pending |

---

## File Map

```
PS01/
├── src/
│   ├── main.py                    Entry point
│   ├── api/
│   │   ├── middleware.py          ConsentDB + @require_consent
│   │   └── tenant.py              TenantMiddleware + TenantContext
│   ├── core/
│   │   ├── wal.py                 WALLogger (append, replay, get_unshipped, mark_shipped)
│   │   ├── mem0_bridge.py         Mem0Bridge.add_with_wal()
│   │   ├── phi4_compactor.py      Phi4Compactor.compact()
│   │   ├── wal_shipper.py         WALShipper background task
│   │   ├── conflict_detector.py   ConflictDetector.detect()
│   │   ├── adversarial_guard.py   AdversarialGuard.check()
│   │   └── derives_worker.py      DerivesWorker.calculate()
│   ├── infra/
│   │   ├── mem0_init.py           init_mem0(bank_id) factory
│   │   ├── redis_cache.py         RedisCache (lock + summary cache)
│   │   ├── redpanda_producer.py   RedpandaProducer (branch → central)
│   │   └── redpanda_consumer.py   RedpandaConsumer (central hub)
│   └── preprocessing/
│       ├── tokenizer.py           BankingTokenizer (PAN/Aadhaar/Phone)
│       └── banking_rules.py       BankingRules.calculate_disposable_income()
├── tests/
│   ├── test_infra_phase1.py       Redis + Redpanda unit tests
│   └── test_phase2_multitenant.py Tenant + WAL multi-tenant tests
├── config/
│   ├── branch.yaml                Branch edge config
│   └── central.yaml               Central hub config
└── .env.example                   Environment template
```

---

## Security Model

1. **PII never stored raw** — tokenized before any persistence
2. **Consent gates every write** — DPDP Act compliance
3. **Tenant isolation at every layer** — bank_id in keys, topics, paths, user IDs
4. **No cloud calls** — all inference local via Ollama
5. **Adversarial detection** — flags suspicious value changes for human review
6. **WAL idempotency** — `idempotency_key` prevents duplicate facts on replay
