"""Generate game_brain/data/*.json (Gen III values) from PokeAPI's CSV dump.

Source: https://github.com/PokeAPI/pokeapi (``data/v2/csv``), BSD-3-Clause; see ../NOTICE.md.
Pinned to one commit so the output is reproducible:

    python -m game_brain.data.tools.gen_pokeapi_tables                 # download pinned CSVs
    python -m game_brain.data.tools.gen_pokeapi_tables --csv-dir DIR   # use local CSVs

Gen III (FireRed/LeafGreen) values are reconstructed from PokeAPI's "past" tables:

* ``*_past.csv`` rows carry the *last* generation a value applied to; for Gen III we take,
  per key, the row with the smallest ``generation_id >= 3``, else the current value.
* ``move_changelog.csv`` rows carry the values a move had *before* ``changed_in_version_group``;
  for FireRed/LeafGreen we take, per field, the earliest change whose version group comes
  after FR/LG (by ``version_groups.order``), else the current value.
* Gen III has no per-move physical/special split: a damaging move's category is its type's
  (``types.damage_class_id``); status moves stay "status".
* Only species 1-386, moves 1-354 and the 17 Gen III types (no Fairy) are kept.
* ``species.json`` also carries each species' FireRed game index (``pokemon_game_indices``,
  version ``firered``): the number the game itself uses (``ram["battle"]`` species). It equals
  the national dex number up to 251; Hoenn species are 277-411 in game order.
  Move ids in Gen III games equal PokeAPI move ids 1-354.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path
from typing import Dict, List

POKEAPI_COMMIT = "bc92d3b6029ef1abe9e7ad424c400b338f3c11fe"
CSV_URL = "https://raw.githubusercontent.com/PokeAPI/pokeapi/{commit}/data/v2/csv/{name}.csv"
NAMES = ("types", "type_efficacy", "type_efficacy_past", "pokemon", "pokemon_stats", "pokemon_stats_past",
         "pokemon_types", "pokemon_types_past", "moves", "move_changelog", "version_groups", "stats",
         "pokemon_game_indices", "versions")
GEN = 3
VERSION_GROUP = "firered-leafgreen"
MAX_SPECIES, MAX_MOVE = 386, 354
STAT_KEYS = {"hp": "hp", "attack": "atk", "defense": "def", "special-attack": "spa",
             "special-defense": "spd", "speed": "spe"}
OUT = Path(__file__).resolve().parents[1]


def load(csv_dir: "Path | None") -> Dict[str, List[dict]]:
    out = {}
    for name in NAMES:
        if csv_dir:
            text = (csv_dir / f"{name}.csv").read_text(encoding="utf-8")
        else:
            with urllib.request.urlopen(CSV_URL.format(commit=POKEAPI_COMMIT, name=name)) as r:
                text = r.read().decode("utf-8")
        out[name] = list(csv.DictReader(io.StringIO(text)))
    return out


def _past(rows, key, gen=GEN):
    """{key: [rows]} keeping, per key, the rows of the smallest generation_id >= gen."""
    best: Dict[tuple, int] = {}
    for r in rows:
        g = int(r["generation_id"])
        if g >= gen:
            k = key(r)
            best[k] = min(best.get(k, 99), g)
    return {k: [r for r in rows if key(r) == k and int(r["generation_id"]) == g] for k, g in best.items()}


def build(t: Dict[str, List[dict]]) -> Dict[str, dict]:
    types = {int(r["id"]): r for r in t["types"] if int(r["id"]) <= 17}  # 1-17: Gen III types
    tname = {i: r["identifier"] for i, r in types.items()}
    tclass = {i: ("physical" if r["damage_class_id"] == "2" else "special") for i, r in types.items()}

    # type chart: chart[attacking][defending] = multiplier (only entries != 1 are stored)
    eff = {(int(r["damage_type_id"]), int(r["target_type_id"])): int(r["damage_factor"])
           for r in t["type_efficacy"]}
    for (a, d), rows in _past(t["type_efficacy_past"],
                              lambda r: (int(r["damage_type_id"]), int(r["target_type_id"]))).items():
        eff[(a, d)] = int(rows[0]["damage_factor"])
    chart: Dict[str, Dict[str, float]] = {}
    for (a, d), f in sorted(eff.items()):
        if a in tname and d in tname and f != 100:
            chart.setdefault(tname[a], {})[tname[d]] = f / 100

    # species (default form pokemon id == species id for 1-386)
    stat_name = {int(r["id"]): r["identifier"] for r in t["stats"]}
    pkm = {int(r["id"]): r["identifier"] for r in t["pokemon"] if int(r["id"]) <= MAX_SPECIES}
    stats: Dict[int, Dict[str, int]] = {}
    for r in t["pokemon_stats"]:
        p, s = int(r["pokemon_id"]), stat_name[int(r["stat_id"])]
        if p in pkm and s in STAT_KEYS:
            stats.setdefault(p, {})[STAT_KEYS[s]] = int(r["base_stat"])
    for (p, sid), rows in _past(t["pokemon_stats_past"],
                                lambda r: (int(r["pokemon_id"]), int(r["stat_id"]))).items():
        s = stat_name[sid]
        if p in pkm and s in STAT_KEYS:
            stats[p][STAT_KEYS[s]] = int(rows[0]["base_stat"])
    ptypes: Dict[int, List[tuple]] = {}
    for r in t["pokemon_types"]:
        p = int(r["pokemon_id"])
        if p in pkm:
            ptypes.setdefault(p, []).append((int(r["slot"]), int(r["type_id"])))
    for p, rows in _past(t["pokemon_types_past"], lambda r: int(r["pokemon_id"])).items():
        if p in pkm:
            ptypes[p] = [(int(r["slot"]), int(r["type_id"])) for r in rows]
    fr_version = next(r["id"] for r in t["versions"] if r["identifier"] == "firered")
    fr_index = {int(r["pokemon_id"]): int(r["game_index"]) for r in t["pokemon_game_indices"]
                if r["version_id"] == fr_version}
    species = {str(p): {"name": pkm[p], "types": [tname[ty] for _, ty in sorted(ptypes[p])],
                        "base": {k: stats[p][k] for k in ("hp", "atk", "def", "spa", "spd", "spe")},
                        "fr_index": fr_index[p]}
               for p in sorted(pkm)}

    # moves at FireRed/LeafGreen
    vg_order = {int(r["id"]): int(r["order"]) for r in t["version_groups"]}
    frlg = next(int(r["order"]) for r in t["version_groups"] if r["identifier"] == VERSION_GROUP)
    changes: Dict[int, List[dict]] = {}
    for r in t["move_changelog"]:
        changes.setdefault(int(r["move_id"]), []).append(r)
    moves = {}
    for r in t["moves"]:
        mid = int(r["id"])
        if mid > MAX_MOVE:
            continue
        cur = {k: r[k] for k in ("type_id", "power", "pp", "accuracy", "priority")}
        later = sorted((c for c in changes.get(mid, []) if vg_order[int(c["changed_in_version_group_id"])] > frlg),
                       key=lambda c: vg_order[int(c["changed_in_version_group_id"])])
        for k in cur:
            for c in later:
                if c.get(k):
                    cur[k] = c[k]
                    break
        ty = int(cur["type_id"])
        status = r["damage_class_id"] == "1"
        moves[str(mid)] = {
            "name": r["identifier"], "type": tname.get(ty, "unknown"),
            "power": int(cur["power"]) if cur["power"] else None,
            "accuracy": int(cur["accuracy"]) if cur["accuracy"] else None,
            "pp": int(cur["pp"]) if cur["pp"] else None,
            "priority": int(cur["priority"] or 0),
            "category": "status" if status else tclass.get(ty, "physical"),
        }
    return {"type_chart.json": {"types": [tname[i] for i in sorted(tname)], "chart": chart},
            "species.json": species, "moves.json": moves}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    meta = {"source": "PokeAPI data/v2/csv", "commit": POKEAPI_COMMIT, "license": "BSD-3-Clause",
            "generation": GEN, "version_group": VERSION_GROUP}
    for name, data in build(load(a.csv_dir)).items():
        path = a.out / name
        path.write_text(json.dumps({"_meta": meta, **data} if isinstance(data, dict) else data,
                                   separators=(",", ":"), sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
