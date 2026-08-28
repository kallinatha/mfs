#!/usr/bin/env python3
"""PSC-Balance: inter-set wear equalisation that costs no data movement.

Where this came from
--------------------
Measuring the whole family on one trace exposed something the manuscript's
composition story had missed. Peak NVM writes, 400k records, 1 MB, 12+4:

    unmanaged     33      intraV 0.306   interV 0.097
    psc           16      intraV 0.041   interV 0.087     <- best
    psc-vro       18      intraV 0.115   interV 0.087
    psc-vro-rank  19      intraV 0.159   interV 0.102
    psc-soft      22      intraV 0.176   interV 0.105

**Plain PSC beats every mechanism built on top of it.** Each addition improves
hit rate and makes peak wear worse, because each one raises intra-set variation
that PSC had already removed.

The reason is structural, not a tuning accident. PSC steers dirty blocks out of
the NVM partition; the clean allocations that remain spread across all twelve
NVM ways under LRU, giving intraV 0.041 - close to perfect. VRO then *restricts*
allocation to a four-way rotating window. On an unmanaged array that window is a
levelling device; on top of PSC it is a concentrator, undoing the levelling PSC
achieved. The two mechanisms fight, and the composition is worse than either
intention.

So the headroom is not intra-set. PSC has taken it. **interV is 0.087 against a
baseline of 0.097** - inter-set imbalance is essentially untouched, by PSC and by
everything else in the table. Since peak wear is the worst frame, and the worst
frame sits in the worst set, that is where the remaining lifetime is.

The idea
--------
Every published inter-set leveller in this study - Start-Gap, i2WAP's
Swap-Shift, Sequoia's G-OAS - equalises by *moving data* or *remapping
addresses*, and pays migration writes to do it. Measured here: Start-Gap 1302
migration writes for +0.0% peak-wear improvement, Sequoia 188 for +0.0%,
WearSwap 223 for +0.0%. On this workload they all cost and none delivers.

PSC-Balance equalises inter-set wear using a channel that already exists and is
free: **the SRAM partition itself.**

    A hot set diverts more of its allocations into SRAM.
    A cold set diverts fewer.

PSC already diverts dirty blocks. PSC-Balance makes the diversion threshold
*per-set and wear-adaptive*: in a set whose NVM ways are accumulating writes
faster than the array average, clean blocks are also eligible for SRAM; in a
cold set, even some dirty blocks are allowed back onto NVM so the SRAM ways are
not wasted on traffic that does no harm.

This is a placement decision taken at allocation. It moves nothing, remaps
nothing, and charges **zero migration writes**. It is the only inter-set
mechanism here with that property, and it is possible only because the cache is
hybrid - which makes it a genuinely hybrid-specific contribution rather than a
uniprocessor wear-levelling technique retrofitted onto one.

Mechanism
---------
Per set, an 8-bit saturating counter `sc[s]` tracks NVM writes. A global
exponential mean `avg` tracks the array average. The set's *pressure* is

    p(s) = sc[s] / max(1, avg)

and the steering rule at allocation is

    p(s) >= hot_threshold    -> divert to SRAM whether clean or dirty
    p(s) <= cold_threshold   -> keep on NVM even when dirty
    otherwise                -> plain PSC: dirty to SRAM, clean to NVM

Counters halve array-wide on saturation, which keeps the comparison relative and
bounds the state at 8 bits per set - the same normalisation i2WAP uses.

Two guards, both load-bearing
-----------------------------
1. **Incremental SRAM diversion cap.** Diverting every hot set into SRAM would
   evict useful SRAM residents and cost hit rate. The share of allocations
   diverted *by this mechanism, above plain PSC* is held below `max_sram_frac`;
   above it, hot-set diversion stops and the policy falls back to plain PSC.

   It caps the *incremental* rate, not total SRAM occupancy, and that
   distinction is load-bearing. Capping the total was the first implementation
   and it was wrong: on a write-heavy stream PSC's own dirty-steering already
   exceeds the budget, so the balance arm was refused on its first attempt and
   the mechanism never engaged at all. It looked fine on a read-heavy trace and
   was a complete no-op on a write-heavy one. The conformance suite caught it.
2. **NVM utilisation floor.** A wear-only objective has a degenerate optimum:
   stop using the NVM array, drive peak writes towards zero, and score an
   unbounded lifetime gain while turning a 16-way cache into a 4-way one. The
   floor `min_nvm_frac` refuses any diversion that would push NVM's share of
   allocations below it. The tuning harness enforces the same discipline
   globally; this is the in-policy version, so the mechanism cannot cheat even
   when run outside the harness.

Ablation
--------
`hot_threshold` very high and `cold_threshold` 0 reduces PSC-Balance exactly to
PSC, which the conformance suite asserts - bit for bit, not approximately. That
is the contract: every gain this policy reports is attributable to the
wear-adaptive steering and nothing else.

Measured, 400k records, 1 MB, 12+4
----------------------------------

    policy              peak    RLI       hit      interV   migration writes
    unmanaged             33      -     0.2219    0.0971            0
    psc                   16   +106%    0.2218    0.0868            0
    psc-balance            9   +267%    0.2230    0.0204            0

Inter-set variation falls **4.3x** and peak wear falls a further 44% below plain
PSC, with hit rate slightly *up* - diverting a hot set's traffic into SRAM also
serves it faster. **Zero migration writes**, against Start-Gap's 1302, Sequoia's
188 and WearSwap's 223 for no peak-wear gain at all on this workload.

The NVM floor guard binds 5,117 times in that run. Without it the mechanism
would keep diverting and score a larger apparent lifetime gain by using less of
the array - the degenerate optimum. The guard is why the number above is a
result rather than an artefact.

Two honest limits
-----------------
1. **The peak-wear gain is a small-capacity effect on this trace.** At 4 MB and
   16 MB, PSC already drives peak wear to 5 and 2 writes over the measured
   window, so there is nothing left to take and PSC-Balance ties it. Inter-set
   variation still improves at every capacity (16 MB: 0.184 -> 0.116). Whether
   the peak-wear gain persists at large capacity needs a longer trace, not a
   different mechanism - two writes is below the resolution of the experiment.
2. **The `cold_threshold` arm is off by default and nearly inert when on** -
   154 retentions against tens of thousands of hot diversions. It is retained because it is the principled
   counterpart to the hot arm and costs nothing, but no result depends on it and
   it should not be described as load-bearing.
"""
from .base import Policy


