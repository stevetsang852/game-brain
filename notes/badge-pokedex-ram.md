# Badge and Pokédex RAM (verified FireRed ROM only)

ROM: header `POKEMON FIRE` / `BPRE`, SHA1 `e0194282c427689768f8e618a285552f264524a4`
(modified US FireRed; not clean 1.0 `41cb23d8…`). Starters in this image only know METRONOME.
Every address below was read back through `MgbaFireRedAdapter.load_state` on local milestone
sidecars under `~/.game-brain/saves` (not committed). The same sidecars' `rom_sha1` is the hash
above. pokefirered struct offsets were treated as hypotheses until a before/after on this ROM
agreed. No address is shipped in `firered.py` unless it is marked VERIFIED here.

`gSaveBlock1Ptr` (`0x03005008`) was already verified (player coords). On these saves the
pointer moves (the block is not at a fixed EWRAM address) but `player_x/y` and `map_bank/map_id`
still match the sidecar. Field addresses below are **pointer + offset**, not a fixed EWRAM address.

Saves used (run `20261002-081843` unless noted). No sidecar in `~/.game-brain/saves` lists a gym
milestone. The milestone vocabulary on these runs stops at `deliver_parcel` (next goal id
`pewter_city`). None of them is a badge. They are pre-gym controls.

| save | step | map (bank, id, x, y) | milestones include | party |
|---|---|---|---|---|
| `0000558_milestone-intro` | 558 | 4, 1, 6, 6 (bedroom) | `intro` only | 0 |
| `0000700_milestone-oak_lab` | 700 | 4, 3, 6, 12 (lab door) | up to `oak_lab` | 0 |
| `0000964_milestone-get_starter` | 964 | 4, 3, 8, 5 | up to `get_starter` (rival battle not done) | BULBASAUR `species_id` 1 |
| `0002170_milestone-rival_battle_over` | 2170 | 4, 3, 7, 8 | `rival_battle_over` done, not `deliver_parcel` | BULBASAUR 1 |
| `0002315_milestone-route_1` | 2315 | 3, 19, 13, 39 | `route_1` done | BULBASAUR 1 |
| `0003730_milestone-back_to_pallet` | 3730 | 3, 0, 12, 0 | `back_to_pallet` done, not `deliver_parcel` | BULBASAUR 1 |
| `0003945_milestone-deliver_parcel` | 3945 | 4, 3, 6, 4 | `deliver_parcel` just done | BULBASAUR 1 |
| `0004000_periodic` | 4000 | 4, 3, 6, 4 | `deliver_parcel` done | BULBASAUR 1 |

The same owned/seen lists and the same badge/system flag bytes were read on every other unique
overworld state in `~/.game-brain/saves` (28 distinct `state_sha1`, three run ids). Owned was
either `[]` (before the starter) or `[1]`. The badge byte was `0x00` on all 28.

## `gSaveBlock2Ptr` — VERIFIED `0x0300500C`

Not previously read by `firered.py`. `notes/adapter-interface.md` only recorded it as an
unchecked probe hint. Checked the same way as SaveBlock1: the pret symbol address, then the
bytes it points at.

On every overworld save above the pointer is in EWRAM (`0x020245DC` … `0x02024604`). It stays
`0xFA4` bytes below `gSaveBlock1Ptr` on each of those saves (both blocks move together). At
power-on frame 0 both pointers are 0, so nothing is read.

Supporting read, not itself a dex flag: the 8 bytes at `SaveBlock2+0` are
`BB BB BB BB BB BB BB FF` on every one of those saves. Gen 3 charset `0xBB` is `A` and `0xFF`
ends the string, so the name is `AAAAAAA` (the A-mash intro). The same bytes on every save
mean the pointer is not a random EWRAM word.

## Pokédex owned / seen — VERIFIED

Hypothesis (pokefirered `struct SaveBlock2` / `struct Pokedex` / `DexScreen_GetSetPokedexFlag`):
`pokedex` at `SaveBlock2+0x18`, `owned[]` at `+0x10` inside it (`SaveBlock2+0x28`), `seen[]` at
`+0x44` (`SaveBlock2+0x5C`). The game does `nationalDexNo--` then uses bit `n % 8` of byte
`n / 8`. National dex 1 is therefore bit 0 of byte 0. For Kanto, national number == FireRed
internal species id (`SPECIES_BULBASAUR` 1 … `SPECIES_MEW` 151). `FLAG_SET_SEEN` also sets
`SaveBlock1.seen1` (`+0x5F8`) and `seen2` (`+0x3A18`).

| save | step | owned raw (first 4) | seen raw (first 4) | owned ids | seen ids |
|---|---|---|---|---|---|
| intro | 558 | `00 00 00 00` | `00 00 00 00` | `[]` | `[]` |
| oak_lab | 700 | `00 00 00 00` | `00 00 00 00` | `[]` | `[]` |
| get_starter | 964 | `01 00 00 00` | `01 00 00 00` | `[1]` | `[1]` |
| rival_battle_over | 2170 | `01 00 00 00` | `09 00 00 00` | `[1]` | `[1, 4]` |
| route_1 | 2315 | `01 00 00 00` | `09 00 00 00` | `[1]` | `[1, 4]` |
| back_to_pallet | 3730 | `01 00 00 00` | `09 80 04 00` | `[1]` | `[1, 4, 16, 19]` |
| deliver_parcel | 3945 | `01 00 00 00` | `09 80 04 00` | `[1]` | `[1, 4, 16, 19]` |
| periodic | 4000 | `01 00 00 00` | `09 80 04 00` | `[1]` | `[1, 4, 16, 19]` |

