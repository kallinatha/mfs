#!/usr/bin/env python3
"""Physically Split Cache (PSC) and Virtual ReOrdering (VRO).

PSC  ways [0, nvm_ways) are SOT-MRAM, ways [nvm_ways, ways) are SRAM.
     A block arriving dirty (write-intensive) is steered to the SRAM
     partition; clean blocks are steered to the NVM partition.  This is the
     mechanism by which PSC lowers mu for the NVM partition.

VRO  Within the NVM partition, R ways are designated read-ways and (M-R) are
     write-ways.  Allocations land only in write-ways.  A saturating counter
     beta advances on every NVM write; on reaching beta_max the write-way
     window rotates (Swap-Ways) and the window origin advances
     (Advance-Partition), so wear migrates around the partition.

     The rotation is metadata only: which physical ways are currently
     designated write-ways.  No cache lines are moved.
"""
from .base import Policy


class _NVMPartition(object):
    """Shared VRO state: rotating write-way window inside the NVM partition."""

    def setup_vro(self, M, R, beta_max):
        self.M = M
        self.R = min(max(R, 1), M - 1)
        self.beta = 0
        self.beta_max = beta_max
        self.w0 = 0                       # window origin inside [0, M)
        self.rotations = 0

    def write_ways(self):
        n = self.M - self.R
        w0, M = self.w0, self.M
        return [(w0 + k) % M for k in range(n)]

    def tick(self):
        self.beta += 1
        if self.beta >= self.beta_max:
            self.beta = 0
            self.w0 = (self.w0 + (self.M - self.R)) % self.M   # Swap+Advance
            self.rotations += 1
            self.llc.remaps += 1


class PSC(Policy):
    """Technology-aware placement only (configuration A1)."""
    name = 'psc'

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        nvm, ways = llc.nvm_ways, llc.ways
        if is_dirty and llc.sram_ways:
            w = llc.lru(cs, range(nvm, ways))
            if w >= 0:
                return w
            return llc.lru(cs, range(nvm))
        w = llc.lru(cs, range(nvm))
        if w >= 0:
            return w
        return llc.lru(cs, range(nvm, ways)) if llc.sram_ways else -1


class VRO(Policy, _NVMPartition):
    """Way rotation only, on a unified NVM array (configuration A2)."""
    name = 'vro'
    PARAMS = dict(beta_max=1023, read_frac=2.0 / 3.0)

    def setup(self):
        M = self.llc.nvm_ways
        self.setup_vro(M, int(round(M * self.params['read_frac'])),
                       self.params['beta_max'])

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        w = llc.lru(cs, self.write_ways())
        if w >= 0:
            return w
        return llc.lru(cs, range(llc.ways))

    def on_write(self, cs, way):
        if way < self.llc.nvm_ways:
            self.tick()


class PSCVRO(PSC, _NVMPartition):
    """Proposed mechanism: technology-aware split plus VRO (configuration A3)."""
    name = 'psc-vro'
    PARAMS = dict(beta_max=1023, read_frac=2.0 / 3.0)

    def setup(self):
        M = self.llc.nvm_ways
        self.setup_vro(M, int(round(M * self.params['read_frac'])),
                       self.params['beta_max'])

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        nvm, ways = llc.nvm_ways, llc.ways
        if is_dirty and llc.sram_ways:
            w = llc.lru(cs, range(nvm, ways))
            if w >= 0:
                return w
        w = llc.lru(cs, self.write_ways())
        if w >= 0:
            return w
        w = llc.lru(cs, range(nvm))
        if w >= 0:
            return w
        return llc.lru(cs, range(nvm, ways)) if llc.sram_ways else -1

    def on_write(self, cs, way):
        if way < self.llc.nvm_ways:
            self.tick()
