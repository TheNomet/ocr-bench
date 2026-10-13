"""Print one value from the active config (for the justfile): prefix | profile | region | selfhosted.

selfhosted: space-separated names of the self-hosted backends served by SageMaker (not direct_url).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ocrbench.config import load  # noqa: E402

c = load(os.environ.get("CONFIG") or os.environ.get("OCRBENCH_CONFIG"))
sagemaker = [k for k in c.selfhosted() if not c.backend(k).get("direct_url")]
print(
    {
        "prefix": c.prefix,
        "profile": c["aws"].get("profile") or "",
        "region": c.region,
        "selfhosted": " ".join(sagemaker),
    }[sys.argv[1]]
)
