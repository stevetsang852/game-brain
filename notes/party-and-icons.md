# Party RAM (`ram["party"]`) and the ROM's party icon tables

Everything here was checked on the supplied ROM: header title `POKEMON FIRE`, game code `BPRE`,
maker `01`, version byte (0xBC) `0` = **US 1.0 layout**. Its SHA1 (`e0194282...`) is not the clean
dump: it is a modified ROM (see "ROM quirks"). No ROM bytes, images or saves are in the repo; only
addresses and formats.

## 1. Party in RAM

| symbol (pokefirered) | address | notes |
|---|---|---|
| `gPlayerPartyCount` | `0x02024029` u8 | 0 before the starter, 1 after (step 963 of the demo run) |
| `gPlayerParty` | `0x02024284` | `struct Pokemon[6]`, **100 bytes** each (600 bytes total) |
| `gBattleMons` | `0x02023BE4` | `struct BattlePokemon[4]`, 0x58 bytes each; personality u32 at `+0x48`, hp u16 at `+0x28` |

### `struct Pokemon` (100 bytes)

| offset | field |
|---|---|
| 0x00 | personality u32 |
| 0x04 | OT id u32 |
| 0x08 | nickname, 10 bytes (game charset; the starter's reads `BULBASAUR`) |
| 0x12 | language u8; 0x13 flags: bit0 isBadEgg, bit1 hasSpecies, bit2 isEgg |
| 0x14 | OT name 7 bytes, 0x1B markings |
| 0x1C | checksum u16 |
| 0x20 | 48 bytes encrypted: 4 substructs of 12 bytes |
| 0x50 | status u32 |
| 0x54 | level u8 (0x55 mail id) |
| 0x56 | hp u16, 0x58 max hp u16, then attack/defense/speed/sp.atk/sp.def u16 |

**Decryption:** XOR every u32 of the 48 bytes with `personality ^ otid`. **Checksum:** sum of the 24
decrypted u16, `& 0xFFFF`, must equal the u16 at 0x1C, else the game shows a "Bad Egg".
**Order:** `personality % 24` indexes
`GAEM GAME GEAM GEMA GMAE GMEA AGEM AGME AEGM AEMG AMGE AMEG EGAM EGMA EAGM EAMG EMGA EMAG MGAE MGEA MAGE MAEG MEGA MEAG`
(position of each letter = which 12-byte slot holds that substruct).

| substruct | layout used |
|---|---|
| G growth | species u16 @0, held item u16 @2, exp u32 @4, **ppBonuses u8 @8** (2 bits per move slot), friendship @9 |
| A attacks | moves u16[4] @0, pp u8[4] @8 |
| E EVs/condition | not used |
| M misc | IVs/egg/ability u32 @4: bit30 = isEgg |

Status u32: bits 0-2 sleep turns (non-zero = asleep), bit3 poison, bit4 burn, bit5 freeze, bit6
paralysis, bit7 toxic (bad poison). `gBattleMons[].status1` uses the same bits plus a toxic counter
in bits 8-11.

### Real-ROM evidence (starter run, `--brains battle,path,rule`, 4000 steps from boot)

Raw dump of slot 0 after "received the BULBASAUR": personality `0x71D17281` (`% 24 = 9`, order AEMG),
OT id `0x1DE3791E`, stored checksum `0x15D7` = computed `0x15D7`; species 1, exp 237, moves
`(118, 0, 0, 0)` PP `(40, 0, 0, 0)`, status 0, Lv5, 22/22, nickname `BULBASAUR`. This matches the
rival-battle screen (BULBASAUR Lv5 22/22, only METRONOME, PP 40/40). Later saves: Lv6 25/25
(exp 510, checksum `0x1FE8` ok) after the rival battle; Lv7 19/27 PP 33 at Viridian City.

In the same run (replayed step by step, 4000 steps, 0 replay mismatches):

* `len(party) == party_count` on every overworld step.
* 2038 battle steps, 1677 with `ram["battle"]["player"]` trusted. On all 1677 the party mon is
  `active`, and its **party-struct hp and PP equal `gBattleMons[0]`**: the game writes hp back to the
  party after every hit (e.g. 22 → 20 → 14 → 13 → 11 in the rival battle) and PP after every move use
  (40 → 32). So contrary to the common assumption, the party copy is not only synced after the battle.
* The 8 trusted steps where species/level/max_hp/hp differed are level-ups (steps 2084-2085 and
  3079-3084): the party struct already shows the new level/max_hp/hp (e.g. Lv6 14/25) while
  gBattleMons still shows Lv5 11/22.
* No status condition happened in the run, so status write-back during battle is **not verified**
  (decoding is covered by the unit tests).

### What `ram["party"]` means

Read only on overworld and in-battle observations (not during transitions/menus outside those).
Shape and rules: docstring of `game_brain/adapters/gba_mgba/firered_party.py`. In short: one dict per
slot, `hp`/`pp`/`status`/`level` are always the party struct's values (live, see above; this keeps
hp/max_hp consistent at level-ups); `active` is true for the slot whose personality equals
`gBattleMons[0]`'s, only once the adapter trusts `ram["battle"]["player"]` (gBattleMons is stale for the
first observations of a battle). Bad-checksum slots are `{"slot", "bad_egg": True}`.

