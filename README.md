# PS-01: The Loan Officer Who Never Forgets

A local, memory-augmented loan assistant for multi-session conversations with WAL-first durability, Mem0 recall, and phi4-mini generation through Ollama.

> **🚀 New to PS-01?** Start with [START_HERE.md](START_HERE.md) for a 5-minute quick start guide!

## Overview

PS-01 solves a critical problem in banking: Rajesh, a home loan applicant, speaks to 4 different loan officers across multiple visits. Each time, he repeats the same information. PS-01 provides a persistent, on-premise memory layer that gives every loan officer complete context about every customer from the first word.

## Non-Negotiable Constraints

| Constraint | Requirement |
|---|---|
| **Model** | Phi-4-Mini 3.8B only. No external API calls |
| **Data Residency** | All data stays on bank server. Mem0 self-hosted only |
| **Language** | Hindi + English via AI4Bharat IndicASR v2 |
| **PII** | Regex-based tokenization runs BEFORE every mem0.add() call |
| **Hardware** | CPU-only, 16GB RAM, 500GB SSD |

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Transcription | AI4Bharat IndicASR v2 (Docker) | Hindi + English speech-to-text |
| PII Masking | Regex-based tokenizer | Tokenize PAN, Aadhaar, Phone before storage |
| Memory Engine | Mem0 0.0.15 + ChromaDB | Vector + graph memory, per-bank isolation |
| Local LLM | Phi-4-Mini via Ollama | Post-session compaction, no cloud |
| Durability | WAL (`wal.jsonl`) | Crash-safe fact storage |
| Caching | Redis (async) | Distributed lock + 4-hour summary cache |
| Messaging | Redpanda (Kafka-compatible) | Branch → Central WAL shipping |
| API | FastAPI 0.105 | `/session/start`, `/session/end` endpoints |
| Compliance | SQLite `consent.db` | DPDP Act consent gate |
| Multi-tenancy | `X-Bank-ID` header | Per-bank namespace isolation |

## Quick Start

> **New to It?** Check out [QUICKSTART.md](QUICKSTART.md) for a step-by-step guide!

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and configure:

```bash
export PYTHONPATH=$(pwd)
export REDIS_URL=redis://localhost:6379
export REDPANDA_BROKERS=localhost:9092
export OLLAMA_API=http://localhost:11434
export OLLAMA_HOST=http://localhost:11434
export BANK_ID=cooperative_bank_01
export WAL_PATH=./data/wal/ps01_wal.jsonl
```

### 3. Start Infrastructure Services

```bash
docker-compose -f docker-compose-infra.yml up -d
```

This starts:
- Redis (port 6379)
- Redpanda (port 9092)
- Ollama (port 11434)

### 4. Run API Server

```bash
uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

### 5. Health Check

```bash
curl -s http://localhost:8000/health
```

## Architecture

```
┌─────────────────────── BRANCH EDGE NODES ──────────────────────┐
│                                                                   │
│  Audio/Text → Tokenizer → Consent Gate → WAL → Mem0 (local)   │
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

## Core Features

### WAL-First Durability
Always write to `wal.jsonl` BEFORE calling `mem0.add()`. If the server crashes, facts can be replayed from WAL.

### PII Tokenization
All text passes through `BankingTokenizer` before storage:
```
Raw: "My PAN is ABCDE1234F"
Tokenized: "My PAN is [TOKEN:PAN:a3f2]"
```

### Conflict Detection & Adversarial Guard
- Detects contradictions between facts
- Flags suspicious changes (e.g., income change >50%)
- Requires manual review for suspicious facts

### Multi-Tenant Isolation
Every request requires `X-Bank-ID` header. Data is isolated at every layer:
- Redis keys: `{bank_id}:summary:{customer_id}`
- Mem0 user ID: `{bank_id}::{customer_id}`
- ChromaDB path: `./chroma_db/{bank_id}/`

## API Endpoints

### Session Management
- `POST /session/start` - Start a new session
- `POST /session/converse` - Send message in active session
- `POST /session/end` - End session and trigger compaction
- `POST /session/add-fact` - Add explicit fact to session

