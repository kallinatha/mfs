#!/usr/bin/env python3
"""EqualChance - Mittal & Vetter, INFLOW 2014.

Re-implemented against the paper (Section 3.2 and Algorithm 1). The earlier
version in this file gave EqualChance *more* state than it has and a different
redirection target, which flatters it:

  State.  The paper keeps one `numWrite` counter and one `FlagBit` per set, and
  makes a point of the small storage cost. The previous version selected the
  globally least-written way, which requires a per-way write counter the scheme
  does not have.

  Target.  Algorithm 1 redirects to an **invalid** block first (I-shifting) and
  to a **clean** block otherwise (C-shifting). If neither exists, a normal write
  is performed. It is in-cache data movement, never invalidation, and the
  LRU age is not updated when a shift happens.

Faithful form implemented here:
  numWrite[set] increments on a write to that set
  at the shifting interval Y, FlagBit[set] turns on and the counter resets
  the next write with FlagBit on redirects to invalid, else clean, else normal
"""
from .base import Policy


class EqualChance(Policy):
    name = 'equalchance'
    PARAMS = dict(relocate_interval=16)

    def setup(self):
        self.ctr = [0] * self.llc.sets
        self.flag = [0] * self.llc.sets
        self.i_shifts = 0
        self.c_shifts = 0
        self.no_candidate = 0

    def state_bits(self):
        # per set: one interval counter plus one flag bit
        import math
        w = int(math.ceil(math.log(max(2, self.params['relocate_interval'] * 2), 2)))
        return self.llc.sets * (w + 1)

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        if not self.flag[cs]:
            return llc.lru(cs, range(llc.ways))
        self.flag[cs] = 0
        tag, dirty = llc.tag[cs], llc.dirty[cs]
        # I-shifting: an invalid frame in the NVM partition
        for j in range(llc.nvm_ways):
            if tag[j] == -1:
                self.i_shifts += 1
                return j
        # C-shifting: a clean frame, which can be overwritten without a writeback
        for j in range(llc.nvm_ways):
            if not dirty[j]:
                self.c_shifts += 1
                return j
        self.no_candidate += 1
        return llc.lru(cs, range(llc.ways))

    def on_write(self, cs, way):
        if way >= self.llc.nvm_ways:
            return
        self.ctr[cs] += 1
        if self.ctr[cs] >= self.params['relocate_interval']:
            self.ctr[cs] = 0
            self.flag[cs] = 1
