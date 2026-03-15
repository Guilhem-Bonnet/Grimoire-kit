"""Grimoire Memory — pluggable memory backends.

Usage::

    from grimoire.memory import MemoryBackend, LocalMemoryBackend, MemoryManager
"""

from grimoire.memory.backends.base import MemoryBackend
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager

__all__ = ["LocalMemoryBackend", "MemoryBackend", "MemoryManager"]
