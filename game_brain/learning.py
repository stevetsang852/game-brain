"""Train a deterministic behavior-cloning policy from human experience transitions.

    python -m game_brain.learning --namespace 'gba_mgba/firered:<ROM_SHA1>' \
        --output ~/.game-brain/memory/firered-imitation.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from .brain.imitation import MODEL_VERSION, _action_key, state_features, state_key
from .schema import Action
from .memory import default_memory_dir, memory_database_path


def train_imitation(memory_dir: "str | Path", namespace: str, output: "str | Path") -> Dict[str, Any]:
    """Fit an exact-state majority policy using only transitions performed by a human."""
    database = memory_database_path(memory_dir)
    if not database.is_file():
        raise FileNotFoundError(f"experience database not found: {database}")
    uri = database.as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    examples: Dict[str, Dict[str, Any]] = {}
    try:
        rows = db.execute(
            "SELECT state_before,executed_action FROM transitions "
            "WHERE namespace=? AND actor='human' AND executed_action IS NOT NULL "
            "ORDER BY run_id,step_id", (namespace,))
        sample_count = 0
        for raw_state, raw_action in rows:
            state = json.loads(raw_state)
            action = json.loads(raw_action)
            validated = Action.from_dict(action)
            if not validated.presses:
                continue
            presses = [{"button": press.button, "frames": press.frames,
                        "release_frames": press.release_frames} for press in validated.presses]
            key = state_key(state_features(state))
            encoded_action = _action_key({"presses": presses})
            state_actions = examples.setdefault(key, {})
            example = state_actions.setdefault(encoded_action, {"presses": presses, "count": 0})
            example["count"] += 1
            sample_count += 1
    finally:
        db.close()

    if sample_count == 0:
        raise ValueError(f"no human action examples found for namespace {namespace!r}")

    model = {
        "format_version": MODEL_VERSION,
        "namespace": namespace,
        "samples": sample_count,
        "states": {
            key: sorted(actions.values(), key=lambda item: (-item["count"], _action_key(item)))
            for key, actions in sorted(examples.items())
        },
    }
    output_path = Path(output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n",
                             encoding="utf-8")
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"namespace": namespace, "samples": sample_count, "states": len(examples),
            "actions": len({key for state_actions in examples.values() for key in state_actions}),
            "model": str(output_path)}


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", default=str(default_memory_dir()))
    parser.add_argument("--namespace", required=True,
                        help="exact adapter:ROM-SHA1 namespace shown by `python -m game_brain.memory`")
    parser.add_argument("--output", required=True, help="where to write the trained JSON model")
    args = parser.parse_args(argv)
    print(json.dumps(train_imitation(args.memory_dir, args.namespace, args.output), indent=2))


if __name__ == "__main__":
    main()