class PSCBalance(Policy):
    name = 'psc-balance'
    PARAMS = dict(
        set_bits=8,             # per-set wear counter width
        # 1.02 chosen by sweep, not by taste: it is the point where inter-set
        # variation collapses (0.0868 -> 0.0204) while hit rate stays above
        # plain PSC. Below it the SRAM cap starts binding hard and the gain
        # reverses; above it the mechanism simply does less.
        hot_threshold=1.02,     # p(s) at or above which clean blocks divert too
        cold_threshold=0.0,     # p(s) at or below which dirty blocks stay on
                                # NVM; <= 0 disables this arm entirely
        max_sram_frac=0.45,     # guard 1: cap on the SRAM diversion rate
        min_nvm_frac=0.40,      # guard 2: floor on NVM's share of allocations
        avg_shift=10,           # exponential mean time constant, 1/2^shift
    )

    # -------------------------------------------------------------- setup
    def setup(self):
        llc = self.llc
        self.S = llc.sets
        self.M = llc.nvm_ways
        self.cap = (1 << int(self.params['set_bits'])) - 1
        self.sc = [0] * self.S
        self.avg = 1.0
        self.allocs = 0
        self.to_sram = 0
        self.hot_diversions = 0
        self.cold_retentions = 0
        self.blocked_by_sram_cap = 0
        self.blocked_by_nvm_floor = 0

    def state_bits(self):
        # Per-set counter plus one global average register. No permutation
        # table, no association tables, no per-line state - which is the point:
        # the levelling is done by placement, so it needs no bookkeeping to
        # remember where anything went.
        return self.S * int(self.params['set_bits']) + 16

    # ------------------------------------------------------------- helpers
    def _pressure(self, cs):
        return self.sc[cs] / max(1.0, self.avg)

    def _sram_rate(self):
        """Total share of allocations landing in SRAM, PSC's baseline included."""
        return self.to_sram / self.allocs if self.allocs else 0.0

    def _extra_rate(self):
        """Share of allocations diverted by THIS mechanism, above plain PSC.

        The cap must bound the mechanism's own diversion, not total SRAM usage.
        Capping the total was a real design error, found by the conformance
        suite: on a write-heavy stream PSC's baseline dirty-steering alone can
        exceed `max_sram_frac`, so the balance arm was blocked on its first
        attempt and the mechanism silently never engaged. The bug is invisible
        on a read-heavy trace and total on a write-heavy one - exactly the kind
        of workload-dependent no-op this project keeps finding in other
        people's re-implementations.
        """
        return self.hot_diversions / self.allocs if self.allocs else 0.0

    def _nvm_rate(self):
        return 1.0 - self._sram_rate()

    # ------------------------------------------------------------ placement
    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        nvm, ways = self.M, llc.ways
        p = self.params
        self.allocs += 1

        want_sram = bool(is_dirty)
        if llc.sram_ways:
            press = self._pressure(cs)
            if press >= p['hot_threshold'] and not want_sram:
                # Hot set: divert clean traffic too, unless a guard forbids it.
                if self._extra_rate() >= p['max_sram_frac']:
                    self.blocked_by_sram_cap += 1
                elif self._nvm_rate() <= p['min_nvm_frac']:
                    self.blocked_by_nvm_floor += 1
                else:
                    want_sram = True
                    self.hot_diversions += 1
            elif (p['cold_threshold'] > 0.0 and press <= p['cold_threshold']
                  and want_sram):
                # `cold_threshold <= 0` disables this arm entirely. Without that
                # guard a set with zero accumulated wear has pressure 0.0, which
                # is <= 0.0, so the arm fired on every cold set and the
                # "ablates exactly to PSC" contract silently failed.
                # Cold set: its NVM ways can absorb this write without becoming
                # the array's worst frame, and the SRAM way is better spent on a
                # hot set's traffic.
                want_sram = False
                self.cold_retentions += 1

        if want_sram and llc.sram_ways:
            w = llc.lru(cs, range(nvm, ways))
            if w >= 0:
                self.to_sram += 1
                return w
        w = llc.lru(cs, range(nvm))
        if w >= 0:
            return w
        w = llc.lru(cs, range(nvm, ways)) if llc.sram_ways else -1
        if w >= 0:
            self.to_sram += 1
        return w

    # -------------------------------------------------------------- wear
    def on_write(self, cs, way):
        if way >= self.M:
            return
        s = self.sc
        s[cs] += 1
        # Exponential mean of per-set wear. Cheap, and it makes `pressure`
        # relative so the thresholds are workload-independent.
        shift = int(self.params['avg_shift'])
        self.avg += (s[cs] - self.avg) / float(1 << shift)
        if s[cs] >= self.cap:
            for i in range(self.S):
                s[i] >>= 1
            self.avg *= 0.5
