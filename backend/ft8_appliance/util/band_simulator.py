"""A populated FT8 band simulator for the dev workstation.

Replaces the static FakeOtherStations in dev_run.py with a multi-station
state machine: ~30 simulated operators with realistic callsigns and
grids, each running their own little QSO state machine. They:

  * call CQ periodically (frequency proportional to their "activity")
  * answer each other's CQs (random matchmaking)
  * exchange grid → report → R-report → RR73 like real stations
  * react to OUR transmissions — when we call CQ a couple of them
    might pick us; when we answer a CQ, that station replies with the
    appropriate next step

The simulator is the orchestrator's :class:`DecodeSource`. Notification
of *our* TX flows through :meth:`notify_our_tx` — dev_run.py wraps the
orchestrator's ``_do_tx_message`` to relay it.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..runtime.slot_clock import SlotTick
from ..statemachine import DecodedMsg


# ---------------------------------------------------------------------------
# A starter pack of plausible-looking simulated stations.
# Mix of strong-local (EU), medium (NA), and rare (VK/JA/ZL/Africa).
# (call, grid 4-char, signal-strength category) — sim picks SNR per slot.
_STRONG, _MEDIUM, _WEAK = "strong", "medium", "weak"

# Rein FIKTIVE Population fuer den Demo-Modus (oeffentliche Doku-Screenshots).
# Bewusst Beispiel-Rufzeichen im XYZ/AAA-Stil (Doku-Konvention) ueber alle
# Kontinente verteilt — damit DXCC-/Kontinent-Filter im Screenshot was zeigen
# und KEIN echtes Dritt-Rufzeichen im oeffentlichen Repo landet.
DEMO_POPULATION = [
    ("DL1XYZ", "JO31", _STRONG), ("DK2AAA", "JO40", _STRONG),
    ("OE3XYZ", "JN78", _STRONG), ("HB9AAA", "JN47", _STRONG),
    ("F4XYZ",  "JN13", _STRONG), ("ON4AAA", "JO20", _MEDIUM),
    ("PA2XYZ", "JO22", _MEDIUM), ("G4AAA",  "IO91", _MEDIUM),
    ("SP5XYZ", "JO90", _MEDIUM), ("EA7AAA", "IM98", _MEDIUM),
    ("SM5XYZ", "JP82", _MEDIUM), ("LA9AAA", "JP20", _MEDIUM),
    ("JA1XYZ", "PM95", _WEAK),   ("UA9AAA", "MO04", _WEAK),
    ("ZS6XYZ", "KG44", _WEAK),   ("K3AAA",  "FN31", _MEDIUM),
    ("VE3XYZ", "FN03", _WEAK),   ("PY2AAA", "GG66", _WEAK),
    ("VK2XYZ", "QF56", _WEAK),
]

DEFAULT_POPULATION = [
    # Strong locals (Central Europe, JN-/JO-grids) — frequently active
    ("DL3ABC", "JO31", "strong"),
    ("DL5MAX", "JN58", "strong"),
    ("DK1KX",  "JO40", "strong"),
    ("OE3XYZ", "JN78", "strong"),
    ("HB9DEF", "JN47", "strong"),
    ("F5RZJ",  "JN13", "strong"),
    ("ON4QX",  "JO20", "strong"),
    ("PA3CTV", "JO22", "strong"),
    ("G0XYZ",  "IO91", "strong"),
    ("SP9PWA", "JO90", "strong"),
    # Medium DX (Mediterranean, Scandinavia, Eastern Europe)
    ("IK0XYZ", "JN61", "medium"),
    ("EA5HJ",  "IM98", "medium"),
    ("OH8X",   "KP54", "medium"),
    ("LA2QM",  "JP20", "medium"),
    ("SM3CER", "JP82", "medium"),
    ("UA9CDV", "MO04", "medium"),
    ("YO3ABC", "KN34", "medium"),
    ("SV1XYZ", "KM18", "medium"),
    # Transatlantic
    ("W1AW",   "FN31", "medium"),
    ("K2LE",   "FN42", "medium"),
    ("VE3XYZ", "FN03", "medium"),
    ("KP4ABC", "FK68", "medium"),
    ("PY2ABC", "GG66", "weak"),
    ("CE3SAD", "FF46", "weak"),
    # Far East / Pacific
    ("JA1XYZ", "PM95", "weak"),
    ("BV2KI",  "PL05", "weak"),
    ("VK6ABC", "OF87", "weak"),
    ("ZL2IFB", "RE78", "weak"),
    # Africa
    ("ZS6CCY", "KG44", "weak"),
    ("EA8DX",  "IL18", "medium"),
    # Rare DX
    ("3Y0J",   "GD52", "weak"),
    ("VK0SO",  "MC85", "weak"),
]


SNR_BANDS = {
    "strong": (-3, +5),    # easy copy
    "medium": (-12, -5),
    "weak":   (-22, -13),
}


# ---------------------------------------------------------------------------
@dataclass
class SimStation:
    call: str
    grid: str
    strength: str             # "strong" | "medium" | "weak"
    audio_freq_hz: int        # consistent freq per station
    state: str = "idle"       # idle | cqing | answering | reporting | r_report | rr73_sent
    partner: str | None = None
    qso_step: int = 0
    last_tx_slot: int = -10
    cq_cooldown: int = 0      # slots until they can CQ again


# ---------------------------------------------------------------------------
@dataclass
class FT8BandSimulator:
    my_call: str
    my_grid: str
    band: str = "20m"
    population: list[SimStation] = field(default_factory=list)
    _slot: int = 0
    # Pending stations that should react to *our* last TX
    _react_queue: list[tuple[str, str, str | None]] = field(default_factory=list)
    # Track ongoing QSO involving us so we don't shake their state from random ticks
    _our_qso_partner: str | None = None
    # Seeded RNG so behaviour is reproducible across restarts
    _rng: random.Random = field(default_factory=lambda: random.Random(42))

    def __post_init__(self) -> None:
        if not self.population:
            self.population = self._make_population()

    # ------------------------------------------------------------------ ctor helpers
    def _make_population(self) -> list[SimStation]:
        out: list[SimStation] = []
        for call, grid, strength in DEFAULT_POPULATION:
            out.append(SimStation(
                call=call, grid=grid, strength=strength,
                # Each station picks a stable audio-freq slot in the passband
                audio_freq_hz=300 + (hash(call) & 0xFFF) % 2400,
            ))
        return out

    # ------------------------------------------------------------------ orchestrator hook
    def notify_our_tx(self, message: str, freq_hz: int) -> None:
        """Called every time our orchestrator emits a TX_MESSAGE.

        We parse the message and queue an appropriate next-slot reaction
        from the addressed station. ``message`` is the textual FT8 message
        like "CQ DK9XR JN58" or "W1AW DK9XR FN31".
        """
        tokens = message.strip().split()
        if not tokens:
            return
        # Case 1: we called CQ → 0..3 stations might respond next slot
        if tokens[0] == "CQ":
            self._on_we_cq()
            return
        # Case 2: <to> <from> <something> — we addressed someone specifically
        if len(tokens) >= 2:
            to_call = tokens[0]
            rest = " ".join(tokens[2:]) if len(tokens) > 2 else ""
            self._on_we_addressed(to_call, rest)

    def _on_we_cq(self) -> None:
        # 0..3 stations decide to answer us next slot. Bias toward stronger
        # signals (they hear us better) and inactive ones (not mid-QSO).
        candidates = [
            s for s in self.population
            if s.state in ("idle", "cqing") and s.call != self.my_call
        ]
        if not candidates:
            return
        # Probability weights: strong=3, medium=2, weak=1
        weights = [{"strong": 3, "medium": 2, "weak": 1}[s.strength] for s in candidates]
        # Number that pile-on
        n = self._rng.choices([0, 1, 1, 2, 2, 3], k=1)[0]
        if n == 0:
            return
        responders = self._weighted_sample(candidates, weights, n)
        for s in responders:
            s.state = "answering_us_grid"
            s.partner = self.my_call
            s.last_tx_slot = self._slot  # they'll TX next slot

    def _on_we_addressed(self, to_call: str, payload_rest: str) -> None:
        # The station we addressed should advance one step in the QSO with us
        sim = next((s for s in self.population if s.call == to_call), None)
        if sim is None:
            return
        # Figure out what step we're at based on the rest of the message
        # - "<grid>"     → we just sent our grid (they should send us SNR)
        # - "<report>"   → we sent SNR (they should send R-SNR)
        # - "R<report>"  → we sent R-SNR (they should send RR73)
        # - "RR73"/"73"  → we closed (they're done)
        rest = payload_rest.strip()
        if re.fullmatch(r"R[+-]\d{1,2}", rest):
            sim.state = "sending_rr73_to_us"
        elif re.fullmatch(r"[+-]\d{1,2}", rest):
            sim.state = "sending_r_report_to_us"
        elif re.fullmatch(r"[A-R]{2}\d{2}[a-x]?[a-x]?", rest):
            sim.state = "sending_report_to_us"
        elif rest in ("RR73", "73"):
            sim.state = "idle"  # all done
            sim.partner = None
        sim.partner = self.my_call
        sim.last_tx_slot = self._slot  # they'll TX next slot

    # ------------------------------------------------------------------ slot loop
    async def __call__(self, tick: SlotTick) -> list[DecodedMsg]:
        self._slot += 1
        decodes: list[DecodedMsg] = []
        for s in self.population:
            # Cool-down for CQ-callers
            if s.cq_cooldown > 0:
                s.cq_cooldown -= 1
            d = self._advance_station(s)
            if d is not None:
                decodes.append(d)
        # Background chatter: a few random idle stations match-up into mock QSOs
        self._random_matchmake()
        return decodes

    def _advance_station(self, s: SimStation) -> DecodedMsg | None:
        """Decide whether *s* transmits this slot, and what they say."""
        # Stations addressed to us this slot transmit deterministically
        if s.state == "answering_us_grid" and s.last_tx_slot == self._slot:
            s.last_tx_slot = self._slot
            return self._make_decode(s, f"{self.my_call} {s.call} {s.grid}",
                                     call_to=self.my_call, grid=s.grid)
        if s.state == "sending_report_to_us" and s.last_tx_slot == self._slot:
            snr = self._rng.choice([-15, -12, -10, -8, -5, -3])
            return self._make_decode(s, f"{self.my_call} {s.call} {snr:+03d}",
                                     call_to=self.my_call)
        if s.state == "sending_r_report_to_us" and s.last_tx_slot == self._slot:
            snr = self._rng.choice([-15, -12, -10, -8])
            s.state = "waiting_rr73"
            return self._make_decode(s, f"{self.my_call} {s.call} R{snr:+03d}",
                                     call_to=self.my_call)
        if s.state == "sending_rr73_to_us" and s.last_tx_slot == self._slot:
            s.state = "idle"
            s.partner = None
            s.cq_cooldown = 6  # they'll rest before next CQ
            return self._make_decode(s, f"{self.my_call} {s.call} RR73",
                                     call_to=self.my_call)

        # Normal random behaviour
        if s.state == "idle":
            # Probability of starting a CQ this slot. Strong+active stations
            # call more often. ~1 in 30 slots for strong, 1 in 100 for weak.
            base_prob = {"strong": 0.06, "medium": 0.03, "weak": 0.01}[s.strength]
            if s.cq_cooldown == 0 and self._rng.random() < base_prob:
                s.state = "cqing"
                s.last_tx_slot = self._slot
                return self._make_decode(s, f"CQ {s.call} {s.grid}", grid=s.grid)
            return None

        if s.state == "cqing":
            # If they've been calling CQ for >4 slots, give up
            if self._slot - s.last_tx_slot >= 4:
                s.state = "idle"
                s.cq_cooldown = self._rng.randint(8, 20)
                return None
            # Otherwise repeat CQ every other slot
            if (self._slot - s.last_tx_slot) % 2 == 0 and self._slot != s.last_tx_slot:
                return self._make_decode(s, f"CQ {s.call} {s.grid}", grid=s.grid)
            return None

        return None

    def _random_matchmake(self) -> None:
        """Sometimes two idle stations 'discover' each other and have a QSO
        we just observe. Pure chrome — makes the band feel alive."""
        # Pick a pair every ~5 slots
        if self._slot % 5 != 0:
            return
        idle = [s for s in self.population if s.state == "idle"]
        if len(idle) < 2:
            return
        a = self._rng.choice(idle)
        b = self._rng.choice(idle)
        if a is b:
            return
        # Background QSOs aren't directly emitted here — would need a
        # second pass. Skipped to keep the simulator simple.

    # ------------------------------------------------------------------ helpers
    def _make_decode(
        self, s: SimStation, message: str, *,
        call_from: str | None = None, call_to: str | None = None,
        grid: str | None = None,
    ) -> DecodedMsg:
        lo, hi = SNR_BANDS[s.strength]
        snr = self._rng.randint(lo, hi)
        return DecodedMsg(
            ts=datetime.now(UTC),
            call_from=call_from or s.call,
            call_to=call_to,
            grid=grid,
            message=message,
            snr_db=snr,
            dt_s=round(self._rng.uniform(-0.4, 0.4), 1),
            freq_offset_hz=s.audio_freq_hz,
            band=self.band,
        )

    def _weighted_sample(self, items, weights, k):
        # sample-without-replacement weighted; brute force for tiny k
        chosen = []
        pool = list(zip(items, weights))
        for _ in range(min(k, len(pool))):
            total = sum(w for _, w in pool)
            r = self._rng.uniform(0, total)
            cum = 0
            for i, (item, w) in enumerate(pool):
                cum += w
                if r <= cum:
                    chosen.append(item)
                    pool.pop(i)
                    break
        return chosen