### Memory Operations
- `POST /memory/add` - Add memory directly
- `GET /health` - Health check

## End-to-End Workflow

1. **Start Session**: Officer opens session for customer
   - Consent checked
   - Redis cache checked for existing summary
   - Mem0 searched for prior facts
   - System prompt injected with context

2. **Conversation**: Audio → IndicASR → text → facts extracted
   - Regex-based tokenizer masks PII
   - ConflictDetector checks contradictions
   - AdversarialGuard flags suspicious changes
   - WAL append → mem0.add()

3. **End Session**: Compaction and shipping
   - Phi4Compactor creates summary
   - Summary cached to Redis (4h TTL)
   - WALShipper ships to Redpanda

4. **Central Hub**: Async processing
   - RedpandaConsumer receives WAL entries
   - Applies to central Mem0 instance
   - All branches see updated facts

## Testing

### Run All Tests
```bash
pytest tests/ -v
```

### Run Specific Test Suites
```bash
# Infrastructure tests
pytest tests/test_infra_phase1.py -v

# Multi-tenant tests
pytest tests/test_phase2_multitenant.py -v

# Pipeline tests
pytest tests/test_phase3_pipeline.py -v
```

## Testing

### Quick Test
```bash
bash quick_test.sh
```

### System Tests
Run comprehensive system tests (requires API server running):
```bash
bash test_system.sh
```

Tests all components end-to-end:
- API health and endpoints
- Session management flow
- Memory persistence across sessions
- WAL file integrity
- PII tokenization
- Conflict detection

### End-to-End Demo
Run the complete Rajesh 4-session journey:
```bash
bash run_demo.sh
```

