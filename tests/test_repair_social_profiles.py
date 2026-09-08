from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in os.sys.path:
    os.sys.path.insert(0, str(TOOLS))

from repair_social_profiles import _profiles_for_repair


def test_profiles_for_repair_keeps_article_verified_profiles_without_registry(
    tmp_path: Path,
) -> None:
    payload = {
        "verified_social_profiles": [{
            "name": "本人",
            "service": "x",
            "url": "https://x.com/example",
            "verification_status": "verified",
            "confidence": 98,
        }],
    }

    profiles = _profiles_for_repair(tmp_path, payload)

    assert len(profiles) == 1
    assert profiles[0]["url"] == "https://x.com/example"
    assert profiles[0]["verification_status"] == "verified"
