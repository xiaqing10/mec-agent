#!/usr/bin/env python3
"""
LangChain Tool definitions for MEC diagnostic agent.

Compatibility wrapper — all tools are now in the tools/ package.
"""

import sys
from pathlib import Path

SELF_AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(SELF_AGENT_DIR))

from tools import TOOLS
from tools._shared import set_diag_progress_callback

__all__ = ["TOOLS", "set_diag_progress_callback"]