#!/usr/bin/env python3
"""PSC-Soft: the technology split as a preference, not a wall.

Derived from a measurement, not from theory. In ChampSim on 619.lbm, 293,487 of
293,488 LLC WRITE accesses *hit*: at the last level the writeback stream almost
never causes a fill. PSC as published therefore has two halves that behave very
differently in a real hierarchy:

  "if the block is dirty, place it in SRAM"   -- almost never fires at fill time
  "otherwise place it in NVM only"            -- fires on essentially every fill

The second half is the damaging one. It routes ~100% of fills into the NVM
partition where plain LRU would have routed 12/16 = 75%, so the SRAM ways starve
and NVM wear rises: 635,490 NVM writes under LRU against 817,131 under PSC on
the same trace, peak frame writes 33 against 42.

PSC-Soft keeps the intent and drops the harmful half. Blocks predicted to be
written again -- or arriving dirty -- prefer the SRAM partition; everything else
is placed by ordinary LRU across all ways, exactly as the baseline would.

The predictor is trained on write *hits* as well as write fills, because that is
where the LLC's write traffic actually appears. A predictor updated only at fill
time never learns anything on these workloads.
"""
from .base import Policy


class PSCSoft(Policy):
    name = 'psc-soft'
    PARAMS = dict(entries=4096, bits=2, threshold=2,
                  decay_interval=100000, max_sram_frac=0.45, use_rank=0,
                  wear_bits=4, trigger=4, pool=4)

    def setup(self):
        llc = self.llc
        self.tab = bytearray(self.params['entries'])
        self.cap = (1 << self.params['bits']) - 1
        self.mask = self.params['entries'] - 1
        assert self.params['entries'] & self.mask == 0, 'entries must be a power of two'
        self.steered = 0
        self.alloc = 0
        self.dctr = 0
        self.M = llc.nvm_ways
        self.wcap = (1 << self.params['wear_bits']) - 1
        self.c = [[0] * self.M for _ in range(llc.sets)]
        self.interventions = 0

    def state_bits(self):
        b = self.params['entries'] * self.params['bits']
        if self.params['use_rank']:
            b += self.llc.sets * self.M * self.params['wear_bits']
        return b

    def _sig(self, addr):
        a = addr >> 6
        return int((a ^ (a >> 13) ^ (a >> 23)) & self.mask)

    def hot(self, addr):
        return self.tab[self._sig(addr)] >= self.params['threshold']

    def note_write(self, addr):
        i = self._sig(addr)
        if self.tab[i] < self.cap:
            self.tab[i] += 1
        self.dctr += 1
        if self.dctr >= self.params['decay_interval']:
            self.dctr = 0
            t = self.tab
            for k in range(len(t)):
                t[k] >>= 1

    def sram_ok(self):
        if self.alloc < 1000:
            return True
        return (self.steered / self.alloc) < self.params['max_sram_frac']

    def _rank_way(self, cs):
        c = self.c[cs]
        llc = self.llc
        lo = hi = c[0]
        for j in range(1, self.M):
            v = c[j]
            if v < lo:
                lo = v
            elif v > hi:
                hi = v
        if hi - lo < self.params['trigger']:
            return -1
        tag, mru = llc.tag[cs], llc.mru[cs]
        for j in range(self.M):
            if tag[j] == -1:
                return j
        pool = sorted(range(self.M), key=lambda j: mru[j])[:self.params['pool']]
        best, bw = -1, None
        for j in pool:
            if bw is None or c[j] < bw:
                bw, best = c[j], j
        self.interventions += 1
        return best

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        nvm, ways = llc.nvm_ways, llc.ways
        self.alloc += 1
        if is_dirty:
            self.note_write(addr)
        if llc.sram_ways and self.sram_ok() and (is_dirty or self.hot(addr)):
            w = llc.lru(cs, range(nvm, ways))
            if w >= 0:
                self.steered += 1
                return w
        if self.params['use_rank']:
            w = self._rank_way(cs)
            if w >= 0:
                return w
        return llc.lru(cs, range(ways))     # ALL ways, not NVM only

    def on_hit(self, cs, way):
        # write traffic at the LLC shows up as hits; train the predictor there
        if self.llc.dirty[cs][way]:
            self.note_write(self.llc.tag[cs][way])

    def on_write(self, cs, way):
        if way >= self.M:
            return
        c = self.c[cs]
        c[way] += 1
        if c[way] >= self.wcap:
            for j in range(self.M):
                c[j] >>= 1
