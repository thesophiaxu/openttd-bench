"""Put the test fixtures (model-free harnesses) and the harness source root on the path, so
the harness packages load by id without an install (pyproject lists each of them)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT / "tests" / "fixtures", ROOT / "harnesses"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
