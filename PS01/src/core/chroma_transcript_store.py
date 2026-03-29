"""Local ChromaDB mirror for finalized session transcripts."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import requests

try:
    import chromadb
except Exception:  # pragma: no cover - optional in lightweight test envs
    chromadb = None

logger = logging.getLogger(__name__)


class ChromaTranscriptStore:
    """Persist full transcripts in a local Chroma collection with Ollama embeddings."""

    def __init__(
        self,
        path: str,
        collection_name: str = "ps01_transcripts",
        ollama_api: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.ollama_api = ollama_api or os.getenv("OLLAMA_API", "http://localhost:11434")
        self.embedding_model = embedding_model or os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        self._client = None
        self._collection = None
        if chromadb is not None:
            self._client = chromadb.PersistentClient(path=str(self.path))
            self._collection = self._client.get_or_create_collection(name=self.collection_name)

    def _embed_text(self, text: str) -> list[float]:
        response = requests.post(
            f"{self.ollama_api}/api/embed",
            json={"model": self.embedding_model, "input": text},
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()

        embeddings = payload.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return embeddings[0]

        embedding = payload.get("embedding")
        if isinstance(embedding, list) and embedding:
            return embedding

        raise ValueError("Ollama embed response did not contain an embedding vector")

    def upsert_session(
        self,
        *,
        session_id: str,
        customer_id: str,
        agent_id: str,
        preferred_language: str | None,
        full_transcript: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        transcript = str(full_transcript or "").strip()
        if not transcript or self._collection is None:
            return

        chroma_metadata = {
            "session_id": session_id,
            "customer_id": customer_id,
            "agent_id": agent_id or "",
            "preferred_language": preferred_language or "",
        }
        if metadata:
            for key, value in metadata.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    chroma_metadata[key] = value

        embedding = self._embed_text(transcript)
        self._collection.upsert(
            ids=[session_id],
            documents=[transcript],
            embeddings=[embedding],
            metadatas=[chroma_metadata],
        )
        logger.info("Stored transcript in Chroma for session %s", session_id)
