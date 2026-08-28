#!/usr/bin/env python3
"""ELBA - Elastic Boundary Write Absorption.

Design and evidence: docs/ELBA_DESIGN.md. Three separable mechanisms, each
ablatable through a parameter so the contribution of each can be measured rather
than asserted:

  PR    promote-and-release. On the `promote_on`-th write to a resident NVM
        block, move it to the SRAM side and **invalidate the NVM frame rather
        than backfilling it**. AYUSH writes the displaced SRAM resident into
        that frame, spending one NVM write per promotion; ELBA spends none and
        returns a free frame.

  GWAB  a globally shared write-absorption buffer carved from the SRAM budget,
        so absorption capacity is demand-allocated across sets instead of being
        pinned to set geometry. Measured motivation: at 1 MB the hottest set
        holds 14 burst blocks at p95 against 4 SRAM ways per set.

  EB    an elastic boundary. The buffer target tracks the measured write-burst
        working set, and growth stops when the hit-rate cost exceeds the guard,
        because a wear-only objective has a degenerate optimum.

Set `gwab_entries=0` to ablate GWAB, `elastic=0` to ablate EB, and
`promote_on` very high to ablate PR. All three off reduces to plain LRU on the
hybrid array.

The buffer is modelled honestly: it holds tags, it is consulted on every access,
its capacity is charged against the SRAM budget, and every write into it is
counted as an SRAM write. Its lookup cost is real and is reported, not hidden.
"""
from .base import Policy


class ELBA(Policy):
    name = 'elba'
    PARAMS = dict(
        promote_on=2,          # writes to a resident NVM block before promotion
        gwab_entries=2048,     # global absorption buffer, in blocks
        elastic=1,             # 1 = track the burst set, 0 = fixed buffer
        epoch=200000,          # accesses between boundary adjustments
        grow_factor=1.25,
        shrink_factor=0.9,
        hit_tolerance=0.005,   # give back capacity if hit rate falls this far
        min_entries=256,
        max_entries=16384,
    )

    # ---------------------------------------------------------------- setup
    def setup(self):
        llc = self.llc
        self.M = llc.nvm_ways
        self.cap = int(self.params['gwab_entries'])
        self.buf = {}                     # addr -> [dirty, last_use]
        self.order = []                   # insertion order, for eviction
        self.wcount = {}                  # addr -> writes seen this residency

        self.promotions = 0
        self.frames_released = 0
        self.buf_hits = 0
        self.buf_evictions = 0
        self.buf_writebacks = 0
        self.absorbed_writes = 0
        self.boundary_changes = 0

        # elastic control state
        self.epoch_i = 0
        self.epoch_hits = 0
        self.epoch_accesses = 0
        self.last_hit_rate = None
        self.burst_seen = set()
        self.history = []

    def state_bits(self):
        """Buffer tags plus the small control state. Data bits are not counted
        here because the buffer is carved from the SRAM data budget, which the
        arena already accounts for; the tags are the added cost."""
        import math
        tagbits = 32 - int(math.log(max(2, self.llc.sets), 2)) - 6
        return self.cap * (tagbits + 2) + 64

    # ------------------------------------------------------------ buffer ops
    def _buf_lookup(self, addr):
        return addr in self.buf

    def _buf_insert(self, addr, dirty, now):
        if addr in self.buf:
            self.buf[addr][0] |= dirty
            self.buf[addr][1] = now
            return
        while len(self.order) >= self.cap and self.order:
            self._buf_evict()
        self.buf[addr] = [dirty, now]
        self.order.append(addr)
        self.llc.sram_writes += 1          # the promoted line is written to SRAM

    def _buf_evict(self):
        victim = self.order.pop(0)
        entry = self.buf.pop(victim, None)
        self.buf_evictions += 1
        if entry and entry[0]:
            # Dirty on eviction: it goes to memory, NOT back into an NVM frame.
            # Backfilling is what makes a swap cost NVM endurance; releasing is
            # the point of the mechanism.
            self.buf_writebacks += 1

    # ------------------------------------------------------------- hooks
    def on_write_hit(self, cs, way, is_dirty, now):
        """The event where LLC write traffic actually lives."""
        llc = self.llc
        if way >= self.M:
            return                                     # already on the SRAM side
        a = llc.tag[cs][way]
        if a == -1:
            return
        self.burst_seen.add(a)
        c = self.wcount.get(a, 1) + 1
        self.wcount[a] = c
        if c < self.params['promote_on']:
            return
        if self.cap <= 0:
            return
        # Promote and RELEASE: the NVM frame is invalidated, never rewritten.
        self._buf_insert(a, True, now)
        llc.tag[cs][way] = -1
        self.promotions += 1
        self.frames_released += 1
        self.wcount.pop(a, None)

    def on_hit(self, cs, way):
        self.epoch_hits += 1

    def aux_lookup(self, addr, op, now):
        """The buffer participates in the lookup path.

        Without this the absorbed blocks are invisible: they leave the array,
        serve nothing, and the policy is measured as pure capacity loss. Reads
        hit here; writes are absorbed here, which is the mechanism's whole
        purpose - the write is served in SRAM and the NVM frame is never
        touched again for that block.
        """
        e = self.buf.get(addr)
        if e is None:
            return False
        e[1] = now
        self.buf_hits += 1
        if op == 2:                       # a write absorbed by SRAM
            e[0] = True
            self.absorbed_writes += 1
            self.llc.sram_writes += 1
        return True

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        self.epoch_accesses += 1
        self.epoch_i += 1
        if self.params['elastic'] and self.epoch_i >= self.params['epoch']:
            self._retune()
        return llc.lru(cs, range(llc.ways))

    def on_write(self, cs, way):
        if way < self.M:
            a = self.llc.tag[cs][way]
            if a != -1:
                self.wcount[a] = 1

    # ------------------------------------------------------- elastic control
    def _retune(self):
        """Move the boundary towards the measured burst working set.

        Two-sided by construction: the wear signal asks for growth, the hit-rate
        guard can refuse it. A one-sided controller would discover the
        degenerate optimum - stop using NVM entirely - which scores an unbounded
        lifetime gain while destroying the cache.
        """
        p = self.params
        self.epoch_i = 0
        hit_rate = (self.epoch_hits / self.epoch_accesses) if self.epoch_accesses else 0.0
        burst = len(self.burst_seen)
        old = self.cap

        if self.last_hit_rate is not None and hit_rate < self.last_hit_rate - p['hit_tolerance']:
            self.cap = max(int(p['min_entries']), int(self.cap * p['shrink_factor']))
        elif burst > self.cap:
            self.cap = min(int(p['max_entries']), int(self.cap * p['grow_factor']))
        elif burst < self.cap * 0.5:
            self.cap = max(int(p['min_entries']), int(self.cap * p['shrink_factor']))

        while len(self.order) > self.cap:
            self._buf_evict()
        if self.cap != old:
            self.boundary_changes += 1
        self.history.append(dict(cap=self.cap, burst=burst, hit_rate=hit_rate))
        self.last_hit_rate = hit_rate
        self.epoch_hits = self.epoch_accesses = 0
        self.burst_seen = set()
        if len(self.wcount) > 2_000_000:
            self.wcount.clear()
