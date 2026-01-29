"""Allow running tools as module: python -m tools.export_window"""

import sys

if len(sys.argv) < 2:
    print("Usage: python -m tools.<tool_name> [args]")
    print("Available tools:")
    print("  export_window  - Export leader activity for a time window")
    sys.exit(1)
