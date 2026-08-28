#!/usr/bin/env python3
"""Trace-driven hybrid LLC wear model.

Semantics follow HyCSim (Escuin et al., RAPIDO 2022) BaseHybrid exactly:
  GetS   hit -> touch; if resident block dirty, invalidate (private level owns it)
  GetX   hit only if resident block dirty; touch; then invalidate
  L2_Rpl not resident -> allocate (a write to the array); resident -> touch

Wear is counted as one write event per frame insertion, which is what
`Frame.insertBlock` accounts for in HyCSim.  Ways [0, nvm_ways) are NVM,
ways [nvm_ways, ways) are SRAM.  Only NVM frames wear out.

Policies plug in through `Policy`; see policies.py.
"""
import math
import numpy as np


def extract_bits(num, k, p):
    a = (1 << p) - 1
    b = (1 << (k + p)) - 1
    return (num & (b - a)) >> p


class LLC(object):
    def __init__(self, size_bytes, nvm_ways, sram_ways, blocksize=64, policy=None):
        self.nvm_ways = nvm_ways
        self.sram_ways = sram_ways
        self.ways = nvm_ways + sram_ways
        self.blocksize = blocksize
        self.sets = int(size_bytes // blocksize // self.ways)
        assert self.sets & (self.sets - 1) == 0, 'sets must be a power of two'
        self.bs_bits = int(math.log(blocksize, 2))
        self.set_bits = int(math.log(self.sets, 2)) - 2      # 4 banks, xor index

        S, W = self.sets, self.ways
        self.tag = [[-1] * W for _ in range(S)]
        self.mru = [[0] * W for _ in range(S)]
        self.dirty = [[0] * W for _ in range(S)]
        self.writes = [[0] * W for _ in range(S)]            # wear counters

        self.hits = self.misses = 0
        self.hits_nvm = self.hits_sram = 0
        self.nvm_writes = self.sram_writes = 0
        self.nvm_reads = self.sram_reads = 0
        self.migration_writes = 0
        self.flushes = 0                                      # extra invalidations by policy
        self.remaps = 0                                       # inter-set remap events
        self.policy = policy
        if policy is not None:
            policy.attach(self)

    # ---------------------------------------------------------------- indexing
    def set_index(self, address):
        bank = extract_bits(address, 2, self.bs_bits)
        s = self.bs_bits + 2
        x1 = extract_bits(address, self.set_bits, s)
        x2 = extract_bits(address, self.set_bits, s + self.set_bits)
        return (x1 ^ x2) + (bank << self.set_bits)

    # ------------------------------------------------------------------- core
    def run(self, op, addr, dirty, progress=None):
        n = len(op)
        tag, mru, dirt, writes = self.tag, self.mru, self.dirty, self.writes
        W, NW = self.ways, self.nvm_ways
        pol = self.policy
        remap = pol.remap_set if (pol and pol.does_remap) else None
        # Optional hit hook: AYUSH needs resident-block reuse, and any
        # write-intensity predictor must be trained on write *hits*, which is
        # where LLC write traffic overwhelmingly appears.
        on_hit = getattr(pol, 'on_hit', None) if pol is not None else None
        on_write_hit = getattr(pol, 'on_write_hit', None) if pol is not None else None
        # A policy may own an auxiliary structure (a victim cache, a write
        # absorption buffer) that holds blocks outside the set-indexed array.
        # It must participate in the lookup, or its blocks are simply lost and
        # the policy is measured serving nothing.
        aux_lookup = getattr(pol, 'aux_lookup', None) if pol is not None else None
        # Recency stamp: the record index by default (never ties), or the
        # trace cycle when set_recency() was called (reproduces HyCSim exactly).
        stamp = getattr(self, '_recency', None)
        for i in range(n):
            now = i if stamp is None else int(stamp[i])
            a = int(addr[i])
            cs = self.set_index(a)
            if remap is not None:
                cs = remap(cs)
            t = tag[cs]
            way = -1
            for j in range(W):
                if t[j] == a:
                    way = j
                    break
            o = op[i]

            if o == 2:                                        # L2_Rpl
                if way == -1 and aux_lookup is not None and aux_lookup(a, o, now):
                    continue                                  # absorbed, no array write
                if way == -1:
                    self.allocate(cs, a, now, int(dirty[i]))
                else:
                    # The block is resident: this is a write *hit* on the array.
                    # Several published mechanisms (AYUSH's migration, i2WAP's
                    # PoLF) trigger here and nowhere else, so the hook exists
                    # even though the baseline model charges no write for it.
                    if on_write_hit is not None:
                        on_write_hit(cs, way, int(dirty[i]), now)
                    mru[cs][way] = now
                continue

            if way < 0 and aux_lookup is not None and aux_lookup(a, o, now):
                # served by the policy's auxiliary structure
                self.hits += 1
                self.hits_sram += 1
                self.sram_reads += 1
                continue

            if way >= 0:
                mru[cs][way] = now
                if on_hit is not None:
                    on_hit(cs, way)
                d = dirt[cs][way]
                if o == 0:                                    # GetS
                    self.hits += 1
                    if way < NW:
                        self.hits_nvm += 1
                        self.nvm_reads += 1
                    else:
                        self.hits_sram += 1
                        self.sram_reads += 1
                    if d:
                        t[way] = -1
                else:                                         # GetX
                    if d:
                        self.hits += 1
                        if way < NW:
                            self.hits_nvm += 1
                            self.nvm_reads += 1
                        else:
                            self.hits_sram += 1
                            self.sram_reads += 1
                    else:
                        self.misses += 1
                    t[way] = -1
            else:
                self.misses += 1

            if progress is not None and (i & 0xFFFFF) == 0:
                progress(i, n)

    # -------------------------------------------------------------- allocation
    def allocate(self, cs, addr, now, is_dirty):
        pol = self.policy
        way = pol.choose_way(cs, addr, is_dirty) if pol is not None else self.lru(cs, range(self.ways))
        if way < 0:
            return
        self.tag[cs][way] = addr
        self.mru[cs][way] = now
        self.dirty[cs][way] = is_dirty
        self.writes[cs][way] += 1
        if way < self.nvm_ways:
            self.nvm_writes += 1
        else:
            self.sram_writes += 1
        if pol is not None:
            pol.on_write(cs, way)

    def charge_write(self, cs, way):
        """Account a write that the wear-levelling mechanism itself performs
        (line migration / swap traffic). Wear-levelling papers are routinely
        criticised for omitting this; it is counted here."""
        self.writes[cs][way] += 1
        self.migration_writes += 1
        if way < self.nvm_ways:
            self.nvm_writes += 1
        else:
            self.sram_writes += 1

    def set_recency(self, cycles):
        """Stamp recency with the trace's cycle column instead of the record
        index.

        The official HyCSim model uses the cycle. Real gem5 traces emit many
        records on the same cycle, so LRU ties are common there and HyCSim
        resolves them by lowest way index; stamping by record index never ties
        and is the better ordering, which is why it is the default. This hook
        exists so the bit-exact conformance test can reproduce the reference
        exactly rather than to within two frames - see
        tests/test_hycsim_conformance.py.
        """
        self._recency = np.asarray(cycles)
        return self

    def add_spare_set(self):
        """Append one physical set beyond the indexed range, for a mechanism
        that needs a spare location (Start-Gap's GapLine).

        The paper's PCM system takes the GapLine from the spare pool; a cache
        has no spare pool, so the storage is *added* rather than stolen from the
        indexed sets. Adding it keeps the address mapping an exact bijection -
        folding a logical set away instead would alias two set indices together
        and charge Start-Gap a hit-rate loss it does not have. The extra set
        wears like any other and stays in the wear array, and the policy charges
        its area in `state_bits`.

        Returns the index of the spare set. Idempotent per LLC instance.
        """
        if getattr(self, '_spare_index', None) is not None:
            return self._spare_index
        W = self.ways
        self.tag.append([-1] * W)
        self.mru.append([0] * W)
        self.dirty.append([0] * W)
        self.writes.append([0] * W)
        self._spare_index = len(self.tag) - 1
        self.spare_sets = getattr(self, 'spare_sets', 0) + 1
        return self._spare_index

    def move_set(self, src, dst):
        """Physically relocate the contents of set `src` into set `dst`,
        charging one write per valid frame moved. Returns frames moved."""
        moved = 0
        for j in range(self.ways):
            if self.tag[src][j] != -1:
                self.tag[dst][j] = self.tag[src][j]
                self.mru[dst][j] = self.mru[src][j]
                self.dirty[dst][j] = self.dirty[src][j]
                self.tag[src][j] = -1
                self.charge_write(dst, j)
                moved += 1
        return moved

    def swap_sets(self, a, b):
        """Swap the contents of two sets, charging the migration writes."""
        moved = 0
        for j in range(self.ways):
            ta, tb = self.tag[a][j], self.tag[b][j]
            self.tag[a][j], self.tag[b][j] = tb, ta
            self.mru[a][j], self.mru[b][j] = self.mru[b][j], self.mru[a][j]
            self.dirty[a][j], self.dirty[b][j] = self.dirty[b][j], self.dirty[a][j]
            if ta != -1:
                self.charge_write(b, j); moved += 1
            if tb != -1:
                self.charge_write(a, j); moved += 1
        return moved

    def lru(self, cs, cand):
        tag, mru = self.tag[cs], self.mru[cs]
        victim, best = -1, None
        for j in cand:
            if tag[j] == -1:
                return j
            m = mru[j]
            if best is None or m < best:
                best, victim = m, j
        return victim

    # ----------------------------------------------------------------- metrics
    def wear_array(self, nvm_only=True):
        w = np.asarray(self.writes, dtype=np.float64)
        return w[:, :self.nvm_ways] if nvm_only else w

    def stats(self):
        acc = self.hits + self.misses
        return dict(hits=self.hits, misses=self.misses,
                    hit_rate=self.hits / acc if acc else 0.0,
                    hits_nvm=self.hits_nvm, hits_sram=self.hits_sram,
                    nvm_reads=self.nvm_reads, sram_reads=self.sram_reads,
                    nvm_writes=self.nvm_writes, sram_writes=self.sram_writes,
                    migration_writes=self.migration_writes,
                    flushes=self.flushes, remaps=self.remaps)
