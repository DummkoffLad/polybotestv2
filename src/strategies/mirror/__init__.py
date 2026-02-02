"""Mirror trading strategy.

Copies leader trades in real-time with position sizing and risk controls.
"""

from .strategy import MirrorStrategy

__all__ = ["MirrorStrategy"]
