"""Domain models and catalog loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Model:
    """Represents a model entry from the catalog."""

    name: str
    model: str
    args: tuple[str, ...] = ()


def read_catalog(path: str) -> tuple[dict[str, Model], str]:
    """Read and validate the model catalog JSON file.

    Returns:
        Tuple of (catalog dict, last modified ISO timestamp).
    """
    file = Path(path)
    raw = json.loads(file.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise ValueError("catalog must contain a models array")
    models: dict[str, Model] = {}
    for item in raw["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("model"), str):
            raise ValueError("each model needs string name and model fields")
        name, model, args = item["name"].strip(), item["model"].strip(), item.get("args", [])
        if not name or not model or name in models or not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ValueError(f"invalid or duplicate model entry: {name!r}")
        if any(
            x in {"--host", "--port", "--model", "--model-path"}
            or x.startswith(("--host=", "--port=", "--model="))
            for x in args
        ):
            raise ValueError(f"model {name!r} cannot override host, port or model")
        models[name] = Model(name, model, tuple(args))
    modified_at = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    return models, modified_at
