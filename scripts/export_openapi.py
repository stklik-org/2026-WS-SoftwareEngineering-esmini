"""Writes the OpenAPI document of the service to docs/openapi.json (`make openapi`).

The committed file feeds the static API reference of the documentation site; tests/contract/test_openapi.py fails
when it is stale.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "docs" / "openapi.json"


def render() -> str:
    sys.path.insert(0, str(REPO))
    from app.core.settings import Settings
    from app.main import create_app

    # Settings do not influence the schema; fixed paths keep the export independent of the environment.
    app = create_app(Settings(data_dir=Path("/data"), esmini_home=Path("/opt/esmini")))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)}")
