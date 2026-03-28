# PS-01 Implementation Phases

## Overview

| Phase | Title | Status |
|---|---|---|
| 0 | Documentation Discovery | ✅ Complete |
| 1 | Core Infrastructure | ✅ Complete |
| 2 | Multi-Tenant + WAL Evolution | ✅ Complete |
| 3 | Central Processing Pipeline | 🔲 Next |
| 4 | FastAPI Session Endpoints | 🔲 Pending |
| 5 | End-to-End Demo | 🔲 Pending |

---

## Phase 0 — Documentation Discovery ✅

**Goal**: Collect all API signatures, formats, and patterns before writing code.

**Deliverables**:
- `IMPLEMENTATION_ROADMAP.md` — copy-ready API signatures
- WAL entry JSON schema
- PAN/Aadhaar/Phone regex patterns
- Phi-4-Mini compactor prompt template
- Consent middleware skeleton
- Docker setup commands

---

## Phase 1 — Core Infrastructure ✅

**Goal**: Build and test the infrastructure layer (Redis, Redpanda, config).

**Files created**:
- `src/infra/redis_cache.py` — `RedisCache` (async summary cache + distributed lock)
- `src/infra/redpanda_producer.py` — `RedpandaProducer` (WAL publisher with retry/backoff)
- `src/infra/redpanda_consumer.py` — `RedpandaConsumer` (central hub WAL consumer)
- `src/infra/mem0_init.py` — `init_mem0(bank_id)` factory
- `config/branch.yaml` — Branch edge node configuration
- `config/central.yaml` — Central hub configuration
- `tests/test_infra_phase1.py` — Full unit test suite (all mocked, no live infra needed)

**Key decisions**:
- Token-based distributed locking (Lua script compare-and-delete, prevents accidental unlock)
- Exponential backoff (1s → 300s) on Redpanda connect — handles broker startup race
- Graceful degradation: Redis errors don't crash writes, they skip locking
- Topic naming: `{bank_id}.session.events` for partition-per-bank isolation

**Test results**: 26/26 passing

---

## Phase 2 — Multi-Tenant + WAL Evolution ✅

**Goal**: Add bank-level tenant isolation and evolve WAL to support multi-branch sync.

**Files created/modified**:
- `src/api/tenant.py` — `TenantMiddleware` (enforces `X-Bank-ID` header), `TenantContext` dataclass
- `src/core/wal.py` — Added `bank_id`, `idempotency_key`, `shipped` fields to WAL entries
- `src/core/wal_shipper.py` — `WALShipper` background task (polls + publishes every 5s)
- `src/api/middleware.py` — `ConsentDB` enhanced with `bank_id` column + idempotent migration
- `tests/test_phase2_multitenant.py` — 24 tests covering all Phase 2 components

**Key decisions**:
- `X-Bank-ID` header required on all requests → `HTTP 400` if missing
- `X-Branch-ID` optional header for branch-level routing
- `idempotency_key` (UUID) prevents duplicate fact insertion on WAL replay
- ConsentDB migration is idempotent (`ALTER TABLE IF NOT EXISTS` pattern)
- Tests use `httpx.AsyncClient` + `ASGITransport` (not deprecated `TestClient`) for ASGI middleware testing

**Test results**: 50/50 passing (24 Phase 2 + 26 Phase 1)

---

## Phase 3 — Central Processing Pipeline 🔲

**Goal**: Wire together the consumer, Mem0 write, and conflict detection on the central hub.

**Tasks**:
1. Implement `central_handler(wal_entry)` function
   - Receives WAL entry from `RedpandaConsumer`
   - Runs `ConflictDetector.detect(existing, new_facts)`
   - Runs `AdversarialGuard.check()` on suspicious changes
   - Calls `DerivesWorker.calculate()` for derived facts
   - Calls `Mem0Bridge.add_with_wal()` on central Mem0 instance
2. Wire `RedpandaConsumer.consume(central_handler)` in startup
3. Add central hub entry point (`src/central_main.py` or startup hook)
4. Tests: mock Redpanda consumer, verify handler logic, conflict detection integration

**Files to create**:
- `src/core/central_handler.py` — `CentralHandler.handle(wal_entry)`
- `src/central_main.py` — startup: init consumer, start consume loop
- `tests/test_phase3_central.py`

---

## Phase 4 — FastAPI Session Endpoints 🔲

**Goal**: Expose the full memory pipeline via REST API.

**Endpoints**:

```
POST /session/start
  Body: {session_id, customer_id, agent_id, bank_id}
  Headers: X-Bank-ID, X-Branch-ID (optional)
  - Verify/record consent
  - Pull Redis cache summary (if exists)
  - Run Mem0.search() for prior facts
  - Return: {context_summary, prior_facts, session_token}

POST /session/end
  Body: {session_id, facts: [...]}
  Headers: X-Bank-ID
  - For each fact: ConflictDetector → AdversarialGuard → WAL → Mem0
  - Run Phi4Compactor.compact(facts)
  - Cache compacted summary to Redis (4h TTL)
  - Return: {facts_stored, conflicts_flagged, summary_cached}

GET /customer/{customer_id}/summary
  Headers: X-Bank-ID
  - Pull Redis cache or Mem0.search()
  - Return: {summary, facts, last_session}
```

**Files to create**:
- `src/api/routes.py` — FastAPI router with all 3 endpoints
- Wire into `src/main.py`
- `tests/test_phase4_api.py`

---

## Phase 5 — End-to-End Demo 🔲

**Goal**: Demonstrate 4 Rajesh sessions showing persistent memory across agents.

**Demo scenario** (`demo-rajesh.md`):

| Session | Agent | What Rajesh says | What system remembers |
|---|---|---|---|
| S001 | AGT_A | Income ₹55K, wants ₹30L home loan | Stores: income, loan_amount |
| S002 | AGT_B | "As I told you before..." — mentions wife's income ₹20K | Retrieves S001 facts, adds co-income |
| S003 | AGT_C | EMI burden ₹8K/month | Conflict: income now ₹60K (was ₹55K) → flagged |
| S004 | AGT_D | Final verification | Full history: net_income=₹67K, eligibility=₹40.2L |

**Judge demo criteria**:
- No repeated questions asked
- System surfaces prior facts from different agents
- Conflict flagged and shown
- Derived eligibility calculated correctly
- Zero cloud API calls during demo

**Files to create**:
- `.claude/commands/demo-rajesh.md` — slash command for full demo run
- `.claude/commands/session-start.md` — session initialization workflow
- `.claude/commands/session-end.md` — session close workflow
- `.claude/commands/wal-replay.md` — WAL recovery workflow
- `tests/test_phase5_e2e.py` — full integration test (4 sessions)
- `docker/docker-compose.yml` — single-command stack startup
