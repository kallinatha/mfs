#!/usr/bin/env python3
"""i2WAP - Wang, Dong, Xie & Jouppi, HPCA 2013.

Re-implemented against the paper itself (Sections 5.2 and 6.3), not from a
summary. Two earlier mistakes in this file both charged i2WAP for work the
published scheme does not do, which made the competitor look worse than it is:

  Swap-Shift.  The paper is explicit that SwS "uses data invalidation instead of
  data movement during cache set address re-mapping to minimise both area and
  performance overhead": the swap "only exchanges the set IDs, and the data
  stored in these two sets are simply invalidated (or written-back to the next
  level of caches if dirty)". The previous version migrated both sets and
  charged 2 x NVM_WAYS physical writes per swap. That write amplification was an
  artefact of the implementation, not of i2WAP.

  PoLF.  The paper's counter counts *write hits* to the entire cache, and on
  saturation the new data goes to the write-back buffer "instead of writing it
  to the hit data block" - the array write is avoided, not performed and then
  undone. It also does not mark the flushed line as LRU.

Structure follows the paper:
  SwV  swap pointer; every ST writes, sets SwV and SwV+1 exchange IDs
  ShV  shift value; incremented after a full swap round of N-1 swaps
  FT   line-flush threshold on the global write counter (paper uses FT = 10,
       ST = 100,000 in its evaluation)

Note on trace semantics: the HyCSim trace grammar has no in-place write hit -
a write to a resident block appears as an L2_Rpl that re-allocates - so PoLF's
*write-avoidance* benefit cannot be expressed in this model and only its
redistribution effect is captured. The ChampSim module models the write hit
directly and suppresses the array write, which is the faithful form.
"""
from .base import Policy


class I2WAP(Policy):
    name = 'i2wap'
    PARAMS = dict(polf_threshold=10, swapshift_interval=100000)

    def setup(self):
        self.wcount = 0          # global write counter feeding PoLF
        self.sscount = 0         # writes since the last swap
        self.SwV = 0             # swap pointer
        self.ShV = 0             # shift value
        self.S = self.llc.sets
        self.swap_rounds = 0

    def state_bits(self):
        # the paper's headline cost argument: two registers, not per-set state
        import math
        b = int(math.ceil(math.log(max(2, self.S), 2)))
        return 2 * b + 32

    def choose_way(self, cs, addr, is_dirty):
        return self.llc.lru(cs, range(self.llc.ways))

    def _invalidate_set(self, s):
        """SwS invalidates rather than migrates: no data movement is charged."""
        tag = self.llc.tag[s]
        for j in range(self.llc.ways):
            tag[j] = -1

    def on_write(self, cs, way):
        llc = self.llc
        if way >= llc.nvm_ways:
            return
        self.wcount += 1
        self.sscount += 1

        if self.wcount >= self.params['polf_threshold']:
            self.wcount = 0
            # Flush the line. LRU age is deliberately NOT reset: the paper keeps
            # the flushed line from being immediately refilled with the same data.
            llc.tag[cs][way] = -1
            llc.flushes += 1

        if self.sscount >= self.params['swapshift_interval']:
            self.sscount = 0
            a = self.SwV % self.S
            b = (a + 1) % self.S
            self._invalidate_set(a)
            self._invalidate_set(b)
            llc.remaps += 1
            self.SwV += 1
            if self.SwV >= self.S - 1:      # one swap round complete
                self.SwV = 0
                self.ShV = (self.ShV + 1) % self.S
                self.swap_rounds += 1
