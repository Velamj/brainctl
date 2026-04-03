"""
brainctl CLI entry point — parser definition and dispatch table.

All command implementations are in agentmemory.commands.* modules,
which import from agentmemory._impl (the full implementation).
"""

import sys
import os
from pathlib import Path

# Import everything from _impl — the build_parser and main live there
from agentmemory._impl import build_parser, main

# Re-export for bin/brainctl
__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    main()
