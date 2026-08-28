#!/usr/bin/env python3
"""Novel wear-management variants, designed from the measured behaviour of the
original mechanisms rather than by analogy.

Three empirical facts drove these designs, all measured on the 1 MB and 16 MB
runs before any of this was written:

  1. The mu channel dominates.  PSC's whole benefit comes from removing write
     traffic from the NVM partition (mu 830 -> 425, peak writes 1224 -> 651).
     Nothing that leaves mu alone gets close.
  2. Restriction backfires.  Confining allocations to (M-R) of M ways
     concentrates the remaining traffic instead of spreading it: VRO alone
     measured -24.2% RLI on the hybrid array, with write amplification 1.35.
  3. Non-restrictive intra-set levelling is nearly free.  EqualChance cut IntraV
     from 5.56% to 0.43% at 0.8% write amplification, simply by *steering*
     placement rather than *forbidding* ways.

So: keep the steering, drop the restriction, and attack mu with a better signal
than the dirty bit.

Storage cost is modelled explicitly by `state_bits()` on every policy, because
the manuscript's competitive claim is a storage-overhead claim.
"""
from .base import Policy
from .psc_vro import PSC


# --------------------------------------------------------------------------
# Intra-set: VRO re-specified as Variation-Ranked Ordering
# --------------------------------------------------------------------------
class VRORank(Policy):
    """VRO, same name, different mechanism.

    Original VRO designates (M-R) of M NVM ways as write-ways and rotates the
    window on a saturating counter.  That is a *restriction*: during any epoch,
    2/3 of the partition is unavailable to writes, so the writes pile into the
    remaining third.

    VRO-Rank keeps the intent - move wear around the partition - and drops the
    restriction.  Every NVM way stays allocatable at all times.

    The first version of this policy picked the globally least-worn way in the
    set and cost 8 points of hit rate: recency was being ignored, so live blocks
    were evicted to protect cold frames.  The fix is to make the wear decision
    *inside* the replacement decision rather than instead of it.  The `pool`
    least-recently-used NVM ways are the ways LRU already considers disposable;
    among only those, the least-worn is chosen.  Hit rate is preserved because
    the victim is always an LRU-plausible one, and wear still migrates because
    which of the plausible victims gets taken is decided by wear.

    It also costs less in hardware than picking a global minimum: recency order
    is already maintained by the replacement policy, and the added work is a
    minimum over `pool` coarse counters, not over all M.

    Wear is tracked in `bits`-bit saturating counters per NVM frame, halved
    whenever any counter saturates, so state is bounded.  The intervention only
    fires when (max - min) >= trigger, a comparator on `bits` bits.
    """
    name = 'vro-rank'
    PARAMS = dict(bits=4, trigger=4, pool=4)

    def setup(self):
        llc = self.llc
        self.M = llc.nvm_ways
        self.cap = (1 << self.params['bits']) - 1
        self.c = [[0] * self.M for _ in range(llc.sets)]
        self.interventions = 0

    def state_bits(self):
        return self.llc.sets * self.M * self.params['bits']

    def _rank_way(self, cs):
        """Least-worn among the `pool` least-recently-used NVM ways.

        Returns -1 when the set is already balanced, in which case the caller
        falls back to plain LRU and nothing is perturbed.
        """
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
                return j                       # invalid frame: free, take it
        pool = sorted(range(self.M), key=lambda j: mru[j])[:self.params['pool']]
        best, bw = -1, None
        for j in pool:
            if bw is None or c[j] < bw:
                bw, best = c[j], j
        self.interventions += 1
        return best

    def choose_way(self, cs, addr, is_dirty):
        w = self._rank_way(cs)
        if w >= 0:
            return w
        return self.llc.lru(cs, range(self.llc.ways))

    def on_write(self, cs, way):
        if way >= self.M:
            return
        c = self.c[cs]
        c[way] += 1
        if c[way] >= self.cap:
            for j in range(self.M):
                c[j] >>= 1


