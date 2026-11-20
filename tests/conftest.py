"""Install the test-only validation fake, unless real mode is requested.

``INTERPRET_VALIDATION=real`` uses this checkout's ``copilot.validation.rules``.
Set ``INTERPRET_REAL_ROOT`` only to point at a different checkout. When it is
unset, or set to this repo root, the in-tree package is used.

The fake stays the default so ``python -m pytest -q`` and the real-mode run
remain two separate results.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Tests do not ship a GT CSV. Opt out explicitly; a test that needs the
# required path deletes this variable. CI sets the same value in the workflow.
os.environ.setdefault("TRACKB_FEWSHOT", "off")


@pytest.fixture(autouse=True)
def _clear_fewshot_cache():
    from copilot.interpret.fewshot import clear_fewshot_cache

    clear_fewshot_cache()
    yield
    clear_fewshot_cache()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _real_root() -> Path:
    raw = os.environ.get("INTERPRET_REAL_ROOT")
    if not raw:
        return ROOT.resolve()
    return Path(raw).resolve()


if os.environ.get("INTERPRET_VALIDATION") == "real":
    real_root = _real_root()
    if real_root != ROOT.resolve():
        sys.path.insert(0, str(real_root))
        loaded = sys.modules.get("copilot")
        real_pkg = str(real_root / "copilot")
        if loaded is not None and real_pkg not in list(getattr(loaded, "__path__", [])):
            for name in list(sys.modules):
                if name == "copilot" or name.startswith("copilot."):
                    del sys.modules[name]
    import copilot.validation.rules as _rules

    _sha = subprocess.check_output(
        ["git", "-C", str(real_root), "rev-parse", "HEAD"], text=True
    ).strip()
    print(f"VALIDATION_RULES_FILE {_rules.__file__}", flush=True)
    print(f"VALIDATION_HEAD {_sha}", flush=True)
else:
    from copilot.interpret import seam
    from tests.support.fake_contract import install

    install(seam)
