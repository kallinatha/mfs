#!/usr/bin/env python3
"""PSC-Guard: PSC with pressure-adaptive dirty-partition admission.

WHY THIS POLICY EXISTS
----------------------
Plain PSC steers every dirty fill to the SRAM partition.  Measured over the
ten SUSCOM mixes (`tuning_all_mixes.csv`, run 2026-08-21) that rule is
bimodal, not uniformly good:

    mix                                    hit_rate_delta   rli_direct
    lbm17xz17GemsFDTDwrf                        -0.2289          3.43
    gobmkmilcmcf17lbm17                         -0.2207          7.43
    hmmerbzip2wrfroms17                         -0.1585         11.00
    zeusmpgobmkdealIIbzip2                      -0.0416         47.67
    zeusmpcactuBSSN17hmmersoplex                -0.0276         30.75
    xalancbmkleslie3dbwaves17mcf17              -0.0050          4.07
    cactuBSSN17dealIIlibquantumxalancbmk        -0.0019          8.22
    GemsFDTDomnetpproms17leslie3d               -0.0012         10.83
    xz17astarbwaves17soplex                     -0.0001         62.75
    omnetppastarmilclibquantum                  +0.0001         20.45

Five of ten mixes are already inside the 0.005 acceptance guard while
returning 4x-63x lifetime.  The mean is destroyed by the top three rows, and
both of the worst two contain `lbm17` (SPEC lbm: a streaming stencil with
sustained dirty-writeback bandwidth).  The mechanism of the failure is
saturation, not footprint: `profile_writes.json` measures a burst working set
of p95 = 21,940 blocks against 65,536 SRAM frames at 16 MB, so on average the
dirty set fits.  What does not fit is the *rate*.  Under sustained dirty
fills the SRAM partition becomes 100% dirty churn, clean read-reuse blocks
can never stay resident there, and the 12 NVM ways must absorb the entire
mixed working set.  Hit rate collapses by 22-25 points.

PSC-Guard closes a feedback loop around that saturation.  It leaves PSC's
placement rule untouched below an occupancy threshold and thins dirty
admission proportionally above it, so the partition provably cannot saturate.

THE MECHANISM, AND WHY THE OBVIOUS CONTROLLER DOES NOT WORK
-----------------------------------------------------------
The first design tried here was a global negative-feedback thinner: measure
the fraction u of SRAM frames holding dirty-fill blocks through an EWMA, and
above a threshold theta admit dirty fills only with probability
(1-u)/(1-theta).  It was implemented, and it failed its own test: on a
sustained all-dirty fill stream the occupancy still reached u = 0.996.

The reason is worth stating because it is the real defect in PSC's
specification, not an artefact of the controller.

  1. In this replay model `LLC.allocate` is reached only from the `o == 2`
     (L2_Rpl) branch of `LLC.run`, and only on a miss.  The fill stream is
     writeback-only: nothing else ever installs a block.
  2. PSC's companion clause sends every clean fill to the NVM partition, so
     no clean block may ever occupy an SRAM frame.
  3. Therefore, once the SRAM partition is fully dirty, each further dirty
     fill evicts a dirty line and installs another -- net change zero -- and
     nothing at all decreases the dirty count.

Occupancy is a ratchet, not a queue, and u = 1 is an absorbing state.  A
thinner can stop u rising; it can never bring u down.  The stability
argument for the linear back-off controller assumed a drain delta(u) > 0;
under PSC delta(u) is identically zero.  Worse, in the saturated regime the
thinner does the wrong thing: with u = 1 it drives p_admit to 0 and sends
every dirty fill to NVM, which abandons the SRAM partition exactly when the
write pressure that justifies having it is highest.

The variable that actually collapsed on the lbm17 mixes is not global dirty
occupancy.  It is whether a clean, read-reused block can find a home.  The
measured symptom is a 22-25 point hit-rate loss with the *dirty* set fitting
comfortably (`profile_writes.json`: burst working set p95 = 21,940 blocks
against 65,536 SRAM frames, 0.33x).  Capacity was never the problem;
exclusion was.

So PSC-Guard reserves, in every set, at least one SRAM way that dirty fills
may not take:

    d(s)  = number of SRAM ways in set s currently holding a dirty block
    D_max = max(1, min(K-1, floor(theta * K)))        K = sram_ways

    dirty fill  -> SRAM if d(s) < D_max, else the NVM path (PSC's fallback)
    clean fill  -> LRU over all ways (the clean-ban is lifted)

Two invariants follow immediately and are asserted by the tests:

  * d(s) <= D_max for every set at every instant, by construction.  With
    K = 4 and theta = 0.75, D_max = 3: one SRAM way per set is always
    reachable by a clean block, whatever the write pressure.  The failure
    mode is bounded, not merely discouraged.
  * theta >= 1 makes D_max = K-1... which is still a reservation, so the
    exact PSC ablation is a separate flag rather than a theta value; see
    `reserve=0` below, which restores plain PSC bit-for-bit.

WHY PER-SET AND NOT A GLOBAL FRACTION.  `psc-balance` already caps the
*global* SRAM share (`max_sram_frac`).  A global cap does not prevent a
single hot set from being fully dirty while the average looks healthy, and
the measured collapse is a per-set phenomenon: `profile_writes.json` reports
the hottest set holding 7 burst blocks against 4 SRAM ways per set.  The
reservation must therefore be per-set to bind where the damage happens.

HARDWARE COST: ZERO ADDED STATE.  d(s) is a popcount over the dirty bits of
the K SRAM ways of the set being probed.  Those dirty bits already exist in
every cache tag array -- they are what decides whether an eviction writes
back.  The policy adds a K-input popcount and a comparator to the victim
path and no architectural state whatsoever, so `state_bits()` returns 0 and
`state_overhead_pct` is exactly zero.  This matters for the paper: the fix
for a 22-point hit-rate collapse costs nothing to store.

Zero migration writes: every decision is taken at allocation time, so the
accounting invariant sum(per-frame writes) == nvm_writes + sram_writes holds
unchanged, and `llc.migration_writes` stays 0.

RELATION TO THE OTHER REPAIR.  `psc-soft` removes PSC's clean-ban
unconditionally and is the repair indicated by the ChampSim measurement,
where the dirty->SRAM arm almost never fires because LLC writebacks hit
rather than allocate.  PSC-Guard bounds the dirty arm instead.  They address
opposite ends of the same rule and are reported separately.
"""