# --------------------------------------------------------------------------
# mu channel: predict write intensity instead of trusting the dirty bit
# --------------------------------------------------------------------------
class PSCPred(Policy):
    """PSC with a write-intensity predictor.

    The dirty bit is a one-shot signal: it says this particular writeback is a
    write, not that this address is *repeatedly* written.  A block written once
    and then read many times is steered into the small SRAM partition for no
    lifetime benefit, evicting a genuinely hot block.

    PSC-Pred keeps a direct-mapped signature table of `entries` saturating
    counters, indexed by a hash of the block address.  An address whose counter
    has reached `threshold` is treated as write-intensive and steered to SRAM;
    everything else goes to NVM.  The dirty bit is still used while the entry is
    cold, so behaviour degrades gracefully to plain PSC.

    Storage is one table for the whole cache, not per set.
    """
    name = 'psc-pred'
    PARAMS = dict(entries=4096, bits=2, threshold=2,
                  decay_interval=100000, max_sram_frac=0.45)

    def setup(self):
        self.tab = bytearray(self.params['entries'])
        self.cap = (1 << self.params['bits']) - 1
        self.mask = self.params['entries'] - 1
        assert self.params['entries'] & self.mask == 0, 'entries must be a power of two'
        self.steered = 0
        self.alloc = 0
        self.dctr = 0

    def state_bits(self):
        return self.params['entries'] * self.params['bits']

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
        """Guard against the degenerate optimum.

        RLI rewards *not writing to NVM at all*: a policy that steers everything
        into the SRAM ways drives peak NVM writes towards zero and scores an
        enormous lifetime gain while turning a 16-way cache into a 4-way one.
        This cap bounds the share of allocations that may be steered, so the NVM
        partition keeps carrying capacity.  The guard is a counter comparison,
        not a search.
        """
        if self.alloc < 1000:
            return True
        return (self.steered / self.alloc) < self.params['max_sram_frac']

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        nvm, ways = llc.nvm_ways, llc.ways
        self.alloc += 1
        if is_dirty:
            self.note_write(addr)
        if llc.sram_ways and self.hot(addr) and self.sram_ok():
            w = llc.lru(cs, range(nvm, ways))
            if w >= 0:
                self.steered += 1
                return w
        w = llc.lru(cs, range(nvm))
        if w >= 0:
            return w
        return llc.lru(cs, range(nvm, ways)) if llc.sram_ways else -1


# --------------------------------------------------------------------------
# Combination: better mu signal + non-restrictive intra-set ranking
# --------------------------------------------------------------------------
class PSCVRORank(PSCPred):
    """PSC-Pred for the mu channel, VRO-Rank for the intra-set channel.

    These two are complements by construction rather than by hope: one decides
    *which partition* a block lands in, the other decides *which way inside the
    NVM partition* it lands in.  They act on disjoint decisions, so neither can
    starve the other the way PSC and window-restricted VRO do.
    """
    name = 'psc-vro-rank'
    PARAMS = dict(entries=4096, bits=2, threshold=2,
                  decay_interval=100000, max_sram_frac=0.45,
                  wear_bits=4, trigger=4, pool=4)

    def setup(self):
        PSCPred.setup(self)
        llc = self.llc
        self.M = llc.nvm_ways
        self.wcap = (1 << self.params['wear_bits']) - 1
        self.c = [[0] * self.M for _ in range(llc.sets)]
        self.interventions = 0

    def state_bits(self):
        return (PSCPred.state_bits(self) +
                self.llc.sets * self.M * self.params['wear_bits'])

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
        if llc.sram_ways and self.hot(addr) and self.sram_ok():
            w = llc.lru(cs, range(nvm, ways))
            if w >= 0:
                self.steered += 1
                return w
        w = self._rank_way(cs)
        if w >= 0:
            return w
        w = llc.lru(cs, range(nvm))
        if w >= 0:
            return w
        return llc.lru(cs, range(nvm, ways)) if llc.sram_ways else -1

    def on_write(self, cs, way):
        if way >= self.M:
            return
        c = self.c[cs]
        c[way] += 1
        if c[way] >= self.wcap:
            for j in range(self.M):
                c[j] >>= 1


