#!/usr/bin/env python3
"""Start-Gap - Qureshi, Karidis, Franceschini, Srinivasan, Lastras & Abali,
"Enhancing Lifetime and Security of PCM-Based Main Memory with Start-Gap Wear
Leveling", MICRO 2009, transplanted from PCM main memory to a set-indexed cache.

REWRITTEN. Two earlier versions were wrong in opposite directions and the second
was worse than the first:

  v1 relocated a whole cache set per gap movement and charged one write per
     valid frame, which over-charged the scheme relative to the paper's
     one-line-per-movement cost model.

  v2 "fixed" that by relocating one *frame* instead, and in doing so deleted the
     mechanism. Start-Gap **is** an algebraic remapping; the copy is only the
     bookkeeping that keeps data consistent with the remap. v2 never set
     `does_remap`, never overrode `remap_set`, and wrote `self.start` without
     ever reading it, so `llc.run` bound `remap = None` and no address was ever
     relocated. What remained was a 1/(psi+1) write tax with no wear-levelling
     benefit at all: measured inter-set variation got monotonically *worse* as
     psi shrank, and the best available configuration was the scheme switched
     off. It also corrupted live blocks (no spare frame was reserved) and
     orphaned blocks whose tags were written into a set their address does not
     index to.

Why v1's cost was right and v2's reasoning was inverted: the paper's
one-write-per-movement holds because in PCM the *unit of remapping equals the
unit of writing* - one line. In a set-indexed cache the unit that can be
remapped is the **set index**, because that is the only part of the placement
function that is an algebraic function of the address. A way is chosen
dynamically by LRU and is not a function of the address, so rotating ways
permutes nothing. One gap movement therefore legitimately costs up to `ways`
writes, and the way to hold the paper's <= 1/(psi+1) overhead budget is to scale
the interval - `psi_effective = ways * psi` - not to shrink the relocation unit
until it means nothing.

The paper, verbatim, on the parts implemented here:

  Gap movement (Fig. 6)
      if GAP == 0:  START = (START+1) % N ; [GAP] = [N] ; GAP = N
      else:         [GAP] = [GAP-1]       ; GAP = GAP-1
    The three actions of the wrap branch are simultaneous. Splitting them breaks
    the bijection in Fig. 7 for one movement, so they are applied together here.

  Address mapping (Fig. 7)
      PA = (LA + Start) mod N
      if PA >= GAP:  PA = PA + 1

  Cadence (Sec. 4.3)
      "Gap is moved by 1 location once every psi writes to memory"; the
      evaluation uses psi = 100, and "the extra writes due to wear leveling are
      limited to 1/(psi+1) of the total writes".

  Cost (Sec. 4.3)
      Start and Gap, "each less than four bytes", plus "one global 7-bit
      counter", plus the GapLine. In PCM the GapLine comes free from the spare
      pool; a cache has no spare pool, so one set is reserved here and its cost
      is charged in `state_bits`.

**What this is not.** The paper's headline number is for *Randomized* Start-Gap
(Sec. 5): plain Start-Gap reaches 53% of ideal endurance, the randomized variant
97%, because "in typical programs heavily written lines tend to be located
spatially close to each other" and plain Start-Gap can only move a line to its
neighbour. No Feistel or RIB randomizer is implemented. The array's own set
index is a XOR fold of two address slices (`llc.set_index`), which sits in the
LA->IA position of the paper's Fig. 10 pipeline and destroys much of that
spatial correlation - but it is a many-to-one hash fixed by the array geometry,
not the keyed invertible permutation Sec. 5 specifies. Report this row as plain
Start-Gap, set-granularity adaptation, and cite the 53% figure, not the 97%.
"""
import math

from .base import Policy


class StartGap(Policy):
    name = 'startgap'
    does_remap = True
    PARAMS = dict(
        gap_interval=100,        # psi, per the paper's evaluation
        scale_by_ways=1,         # hold the 1/(psi+1) budget at set granularity
    )

    # ---------------------------------------------------------------- setup
    def setup(self):
        llc = self.llc
        self.S = llc.sets
        # The paper's N lines occupy N+1 physical locations, the extra one being
        # the GapLine. Here the N logical sets are the array's own sets and the
        # spare is *appended*, so the mapping stays an exact bijection. Folding
        # a logical set away instead would alias two set indices together and
        # charge Start-Gap a hit-rate loss the paper does not have.
        self.N = self.S
        if self.N < 3:
            raise ValueError('Start-Gap needs at least 3 sets')
        spare = llc.add_spare_set()
        assert spare == self.N, 'spare set must sit immediately above the array'
        self.gap = self.N                      # spare starts at the top
        self.start = 0
        self.ctr = 0
        self.movements = 0
        self.rotations = 0
        self.frames_moved = 0
        # psi is counted in demand writes; the mechanism's own relocation writes
        # are excluded, which is what makes the paper's arithmetic
        # (psi demand + 1 relocation = psi+1 total) come out exactly.
        w = llc.ways if int(self.params['scale_by_ways']) else 1
        self.psi = max(1, int(self.params['gap_interval']) * w)

    def state_bits(self):
        # Two registers, the 7-bit global counter, and the reserved GapLine set
        # (a cache has no spare pool; the paper's fallback is an extra line).
        b = int(math.ceil(math.log(max(2, self.N + 1), 2)))
        tagbits = 32 - int(math.log(max(2, self.S), 2)) - 6
        gapline = self.llc.ways * (self.llc.blocksize * 8 + tagbits + 2)
        return 2 * b + 7 + gapline

    # -------------------------------------------------------------- mapping
    def remap_set(self, cs):
        """Figure 7, verbatim. Logical set index -> physical set index."""
        pa = (cs + self.start) % self.N
        if pa >= self.gap:
            pa += 1
        return pa

    # ------------------------------------------------------------- movement
    def on_write(self, cs, way):
        """psi counts demand writes into the NVM partition.

        SRAM ways are excluded because only NVM wears; the consequence is that
        the gap advances more slowly in the hybrid arena than in the all-NVM
        arena for the same trace, and that is recorded rather than hidden.
        """
        if way >= self.llc.nvm_ways:
            return
        self.ctr += 1
        if self.ctr < self.psi:
            return
        self.ctr = 0
        self._gap_movement()

    def _gap_movement(self):
        llc = self.llc
        if self.gap == 0:
            # Wrap. Figure 6's three actions are one atomic step.
            self.frames_moved += llc.move_set(self.N, 0)
            self.start = (self.start + 1) % self.N
            self.gap = self.N
            self.rotations += 1
        else:
            self.frames_moved += llc.move_set(self.gap - 1, self.gap)
            self.gap -= 1
        self.movements += 1
        llc.remaps += 1
