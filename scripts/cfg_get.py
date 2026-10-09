"""Print one value from the active config (for the justfile): prefix | profile | region."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ocrbench.config import load  # noqa: E402

c = load(os.environ.get("CONFIG") or os.environ.get("OCRBENCH_CONFIG"))
print({"prefix": c.prefix, "profile": c["aws"].get("profile") or "", "region": c.region}[sys.argv[1]])
