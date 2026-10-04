"""Goal planner: an ordered milestone list; the first milestone not yet done is the current goal.

Milestones are sticky: once ``done`` is observed true it stays done. A milestone gives a
``Target`` for PathBrain:

* ``Target.warp(dest_bank, dest_map)`` -- reach any usable warp leading to that map
* ``Target.at(x, y)``                  -- reach a tile on the current map
* ``Target.interact(x, y, face, btn)`` -- stand on (x, y), face ``face``, press ``btn`` (repeat
  until the milestone is done: talking to someone / picking up a ball)
* ``Target.edge(direction)``          -- walk off that edge of the current map (a map connection,
  e.g. Pallet Town's north edge -> Route 1): reach a walkable tile on the edge, face out, press
* ``Target.script()``                  -- a scripted event is running (player moved by the game):
  don't navigate, advance text with the milestone's ``script_button``
* ``None``                             -- nothing to navigate (e.g. the intro: RuleBrain's job)

``script_button`` is the button PathBrain uses to advance text boxes while the player is frozen
during this milestone ("A" normally; "B" after receiving the starter, so the nickname prompt is
answered NO instead of opening the naming screen).

``placeholder=True`` milestones are listed (so the dashboard can show the road ahead) but are
not implemented yet; PathBrain reports itself unavailable when one becomes current.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..schema import Observation


@dataclass(frozen=True)
class Target:
    kind: str                     # "warp" | "tile" | "interact" | "script" | "edge"
    dest: Tuple[int, int] = (0, 0)  # warp: destination (bank, map)
    tile: Tuple[int, int] = (0, 0)  # tile / interact: (x, y) on the current map
    face: str = ""                  # interact: direction to face on the tile
    button: str = "A"               # interact: button to press once facing

    @classmethod
    def warp(cls, bank: int, map_id: int) -> "Target":
        return cls("warp", dest=(bank, map_id))

    @classmethod
    def at(cls, x: int, y: int) -> "Target":
        return cls("tile", tile=(x, y))

    @classmethod
    def interact(cls, x: int, y: int, face: str, button: str = "A") -> "Target":
        return cls("interact", tile=(x, y), face=face, button=button)

    @classmethod
    def edge(cls, direction: str) -> "Target":
        if direction not in ("UP", "DOWN", "LEFT", "RIGHT"):
            raise ValueError(f"bad edge direction {direction!r}")
        return cls("edge", face=direction)

    @classmethod
    def script(cls) -> "Target":
        return cls("script")

    def describe(self) -> str:
        if self.kind == "warp":
            return f"warp to map {self.dest[0]}/{self.dest[1]}"
        if self.kind == "interact":
            return f"stand on {self.tile}, face {self.face}, press {self.button}"
        if self.kind == "script":
            return "scripted event (advance text, wait)"
        if self.kind == "edge":
            return f"walk off the map edge ({self.face})"
        return f"tile {self.tile}"


@dataclass
class Milestone:
    id: str
    label: str
    done: Callable[[Observation], bool] = lambda obs: False
    target: Callable[[Observation], Optional[Target]] = lambda obs: None
    placeholder: bool = False
    script_button: str = "A"


def _map(obs: Observation) -> Optional[Tuple[int, int]]:
    if "map_bank" in obs.ram and "map_id" in obs.ram and obs.position is not None:
        return (obs.ram["map_bank"], obs.ram["map_id"])
    return None


# FireRed (BPRE) map ids. 4/1 = player's house 2F (verified, notes/mgba-bridge.md); 4/0 = house 1F,
# 3/0 = Pallet Town and 4/3 = Prof. Oak's lab are warp destinations read from this ROM's map
# events (PR #7) and confirmed by walking through them (notes/nav.md).
FR_HOUSE_2F = (4, 1)
FR_HOUSE_1F = (4, 0)
FR_PALLET_TOWN = (3, 0)
FR_OAKS_LAB = (4, 3)
# Milestone 3: reached by walking off a map edge (a map connection, not a warp) and read back from
# map_bank/map_id on arrival (notes/nav.md, "M3 route").
FR_ROUTE_1 = (3, 19)
FR_VIRIDIAN_CITY = (3, 1)
# Oak's Parcel: the Viridian City Poke Mart is the warp at (36,19) of 3/1; measured on the ROM
# (entering it starts the clerk's parcel script, notes/nav.md "Oak's Parcel").
FR_VIRIDIAN_MART = (5, 3)
#: Oak's lab objects (``npcs`` local_id), measured on the ROM: Oak at (6,3); the two Pokedexes on
#: the table at (4,1)/(5,1) until Oak hands them out after the parcel
FR_OAK_LOCAL_ID = 4
FR_POKEDEX_LOCAL_IDS = (9, 10)
FR_OAK_DESK_STAND = (6, 4)

# Milestone 2 tiles, all measured on this ROM (notes/nav.md, "M2 route"):
#: stepping onto either tile of Pallet Town's north exit starts Oak's "wait, don't go out" script
FR_OAK_TRIGGER = ((12, 1), (13, 1))
#: the three starter balls on the lab table; we pick Bulbasaur (left ball) -- see notes/nav.md
FR_STARTER_BALLS = {"BULBASAUR": (8, 4), "SQUIRTLE": (9, 4), "CHARMANDER": (10, 4)}
FR_STARTER = "BULBASAUR"


def _party(obs: Observation) -> int:
    v = obs.ram.get("party_count")
    return v if isinstance(v, int) else 0



def _left_viridian_north():
    """Done only after standing in Viridian, then arriving on another overworld map."""
    seen = {"viridian": False}

    def done(o: Observation) -> bool:
        if o.ram.get("in_battle"):
            return False
        here = _map(o)
        if here == FR_VIRIDIAN_CITY:
            seen["viridian"] = True
            return False
        if not seen["viridian"] or here is None or here[0] != 3:
            return False
        return here not in (FR_VIRIDIAN_CITY, FR_ROUTE_1, FR_PALLET_TOWN)
    return done


def firered_milestones(starter: str = FR_STARTER) -> List[Milestone]:
    starter = starter.upper()
    m = _map
    bx, by = FR_STARTER_BALLS[starter]

    def at_trigger(o: Observation) -> bool:
        return m(o) == FR_PALLET_TOWN and tuple(o.position or ()) in FR_OAK_TRIGGER

    def to_lab(o: Observation) -> Optional[Target]:
        return Target.script() if m(o) == FR_PALLET_TOWN else None

    def to_ball(o: Observation) -> Optional[Target]:
        if m(o) == FR_OAKS_LAB:
            return Target.interact(bx, by + 1, "UP", "A")
        if m(o) == FR_PALLET_TOWN:
            return Target.warp(*FR_OAKS_LAB)  # e.g. walked out before choosing
        return None

    def left_lab(o: Observation) -> bool:
        # The game does not let you leave the lab with the starter before the rival battle, so
        # the starter outside the lab (also at home: a whiteout wakes you up in the house 1F,
        # verified on the ROM, notes/nav.md "Whiteout") means the rival battle is over.
        return _party(o) >= 1 and m(o) in (FR_PALLET_TOWN, FR_ROUTE_1, FR_VIRIDIAN_CITY, FR_VIRIDIAN_MART,
                                       FR_HOUSE_1F, FR_HOUSE_2F)

    def to_pallet(o: Observation) -> Optional[Target]:
        """From the lab or the player's house (e.g. after a whiteout) back out to Pallet Town."""
        here = m(o)
        if here == FR_HOUSE_2F:
            return Target.warp(*FR_HOUSE_1F)
        if here in (FR_HOUSE_1F, FR_OAKS_LAB):
            return Target.warp(*FR_PALLET_TOWN)
        return None

    def got_pokedex(o: Observation) -> bool:
        if m(o) != FR_OAKS_LAB:
            return False
        ids = {n.get("local_id") for n in o.ram.get("npcs") or ()}
        # npcs only lists objects near the camera (measured: Oak at y=3 appears once the player is
        # at y<=10, so the table at y=1 needs y<=8): require Oak in the list AND the player close to
        # the table, so "no Pokedex objects" is not just "out of view"
        y = o.ram.get("player_y")
        return (y is not None and y <= FR_OAK_DESK_STAND[1] + 1
                and FR_OAK_LOCAL_ID in ids and not (set(FR_POKEDEX_LOCAL_IDS) & ids))

    def to_viridian(o: Observation) -> Optional[Target]:
        here = m(o)
        if here in (FR_PALLET_TOWN, FR_ROUTE_1):
            return Target.edge("UP")
        return to_pallet(o)

    return [
        Milestone("intro", "Get through the intro (RuleBrain mashes A)",
                  done=lambda o: o.position is not None),
        Milestone("leave_bedroom", "Leave the bedroom (2F stairs -> 1F)",
                  done=lambda o: m(o) in (FR_HOUSE_1F, FR_PALLET_TOWN, FR_OAKS_LAB) or _party(o) > 0,
                  target=lambda o: Target.warp(*FR_HOUSE_1F)),
        Milestone("leave_house", "Leave the house (1F door mat -> outside)",
                  done=lambda o: m(o) in (FR_PALLET_TOWN, FR_OAKS_LAB) or _party(o) > 0,
                  target=lambda o: Target.warp(*FR_PALLET_TOWN)),
        Milestone("pallet_town", "Stand in Pallet Town",
                  done=lambda o: m(o) in (FR_PALLET_TOWN, FR_OAKS_LAB) or _party(o) > 0),
        # --- milestone 2 ---
        Milestone("oak_stops_you", "Walk to Pallet Town's north exit (Prof. Oak stops you)",
                  done=lambda o: at_trigger(o) or m(o) == FR_OAKS_LAB or _party(o) > 0,
                  target=lambda o: Target.at(*FR_OAK_TRIGGER[0]) if m(o) == FR_PALLET_TOWN else None),
        Milestone("oak_lab", "Prof. Oak walks you to his lab (scripted; advance text with A)",
                  done=lambda o: m(o) == FR_OAKS_LAB or _party(o) > 0,
                  target=to_lab),
        Milestone("get_starter", f"Choose {starter.title()} (ball at {(bx, by)}): face it, A, YES",
                  done=lambda o: _party(o) >= 1,
                  target=to_ball),
        # --- rival battle. B advances the rest of the starter dialogue (nickname -> NO). On the way
        # to the exit the rival stops you (row 8, Backend: at (7,8)) and the battle starts; a
        # battle brain fights it (PathBrain is not consulted while ``in_battle`` is True, but the
        # arbiter still shows it every observation via ``observe``). Win or lose the story goes on:
        # you are back at (7,8) in the lab.
        Milestone("rival_battle", "Walk to the lab exit; the rival stops you and the battle starts",
                  done=lambda o: o.ram.get("in_battle") is True or left_lab(o),
                  target=lambda o: Target.warp(*FR_PALLET_TOWN) if m(o) == FR_OAKS_LAB else None,
                  script_button="B"),
        Milestone("rival_battle_over", "Finish the rival battle (win or lose, the story goes on)",
                  done=lambda o: (o.position is not None and o.ram.get("in_battle") is False) or left_lab(o)),
        # --- milestone 3: Pallet Town -> Route 1 -> Viridian City. Pallet's north edge connects to
        # Route 1 and Route 1's north edge to Viridian City (map connections: walk off the edge).
        # Wild battles in Route 1's grass are fought by the battle brain. If the starter faints you
        # wake up at home (whiteout): the targets lead back out of the house.
        Milestone("leave_lab", "Leave Oak's lab (exit mat -> Pallet Town)",
                  done=lambda o: m(o) in (FR_PALLET_TOWN, FR_ROUTE_1, FR_VIRIDIAN_CITY),
                  target=to_pallet),
        Milestone("route_1", "Walk off Pallet Town's north edge onto Route 1",
                  done=lambda o: m(o) in (FR_ROUTE_1, FR_VIRIDIAN_CITY),
                  target=lambda o: Target.edge("UP") if m(o) == FR_PALLET_TOWN else to_pallet(o)),
        Milestone("viridian_city", "Walk north through Route 1 to Viridian City (wild battles: battle brain)",
                  done=lambda o: m(o) == FR_VIRIDIAN_CITY,
                  target=lambda o: Target.edge("UP") if m(o) in (FR_PALLET_TOWN, FR_ROUTE_1) else to_pallet(o)),
        # --- Oak's Parcel. Entering the mart starts the clerk's script (you are walked to the
        # counter, "received OAK'S PARCEL"); you can only walk out once it is over, so being back
        # in Viridian after the mart means you have the parcel (the bag is not readable yet).
        Milestone("viridian_mart", "Enter the Viridian City Poke Mart (the clerk calls you over)",
                  done=lambda o: m(o) == FR_VIRIDIAN_MART,
                  target=lambda o: Target.warp(*FR_VIRIDIAN_MART) if m(o) == FR_VIRIDIAN_CITY else to_viridian(o)),
        Milestone("oaks_parcel", "Receive Oak's Parcel from the clerk (scripted, A), then leave the mart",
                  done=lambda o: m(o) in (FR_VIRIDIAN_CITY, FR_ROUTE_1, FR_PALLET_TOWN, FR_OAKS_LAB),
                  target=lambda o: Target.warp(*FR_VIRIDIAN_CITY) if m(o) == FR_VIRIDIAN_MART else None),
        Milestone("back_to_pallet", "Walk back south: Viridian City -> Route 1 -> Pallet Town",
                  done=lambda o: m(o) in (FR_PALLET_TOWN, FR_OAKS_LAB),
                  target=lambda o: Target.edge("DOWN") if m(o) in (FR_VIRIDIAN_CITY, FR_ROUTE_1)
                  else Target.warp(*FR_VIRIDIAN_CITY) if m(o) == FR_VIRIDIAN_MART else to_pallet(o)),
        # Delivering: stand below Oak (6,3), face him, A. His script fetches the two Pokedexes
        # from the table (objects local_id 9/10 at (4,1)/(5,1)) and gives you one, so "Oak in view
        # and both Pokedex objects gone" is the evidence (no Pokedex flag is read; notes/nav.md).
        Milestone("deliver_parcel", "Give the parcel to Prof. Oak in his lab and receive the Pokedex",
                  done=got_pokedex,
                  target=lambda o: Target.interact(*FR_OAK_DESK_STAND, "UP", "A") if m(o) == FR_OAKS_LAB
                  else Target.warp(*FR_OAKS_LAB) if m(o) == FR_PALLET_TOWN
                  else Target.edge("DOWN") if m(o) in (FR_VIRIDIAN_CITY, FR_ROUTE_1)
                  else Target.warp(*FR_VIRIDIAN_CITY) if m(o) == FR_VIRIDIAN_MART else to_pallet(o)),
<<<<<<< HEAD
<<<<<<< HEAD
=======
>>>>>>> origin/fix/progress-over-wander
        # Goal 17: leave Viridian by the north edge only. Indoor maps and menus must not
        # complete it (that used to drop PathBrain and let RandomBrain wander).
        Milestone("pewter_city", "Leave Viridian City north toward Route 2 (speedrun probe)",
                  done=_left_viridian_north(),
<<<<<<< HEAD
=======
        # Goal 17: a probe, not free wander. Walk off Viridian's north edge. Done when the map
        # changes to something that is not the route south or the mart. Forest/Pewter routes
        # after that are still unverified, so later milestones stay unimplemented.
        Milestone("pewter_city", "Probe north from Viridian City toward Route 2 (Pewter path)",
                  done=lambda o: m(o) not in (None, FR_VIRIDIAN_CITY, FR_VIRIDIAN_MART, FR_ROUTE_1,
                                              FR_PALLET_TOWN, FR_OAKS_LAB, FR_HOUSE_1F, FR_HOUSE_2F),
>>>>>>> origin/feat/goal17-north-probe
=======
>>>>>>> origin/fix/progress-over-wander
                  target=lambda o: Target.edge("UP") if m(o) == FR_VIRIDIAN_CITY
                  else Target.warp(*FR_VIRIDIAN_CITY) if m(o) == FR_VIRIDIAN_MART
                  else Target.edge("UP") if m(o) in (FR_PALLET_TOWN, FR_ROUTE_1)
                  else to_pallet(o)),
<<<<<<< HEAD
<<<<<<< HEAD
=======
>>>>>>> origin/fix/progress-over-wander
        # Keep a story target after the probe so the AI never falls through to free wandering.
        Milestone("advance_story", "Keep moving north toward Pewter; do not wander",
                  done=lambda o: False,
                  target=lambda o: Target.edge("UP") if m(o) not in (None, FR_VIRIDIAN_MART, FR_OAKS_LAB, FR_HOUSE_1F, FR_HOUSE_2F)
                  else Target.warp(*FR_VIRIDIAN_CITY) if m(o) == FR_VIRIDIAN_MART
                  else to_pallet(o)),
<<<<<<< HEAD
=======
>>>>>>> origin/feat/goal17-north-probe
=======
>>>>>>> origin/fix/progress-over-wander
    ]


class GoalPlanner:
    def __init__(self, milestones: Optional[List[Milestone]] = None):
        self.milestones = milestones if milestones is not None else firered_milestones()
        self.reset()

    def reset(self) -> None:
        self._done: Dict[str, bool] = {m.id: False for m in self.milestones}

    def update(self, obs: Observation) -> Optional[Milestone]:
        """Mark milestones done (in order) and return the current one (None = all done)."""
        for m in self.milestones:
            if self._done[m.id]:
                continue
            if not m.placeholder and m.done(obs):
                self._done[m.id] = True
                continue
            return m
        return None

    def restore(self, done_ids) -> None:
        """Mark milestones done from a save sidecar (resume); unknown ids are ignored."""
        for mid in done_ids or ():
            if mid in self._done:
                self._done[mid] = True

    @property
    def current(self) -> Optional[Milestone]:
        return next((m for m in self.milestones if not self._done[m.id]), None)

    def summary(self) -> List[Dict[str, object]]:
        return [{"id": m.id, "label": m.label, "done": self._done[m.id]} for m in self.milestones]
