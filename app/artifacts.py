from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


def write_json(path: Path, payload: object) -> None:
    """Atomically replace a JSON artifact so interrupted writes stay readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
