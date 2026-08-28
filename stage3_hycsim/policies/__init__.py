#!/usr/bin/env python3
"""Wear-management policies for the hybrid LLC model.

Every policy exposes:
    attach(llc)                       bind to the cache instance
    choose_way(cs, addr, is_dirty)    pick the frame that receives the write
    on_write(cs, way)                 post-write hook (counters, rotation)
    remap_set(cs)                     optional inter-set index remap
    does_remap                        True if remap_set is meaningful

Re-implementations of published schemes are written from the papers' textual
descriptions, not from author code; parameters are stated in PARAMS and are
reported alongside every result so the configuration is auditable.
"""
from .base import Policy, Unmanaged, LRUHybrid
from .psc_vro import PSC, VRO, PSCVRO
from .i2wap import I2WAP
from .equalchance import EqualChance
from .startgap import StartGap
from .tailguard import TailGuard
from .psc_balance import PSCBalance
from .sequoia import Sequoia
from .swwr import SWWR, DWWR
from .ayush import AYUSH
from .dwawr import DWAWR
from .psc_soft import PSCSoft
from .psc_guard import PSCGuard
from .elba import ELBA
from .novel import VRORank, PSCPred, PSCVRORank, WearSwap, Full

REGISTRY = {
    PSCBalance.name: PSCBalance,
    TailGuard.name: TailGuard,
    'unmanaged':  Unmanaged,     # all-NVM, LRU, no wear management  (A0)
    'lru-hybrid': LRUHybrid,     # hybrid array, technology-blind LRU
    'psc':        PSC,           # physical split only               (A1)
    'vro':        VRO,           # way rotation only                 (A2)
    'psc-vro':    PSCVRO,        # proposed                          (A3)
    'i2wap':      I2WAP,         # Wang et al., HPCA 2013
    'equalchance': EqualChance,  # Mittal & Vetter, INFLOW 2014
    'startgap':   StartGap,      # Qureshi et al., MICRO 2009 (PCM), set-level
    'sequoia':    Sequoia,       # Jokar et al., IEEE TVLSI 24(3) 2016
    'swwr':       SWWR,          # Agarwal, PhD thesis IITG 2020, Ch. 4 Alg. 1
    'dwwr':       DWWR,          # Agarwal, PhD thesis IITG 2020, Ch. 4 Alg. 2
    'ayush':      AYUSH,         # Mittal & Vetter, IEEE CAL 2015
    'dwawr':      DWAWR,         # Agarwal & Kapoor, IEEE TC 2019
    # --- proposed variants (this work) ---
    'vro-rank':      VRORank,     # VRO re-specified: rank, do not restrict
    'psc-pred':      PSCPred,     # write-intensity predictor instead of dirty bit
    'psc-vro-rank':  PSCVRORank,  # both, acting on disjoint decisions
    'wear-swap':     WearSwap,    # measured inter-set levelling
    'psc-soft':      PSCSoft,     # soft partition; repair derived from ChampSim
    'full':          Full,        # all three channels
    'psc-guard':     PSCGuard,   # PSC + pressure-adaptive dirty admission (this work)
    'elba':          ELBA,        # elastic boundary write absorption (proposed)
}