This simulates the real-world scenario:
1. Session 1 (Agent A): Initial information
2. Session 2 (Agent B): Additional details (sees Agent A's notes)
3. Session 3 (Agent C): Property documents (sees full history)
4. Session 4 (Agent D): Final verification (complete context)

### Unit Tests
```bash
# Run all unit tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_phase5_session.py -v
```

### Detailed Testing Guide
See [TESTING_GUIDE.md](TESTING_GUIDE.md) for comprehensive testing instructions, troubleshooting, and manual test procedures.

## Project Structure

```
wam01/
├── src/
│   ├── api/              # FastAPI endpoints and middleware
│   │   ├── app.py        # Main FastAPI application
│   │   ├── session.py    # Session management endpoints
│   │   ├── middleware.py # Consent gate middleware
│   │   ├── tenant.py     # Multi-tenant middleware
│   │   └── models.py     # Pydantic models
│   ├── core/             # Core business logic
│   │   ├── wal.py        # Write-ahead log
│   │   ├── mem0_bridge.py # Mem0 integration
│   │   ├── conflict_detector.py
│   │   ├── adversarial_guard.py
│   │   ├── derives_worker.py
│   │   ├── phi4_compactor.py
│   │   └── wal_shipper.py
│   ├── infra/            # Infrastructure
│   │   ├── mem0_init.py  # Mem0 initialization
│   │   ├── redis_cache.py
│   │   ├── redpanda_producer.py
│   │   └── redpanda_consumer.py
│   └── preprocessing/    # PII tokenization
│       ├── tokenizer.py
│       └── banking_rules.py
├── tests/                # Test suites
│   ├── test_infra_phase1.py
│   ├── test_phase2_multitenant.py
│   └── test_phase3_pipeline.py
├── config/               # Configuration files
│   ├── branch.yaml
│   └── central.yaml
├── data/                 # Runtime data
│   ├── wal/              # Write-ahead logs
│   └── sqlite/           # SQLite databases
├── docs/                 # Detailed documentation
│   ├── ARCHITECTURE.md
│   ├── MEMORY_LAYER_GUIDE.md
│   ├── MEMORY_STORAGE_ANATOMY.md
│   ├── IMPLEMENTATION_ROADMAP.md
│   ├── PHASE3_COMPLETION.md
│   └── WORKFLOW_STEPS.md
├── chroma_db/            # ChromaDB vector store
├── mem0_history/         # Mem0 history database
├── .env.example          # Environment template
├── requirements.txt      # Python dependencies
├── requirements-dev.txt  # Development dependencies
├── setup.py              # Package setup
├── docker-compose-infra.yml # Infrastructure services
└── README.md             # This file
```

## System Layers

### Layer 1: API Layer (FastAPI)
- Session routes in `src/api/session.py`
- Dependency wiring in `src/api/dependencies.py`
- Runtime agent identity from session metadata

### Layer 2: Preprocessing Layer
- Tokenization with `BankingTokenizer` (regex-based) before persisted writes
- Prevents raw sensitive values from being written into WAL facts

### Layer 3: Durability Layer (WAL)
- WAL-first rule enforced before memory sync
- Durable WAL file under `data/wal/ps01_wal.jsonl`
- Timestamp format normalized to UTC `Z`

### Layer 4: Memory Layer (Mem0 + Chroma)
- Mem0 sync follows WAL write
- Briefing retrieval merges WAL facts with Mem0 hits
- Compacted summary can be written and reused as context

### Layer 5: Cache Layer (Redis)
- Session metadata cache (`session:{session_id}`)
- Briefing cache (`briefing:{customer_id}`)
- Summary cache (`summary:{customer_id}`)

### Layer 6: Conversation Layer (phi4-mini)
- Opening/greeting generation via briefing speech builder
- Live conversation via conversation agent
- Deterministic recall fields reduce hallucinated openings

### Layer 7: Language Consistency Layer
- Detect user language from customer message (`hindi` or `english`)
- Persist `preferred_language` as a fact
- Reuse same language for future sessions and greetings
- Prevent language drift across sessions for the same customer

## Documentation

- [Architecture Guide](docs/ARCHITECTURE.md) - Detailed system architecture
- [Memory Layer Guide](docs/MEMORY_LAYER_GUIDE.md) - Memory system deep dive
- [Memory Storage Anatomy](docs/MEMORY_STORAGE_ANATOMY.md) - Storage internals
- [Memory Storage Quick Reference](docs/MEMORY_STORAGE_QUICK_REF.md) - Quick reference
- [Implementation Roadmap](docs/IMPLEMENTATION_ROADMAP.md) - Development phases
- [Phase 3 Completion](docs/PHASE3_COMPLETION.md) - Latest milestone
- [Workflow Steps](docs/WORKFLOW_STEPS.md) - Detailed workflow documentation
- [Claude Integration](docs/CLAUDE.md) - Claude Code integration guide

## Development Status

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Documentation discovery | ✅ Complete |
| Phase 1 | Infrastructure setup | ✅ Complete |
| Phase 2 | Multi-tenant support | ✅ Complete |
| Phase 3 | Event-driven pipeline | ✅ Complete |
| Phase 4 | FastAPI endpoints | ✅ Complete |
| Phase 5 | IndicASR integration | 🔲 Pending |
| Phase 6 | End-to-end testing | 🔲 Pending |

## Troubleshooting

### API returns empty/JSON decode errors after restart
Wait 1-2 seconds and retry. Services need time to initialize.

### Memory seems stale
Verify WAL_PATH and inspect latest entries:
```bash
tail -n 20 ./data/wal/ps01_wal.jsonl
```

### Command returns exit 127
Shell command formatting issue or unknown command.

### Ollama connection fails
Ensure Ollama is running:
```bash
curl http://localhost:11434/api/tags
```

### Redis connection fails
Check if Redis is running:
```bash
docker ps | grep redis
```

### Redpanda connection fails
Check if Redpanda is running:
```bash
docker ps | grep redpanda
```

## Contributing

This is a proof-of-concept for on-premise banking memory systems. Follow these principles:

1. **WAL-first**: Always append to WAL before mem0.add()
2. **PII safety**: Never store raw PAN/Aadhaar
3. **Consent gate**: Every write requires consent
4. **Multi-tenant**: Always use bank_id in keys
5. **No cloud**: All inference local via Ollama

## License

MIT License - Open source project for educational and research purposes

## Contact

For questions about  architecture or implementation, refer to the documentation in the `docs/` folder.
