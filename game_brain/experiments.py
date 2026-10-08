"""Write small experiment summaries. Never store a ROM, savestate, or screenshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

FORBIDDEN = {"rom", "screenshot", "frame", "savestate", "battery", "image"}


class ExperimentError(ValueError):
    pass


def _check(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN:
                raise ExperimentError(f"refusing to store {path}{key}")
            _check(item, f"{path}{key}.")
    elif isinstance(value, list):
        for item in value:
            _check(item, path)


def write_summary(root: str | Path, name: str, result: Dict[str, Any]) -> Dict[str, str]:
    if not name or "/" in name or name.startswith("."):
        raise ExperimentError("summary name must be a single file stem")
    _check(result)
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, **result}
    json_path = folder / f"{name}.json"
    md_path = folder / f"{name}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# {name}",
        "",
        f"- algorithm: {result.get('algorithm', 'unknown')}",
        f"- namespace: {result.get('namespace', 'unknown')}",
        f"- updates: {result.get('updates', 'n/a')}",
        "",
        "ROM, savestate, and screenshots are not stored in this summary.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write an experiment summary from a policy JSON")
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--root", default="experiments")
    args = parser.parse_args(argv)
    result = json.loads(Path(args.source).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ExperimentError("policy file must be a JSON object")
    paths = write_summary(args.root, args.name, result)
    print(f"wrote {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
