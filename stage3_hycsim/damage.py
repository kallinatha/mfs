#!/usr/bin/env python3
"""Endurance-normalised damage: what a write actually costs a frame.

Every wear metric in this project until now counted **writes**. That is the
right primary metric and it stays the primary metric, but it rests on an
assumption that is false in a real array: that every frame fails at the same
write count. It does not. Process variation gives each cell a different
endurance, and a hot bank consumes its budget faster than a cold one. A policy
that perfectly levels raw write counts is not levelling *damage* when endurance
itself varies — it is levelling the wrong quantity.

This module is the Python counterpart of the damage model in
`stage5_champsim/module/psc_family.cc`. The definitions match line for line, so
a damage number from the replay and one from ChampSim mean the same thing.

Three terms, all **off by default**:

  endurance bins   lognormal per frame, coefficient of variation `endurance_cv`.
                   sigma = sqrt(ln(1+cv^2)), mu = -sigma^2/2, clamped at 0.05,
                   so the distribution has unit mean. cv=0 gives every frame
                   endurance 1.0.
  thermal          bank temperature evolves as a discrete RC step driven by
                   write activity, and damage is accelerated by an Arrhenius
                   factor exp((Ea/k)(1/T_amb - 1/T)).
  hazard           Weibull: failure probability as a function of accumulated
                   damage, shape `kappa`.

**The identity that keeps every earlier result valid.** With `endurance_cv=0`
and `thermal=False`, `damage_per_write` returns exactly 1.0, so accumulated
damage equals the write count, bit for bit. `tests/test_policy_conformance.py`
asserts it. Turning either term on is what converts "level the writes" into
"level the damage", and that is a different objective with a different winner.

Why CVaR
--------
Peak wear is the alpha -> 0 limit of a tail statistic: it is whatever the single
unluckiest frame did, and it moves when one frame moves. CVaR at alpha is the
mean of the worst (1-alpha) fraction, so it describes the shape of the tail
rather than its single extreme point. For a lifetime claim that is the more
honest statistic, and it is far less noisy across mixes. Both are reported;
neither replaces the other.

A caveat stated rather than buried: the C++ draws its bins with `mt19937_64`
and this module uses NumPy's generator. The *distribution* is identical and the
draws are reproducible within each simulator, but the two will not produce
frame-for-frame identical endurance maps. Compare distributions and aggregate
statistics across the simulators, not individual frames.
"""
from __future__ import annotations

import math

import numpy as np

BOLTZMANN_EV_PER_K = 8.617333e-5


