"""Test package: make the driver under kit/ and the repository root importable."""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(_ROOT / 'kit'), str(_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