# --------------------------------------------------------------------------
# Inter-set: measured wear-driven swapping instead of blind rotation
# --------------------------------------------------------------------------
class WearSwap(Policy):
    """Inter-set levelling driven by measured wear.

    Start-Gap and i2WAP's Swap-Shift rotate the set mapping blindly: they move
    lines whether or not the source set is actually hot.  Measured here, both
    cost more in migration writes than they return (Start-Gap: +6.4% write
    amplification, -3.7% RLI on the hybrid array).

    WearSwap only acts when it has evidence.  Coarse per-set wear counters are
    kept; every `interval` NVM writes, the hottest set seen in a sampled window
    is swapped with the coldest, and the migration writes are charged.  Sampling
    keeps the search cost constant instead of O(sets).

    **Defect fixed here, and it is the fourth instance of the same bug.**
    The previous version called `llc.swap_sets(hot, cold)`, which moves set
    *contents* between two physical sets, but never set `does_remap` and never
    overrode `remap_set`.  So the address of every moved block still indexed to
    its original set: the blocks became unreachable, the policy paid the full
    migration tax, and it delivered no levelling whatsoever.  That is precisely
    the failure documented in `startgap.py`'s post-mortem of its own v2, and it
    has now appeared in Start-Gap, i2WAP's Swap-Shift, ELBA's buffer and here.
    The lesson each time: *moving data is not remapping addresses*, and a
    mechanism that does the first without the second is a pure cost.

    The fix follows the pattern `startgap.py` established - a real index remap.
    A permutation table `perm` maps logical set -> physical set; a swap
    exchanges two entries, which is a genuine bijection, and the data movement
    then keeps the array consistent with it.  `does_remap` is set so `llc.run`
    actually consults it.  Two invariants are asserted in the conformance
    suite: `perm` stays a permutation, and no live block is left unreachable.

    Note the counters stay attached to **physical** sets and are NOT swapped
    with the mapping.  Wear does not travel with the data - it is a property of
    the cells - and the old code's counter swap was only self-consistent
    because it had no remap at all.
    """
    name = 'wear-swap'
    does_remap = True
    PARAMS = dict(interval=20000, bits=8, sample=64)

    def setup(self):
        llc = self.llc
        self.S = llc.sets
        self.cap = (1 << self.params['bits']) - 1
        self.sc = [0] * self.S
        self.ctr = 0
        self.cursor = 0
        self.swaps = 0
        # logical set -> physical set. Identity until the first swap.
        self.perm = list(range(self.S))

    def remap_set(self, cs):
        return self.perm[cs]

    def state_bits(self):
        import math
        # per-set wear counters plus the permutation table, which is the real
        # cost of an inter-set remap and is what Start-Gap avoids by using two
        # registers and an algebraic mapping instead of a table.
        idx_bits = int(math.ceil(math.log(max(2, self.S), 2)))
        return self.llc.sets * (self.params['bits'] + idx_bits)

    def on_write(self, cs, way):
        if way >= self.llc.nvm_ways:
            return
        s = self.sc
        s[cs] += 1
        if s[cs] >= self.cap:
            for i in range(self.S):
                s[i] >>= 1
        self.ctr += 1
        if self.ctr < self.params['interval']:
            return
        self.ctr = 0
        n = self.params['sample']
        hot = cold = self.cursor
        hv = cv = s[self.cursor]
        for k in range(1, n):
            i = (self.cursor + k * 7919) % self.S
            v = s[i]
            if v > hv:
                hv, hot = v, i
            if v < cv:
                cv, cold = v, i
        self.cursor = (self.cursor + n) % self.S
        if hot != cold and hv - cv >= 2:
            # Exchange the MAPPING, then move the data so the array agrees with
            # it. Doing only the second half is the defect described above.
            a, b = self.perm.index(hot), self.perm.index(cold)
            self.perm[a], self.perm[b] = self.perm[b], self.perm[a]
            self.llc.swap_sets(hot, cold)
            # counters are NOT swapped: wear belongs to the cells, not the data
            self.swaps += 1
            self.llc.remaps += 1


class Full(PSCVRORank):
    """All three channels: predicted steering, ranked intra-set placement,
    measured inter-set swapping."""
    name = 'full'
    does_remap = True          # it inherits WearSwap's inter-set swap
    PARAMS = dict(entries=4096, bits=2, threshold=2,
                  decay_interval=100000, max_sram_frac=0.45,
                  wear_bits=4, trigger=4, pool=4,
                  interval=20000, set_bits=8, sample=64)

    def setup(self):
        PSCVRORank.setup(self)
        self.S = self.llc.sets
        self.scap = (1 << self.params['set_bits']) - 1
        self.sc = [0] * self.S
        self.sctr = 0
        self.cursor = 0
        self.swaps = 0
        # Same permutation state WearSwap needs. Without it `full` raised
        # AttributeError on its first swap - it had never actually executed the
        # inter-set channel it claims to compose.
        self.perm = list(range(self.S))

    def remap_set(self, cs):
        return self.perm[cs]

    def state_bits(self):
        import math
        idx_bits = int(math.ceil(math.log(max(2, self.llc.sets), 2)))
        return (PSCVRORank.state_bits(self) +
                self.llc.sets * (self.params['set_bits'] + idx_bits))

    def on_write(self, cs, way):
        PSCVRORank.on_write(self, cs, way)
        if way >= self.M:
            return
        s = self.sc
        s[cs] += 1
        if s[cs] >= self.scap:
            for i in range(self.S):
                s[i] >>= 1
        self.sctr += 1
        if self.sctr < self.params['interval']:
            return
        self.sctr = 0
        n = self.params['sample']
        hot = cold = self.cursor
        hv = cv = s[self.cursor]
        for k in range(1, n):
            i = (self.cursor + k * 7919) % self.S
            v = s[i]
            if v > hv:
                hv, hot = v, i
            if v < cv:
                cv, cold = v, i
        self.cursor = (self.cursor + n) % self.S
        if hot != cold and hv - cv >= 2:
            # Exchange the MAPPING, then move the data so the array agrees with
            # it. Doing only the second half is the defect described above.
            a, b = self.perm.index(hot), self.perm.index(cold)
            self.perm[a], self.perm[b] = self.perm[b], self.perm[a]
            self.llc.swap_sets(hot, cold)
            # counters are NOT swapped: wear belongs to the cells, not the data
            self.swaps += 1
            self.llc.remaps += 1
