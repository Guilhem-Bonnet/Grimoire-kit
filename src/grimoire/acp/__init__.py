"""Pont ACP (Agent Client Protocol) : le kit comme client de l'agent de chaque hôte.

``machine`` valide les messages et tient l'état de la session (repli Python livré,
oracle Rust ``grimoire_acp_core`` facultatif) ; ``client`` lance l'agent en
sous-processus et fait circuler les messages.
"""

from grimoire.acp.client import AcpClient, AcpError, PermissionHandler, PermissionRequest
from grimoire.acp.machine import Machine, PyMachine, new_machine, replay, rust_backend_available

__all__ = [
    "AcpClient",
    "AcpError",
    "Machine",
    "PermissionHandler",
    "PermissionRequest",
    "PyMachine",
    "new_machine",
    "replay",
    "rust_backend_available",
]