from .psc_vro import PSC


class PSCGuard(PSC):
    """PSC with a per-set clean-block reservation in the SRAM partition.

    theta    fraction of the SRAM ways per set that dirty fills may occupy.
             D_max = max(1, min(K-1, floor(theta*K))), so at least one way
             per set is always reachable by a clean block.
    reserve  0 disables the reservation entirely and restores plain PSC
             bit-for-bit (the ablation control).
    """

    name = 'psc-guard'
    PARAMS = dict(theta=0.75, reserve=1)

    def setup(self):
        super(PSCGuard, self).setup()
        llc = self.llc
        K = llc.sram_ways
        self.K = K
        if K and int(self.params['reserve']):
            self.d_max = max(1, min(K - 1, int(self.params['theta'] * K)))
        else:
            self.d_max = K                      # no reservation: plain PSC
        self.pg_admitted = 0
        self.pg_rejected = 0                    # dirty fills pushed to NVM
        self.pg_drained = 0                     # clean fills that took SRAM
        self.pg_d_max_hits = 0                  # times the reservation bound

    # ---------------------------------------------------------------- probe
    def _dirty_in_sram_set(self, cs):
        llc = self.llc
        tag, dirt = llc.tag[cs], llc.dirty[cs]
        return sum(1 for w in range(llc.nvm_ways, llc.ways)
                   if tag[w] != -1 and dirt[w])

    def _nvm_victim(self, cs):
        """PSC's clean/NVM path, named so the guard can reuse it.

        Deliberately not a copy of PSC.choose_way's NVM branch: duplicated
        victim logic is how two paths silently drift apart.
        """
        llc = self.llc
        w = llc.lru(cs, range(llc.nvm_ways))
        if w >= 0:
            return w
        return llc.lru(cs, range(llc.nvm_ways, llc.ways)) if llc.sram_ways else -1

    # --------------------------------------------------------------- policy
    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        if not llc.sram_ways or self.d_max >= self.K:
            return super(PSCGuard, self).choose_way(cs, addr, is_dirty)

        if is_dirty:
            d = self._dirty_in_sram_set(cs)
            if d >= self.d_max:
                # The reservation binds.  Note a dirty SRAM way may still be
                # chosen as the LRU victim *within* the dirty allowance; what
                # is forbidden is growing d(s) past d_max, and replacing a
                # dirty way leaves d(s) unchanged.  Only a fill that would
                # occupy a *clean or invalid* SRAM way is diverted.
                w = llc.lru(cs, range(llc.nvm_ways, llc.ways))
                if w >= 0 and llc.tag[cs][w] != -1 and llc.dirty[cs][w]:
                    self.pg_admitted += 1
                    return w
                self.pg_d_max_hits += 1
                self.pg_rejected += 1
                return self._nvm_victim(cs)
            self.pg_admitted += 1
            return super(PSCGuard, self).choose_way(cs, addr, is_dirty)

        # Clean fill: the ban is lifted, ordinary LRU over the whole set.
        w = llc.lru(cs, range(llc.ways))
        if w < 0:
            return super(PSCGuard, self).choose_way(cs, addr, is_dirty)
        if w >= llc.nvm_ways:
            self.pg_drained += 1
        return w

    # ------------------------------------------------------------- metadata
    def state_bits(self):
        """Zero added architectural state.

        d(s) is a popcount over dirty bits that every cache tag array already
        maintains (they decide writeback on eviction).  The policy adds
        combinational logic on the victim path and stores nothing.
        """
        return 0
