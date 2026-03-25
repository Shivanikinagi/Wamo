# PS-01: The Loan Officer Who Never Forgets

A memory-augmented AI system for loan officers that maintains persistent context across conversations using Mem0 for semantic memory, spaCy for NER, and local LLMs via Ollama.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Environment

```bash
cp .env.example .env
# Edit .env with your configuration
```

### 3. Run Entry Point

```bash
python src/main.py
```

## Architecture

See [system_architecture.md](../system_architecture.md) for detailed system design and component interactions.

## Development

See [IMPLEMENTATION_ROADMAP.md](../IMPLEMENTATION_ROADMAP.md) for the complete implementation roadmap.

## Directory Structure

```
PS01/
├── src/              # Application source code
├── tests/            # Test suite
├── docker/           # Docker configurations
├── docs/             # Documentation
└── config/           # Configuration files
```