class DamageModel(object):
    """Per-frame damage accounting for a set-associative array.

    Parameters mirror the ChampSim module's environment knobs one for one:

      endurance_cv  PSC_ENDURANCE_CV   0 disables variation
      thermal       PSC_THERMAL        0 disables acceleration
      t_amb         PSC_T_AMB          330.0 K
      r_th          PSC_R_TH           0.6
      tau_th        PSC_TAU_TH         100000.0
      e_a           PSC_E_A            0.35 eV
      kappa         PSC_TG_KAPPA       2.0  (Weibull shape)
      alpha         PSC_TG_ALPHA       0.99 (CVaR tail)
    """

    def __init__(self, sets, ways, nvm_ways, banks=4, endurance_cv=0.0,
                 thermal=False, t_amb=330.0, r_th=0.6, tau_th=100000.0,
                 e_a=0.35, kappa=2.0, alpha=0.99, seed=12345):
        self.sets, self.ways, self.nvm_ways = sets, ways, nvm_ways
        self.banks = max(1, int(banks))
        self.endurance_cv = float(endurance_cv)
        self.thermal = bool(thermal)
        self.t_amb, self.r_th, self.tau_th = float(t_amb), float(r_th), float(tau_th)
        self.e_a, self.kappa, self.alpha = float(e_a), float(kappa), float(alpha)

        self.damage = np.zeros((sets, ways), dtype=np.float64)
        self.endurance = self._init_endurance(seed)
        self.bank_temp = np.full(self.banks, self.t_amb, dtype=np.float64)
        self.bank_writes_epoch = np.zeros(self.banks, dtype=np.int64)
        self.thermal_epoch = 0
        self.enabled = bool(endurance_cv) or bool(thermal)

    # ------------------------------------------------------------- setup
    def _init_endurance(self, seed):
        """Lognormal bins with unit mean, or all-ones when cv = 0.

        Uniform endurance is the optimistic assumption and the one the
        write-count literature makes. Sweeping cv is how you find out whether a
        levelling result survives contact with process variation.
        """
        e = np.ones((self.sets, self.ways), dtype=np.float64)
        if self.endurance_cv <= 0.0:
            return e
        sigma = math.sqrt(math.log(1.0 + self.endurance_cv ** 2))
        mu = -0.5 * sigma * sigma                       # unit mean
        rng = np.random.default_rng(seed)
        draws = rng.lognormal(mu, sigma, size=e.shape)
        return np.maximum(0.05, draws)                  # same clamp as the C++

    def bank_of(self, cs):
        return cs % self.banks

    # ------------------------------------------------------------ damage
    def damage_per_write(self, cs, way):
        """Cost of one write to this frame, normalised by its endurance bin.

        Returns exactly 1.0 when both terms are disabled. That identity is the
        reason every earlier write-count result in this project remains valid
        and directly comparable.
        """
        acc = 1.0
        if self.thermal:
            t = self.bank_temp[self.bank_of(cs)]
            acc = math.exp((self.e_a / BOLTZMANN_EV_PER_K)
                           * (1.0 / self.t_amb - 1.0 / t))
        return acc / self.endurance[cs, way]

    def charge(self, cs, way):
        """Accumulate the damage of one write, and advance the thermal state."""
        self.damage[cs, way] += self.damage_per_write(cs, way)
        if self.thermal:
            self._thermal_tick(cs)

    def _thermal_tick(self, cs):
        """Discrete RC step: T(t+D) = Tamb + (T-Tamb)e^{-D/RC} + R*P*(1-e^{-D/RC}).

        Write activity in the epoch stands in for power. Used only to rank
        candidate frames, so ordering matters more than absolute accuracy.
        """
        self.bank_writes_epoch[self.bank_of(cs)] += 1
        self.thermal_epoch += 1
        if self.thermal_epoch < 10000:
            return
        self.thermal_epoch = 0
        decay = math.exp(-10000.0 / self.tau_th)
        p = self.bank_writes_epoch / 10000.0
        self.bank_temp = (self.t_amb + (self.bank_temp - self.t_amb) * decay
                          + self.r_th * p * 100.0 * (1.0 - decay))
        self.bank_writes_epoch[:] = 0

    # ------------------------------------------------------------ hazard
    def hazard(self, z, dz):
        """Marginal Weibull hazard of adding `dz` damage to accumulated `z`.

        (z+dz)^kappa - z^kappa. With kappa = 1 this is just dz, i.e. a linear
        wear model; kappa > 1 makes an already-damaged frame more expensive to
        write, which is the whole point of placing by hazard rather than by
        write count.
        """
        return (z + dz) ** self.kappa - z ** self.kappa

    # ----------------------------------------------------------- metrics
    def nvm_damage(self):
        return self.damage[:, :self.nvm_ways]

    def cvar(self, alpha=None):
        """Mean of the worst (1-alpha) tail of per-frame damage.

        Peak damage is the alpha -> 0 limit and is hostage to one frame. CVaR
        describes the shape of the tail instead of its single extreme point, and
        is markedly less noisy across mixes.
        """
        a = self.alpha if alpha is None else alpha
        t = np.sort(self.nvm_damage().ravel())
        if t.size == 0:
            return 0.0
        k = int(math.ceil(a * t.size))
        k = min(k, t.size - 1)
        tail = t[k:]
        return float(tail.mean()) if tail.size else 0.0

    def time_to_first_failure(self, endurance_writes, duration_s):
        """Years until the first frame exhausts its endurance budget.

        The frame that fails first is the one with the highest accumulated
        damage, because damage is already normalised by each frame's own bin.
        Returns infinity when nothing was written.
        """
        z = self.nvm_damage()
        peak = float(z.max()) if z.size else 0.0
        if peak <= 0 or duration_s <= 0:
            return float('inf')
        return endurance_writes * duration_s / peak / 31_557_600.0

    def summary(self, endurance_writes=1e14, duration_s=0.0):
        z = self.nvm_damage()
        flat = z.ravel()
        mean = float(flat.mean()) if flat.size else 0.0
        out = {
            'damage_model_enabled': int(self.enabled),
            'endurance_cv': self.endurance_cv,
            'thermal': int(self.thermal),
            'max_damage': float(flat.max()) if flat.size else 0.0,
            'mean_damage': mean,
            'damage_cv': float(flat.std() / mean) if mean else 0.0,
            'cvar_damage': self.cvar(),
            'cvar_alpha': self.alpha,
        }
        if duration_s > 0:
            out['damage_years_to_first_failure'] = self.time_to_first_failure(
                endurance_writes, duration_s)
        return out


def attach(llc, **kw):
    """Give an LLC a damage model and route `charge_write`/`allocate` into it.

    Wrapping rather than editing `llc.py` keeps the hot loop free of a branch
    that is disabled in almost every run, and keeps every existing result
    reachable by simply not calling this.
    """
    dm = DamageModel(llc.sets, llc.ways, llc.nvm_ways, **kw)
    llc.damage_model = dm
    orig_charge, orig_alloc = llc.charge_write, llc.allocate

    def charge_write(cs, way):
        orig_charge(cs, way)
        dm.charge(cs, way)

    def allocate(cs, addr, now, is_dirty):
        before = llc.writes[cs]
        snapshot = list(before)
        orig_alloc(cs, addr, now, is_dirty)
        for j in range(llc.ways):                # exactly the frame that moved
            if llc.writes[cs][j] != snapshot[j]:
                dm.charge(cs, j)
                break

    llc.charge_write = charge_write
    llc.allocate = allocate
    return dm
