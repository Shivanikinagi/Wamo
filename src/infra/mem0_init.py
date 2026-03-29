from pathlib import Path
from typing import Any
import logging
import os


logger = logging.getLogger(__name__)


class LocalMemoryFallback:
    """Tiny no-op memory adapter for local demo environments without mem0."""

    def add(self, *args, **kwargs) -> None:
        return None

    def search(self, *args, **kwargs) -> list[Any]:
        return []

    def get(self, *args, **kwargs) -> list[Any]:
        return []

    def delete_all(self, *args, **kwargs) -> None:
        return None


def _ensure_local_mem0_home() -> None:
    """Force mem0's implicit ~/.mem0 bootstrap into the project workspace."""
    project_root = Path(__file__).resolve().parents[2]
    local_home = project_root / ".mem0_home"
    local_home.mkdir(parents=True, exist_ok=True)

    # mem0 uses expanduser("~") during import, so set both variables first.
    os.environ["HOME"] = str(local_home)
    os.environ["USERPROFILE"] = str(local_home)


def init_mem0(bank_id: str = "default") -> Any:
    _ensure_local_mem0_home()
    try:
        from mem0 import Memory
    except Exception as exc:  # pragma: no cover - depends on local optional install
        logger.warning("mem0 unavailable, using local fallback memory adapter: %s", exc)
        return LocalMemoryFallback()

    vector_db_base = os.getenv("MEM0_VECTOR_DB_PATH", "./chroma_db")
    history_db_base = os.getenv("MEM0_HISTORY_DB_PATH", "./mem0_history")
    ollama_api = os.getenv("OLLAMA_API", "http://localhost:11434")

    # Mem0's Ollama providers may read OLLAMA_HOST internally, so align it
    # with the runtime API setting to avoid connection mismatches.
    if not os.getenv("OLLAMA_HOST"):
        os.environ["OLLAMA_HOST"] = ollama_api

    vector_db_path = os.path.join(vector_db_base, bank_id)
    history_db_path = os.path.join(history_db_base, bank_id, f"{bank_id}.db")

    Path(vector_db_path).mkdir(parents=True, exist_ok=True)
    Path(history_db_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        memory = Memory.from_config({
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": "phi4-mini",
                    "ollama_base_url": ollama_api,
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            },
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model": "nomic-embed-text",
                    "ollama_base_url": ollama_api
                }
            },
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"ps01_{bank_id}",
                    "path": vector_db_path
                }
            },
            "history_db_path": history_db_path
        })
        return memory
    except Exception as exc:  # pragma: no cover - depends on local runtime services
        logger.warning("mem0 init failed, using local fallback memory adapter: %s", exc)
        return LocalMemoryFallback()