Cost: one 600-byte copy of EWRAM through the bindings' cffi buffer (~0.1 µs; byte-wise bus reads
would be ~300 µs) plus decoding, ~5 µs per observe with one mon. `observe()` as a whole is ~300 µs.
Run log: `party` is deduped like `npcs` (`party_same` / `party_delta`); the 4000-step demo log grew
from 4,286,189 to 4,354,157 bytes (+1.6 %).

## 2. ROM tables (names, PP)

Read once from the ROM file when the adapter opens it (`RomTables.from_rom`), checked against
BULBASAUR/CHARMANDER/SQUIRTLE, POUND/TACKLE and PP 35/35; if the check fails names/max_pp are None.

| symbol | address | format |
|---|---|---|
| `gSpeciesNames` | `0x08245EE0` | `u8[412][11]`, game charset, `0xFF` terminated; [0] = `??????????` |
| `gMoveNames` | `0x08247094` | `u8[355][13]` (directly after gSpeciesNames) |
| `gBattleMoves` | `0x08250C04` | 12 bytes per move: effect, power, type, accuracy, **pp @+4**, secondary chance, target, priority, flags |

Charset (English): `A-Z` = 0xBB-0xD4, `a-z` = 0xD5-0xEE, `0-9` = 0xA1-0xAA, space 0x00, `-` 0xAE,
`?` 0xAC, `.` 0xAD, `'` 0xB4, `♂` 0xB5, `♀` 0xB6, end 0xFF.

`max_pp = base + base * ups // 5` with `ups = (ppBonuses >> (2 * slot)) & 3` (CalculatePPWithBonus).

## 3. ROM quirks (this modified ROM)

* Starters know only METRONOME, and METRONOME has **40 PP** in the ROM's gBattleMoves (vanilla 10).
* Compared with the PokeAPI table in `game_brain/data/moves.json`, 15 moves differ in the ROM: CUT,
  FLY, SURF, STRENGTH, WATERFALL, ROCK SMASH, DIVE have power 0 and PP 40; METRONOME 40 PP;
  SOFTBOILED/REST/MILK DRINK/SLACK OFF 10 PP; FLASH and MIND READER 5 PP; LUSTER PURGE power 70.
  That is why `max_pp`/names come from the ROM, not from `game_brain.data`.
* The starter's exp (237 at Lv5, 510 at Lv6, 728 at Lv7) does not fit BULBASAUR's vanilla
  Medium Slow curve (135 / 179 / 236), so growth rates or exp are probably modified too (not
  investigated further).

## 4. Species ids

`species_id` is FireRed's **internal** species number (as in `ram["battle"]`), not the national dex:

* 1-251: same as the national dex.
* 252-276: 25 unused placeholder slots, named `?` in gSpeciesNames; their icon pointer is the
  same `?` icon as species 0.
* 277-411: Hoenn species in internal order (277 = TREECKO = dex 252, 410 = DEOXYS, 411 = CHIMECHO).
  Use `game_brain.data.species_by_game_index()` for the dex mapping.
* 412 = egg (icon table only; gSpeciesNames has 412 entries 0-411). An egg in the party keeps its
  real species in the growth substruct; `egg: True` comes from the misc substruct bit 30. Show the
  egg icon (412) for it.
