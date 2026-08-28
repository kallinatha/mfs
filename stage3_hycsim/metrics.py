#!/usr/bin/env python3
"""Wear-distribution and lifetime metrics.

Definitions follow Wang et al. (i2WAP, HPCA 2013) and Mittal & Vetter, which is
also the formulation used in the manuscript (Eqs. 1-5):

  mu      = mean write count per NVM frame
  sigma_i = mean write count of set i
  InterV  = (1/mu) * sample_std_over_sets(sigma_i)
  IntraV  = (1/(S*mu)) * sum_i sample_std_over_ways(w_ij)
  Lambda  = 1 / max_ij w_ij                      (raw lifetime, first failure)

Two lifetime ratios are reported:

  RLI_proxy  = (mu_b/mu_p) * (1+InterV_b+IntraV_b)/(1+InterV_p+IntraV_p) - 1
               the closed-form estimate used in the manuscript
  RLI_direct = max_b / max_p - 1
               measured directly from the peak frame write count

RLI_proxy is an estimator; RLI_direct is the quantity it estimates.  Reporting
both is deliberate: a large gap between them means the estimator is carrying the
result rather than the mechanism.
"""
import numpy as np


def wear_metrics(w):
    """w: 2-D array [sets][nvm_ways] of per-frame write counts."""
    w = np.asarray(w, dtype=np.float64)
    S, A = w.shape
    mu = w.mean()
    if mu == 0:
        return dict(mu=0.0, interv=0.0, intrav=0.0, max_write=0.0,
                    total_writes=0.0, cov=0.0, sets=S, ways=A)
    sigma = w.mean(axis=1)
    interv = sigma.std(ddof=1) / mu
    intrav = (w.std(axis=1, ddof=1).sum()) / (S * mu)
    return dict(mu=float(mu), interv=float(interv), intrav=float(intrav),
                max_write=float(w.max()), total_writes=float(w.sum()),
                cov=float(w.std(ddof=1) / mu), sets=S, ways=A)


def rli_proxy(base, prop):
    """Manuscript Eq. (5)."""
    if prop['mu'] == 0:
        return float('nan')
    return ((base['mu'] / prop['mu']) *
            (1.0 + base['interv'] + base['intrav']) /
            (1.0 + prop['interv'] + prop['intrav'])) - 1.0


def rli_direct(base, prop):
    """Ratio of raw lifetimes: Lambda_p / Lambda_b - 1 = max_b/max_p - 1."""
    if prop['max_write'] == 0:
        return float('nan')
    return base['max_write'] / prop['max_write'] - 1.0


def multiplicative_null(a, b):
    """Expected combined gain if two mechanisms compose independently on a
    ratio metric: (1+a)(1+b) - 1.  This is the correct null hypothesis for a
    super-additivity claim about RLI, which is a ratio of ratios minus one."""
    return (1.0 + a) * (1.0 + b) - 1.0


def summarise(base_w, prop_w):
    b, p = wear_metrics(base_w), wear_metrics(prop_w)
    return dict(
        mu_base=b['mu'], mu_prop=p['mu'],
        interv_base=b['interv'], interv_prop=p['interv'],
        intrav_base=b['intrav'], intrav_prop=p['intrav'],
        max_base=b['max_write'], max_prop=p['max_write'],
        intrav_reduction=(1 - p['intrav'] / b['intrav']) if b['intrav'] else float('nan'),
        interv_reduction=(1 - p['interv'] / b['interv']) if b['interv'] else float('nan'),
        rli_proxy=rli_proxy(b, p), rli_direct=rli_direct(b, p))


def damage_summary(llc, endurance_writes=1e14, duration_s=0.0):
    """Damage-model metrics, ADDED BESIDE the write-count metrics.

    Returns {} when no damage model is attached, so every existing result and
    the whole RLI_direct story are untouched. When a model is attached with both
    terms disabled the numbers are identical to the write-count ones by
    construction - see stage3_hycsim/damage.py and the conformance suite, which
    asserts that identity exactly rather than approximately.

    CVaR is the addition that matters. Peak wear is the alpha -> 0 limit of a
    tail statistic and moves whenever one unlucky frame moves; CVaR describes
    the shape of the worst (1-alpha) fraction and is far less noisy across
    mixes. Report both.
    """
    dm = getattr(llc, 'damage_model', None)
    if dm is None:
        return {}
    return dm.summary(endurance_writes=endurance_writes, duration_s=duration_s)
