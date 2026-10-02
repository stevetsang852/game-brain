# game_brain/data: static Gen III battle tables (from PokeAPI)

| file | content |
|---|---|
| `type_chart.json` | `types`: the 17 Gen III types. `chart[attacking][defending]` = multiplier; only entries ≠ 1 are stored |
| `species.json` | `"<national dex id>"`: `{name, types, base: {hp, atk, def, spa, spd, spe}, fr_index}` for species 1–386 (Gen III values). `fr_index` is FireRed's internal species number, from PokeAPI's `pokemon_game_indices`. It is what `ram["battle"]` reports, and differs from the dex number for Hoenn species |
| `moves.json` | `"<move id>"`: `{name, type, power, accuracy, pp, priority, category}` for moves 1–354, as in FireRed/LeafGreen. `category` is the Gen III type-based physical/special split, or `status` |

`power`/`accuracy` are `null` where PokeAPI has none: status moves, fixed or variable damage, never-miss moves.

## Source and license

- **Source:** [PokéAPI](https://github.com/PokeAPI/pokeapi), CSV dump `data/v2/csv`, pinned to commit `bc92d3b6029ef1abe9e7ad424c400b338f3c11fe`.
- **License:** BSD-3-Clause; the full text is below.
- **Not used:** pokefirered, or any data extracted from a ROM.

## Regenerating

```bash
python -m game_brain.data.tools.gen_pokeapi_tables              # downloads the pinned CSVs
python -m game_brain.data.tools.gen_pokeapi_tables --csv-dir D  # or from local CSVs
```

The script (`tools/gen_pokeapi_tables.py`) rebuilds the Gen III values from PokeAPI's "past" tables:
- `type_efficacy_past`, `pokemon_stats_past` and `pokemon_types_past`;
- `move_changelog`, read up to the FireRed/LeafGreen version group.

Move ids in Gen III games equal PokeAPI move ids 1–354.

Two runs, one from a fresh download and one from local CSVs, gave byte-identical output.

Spot checks:
- Tackle 35 power / 95 accuracy; Vine Whip 35 / 10 PP.
- Clefairy is Normal type.
- Steel resists Ghost and Dark.
- Butterfree has 80 Sp. Atk.
- Bite is a special move (Dark type).
- FireRed index 277 = Treecko (national #252).

## PokéAPI license (BSD-3-Clause)

Copyright (c) © 2013–2023 Paul Hallett and PokéAPI contributors (https://github.com/PokeAPI/pokeapi#contributing). Pokémon and Pokémon character names are trademarks of Nintendo.

All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

* Neither the name of PokéAPI nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