* Unown: species 201 is UNOWN A; the icon table has 27 extra entries 413-439 for Unown B…Z, `!`, `?`.
  The letter comes from the personality (standard Gen III formula on bits of the personality), not
  from the species id; `ram["party"]` does not compute it.

## 5. Party icons

| symbol | address | format |
|---|---|---|
| `gMonIconTable` | `0x083D37A0` | `const u8 *[440]`: one pointer per icon id (0-411 species, 412 egg, 413-439 Unown B…?) |
| `gMonIconPaletteIndices` | `0x083D3E80` | `u8[440]`, values 0-2 (counts: 0 ×215, 1 ×94, 2 ×131) |
| `gMonIconPaletteTable` | `0x083D4038` | `struct SpritePalette[6]` = `{const u16 *data; u16 tag; u16 pad}` (8 bytes); tags 0xDAC0-0xDAC5 |
| icon palettes | `0x083D3740`, `0x083D3760`, `0x083D3780` | 3 palettes × 16 colours × u16 BGR555 = 32 bytes each, uncompressed |

Only palettes 0-2 are real: entries 3-5 of gMonIconPaletteTable point to `0x083D37A0`, `…37C0`,
`…37E0`, i.e. into gMonIconTable itself (unused in FireRed). Use `gMonIconPaletteTable[idx].data`
with `idx = gMonIconPaletteIndices[icon_id]`, or directly `0x083D3740 + 32 * idx`.

**Tile data:** each pointer points to **0x400 bytes, uncompressed 4bpp**, = 2 frames of 32×32.
**Frame 0 is bytes 0x000-0x1FF and frame 1 is bytes 0x200-0x3FF (contiguous)**, so a 32×64 image with
frame 0 on top and frame 1 below is just the 32 tiles drawn in order. The icons are not stored
back-to-back in species order in this ROM (pointer gaps vary, ~0xA00-0xBD0), so always follow the
pointer. Bulbasaur: icon at file offset 0xD3018C, palette 1.

Decode recipe (32×64 RGBA, frames stacked):

```python
import struct
def mon_icon_rgba(rom: bytes, icon_id: int):
    ptr = struct.unpack_from("<I", rom, 0x3D37A0 + 4 * icon_id)[0] - 0x08000000
    pal_idx = rom[0x3D3E80 + icon_id]
    pal_off = struct.unpack_from("<I", rom, 0x3D4038 + 8 * pal_idx)[0] - 0x08000000
    pal = []
    for c in struct.unpack_from("<16H", rom, pal_off):            # BGR555: R bits 0-4, G 5-9, B 10-14
        r, g, b = c & 31, (c >> 5) & 31, (c >> 10) & 31
        pal.append((r * 255 // 31, g * 255 // 31, b * 255 // 31, 255))
    pal[0] = (0, 0, 0, 0)                                        # colour 0 = transparent
    px = [[None] * 32 for _ in range(64)]
    tiles = rom[ptr:ptr + 0x400]
    for t in range(32):                       # tile t: frame t // 16; inside a frame 4×4 tiles, row-major
        frame, i = divmod(t, 16)
        ty, tx = divmod(i, 4)
        for y in range(8):                    # 32 bytes per 8×8 tile, 4 bytes per row
            for x in range(8):
                byte = tiles[32 * t + 4 * y + x // 2]
                v = byte & 0xF if x % 2 == 0 else byte >> 4   # low nibble = left pixel
                px[frame * 32 + ty * 8 + y][tx * 8 + x] = pal[v]
    return px                                 # 64 rows × 32 RGBA tuples
```

(`r * 255 // 31` is one common 5→8-bit expansion; `(r << 3) | (r >> 2)` is the other.)

**Verification:** decoded with this recipe to PNGs under /tmp (not in the repo) and viewed: icon ids 1
BULBASAUR (green, palette 1), 4 CHARMANDER, 7 SQUIRTLE, 25 PIKACHU (palette 2), 277 TREECKO, 0/252 the
`?` block, 201 UNOWN A, 410 DEOXYS, 411 CHIMECHO, 412 the egg, 413 UNOWN B, 439 UNOWN `?`. All are
correct with transparent backgrounds; frame 1 is the second animation frame of the same pose
(slightly shifted). The recipe above was also run and compared pixel by pixel with that decoder
(ids 1, 25, 412, 439: identical).
