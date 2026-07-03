"""Grimoire Kit — Composable AI agent platform.

Public SDK surface (L2):

    from grimoire import (
        GrimoireConfig, GrimoireProject,
        MissionLedger, TaskState, MissionState,
        EvidenceService, EvidenceItem, EvidenceProfile, EvidenceKind,
        PolicyEngine,
        RuntimeKernel,
        TraceLedger, TraceOutcome,
        PackManifest,
        MemoryManager,
    )
"""

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireError
from grimoire.core.project import GrimoireProject
from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.memory.manager import MemoryManager
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionState, MissionTask, TaskState
from grimoire.policies.engine import PolicyEngine
from grimoire.registry.packs import PackManifest
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TraceOutcome

__all__ = [
    # Evidence
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceProfile",
    "EvidenceService",
    # Core
    "GrimoireConfig",
    "GrimoireError",
    "GrimoireProject",
    # Memory
    "MemoryManager",
    # Mission Ledger
    "MissionLedger",
    "MissionState",
    "MissionTask",
    # Registry
    "PackManifest",
    # Policy
    "PolicyEngine",
    # Runtime
    "RuntimeKernel",
    "TaskState",
    "TraceLedger",
    # Traces
    "TraceOutcome",
    "__version__",
]
