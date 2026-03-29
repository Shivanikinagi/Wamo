try:
    from .mem0_bridge import Mem0Bridge
except Exception:  # pragma: no cover - optional dependency during local tests
    Mem0Bridge = None

from .conflict_detector import ConflictDetector
from .wal import WALLogger
from .cbs_preseeder import CBSPreseeder
from .briefing_builder import BriefingBuilder
from .conversation_agent import ConversationAgent
from .evaluation_harness import EvaluationHarness

try:
    from .phi4_compactor import Phi4Compactor
except Exception:  # pragma: no cover - optional infra during local tests
    Phi4Compactor = None

try:
    from .adversarial_guard import AdversarialGuard
except Exception:  # pragma: no cover - optional infra during local tests
    AdversarialGuard = None

try:
    from .derives_worker import DerivesWorker
except Exception:  # pragma: no cover - optional infra during local tests
    DerivesWorker = None

try:
    from .pipeline_orchestrator import PipelineOrchestrator
except Exception:  # pragma: no cover - optional infra during local tests
    PipelineOrchestrator = None

try:
    from .voice_bot import VoiceBot
except Exception:  # pragma: no cover - optional infra during local tests
    VoiceBot = None
