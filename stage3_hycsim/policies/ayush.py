#!/usr/bin/env python3
"""AYUSH - Mittal & Vetter, "AYUSH: Extending Lifetime of SRAM-NVM Way-based
Hybrid Caches Using Wear-leveling", MASCOTS 2015.

Implemented from the paper's Algorithm 1, after an earlier version built from a
description turned out to be a different mechanism entirely. AYUSH is not an
admission policy: it is a **write-hit-triggered swap** between the NVM block
being written and the LRU block of the SRAM partition.

Algorithm 1, on a write hit in set i:

    indexOfWritten <- way of the write-hit block
    if indexOfWritten is an SRAM way:
        write in place, no migration
    else:
        ageOfWritten   <- LRU age of the written block
        indexOfSRAMLru <- LRU way among the SRAM ways
        ageOfSRAMLru   <- its LRU age
        if ageOfSRAMLru > ageOfWritten and (ageOfSRAMLru + 1) >= Z:
            migrate cacheData[indexOfSRAMLru] -> cacheData[indexOfWritten]
            write the new data into cacheData[indexOfSRAMLru]
        else:
            write in place, no migration

The age condition is the whole point: the SRAM victim must be *older* than the
block being written, so the swap moves genuinely colder data into NVM rather
than shuffling live data.

**Z is adaptive, and an earlier version here got that wrong.** It was exposed as
a static tuned constant, which is not what the paper does and which also handed
the competitor an oracle: a grid search picks the single best Z for the whole
trace, so AYUSH could neither track a phase change nor be blamed for missing
one. Section II-B defines Z as a measured quantity, recomputed every I cycles:

    "For a W-way cache, we use W counters, where the kth counter records the
     number of hits in the kth LRU-position. Assuming that the total number of
     hits in all the ways is H, we choose Z (>= WS) such that the total hits in
     most-recent Z positions are equal to or greater than alpha*H."

then floored by a tunable L: "Z <- MAX(Z, L)", with L >= WS. The paper's
defaults are alpha = 60%, L = WS, and I = 500K cycles single-core, 250K dual,
125K quad. Those are the defaults here. `adaptive=0` restores the old fixed-Z
behaviour so the difference can be measured rather than argued about.

Cost per the paper: W counters of 40 bits, a 5-bit Z register, and an E-entry
migration buffer (E = 256 for a single core). Both writes of a swap are charged.

One caveat this model forces. The paper's Algorithm 1 fires on a write hit, and
in HyCSim's exclusive protocol a writeback to a resident block performs no array
write at all (verified against the official BaseHybrid source, which asserts
such a writeback is never dirty). AYUSH is therefore trigger-starved here
through no fault of its own, and `write_hits` records how starved. That is a
property of the trace protocol, not a defect in the scheme, and must be said in
any table that reports an AYUSH row from this simulator.
"""
from .base import Policy


class AYUSH(Policy):
    name = 'ayush'
    PARAMS = dict(
        adaptive=1,          # 1 = compute Z per the paper; 0 = use the fixed Z
        Z=4,                 # only consulted when adaptive=0
        alpha=0.60,          # paper's default
        L=0,                 # floor on Z; 0 means "use WS", the paper's default
        interval=500000,     # I, in accesses (paper: cycles; see note below)
        counter_bits=40,
        buffer_entries=256,
    )

    def setup(self):
        llc = self.llc
        self.W = llc.ways
        # Naming trap, and an earlier version fell into it. The paper's WS is
        # the *number of SRAM ways*; in this array SRAM occupies the top ways,
        # so the partition boundary is nvm_ways and the SRAM count is W-nvm_ways.
        # Conflating the two clamps Z to 12 instead of 4 on a 12+4 array and
        # silently disables the adaptation.
        self.NW = llc.nvm_ways          # boundary: SRAM ways are [NW, W)
        self.WS = llc.ways - llc.nvm_ways   # the paper's WS
        self.migrations = 0
        self.blocked_by_age = 0
        self.blocked_by_Z = 0
        self.write_hits = 0
        # Per-LRU-position hit counters, the paper's W counters.
        self.pos_hits = [0] * self.W
        self.Z = max(int(self.params['Z']), self.WS)
        self.z_updates = 0
        self.z_history = []
        self.since_update = 0
        # I is specified in cycles. This replay is indexed by record, not cycle,
        # so the interval is applied in accesses and the substitution is
        # recorded rather than hidden. On these traces the two differ by roughly
        # the average cycles-per-access, so scale I if you want cycle parity.
        self.interval_unit = 'accesses'

    # ------------------------------------------------------- adaptive Z
    def on_hit(self, cs, way):
        """Section II-B: count hits by LRU position, recompute Z every I."""
        if not self.params['adaptive']:
            return
        self.pos_hits[self._lru_age(cs, way)] += 1
        self.since_update += 1
        if self.since_update >= int(self.params['interval']):
            self._recompute_Z()

    def _recompute_Z(self):
        self.since_update = 0
        H = sum(self.pos_hits)
        floor_L = int(self.params['L']) or self.WS
        if H:
            target = self.params['alpha'] * H
            run = 0
            z = self.W
            for k in range(self.W):
                run += self.pos_hits[k]
                if run >= target:
                    z = k + 1           # positions are 0-indexed; Z counts them
                    break
            self.Z = max(z, self.WS, floor_L)
        self.z_history.append(self.Z)
        self.z_updates += 1
        self.pos_hits = [0] * self.W    # "these counters are reset after every I"

    def state_bits(self):
        # W per-position hit counters + the Z register + the migration buffer
        blk = self.llc.blocksize * 8
        return (self.W * self.params['counter_bits'] + 5
                + self.params['buffer_entries'] * blk)

    def _lru_age(self, cs, way):
        """Rank of `way` by recency: 0 = MRU, ways-1 = LRU."""
        mru = self.llc.mru[cs]
        order = sorted(range(self.W), key=lambda j: mru[j], reverse=True)
        return order.index(way)

    def on_write_hit(self, cs, way, is_dirty, now):
        """Algorithm 1. Fires only on a write to a block already resident."""
        llc = self.llc
        self.write_hits += 1
        if way >= self.NW or self.NW >= self.W:
            return                                   # already in SRAM: write in place

        age_written = self._lru_age(cs, way)
        sram_lru = llc.lru(cs, range(self.NW, self.W))
        if sram_lru < 0:
            return
        age_sram_lru = self._lru_age(cs, sram_lru)

        if not (age_sram_lru > age_written):
            self.blocked_by_age += 1
            return
        if not (age_sram_lru + 1 >= self.Z):
            self.blocked_by_Z += 1
            return

        # Swap: the colder SRAM resident moves into the NVM frame, the new data
        # takes the SRAM frame. Both array writes are charged.
        tag, mru, dirty = llc.tag[cs], llc.mru[cs], llc.dirty[cs]
        tag[way], tag[sram_lru] = tag[sram_lru], tag[way]
        mru[way], mru[sram_lru] = mru[sram_lru], now
        dirty[way], dirty[sram_lru] = dirty[sram_lru], is_dirty
        llc.charge_write(cs, way)                    # migrated data lands in NVM
        llc.charge_write(cs, sram_lru)               # new data lands in SRAM
        self.migrations += 1

    def choose_way(self, cs, addr, is_dirty):
        # Allocation is unchanged from the baseline: AYUSH acts on write hits.
        return self.llc.lru(cs, range(self.llc.ways))
