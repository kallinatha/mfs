#!/usr/bin/env python3
"""TailGuard - placement by marginal Weibull hazard on endurance-normalised damage.

Ported from `stage5_champsim/module/psc_family.cc`, case `policy_t::TAILGUARD`,
so the two simulators run the same mechanism under different protocols. Until
now this policy existed only in ChampSim, which meant it had never been measured
in the regime where every charged write is an allocation.

The idea, and why it is not just another wear-leveller
-----------------------------------------------------
Every other policy here places by *write count*: put the line in the way that
has been written least. That is correct only if all frames fail at the same
write count. They do not — process variation gives each cell its own endurance,
and a hot bank burns its budget faster. So a frame with a weak endurance bin can
be the closest to failure while having one of the lowest raw write counts, and
a write-count leveller will keep choosing it.

TailGuard places by the *marginal hazard* of the write instead:

    hazard(w) = (z_w + dz_w)^kappa - z_w^kappa

where `z_w` is the frame's accumulated endurance-normalised damage and `dz_w`
the damage this write would add. With kappa > 1 an already-damaged frame is
superlinearly more expensive to write, so the policy steers away from the tail
rather than merely flattening the mean. The objective is tail risk, which is
also why the reported statistic is CVaR and not only peak.

Cost function, verbatim from the C++:

    cost(w) = lambda_r * hazard(w) + lambda_wa * (i / k)

`i` is the candidate's rank by recency among the `k` least-recently-used NVM
ways, so `i/k` is 0 for the coldest. The second term is what stops the policy
from evicting a hot line purely because its frame is healthy; without it,
TailGuard trades hit rate for wear and finds the degenerate optimum that every
wear-only objective has.

Protocol note - what the trigger maps to here
---------------------------------------------
In ChampSim the decision point is `find_victim`, reached on both allocations and
the write-hit path. This replay follows HyCSim's `BaseHybrid`, which is
near-exclusive: the LLC is filled only by an L2 writeback that misses, a
writeback to a resident block performs no data write, and `GetS` on a dirty
block and `GetX` both invalidate. Every charged write is therefore an
**allocation**, and TailGuard's decision point here is `choose_way` alone.

That is not a weakened port, it is the same decision at the only point this
protocol offers. But it does mean the two simulators exercise different amounts
of it: on `619.lbm` ChampSim sees 37,083 LLC write hits against 23,515
allocations, while the replay sees essentially no chargeable write hits at all.
Report both and say which protocol produced which number.

The damage model is off by default (`stage3_hycsim/damage.py`), in which case
`dz` is exactly 1.0 for every frame and hazard reduces to a monotone function of
accumulated writes - TailGuard then behaves like a hazard-weighted LRU. Enabling
`endurance_cv` or `thermal` is what makes it a distinct mechanism, so sweep at
least `endurance_cv` before reporting a TailGuard row.
"""
from .base import Policy


class TailGuard(Policy):
    name = 'tailguard'
    PARAMS = dict(
        kappa=2.0,        # Weibull shape; 1.0 = linear wear, no tail pressure
        lambda_r=1.0,     # weight on hazard
        lambda_wa=0.1,    # weight on recency; 0 finds the degenerate optimum
        candidates=4,     # how many of the LRU ways to consider
        alpha=0.99,       # CVaR tail reported in stats
    )

    def setup(self):
        llc = self.llc
        self.M = llc.nvm_ways
        self.actions = 0
        self.invalid_taken = 0
        self.hazard_choices = 0
        # A local damage view is used when no model is attached, so the policy
        # is always well defined. With no model this is all ones, dz = 1.0, and
        # the mechanism degenerates to hazard-weighted LRU - which is stated in
        # the docstring rather than hidden.
        self._local = None

    def _dm(self):
        dm = getattr(self.llc, 'damage_model', None)
        if dm is not None:
            return dm
        if self._local is None:
            from damage import DamageModel
            self._local = DamageModel(self.llc.sets, self.llc.ways, self.M)
        return self._local

    def state_bits(self):
        # Accumulated damage per NVM frame. The endurance bin is a physical
        # property of the cell, not stored state; the C++ keeps it in a table
        # only because a simulator must materialise what silicon just has.
        return self.llc.sets * self.M * 16

    def choose_way(self, cs, addr, is_dirty):
        llc = self.llc
        p = self.params
        dm = self._dm()

        # An invalid frame anywhere is free capacity and always wins.
        for j in range(llc.ways):
            if llc.tag[cs][j] == -1:
                self.invalid_taken += 1
                return j
        # Candidates: the k least-recently-used NVM ways, coldest first.
        mru = llc.mru[cs]
        order = sorted(range(self.M), key=lambda j: mru[j])
        k = min(int(p['candidates']), self.M)
        if k <= 0:
            return llc.lru(cs, range(llc.ways))

        best, bcost = -1, 0.0
        for i in range(k):
            w = order[i]
            z = float(dm.damage[cs][w])
            dz = dm.damage_per_write(cs, w)
            hazard = (z + dz) ** p['kappa'] - z ** p['kappa']
            recency = float(i) / float(k)          # 0 = coldest
            cost = p['lambda_r'] * hazard + p['lambda_wa'] * recency
            if best < 0 or cost < bcost:
                best, bcost = w, cost
        self.actions += 1
        self.hazard_choices += 1
        if best < 0:
            return llc.lru(cs, range(llc.ways))
        return best
