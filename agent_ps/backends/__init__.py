"""The registry.

Adding an agent means writing one class and adding it to this list. Order sets
the order rows appear in when nothing else separates them.
"""

from .antigravity import AntigravityBackend
from .claude import ClaudeBackend
from .codex import CodexBackend
from .copilot import CopilotBackend
from .hermes import HermesBackend
from .opencode import OpenCodeBackend
from .pilike import CommandCodeBackend, PiBackend

ALL = (ClaudeBackend, PiBackend, CommandCodeBackend, CodexBackend,
       OpenCodeBackend, HermesBackend, CopilotBackend, AntigravityBackend)


def available(only=None):
    """Backends whose data directory exists, optionally narrowed by name."""
    chosen = []
    for cls in ALL:
        backend = cls()
        if only and backend.name not in only:
            continue
        if backend.available():
            chosen.append(backend)
    return chosen


def names():
    return [cls.name for cls in ALL]