`seen1` and `seen2` matched `seen` on each of those eight saves (same raw bytes).

What the transitions prove:

* Step 700 → 964: the only new owned bit and the only new seen bit are bit 0 (species 1).
  `ram["party"][0].species_id` is 1, name `BULBASAUR`. Before the starter both arrays are clear.
  Caught-before-Pokédex is what this ROM actually does: the owned bit is set at the catch, not
  when Oak hands over the Pokédex.
* Step 964 → 2170: seen gains bit 3 (species 4) and owned does not. Step 964 is before
  `rival_battle`; step 2170 has `rival_battle_over` done. The rival in this ROM is CHARMANDER
  (internal id 4; `notes/mgba-bridge.md` battle dump). That is a second species on the same
  bit formula.
* By step 3730 (after Route 1, still before `deliver_parcel`) seen also has bit 15 (species 16)
  and bit 18 (species 19). Owned is still only species 1. Species 16 and 19 are PIDGEY and
  RATTATA, the two wilds already recorded on this route (`notes/mgba-bridge.md`). The ids come
  from the bit formula verified by species 1 and 4, not from a new guess.
* No owned or seen bit above national 151 was set on any of the 28 overworld states.

`ram["pokedex_owned"]` and `ram["pokedex_seen"]` are those Kanto id lists. They are present
whenever `gSaveBlock2Ptr` is in EWRAM, including battle (`scene: other`), so the progress score
does not drop the list for one step and award it again afterwards. At power-on the pointer is 0
and the keys are absent. `rl/progress.py` weights were not changed. The score already prefers
`pokedex_seen` when that list is non-empty.

Not a "has Pokédex" flag: `SaveBlock2+0x1A` is `0xDA` on the intro save and on the post-dex save
(`dex` header `00 00 DA 00 …`). pret marks that byte unused and initialised to `0xDA`. It does
not flip when the Pokédex is received. It was not used.

## `FLAG_SYS_POKEMON_GET` / `FLAG_SYS_POKEDEX_GET` — VERIFIED

Hypothesis: `SaveBlock1.flags` at `+0x0EE0`, flag id `n` in bit `n % 8` of byte `n / 8`.
`FLAG_SYS_POKEMON_GET` is `0x828` (byte `0x105`, bit 0). `FLAG_SYS_POKEDEX_GET` is `0x829`
(same byte, bit 1). Absolute field: `SaveBlock1+0x0FE5`.

| save | step | byte `flags+0x105` |
|---|---|---|
| oak_lab (no Pokémon) | 700 | `0x00` |
| get_starter | 964 | `0x01` |
| deliver_parcel (objects already gone) | 3945 | `0x01` |
| still in the lab | 4000 | `0x03` |

* `0x00` → `0x01` between step 700 and 964, the same window as `party_count` 0 → 1. Bit 0 is
  the "got a Pokémon" flag. VERIFIED.
* The only flag byte that changes between step 3945 and 4000 is byte 261 (`0x105`): `0x01` →
  `0x03`. Both saves are map 4/3 at (6, 4), and both already list `deliver_parcel`. Bit 1
  flips while Oak's post-delivery script is still running. The milestone itself fires earlier,
  when the two Pokédex objects leave `npcs` (`notes/nav.md`); that is not this flag.
  `FLAG_SYS_POKEDEX_GET` is VERIFIED. It is not a separate `ram` key. `progress.py` does not
  read it.

A third anchor for the same flag array, not exposed: flag `0x03A` (byte `0x07` bit 2), pret
`FLAG_HIDE_POKEDEX`, is clear at step 3730 and set at step 3945, when the table objects
disappear. That only confirms the flag-array base. It is not "player owns the Pokédex".

## Kanto badge bits — UNVERIFIED (not in `ram`)

Hypothesis: `FLAG_BADGE01_GET` … `FLAG_BADGE08_GET` are `0x820`–`0x827`, the eight bits of the
byte immediately before the verified system-flag byte: `SaveBlock1.flags + 0x104`
(`SaveBlock1+0x0FE4`).

That byte was `0x00` on all 28 unique overworld states checked, and on the in-battle sample
loaded while confirming `gSaveBlock2Ptr` stays valid in battle. No save has a story in which a
badge could already have been earned (no gym milestone; the verified route ends at Pewter's
door, not inside the gym).

The flag-array offset is VERIFIED (the Pokémon and Pokédex flags above). The eight bits pret
names as badges therefore sit in that zero byte. None of them has been observed to go from 0
to 1, so which bit is which badge, and that the game uses them as badges on this modified ROM,
is **UNVERIFIED**. `ram["badges"]` is intentionally absent. A count of zero would be a guess
about those bits, not a measurement.
