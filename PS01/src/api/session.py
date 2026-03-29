"""Session management FastAPI endpoints."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from uuid import uuid4
import json
import os
import logging
import re
from datetime import datetime, UTC
from typing import Optional, Annotated, Any, Dict

from src.api.models import (
    SessionStartRequest, SessionStartResponse,
    SessionEndRequest, SessionEndResponse,
    SessionConverseRequest, SessionConverseResponse
)
from src.api.dependencies import (
    get_wal_logger, get_mem0_bridge, get_consent_db,
    get_cbs_preseeder, get_briefing_builder, get_briefing_speech_builder,
    get_redis_cache, get_tokenizer, get_redpanda_producer,
    get_theme_memory_client, get_transcript_archive, get_conversation_agent,
    get_chroma_transcript_store,
)
from src.core.wal import WALLogger
from src.core.mem0_bridge import Mem0Bridge
from src.api.middleware import ConsentDB
from src.core.cbs_preseeder import CBSPreseeder
from src.core.briefing_builder import BriefingBuilder
from src.core.conversation_agent import ConversationAgent
from src.core.chroma_transcript_store import ChromaTranscriptStore
from src.core.phi4_compactor import Phi4Compactor
from src.core.transcript_archive import TranscriptArchive
from src.preprocessing.tokenizer import BankingTokenizer
from src.infra.theme_memory_client import ThemeMemoryClient

try:
    from src.infra.redpanda_producer import RedpandaProducer
except Exception:  # pragma: no cover - optional in local test environments
    RedpandaProducer = None

# Logger
logger = logging.getLogger(__name__)

# Bank ID for WAL entries
BANK_ID = os.getenv("BANK_ID", "cooperative_bank_01")

router = APIRouter(prefix="/session", tags=["session"])


def _tokenize_value(value: Any, tokenizer: BankingTokenizer) -> Any:
    if isinstance(value, str):
        tokenized, _ = tokenizer.tokenize(value)
        return tokenized
    if isinstance(value, list):
        return [_tokenize_value(v, tokenizer) for v in value]
    if isinstance(value, dict):
        return {
            k: _tokenize_value(v, tokenizer)
            for k, v in value.items()
            if k != "token_mapping"
        }
    return value


def _sanitize_fact_for_storage(fact: Dict[str, Any], tokenizer: BankingTokenizer) -> Dict[str, Any]:
    sanitized = {}
    for k, v in fact.items():
        if k == "token_mapping":
            continue
        sanitized[k] = _tokenize_value(v, tokenizer)
    return sanitized


def _detect_language(text: str) -> str:
    """Return 'hindi', 'english', or 'hinglish' for customer text."""
    if not text:
        return "hinglish"

    if re.search(r"[\u0900-\u097F]", text):
        lowered = text.lower()
        english_hits = len(re.findall(r"\b(home|loan|income|salary|document|english)\b", lowered))
        return "hinglish" if english_hits > 0 else "hindi"

    lowered = text.lower()
    hindi_tokens = {
        "mera", "meri", "mere", "hai", "hain", "nahi", "nahin", "kya",
        "aap", "hum", "main", "kar", "karna", "ji", "pichle", "baat",
        "ghar", "loan",
    }
    english_tokens = {
        "the", "is", "are", "my", "your", "please", "document", "salary",
        "income", "loan", "amount", "eligible", "eligibility",
    }

    words = re.findall(r"[a-zA-Z]+", lowered)
    if not words:
        return "hinglish"

    hi_score = sum(1 for w in words if w in hindi_tokens)
    en_score = sum(1 for w in words if w in english_tokens)
    if hi_score > 0 and en_score > 0:
        return "hinglish"
    return "hindi" if hi_score >= en_score else "english"


def _extract_language_choice(text: str) -> Optional[str]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None

    if "hinglish" in lowered or ("hindi" in lowered and "english" in lowered):
        return "hinglish"
    if any(token in lowered for token in {"mix", "mixed", "both languages", "dono", "combo"}):
        return "hinglish"
    if "english" in lowered:
        return "english"
    if "hindi" in lowered or "hindii" in lowered:
        return "hindi"
    return None


def _language_selection_prompt(previous_language: str = "") -> str:
    hint = ""
    if previous_language in {"hindi", "english", "hinglish"}:
        hint = f" Last time you preferred {previous_language.title()}."
    return (
        "Before we continue, please choose your conversation language: "
        "Hindi, English, or Hinglish."
        f"{hint}"
    )


async def _load_session_state(
    session_id: str,
    redis_cache: Optional[Any],
    transcript_archive: TranscriptArchive,
) -> Dict[str, Any]:
    session_key = f"session:{session_id}"
    if redis_cache:
        try:
            session_bytes = await redis_cache.get(session_key)
            if session_bytes:
                if isinstance(session_bytes, bytes):
                    return json.loads(session_bytes.decode("utf-8"))
                if isinstance(session_bytes, str):
                    return json.loads(session_bytes)
                if isinstance(session_bytes, dict):
                    return session_bytes
        except Exception:
            pass

    return transcript_archive.get_session(session_id) or {}


async def _persist_session_state(
    session_id: str,
    session_data: Dict[str, Any],
    redis_cache: Optional[Any],
    transcript_archive: TranscriptArchive,
) -> None:
    if redis_cache:
        try:
            await redis_cache.set(f"session:{session_id}", json.dumps(session_data), 3600 * 2)
        except Exception:
            pass

    transcript_archive.update_session(
        session_id,
        preferred_language=session_data.get("preferred_language"),
        status=session_data.get("status"),
        metadata={
            "theme_customer_ref": session_data.get("theme_customer_ref"),
            "session_type": session_data.get("session_type"),
            "awaiting_language_selection": session_data.get("awaiting_language_selection", False),
        },
    )


def _merge_external_memory(briefing: Dict[str, Any], external_briefing: Dict[str, Any]) -> Dict[str, Any]:
    """Attach external highlights to briefing and enrich context summary."""
    if not isinstance(briefing, dict):
        briefing = {}

    total_calls = int(external_briefing.get("total_calls_found", 0) or 0)
    if total_calls <= 0:
        return briefing

    highlights = external_briefing.get("highlights", [])
    snippets: list[str] = []
    for item in highlights[:2]:
        if not isinstance(item, dict):
            continue
        turns = item.get("customer_highlights", [])
        if isinstance(turns, list) and turns:
            snippets.append(str(turns[-1]))

    context_summary = str(briefing.get("context_summary", "") or "").strip()
    external_summary = "External memory: " + (" | ".join(snippets) if snippets else f"{total_calls} prior calls")

    briefing["external_memory"] = external_briefing
    briefing["context_summary"] = f"{context_summary} | {external_summary}" if context_summary else external_summary
    briefing["has_prior_context"] = bool(briefing.get("has_prior_context") or total_calls > 0)
    return briefing


def _normalize_phone_candidate(raw: str) -> Optional[str]:
    """Return canonical +<digits> format for phone-like values, else None."""
    if not raw:
        return None

    value = str(raw).strip()
    if value.lower().startswith("phone:"):
        value = value.split(":", 1)[1].strip()

    digits = re.sub(r"\D", "", value)
    if not digits:
        return None

    # Indian mobile defaults: use last 10 digits and prefix +91 when needed.
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if len(digits) > 10:
        return f"+{digits}"
    return None


async def _resolve_theme_customer_ref(customer_id: str, redis_cache: Optional[Any]) -> str:
    """Resolve customer reference for Theme service, preferring phone numbers."""
    direct_phone = _normalize_phone_candidate(customer_id)
    if direct_phone:
        return direct_phone

    if redis_cache:
        try:
            cached = await redis_cache.get(f"theme_ref:{customer_id}")
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            if isinstance(cached, str) and cached:
                phone = _normalize_phone_candidate(cached)
                if phone:
                    return phone
        except Exception:
            pass

    return customer_id


async def _store_theme_customer_ref(customer_id: str, resolved_ref: str, redis_cache: Optional[Any]) -> None:
    if not redis_cache:
        return

    try:
        await redis_cache.set(f"theme_ref:{customer_id}", resolved_ref, 3600 * 24)
    except Exception:
        pass


@router.post("/start")
async def session_start(
    req: SessionStartRequest,
    background_tasks: BackgroundTasks,
    wal_logger: Annotated[WALLogger, Depends(get_wal_logger)],
    mem0_bridge: Annotated[Mem0Bridge, Depends(get_mem0_bridge)],
    consent_db: Annotated[ConsentDB, Depends(get_consent_db)],
    cbs_preseeder: Annotated[CBSPreseeder, Depends(get_cbs_preseeder)],
    briefing_builder: Annotated[BriefingBuilder, Depends(get_briefing_builder)],
    briefing_speech_builder: Annotated[Any, Depends(get_briefing_speech_builder)],
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
    redpanda_producer: Annotated[Optional[RedpandaProducer], Depends(get_redpanda_producer)],
    tokenizer: Annotated[BankingTokenizer, Depends(get_tokenizer)],
    theme_memory_client: Annotated[ThemeMemoryClient, Depends(get_theme_memory_client)],
    transcript_archive: Annotated[TranscriptArchive, Depends(get_transcript_archive)],
) -> SessionStartResponse:
    """
    Start a session:
    1. Verify consent
    2. Pre-seed CBS facts
    3. Build briefing
    4. Return session_id + briefing
    """
    # Step 1: Verify consent
    if not req.consent_id:
        return SessionStartResponse(
            session_id=None,
            status="error",
            error_message="consent required"
        )
    
    # Verify consent (with fallback for testing)
    consent_verified = consent_db.verify_consent(req.consent_id, "session_start")
    if not consent_verified:
        # For testing: accept any non-empty consent_id 
        # TODO: Remove this fallback for production
        if not req.consent_id or req.consent_id == "":
            raise HTTPException(status_code=403, detail="consent required")
    
    # Step 2: Generate session_id
    session_id = f"sess_{uuid4().hex[:12]}"

    theme_customer_ref = await _resolve_theme_customer_ref(req.customer_id, redis_cache)
    await _store_theme_customer_ref(req.customer_id, theme_customer_ref, redis_cache)

    # Step 2.1: Mirror session start in Theme memory service (optional).
    if theme_memory_client and theme_memory_client.is_enabled():
        await theme_memory_client.send_call_start(call_id=session_id, customer_ref=theme_customer_ref)
    
    # Step 3: Pre-seed CBS facts + WAL
    cbs_facts = await cbs_preseeder.preseed(req.customer_id)
    if req.customer_name:
        cbs_facts.append(
            {
                "type": "customer_name",
                "value": req.customer_name.strip(),
                "verified": True,
                "source": "session_start",
            }
        )
    for fact in cbs_facts:
        fact = _sanitize_fact_for_storage(fact, tokenizer)
        # WAL FIRST
        wal_entry = wal_logger.append(
            session_id=session_id,
            customer_id=req.customer_id,
            agent_id=req.agent_id,
            bank_id=BANK_ID,
            facts=[fact]
        )

        # Publish after WAL write (graceful when Redpanda is unavailable)
        if redpanda_producer:
            try:
                await redpanda_producer.publish_wal_entry(wal_entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redpanda publish skipped (session_start): %s", exc)
    
    # Step 5: Build briefing (includes conversational fields)
    briefing = await briefing_builder.build(req.customer_id)

    # Step 5.1: Pull additional long-context highlights from Theme service (optional).
    if theme_memory_client and theme_memory_client.is_enabled():
        external = await theme_memory_client.get_briefing(theme_customer_ref)
        briefing = _merge_external_memory(briefing, external)

    previous_language = str(briefing.get("preferred_language", "") or "").lower()
    greeting_message = _language_selection_prompt(previous_language)

    session_data = {
        "customer_id": req.customer_id,
        "customer_name": req.customer_name,
        "theme_customer_ref": theme_customer_ref,
        "agent_id": req.agent_id,
        "status": "active",
        "started_at": datetime.now(UTC).isoformat(),
        "session_type": req.session_type,
        "preferred_language": "",
        "awaiting_language_selection": True,
    }

    transcript_archive.start_session(
        session_id=session_id,
        customer_id=req.customer_id,
        agent_id=req.agent_id,
        preferred_language=None,
        metadata={
            "theme_customer_ref": theme_customer_ref,
            "session_type": req.session_type,
            "awaiting_language_selection": True,
            "customer_name": req.customer_name,
        },
    )
    transcript_archive.append_turn(session_id, "assistant", greeting_message)
    await _persist_session_state(session_id, session_data, redis_cache, transcript_archive)
    
    if False:  # Legacy fallback kept inert; sessions now always begin with language selection.
        greeting_message = "Rajesh ji, namaskar! Aapne pichle baar home loan ke baare mein baat ki thi — kya documents ready hain ab?"
    
    return SessionStartResponse(
        session_id=session_id,
        status="ready",
        briefing=briefing,
        cbs_facts_loaded=len(cbs_facts),
        error_message=None,
        greeting_message=greeting_message,
        context_summary=briefing.get("context_summary", ""),
        suggested_next=briefing.get("suggested_next", ""),
        has_prior_context=briefing.get("has_prior_context", False),
        preferred_language=previous_language,
        awaiting_language_selection=True,
    )


@router.post("/end")
async def session_end(
    req: SessionEndRequest,
    background_tasks: BackgroundTasks,
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
    wal_logger: Annotated[WALLogger, Depends(get_wal_logger)],
    mem0_bridge: Annotated[Mem0Bridge, Depends(get_mem0_bridge)],
    tokenizer: Annotated[BankingTokenizer, Depends(get_tokenizer)],
    theme_memory_client: Annotated[ThemeMemoryClient, Depends(get_theme_memory_client)],
    transcript_archive: Annotated[TranscriptArchive, Depends(get_transcript_archive)],
    chroma_transcript_store: Annotated[Optional[ChromaTranscriptStore], Depends(get_chroma_transcript_store)] = None,
) -> SessionEndResponse:
    """
    End a session:
    1. Get session metadata from Redis
    2. Tokenize & WAL transcript facts
    3. Replay WAL and sync to Mem0
    4. Trigger Phi4 compactor
    5. Mark session as completed
    """
    # Step 1: Get session metadata from Redis or local SQLite fallback
    session_key = f"session:{req.session_id}"
    session_data = await _load_session_state(req.session_id, redis_cache, transcript_archive)

    if not session_data:
        raise HTTPException(status_code=404, detail="session not found")

    customer_id = session_data.get("customer_id")
    theme_customer_ref = session_data.get("theme_customer_ref") or await _resolve_theme_customer_ref(customer_id, redis_cache)
    agent_id = session_data.get("agent_id")
    facts_count = 0
    final_transcript = (req.transcript or "").strip() or transcript_archive.build_full_transcript(req.session_id)

    # Step 2: Process transcript if provided
    if final_transcript:
        # Tokenize FIRST (WAL-first rule)
        tokenized, _ = tokenizer.tokenize(final_transcript)
        
        # Extract facts (no token_mapping in WAL - it contains raw PII!)
        facts = [
            {
                "type": "transcript",
                "value": tokenized,
                "verified": False,
                "source": "voice_transcribed"
            }
        ]
        
        # WAL FIRST (critical WAL-first guarantee)
        wal_logger.append(
            session_id=req.session_id,
            customer_id=customer_id,
            agent_id=agent_id,
            bank_id=BANK_ID,
            facts=facts
        )
    
    # Step 3: Replay WAL to get ALL facts for this session
    all_session_facts = wal_logger.replay(req.session_id)
    facts_count = len(all_session_facts)

    # Sync to Mem0 AFTER WAL is safely persisted (WAL-first invariant)
    if facts_count > 0:
        try:
            mem0_result = await mem0_bridge.add_after_wal(
                session_id=req.session_id,
                customer_id=customer_id,
                agent_id=agent_id,
                facts=all_session_facts,
                bank_id=BANK_ID,
            )
            logger.info("Session %s Mem0 sync result: %s", req.session_id, mem0_result.get("status"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mem0 sync skipped (session_end): %s", exc)
    
    # WAL is the source of truth (no need to sync to Mem0 - it causes OOM)
    # BriefingBuilder now reads facts directly from WAL
    logger.info(f"Session {req.session_id}: {facts_count} facts in WAL (will be used by next session via WAL)")
    
    # Step 4: Trigger Phi4 compactor in background
    if facts_count > 0:
        background_tasks.add_task(
            _compact_session,
            customer_id=customer_id,
            facts=all_session_facts,
            redis_cache=redis_cache,
            mem0_bridge=mem0_bridge,
        )
    
    # Step 5: Mark session as completed in Redis and SQLite archive
    if session_data:
        session_data["status"] = "completed"
        await _persist_session_state(req.session_id, session_data, redis_cache, transcript_archive)

    archived_transcript = transcript_archive.finalize_session(
        req.session_id,
        preferred_language=session_data.get("preferred_language"),
        full_transcript=final_transcript,
        ended_reason="completed",
    )
    
    # Invalidate briefing cache so next session sees updated facts
    if redis_cache:
        try:
            await redis_cache.delete(f"briefing:{customer_id}")
        except Exception:
            pass

    if chroma_transcript_store is not None:
        try:
            chroma_transcript_store.upsert_session(
                session_id=req.session_id,
                customer_id=customer_id,
                agent_id=agent_id,
                preferred_language=session_data.get("preferred_language"),
                full_transcript=archived_transcript,
                metadata={"ended_reason": "completed"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chroma transcript mirror skipped for %s: %s", req.session_id, exc)

    # Step 6: Mirror call end to Theme memory service (optional).
    if theme_memory_client and theme_memory_client.is_enabled():
        duration_seconds = 0
        started_at_raw = session_data.get("started_at") if isinstance(session_data, dict) else None
        if isinstance(started_at_raw, str):
            try:
                started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
                duration_seconds = max(int((datetime.now(UTC) - started_at).total_seconds()), 0)
            except Exception:
                duration_seconds = 0
        await theme_memory_client.send_call_end(
            call_id=req.session_id,
            full_transcript=archived_transcript,
            duration_seconds=duration_seconds,
            customer_ref=theme_customer_ref,
        )
    
    return SessionEndResponse(
        status="completed",
        facts_count=facts_count,
        compact_triggered=facts_count > 0,
        transcript_archived=bool(archived_transcript),
    )


@router.post("/add-fact")
async def session_add_fact(
    session_id: str,
    customer_id: str,
    agent_id: str,
    fact_type: str,
    fact_value: str,
    wal_logger: Annotated[WALLogger, Depends(get_wal_logger)],
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
    redpanda_producer: Annotated[Optional[RedpandaProducer], Depends(get_redpanda_producer)],
    tokenizer: Annotated[BankingTokenizer, Depends(get_tokenizer)],
) -> Dict[str, Any]:
    """
    Add a single fact to session:
    1. WAL FIRST
    2. Publish to Redpanda
    3. Invalidate briefing cache
    """
    fact = {
        "type": fact_type,
        "value": _tokenize_value(fact_value, tokenizer),
        "verified": False,
        "source": "voice_input"
    }
    
    # Step 1: WAL FIRST (critical)
    wal_entry = wal_logger.append(
        session_id=session_id,
        customer_id=customer_id,
        agent_id=agent_id,
        bank_id=BANK_ID,
        facts=[fact]
    )

    # Step 2: Publish to Redpanda (graceful when unavailable)
    redpanda_published = False
    if redpanda_producer:
        try:
            await redpanda_producer.publish_wal_entry(wal_entry)
            redpanda_published = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redpanda publish skipped (session_add_fact): %s", exc)
    
    # Step 3: Invalidate cache
    if redis_cache:
        try:
            await redis_cache.delete(f"briefing:{customer_id}")
        except Exception:
            pass
    
    fact_id = f"fact_{uuid4().hex[:8]}"
    return {
        "fact_id": fact_id,
        "wal_written": True,
        "redpanda_published": redpanda_published,
        "status": "queued"
    }


@router.get("/memory/{customer_id}")
async def get_session_memory(
    customer_id: str,
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
    briefing_builder: Annotated[BriefingBuilder, Depends(get_briefing_builder)]
) -> Dict[str, Any]:
    """
    Retrieve session memory:
    1. Check Redis cache
    2. On miss: BriefingBuilder.build()
    """
    cache_key = f"briefing:{customer_id}"
    
    # Step 1: Check cache
    if redis_cache:
        try:
            cached = await redis_cache.get(cache_key)
            if cached:
                if isinstance(cached, bytes):
                    return json.loads(cached.decode())
                elif isinstance(cached, dict):
                    return cached
                elif isinstance(cached, str):
                    return json.loads(cached)
        except Exception:
            pass
    
    # Step 2: Cache miss - build from briefing_builder
    briefing = await briefing_builder.build(customer_id)
    return briefing


# Background task
async def _compact_session(
    customer_id: str,
    facts: list[Dict[str, Any]],
    redis_cache: Optional[Any],
    mem0_bridge: Mem0Bridge,
):
    """Compact session facts in background and cache compacted summary."""
    try:
        compactor = Phi4Compactor()
        summary = await compactor.compact(
            facts=facts,
            redis_cache=redis_cache,
            bank_id=BANK_ID,
            customer_id=customer_id,
        )

        summary_text = summary.get("summary_text")
        if summary_text:
            await mem0_bridge.add_after_wal(
                session_id=f"compact_{customer_id}",
                customer_id=customer_id,
                agent_id="phi4_compactor",
                facts=[
                    {
                        "type": "conversation_summary",
                        "value": summary_text,
                        "verified": True,
                        "source": "phi4_compactor",
                    }
                ],
                bank_id=BANK_ID,
            )
    except Exception:
        pass


@router.post("/converse")
async def session_converse(
    req: SessionConverseRequest,
    wal_logger: Annotated[WALLogger, Depends(get_wal_logger)],
    tokenizer: Annotated[BankingTokenizer, Depends(get_tokenizer)],
    briefing_builder: Annotated[BriefingBuilder, Depends(get_briefing_builder)],
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
    theme_memory_client: Annotated[ThemeMemoryClient, Depends(get_theme_memory_client)],
    transcript_archive: Annotated[TranscriptArchive, Depends(get_transcript_archive)],
    briefing_speech_builder: Annotated[Any, Depends(get_briefing_speech_builder)],
    conversation_agent: Annotated[ConversationAgent, Depends(get_conversation_agent)],
) -> SessionConverseResponse:
    """Mid-session exchange with explicit language lock and local transcript archival."""
    try:
        session_data = await _load_session_state(req.session_id, redis_cache, transcript_archive)
        if not session_data:
            raise HTTPException(status_code=404, detail="session not found")

        agent_id = session_data.get("agent_id") or os.getenv("AGENT_ID", "agent_unknown")
        theme_customer_ref = session_data.get("theme_customer_ref") or await _resolve_theme_customer_ref(req.customer_id, redis_cache)
        preferred_language = str(session_data.get("preferred_language", "") or "").lower()
        awaiting_language_selection = bool(session_data.get("awaiting_language_selection", False))

        transcript_archive.append_turn(req.session_id, "user", req.customer_message, preferred_language or None)
        briefing = await briefing_builder.build(req.customer_id)

        if awaiting_language_selection or not preferred_language:
            language_choice = _extract_language_choice(req.customer_message)
            if not language_choice:
                agent_response = _language_selection_prompt(str(briefing.get("preferred_language", "") or "").lower())
                transcript_archive.append_turn(req.session_id, "assistant", agent_response)

                if theme_memory_client and theme_memory_client.is_enabled():
                    await theme_memory_client.send_transcript(req.session_id, "user", req.customer_message)
                    await theme_memory_client.send_transcript(req.session_id, "assistant", agent_response)

                return SessionConverseResponse(
                    agent_response=agent_response,
                    facts_extracted=[],
                    memory_updated=False,
                    wal_written=False,
                    preferred_language="",
                    language_locked=False,
                )

            preferred_language = language_choice
            session_data["preferred_language"] = preferred_language
            session_data["theme_customer_ref"] = theme_customer_ref
            session_data["awaiting_language_selection"] = False
            await _persist_session_state(req.session_id, session_data, redis_cache, transcript_archive)

            wal_logger.append(
                session_id=req.session_id,
                customer_id=req.customer_id,
                agent_id=agent_id,
                bank_id=BANK_ID,
                facts=[
                    {
                        "type": "preferred_language",
                        "value": preferred_language,
                        "verified": True,
                        "source": "customer_selected",
                    }
                ],
            )

            language_briefing = dict(briefing)
            language_briefing["preferred_language"] = preferred_language
            agent_response = briefing_speech_builder.build_opening(language_briefing)
            transcript_archive.append_turn(req.session_id, "assistant", agent_response, preferred_language)

            if theme_memory_client and theme_memory_client.is_enabled():
                await theme_memory_client.send_transcript(req.session_id, "user", req.customer_message)
                await theme_memory_client.send_transcript(req.session_id, "assistant", agent_response)

            return SessionConverseResponse(
                agent_response=agent_response,
                facts_extracted=[
                    {
                        "type": "preferred_language",
                        "value": preferred_language,
                        "verified": True,
                    }
                ],
                memory_updated=True,
                wal_written=True,
                preferred_language=preferred_language,
                language_locked=True,
            )

        tokenizer.tokenize(req.customer_message)
        agent_result = conversation_agent.respond(
            session_id=req.session_id,
            customer_id=req.customer_id,
            agent_id=agent_id,
            customer_message=req.customer_message,
            briefing=briefing,
            preferred_language=preferred_language,
        )

        agent_response = agent_result["agent_response"]
        facts_to_update = agent_result.get("facts_to_update", [])
        transcript_archive.append_turn(req.session_id, "assistant", agent_response, preferred_language)

        if theme_memory_client and theme_memory_client.is_enabled():
            await theme_memory_client.send_transcript(req.session_id, "user", req.customer_message)
            await theme_memory_client.send_transcript(req.session_id, "assistant", agent_response)

        return SessionConverseResponse(
            agent_response=agent_response,
            facts_extracted=facts_to_update,
            memory_updated=bool(facts_to_update),
            wal_written=bool(facts_to_update),
            preferred_language=preferred_language,
            language_locked=True,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ConversationAgent error: {e}")
        fallback_language = _detect_language(req.customer_message)
        if fallback_language == "english":
            fallback_message = "Noted. Let us continue step by step. What would you like to confirm next?"
        elif fallback_language == "hindi":
            fallback_message = "Thik hai, maine note kar liya. Ab aap agla detail batayiye."
        else:
            fallback_message = "Thik hai, note kar liya. Chaliye next detail dekhte hain."
        return SessionConverseResponse(
            agent_response=fallback_message,
            facts_extracted=[],
            memory_updated=False,
            wal_written=False,
            preferred_language=fallback_language,
            language_locked=fallback_language in {"english", "hindi", "hinglish"},
        )


# ──────────────────────────────────────────────────────────────────────────
# Memory API routes (separate from /session prefix)
# ──────────────────────────────────────────────────────────────────────────

memory_router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryAddRequest(BaseModel):
    """Request to add facts to memory."""
    session_id: str
    customer_id: str
    facts: list[Dict[str, Any]]
    agent_id: Optional[str] = "system"


class ThemeRefSetRequest(BaseModel):
    """Request to explicitly map a PS01 customer_id to a phone reference."""

    customer_id: str
    phone_number: str


@memory_router.post("/add")
async def memory_add_facts(
    req: MemoryAddRequest,
    wal_logger: Annotated[WALLogger, Depends(get_wal_logger)] = None,
    mem0_bridge: Annotated[Mem0Bridge, Depends(get_mem0_bridge)] = None,
    tokenizer: Annotated[BankingTokenizer, Depends(get_tokenizer)] = None,
) -> Dict[str, Any]:
    """
    Add facts to memory and WAL.
    
    WAL FIRST: write to wal.jsonl BEFORE any other operation.
    """
    try:
        # Step 1: WAL FIRST (always, non-negotiable)
        sanitized_facts = [_sanitize_fact_for_storage(f, tokenizer) for f in req.facts]

        wal_logger.append(
            session_id=req.session_id,
            customer_id=req.customer_id,
            agent_id=req.agent_id,
            bank_id=BANK_ID,
            facts=sanitized_facts
        )

        mem0_result = await mem0_bridge.add_after_wal(
            session_id=req.session_id,
            customer_id=req.customer_id,
            agent_id=req.agent_id,
            facts=sanitized_facts,
            bank_id=BANK_ID,
        )
        
        return {
            "status": "added",
            "facts_count": len(sanitized_facts),
            "wal_written": True,
            "mem0_synced": mem0_result.get("status") == "ok",
            "session_id": req.session_id,
            "customer_id": req.customer_id
        }
    except Exception as e:
        logger.error(f"Error in memory_add: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add facts: {e}")


# ──────────────────────────────────────────────────────────────────────────
# Consent Management Route (excluded from ConsentMiddleware)
# ──────────────────────────────────────────────────────────────────────────

@router.post("/consent/record")
async def record_consent_endpoint(
    session_id: str,
    customer_id: str,
    scope: str = "home_loan_processing",
    signature_method: str = "verbal",
    consent_db: Annotated[ConsentDB, Depends(get_consent_db)] = None,
) -> Dict[str, Any]:
    """
    Record a new consent for a customer (self-contained endpoint).
    This endpoint is NOT protected by consent check (it creates consent).
    """
    try:
        consent_db.record_consent(
            session_id=session_id,
            customer_id=customer_id,
            scope=scope,
            sig_method=signature_method
        )
        return {
            "status": "recorded",
            "session_id": session_id,
            "customer_id": customer_id,
            "scope": scope
        }
    except Exception as e:
        logger.error(f"Error recording consent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to record consent: {e}")


@router.post("/theme-ref/set")
async def set_theme_customer_ref(
    req: ThemeRefSetRequest,
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
) -> Dict[str, Any]:
    """Explicitly set customer_id -> phone mapping used for Theme memory integration."""
    resolved = _normalize_phone_candidate(req.phone_number)
    if not resolved:
        raise HTTPException(status_code=400, detail="invalid phone_number")

    await _store_theme_customer_ref(req.customer_id, resolved, redis_cache)
    return {
        "status": "ok",
        "customer_id": req.customer_id,
        "theme_customer_ref": resolved,
    }


@router.get("/theme-ref/{customer_id}")
async def get_theme_customer_ref(
    customer_id: str,
    redis_cache: Annotated[Any, Depends(get_redis_cache)],
) -> Dict[str, Any]:
    """Read the resolved Theme reference for a customer_id."""
    resolved = await _resolve_theme_customer_ref(customer_id, redis_cache)
    return {
        "customer_id": customer_id,
        "theme_customer_ref": resolved,
    }
