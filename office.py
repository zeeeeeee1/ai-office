#!/usr/bin/env python3
"""AI社員オフィスの入口。使い方は ai-office/README.md。

  python3 ai-office/office.py serve
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_office.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
