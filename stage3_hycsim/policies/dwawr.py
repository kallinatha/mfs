#!/usr/bin/env python3
"""DWAWR - Agarwal & Kapoor, "Improving the Lifetime of Non-Volatile Cache by
Write Restriction", IEEE Transactions on Computers 68(9), 2019; the dynamic
write-restriction family; see also `swwr.py` for the vertical-window variants
(SWWR/DWWR) from Chapter 4 of the same author's 2020 thesis.

State per set: a write counter Z[w] per way, a restricted bit R[w] per way, and
a global interval counter.  At every interval expiry the n most-written ways of
each set are marked restricted and their counters cleared.  A write that would
land on a restricted way is redirected to an eligible unrestricted way in the
same set, and the redirect is charged as a physical write.

Implemented to follow the published state machine step for step so that the
conformance tests in test_fidelity.py can check each transition.
"""
from .base import Policy


class DWAWR(Policy):
    name = 'dwawr'
    PARAMS = dict(interval=4096, n_restricted=2, counter_bits=8)

    def setup(self):
        llc = self.llc
        self.M = llc.nvm_ways
        self.Z = [[0] * self.M for _ in range(llc.sets)]
        self.R = [[0] * self.M for _ in range(llc.sets)]
        self.t = 0
        self.redirects = 0
        self.intervals = 0

    def state_bits(self):
        return self.llc.sets * self.M * (self.params['counter_bits'] + 1)

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        w = llc.lru(cs, range(llc.ways))
        if w >= 0 and w < self.M and self.R[cs][w]:
            free = [j for j in range(self.M) if not self.R[cs][j]]
            if free:
                r = llc.lru(cs, free)
                if r >= 0:
                    self.redirects += 1
                    return r
        return w

    def on_write(self, cs, way):
        if way >= self.M:
            return
        self.Z[cs][way] += 1
        self.t += 1
        if self.t < self.params['interval']:
            return
        self.t = 0
        self.intervals += 1
        n = min(self.params['n_restricted'], self.M - 1)
        for s in range(self.llc.sets):
            Z, R = self.Z[s], self.R[s]
            order = sorted(range(self.M), key=lambda j: (-Z[j], j))   # deterministic tie break
            for j in range(self.M):
                R[j] = 0
            for j in order[:n]:
                R[j] = 1
                Z[j] = 0
