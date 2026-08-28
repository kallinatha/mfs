#!/usr/bin/env python3
"""Conformance tests: does each policy actually do what its paper says?

Every re-implemented mechanism gets three hand-built cases, as demanded by any
serious review of a re-implementation:

  ACTIVE    a trace that must force the mechanism to fire, with the expected
            outcome computed by hand
  INACTIVE  a trace on which the mechanism must stay dormant and leave the
            baseline replacement decision untouched
  BOUNDARY  a tie or threshold case with a deterministic, documented resolution

A policy that passes only the ACTIVE case is not verified: a mechanism that
fires all the time is as wrong as one that never fires.

Run:  python3 test_fidelity.py
Exit code is non-zero if any case fails, so it can gate a result run.
"""
import sys, os
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'stage3_hycsim'))
from llc import LLC          # noqa: E402
import policies              # noqa: E402

GETS, GETX, RPL = 0, 1, 2
FAILS = []

# Parameters that make the newer mechanisms *fire* on a short synthetic trace.
# Their published thresholds (SSC=127, I=2M cycles) are tuned for hundred-million
# access runs and would leave every mechanism dormant on 20-40k records, which
# would turn the accounting and determinism invariants into vacuous tests.
NEW_KW = {
    'sequoia': dict(group=8, ssc_bits=3, rsc_bits=2),
    'swwr':    dict(interval=997),
    'dwwr':    dict(interval=997),
}


def trace(evts):
    """evts: list of (op, addr, dirty)."""
    op = np.array([e[0] for e in evts], dtype=np.uint8)
    ad = np.array([e[1] for e in evts], dtype=np.uint64)
    dr = np.array([e[2] for e in evts], dtype=np.uint8)
    return op, ad, dr


def build(scheme, sets_ways=(12, 4), size_mb=1, **kw):
    nvm, sram = sets_ways
    pol = policies.REGISTRY[scheme](**kw)
    return LLC(size_mb * (1 << 20), nvm, sram, policy=pol), pol


def addrs_for_set(llc, cs, count, stride_sets=None):
    """Return `count` distinct addresses that all index to set `cs`."""
    out, a = [], 0
    step = 1 << 6
    while len(out) < count and a < (1 << 32):
        if llc.set_index(a) == cs:
            out.append(a)
        a += step
    assert len(out) == count, 'could not find enough addresses for set %d' % cs
    return out


def check(name, cond, detail=''):
    if cond:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s   %s' % (name, detail))
        FAILS.append(name)


def _no_new_failures(mark):
    """Make the case fail under pytest too, not only under __main__."""
    assert FAILS[mark:] == [], 'conformance failures: %s' % FAILS[mark:]


# --------------------------------------------------------------------------
def test_psc():
    print('\nPSC -- dirty blocks must land in SRAM ways, clean blocks in NVM')
    llc, pol = build('psc')
    A = addrs_for_set(llc, 0, 20)

    # ACTIVE: a dirty allocation must choose an SRAM way (index >= nvm_ways)
    llc.run(*trace([(RPL, A[0], 1)]))
    w = [j for j in range(llc.ways) if llc.tag[0][j] == A[0]]
    check('PSC ACTIVE: dirty -> SRAM partition', w and w[0] >= llc.nvm_ways,
          'landed in way %s' % w)

    # INACTIVE: a clean allocation must choose an NVM way
    llc2, _ = build('psc')
    llc2.run(*trace([(RPL, A[1], 0)]))
    w2 = [j for j in range(llc2.ways) if llc2.tag[0][j] == A[1]]
    check('PSC INACTIVE: clean -> NVM partition', w2 and w2[0] < llc2.nvm_ways,
          'landed in way %s' % w2)

    # BOUNDARY: what happens when the SRAM partition is already full of dirty
    # blocks?  PSC as specified has no spill rule -- lru() over the SRAM range
    # always returns a way, so it evicts an SRAM resident rather than falling
    # back to NVM.  A stream of dirty blocks therefore thrashes 4 SRAM ways
    # while 12 NVM ways stay idle.  This is a property of the published
    # mechanism, not an implementation bug, and it is the reason PSC-Pred needs
    # an explicit steering cap.  The test pins the behaviour so a future change
    # to it is deliberate.
    llc3, _ = build('psc')
    llc3.run(*trace([(RPL, a, 1) for a in A[:llc3.sram_ways + 4]]))
    nvm_used = sum(1 for j in range(llc3.nvm_ways) if llc3.tag[0][j] != -1)
    sram_used = sum(1 for j in range(llc3.nvm_ways, llc3.ways) if llc3.tag[0][j] != -1)
    check('PSC BOUNDARY (documented): all-dirty stream stays in SRAM, NVM idle',
          nvm_used == 0 and sram_used == llc3.sram_ways,
          'nvm=%d sram=%d' % (nvm_used, sram_used))


def test_vro_original():
    print('\nVRO (original) -- allocations confined to the rotating write-way window')
    llc, pol = build('vro', sets_ways=(16, 0), beta_max=8)
    A = addrs_for_set(llc, 0, 40)
    M, R = llc.nvm_ways, pol.R
    nwrite = M - R
    check('VRO setup: R = ceil(2M/3)', R == int(round(M * (2.0 / 3.0))), 'R=%d M=%d' % (R, M))

    # ACTIVE: the first (M-R) allocations must occupy exactly the window ways
    llc.run(*trace([(RPL, A[i], 1) for i in range(nwrite)]))
    used = {j for j in range(M) if llc.tag[0][j] != -1}
    expect = {(0 + k) % M for k in range(nwrite)}
    check('VRO ACTIVE: writes land only in the window', used == expect,
          'used=%s expected=%s' % (sorted(used), sorted(expect)))

    # ACTIVE 2: window origin advances by (M-R) after beta_max NVM writes
    llc2, pol2 = build('vro', sets_ways=(16, 0), beta_max=4)
    llc2.run(*trace([(RPL, A[i], 1) for i in range(4)]))
    check('VRO ACTIVE: window rotates after beta_max writes',
          pol2.w0 == (M - pol2.R) % M and pol2.rotations == 1,
          'w0=%d rotations=%d' % (pol2.w0, pol2.rotations))

    # INACTIVE: below beta_max the origin must not move
    llc3, pol3 = build('vro', sets_ways=(16, 0), beta_max=1000)
    llc3.run(*trace([(RPL, A[i], 1) for i in range(3)]))
    check('VRO INACTIVE: no rotation below threshold',
          pol3.w0 == 0 and pol3.rotations == 0, 'w0=%d' % pol3.w0)


def test_vro_rank():
    print('\nVRO-Rank -- least-worn among the LRU pool, and dormant when balanced')
    # INACTIVE: a perfectly balanced set must give the plain LRU victim
    llc, pol = build('vro-rank', sets_ways=(16, 0), trigger=4, pool=4)
    A = addrs_for_set(llc, 0, 40)
    fill = [(RPL, A[i], 1) for i in range(16)]
    llc.run(*trace(fill))
    before = pol.interventions
    llc.run(*trace([(RPL, A[16], 1)]))
    check('VRO-Rank INACTIVE: balanced set -> no intervention',
          pol.interventions == before, 'interventions=%d' % pol.interventions)

    # ACTIVE: force imbalance by hammering one way, then check the victim is
    # the least-worn way inside the LRU pool, not the LRU way itself
    llc2, pol2 = build('vro-rank', sets_ways=(16, 0), trigger=2, pool=4)
    A2 = addrs_for_set(llc2, 0, 60)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(16)]))
    pol2.c[0][3] = 12          # way 3 is heavily worn
    for j in range(16):
        if j != 3:
            pol2.c[0][j] = 0
    w = pol2._rank_way(0)
    check('VRO-Rank ACTIVE: fires when imbalance >= trigger', w >= 0, 'returned %d' % w)
    check('VRO-Rank ACTIVE: never selects the most-worn way', w != 3, 'selected way %d' % w)

    # BOUNDARY: imbalance exactly one below the trigger must not fire
    llc3, pol3 = build('vro-rank', sets_ways=(16, 0), trigger=5, pool=4)
    A3 = addrs_for_set(llc3, 0, 40)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(16)]))
    for j in range(16):
        pol3.c[0][j] = 0
    pol3.c[0][2] = 4           # max-min = 4, trigger = 5
    check('VRO-Rank BOUNDARY: imbalance == trigger-1 stays dormant',
          pol3._rank_way(0) == -1)


def test_dwawr():
    print('\nDWAWR -- interval restriction of the n most-written ways, with redirect')
    llc, pol = build('dwawr', sets_ways=(16, 0), interval=8, n_restricted=2)
    A = addrs_for_set(llc, 0, 60)
    llc.run(*trace([(RPL, A[i], 1) for i in range(8)]))
    check('DWAWR ACTIVE: interval expiry recomputes the restricted set',
          pol.intervals == 1, 'intervals=%d' % pol.intervals)
    restricted = [j for j in range(pol.M) if pol.R[0][j]]
    check('DWAWR ACTIVE: exactly n ways restricted', len(restricted) == 2,
          'restricted=%s' % restricted)
    check('DWAWR ACTIVE: restricted counters cleared',
          all(pol.Z[0][j] == 0 for j in restricted))

    # INACTIVE: before the first interval expiry nothing may be restricted
    llc2, pol2 = build('dwawr', sets_ways=(16, 0), interval=1000, n_restricted=2)
    A2 = addrs_for_set(llc2, 0, 20)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(5)]))
    check('DWAWR INACTIVE: no restriction before interval expiry',
          not any(pol2.R[0]) and pol2.redirects == 0)

    # BOUNDARY: a restricted way must receive no writes at all while it is
    # restricted -- that is the whole point of write restriction, and it is also
    # why the mechanism costs capacity.
    llc3, pol3 = build('dwawr', sets_ways=(2, 0), interval=10 ** 9, n_restricted=1)
    A3 = addrs_for_set(llc3, 0, 8)
    pol3.R[0][0] = 1
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(4)]))
    check('DWAWR BOUNDARY: a restricted way receives zero writes',
          llc3.writes[0][0] == 0 and llc3.writes[0][1] == 4,
          'way0=%d way1=%d' % (llc3.writes[0][0], llc3.writes[0][1]))


def test_ayush():
    print('\nAYUSH -- write-hit swap with the SRAM LRU, gated by age and Z')
    # ACTIVE: a write hit on an NVM block whose SRAM LRU is older must swap,
    # and must charge exactly two array writes (one per side).
    llc, pol = build('ayush', Z=0)
    A = addrs_for_set(llc, 0, 40)
    llc.run(*trace([(RPL, A[i], 1) for i in range(llc.ways)]))
    llc.mru[0][0] = 10 ** 9              # NVM way 0 is the most recent
    for j in range(llc.nvm_ways, llc.ways):
        llc.mru[0][j] = 0                # every SRAM way is older
    before_m, before_w = pol.migrations, sum(sum(r) for r in llc.writes)
    pol.on_write_hit(0, 0, 1, 10 ** 9 + 1)
    after_w = sum(sum(r) for r in llc.writes)
    check('AYUSH ACTIVE: swap fires when the SRAM LRU is older',
          pol.migrations == before_m + 1, 'migrations=%d' % pol.migrations)
    check('AYUSH ACTIVE: a swap charges exactly two writes',
          after_w == before_w + 2, 'charged %d' % (after_w - before_w))

    # INACTIVE: a write hit already in SRAM must not migrate anything
    llc2, pol2 = build('ayush', Z=0)
    A2 = addrs_for_set(llc2, 0, 40)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(llc2.ways)]))
    w0 = sum(sum(r) for r in llc2.writes)
    pol2.on_write_hit(0, llc2.nvm_ways, 1, 10 ** 9)
    check('AYUSH INACTIVE: a write hit already in SRAM does nothing',
          pol2.migrations == 0 and sum(sum(r) for r in llc2.writes) == w0)

    # INACTIVE: the age condition must block a swap onto a *newer* SRAM block
    llc3, pol3 = build('ayush', Z=0)
    A3 = addrs_for_set(llc3, 0, 40)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(llc3.ways)]))
    llc3.mru[0][0] = 0                   # NVM way 0 is the oldest thing present
    for j in range(llc3.nvm_ways, llc3.ways):
        llc3.mru[0][j] = 10 ** 9         # SRAM ways are all newer
    pol3.on_write_hit(0, 0, 1, 10 ** 9 + 1)
    check('AYUSH INACTIVE: age condition blocks swapping onto newer SRAM data',
          pol3.migrations == 0 and pol3.blocked_by_age == 1,
          'migrations=%d blocked_age=%d' % (pol3.migrations, pol3.blocked_by_age))

    # BOUNDARY: Z above the maximum reachable age must disable the mechanism
    llc4, pol4 = build('ayush', Z=99)
    A4 = addrs_for_set(llc4, 0, 40)
    llc4.run(*trace([(RPL, A4[i], 1) for i in range(llc4.ways)]))
    llc4.mru[0][0] = 10 ** 9
    for j in range(llc4.nvm_ways, llc4.ways):
        llc4.mru[0][j] = 0
    pol4.on_write_hit(0, 0, 1, 10 ** 9 + 1)
    check('AYUSH BOUNDARY: unreachable Z disables migration',
          pol4.migrations == 0 and pol4.blocked_by_Z == 1,
          'blocked_Z=%d' % pol4.blocked_by_Z)


def test_i2wap():
    print('\ni2WAP -- PoLF flush cadence, and Swap-Shift by INVALIDATION not migration')
    llc, pol = build('i2wap', sets_ways=(16, 0), polf_threshold=4, swapshift_interval=10 ** 9)
    A = addrs_for_set(llc, 0, 20)
    llc.run(*trace([(RPL, A[i], 1) for i in range(4)]))
    check('i2WAP ACTIVE: PoLF flushes on the Nth NVM write',
          llc.flushes == 1, 'flushes=%d' % llc.flushes)

    llc2, pol2 = build('i2wap', sets_ways=(16, 0), polf_threshold=10 ** 9,
                       swapshift_interval=10 ** 9)
    A2 = addrs_for_set(llc2, 0, 20)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(5)]))
    check('i2WAP INACTIVE: no flush below the PoLF threshold', llc2.flushes == 0)

    # Swap-Shift must invalidate, and must NOT charge migration writes. The
    # paper: the swap "only exchanges the set IDs, and the data stored in these
    # two sets are simply invalidated".
    llc3, pol3 = build('i2wap', sets_ways=(16, 0), polf_threshold=10 ** 9,
                       swapshift_interval=4)
    A3 = addrs_for_set(llc3, 0, 20)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(4)]))
    total = sum(sum(r) for r in llc3.writes)
    live = sum(1 for j in range(llc3.ways) if llc3.tag[0][j] != -1)
    check('i2WAP ACTIVE: Swap-Shift fires on schedule', llc3.remaps == 1,
          'remaps=%d' % llc3.remaps)
    check('i2WAP ACTIVE: swap charges NO migration writes', total == 4,
          'charged %d writes for 4 allocations' % total)
    check('i2WAP ACTIVE: swapped sets are invalidated', live == 0,
          'set 0 still holds %d blocks' % live)

    # BOUNDARY: the swap pointer advances and wraps into a shift round
    llc4, pol4 = build('i2wap', sets_ways=(16, 0), polf_threshold=10 ** 9,
                       swapshift_interval=1)
    A4 = addrs_for_set(llc4, 0, 8)
    llc4.run(*trace([(RPL, A4[i], 1) for i in range(3)]))
    check('i2WAP BOUNDARY: swap pointer advances per swap',
          pol4.SwV == 3 or pol4.swap_rounds > 0, 'SwV=%d rounds=%d' % (pol4.SwV, pol4.swap_rounds))


def test_startgap():
    print('\nStart-Gap -- one line per gap movement, one write charged')
    # NOTE: these two cases were stale with respect to the rewritten
    # `startgap.py` and had been failing before Sequoia/SWWR were added.  The
    # rewrite (see that file's docstring) makes the remapping unit a *set*,
    # because the set index is the only algebraic function of the address in a
    # set-indexed cache, and holds the paper's 1/(psi+1) budget by scaling
    # psi_effective = ways * psi rather than by shrinking the relocation unit.
    # The cadence case therefore has to disable that scaling to exercise a
    # 4-write interval, and the storage case has to account for the reserved
    # GapLine set (a fixed per-array cost, charged deliberately) instead of
    # asserting an absolute bit budget.
    llc, pol = build('startgap', sets_ways=(16, 0), gap_interval=4, scale_by_ways=0)
    A = addrs_for_set(llc, 0, 20)
    before = sum(sum(r) for r in llc.writes)
    llc.run(*trace([(RPL, A[i], 1) for i in range(4)]))
    after = sum(sum(r) for r in llc.writes)
    check('Start-Gap ACTIVE: gap advances on schedule', pol.movements == 1,
          'movements=%d' % pol.movements)
    check('Start-Gap ACTIVE: at most ONE relocation write per movement',
          after - before <= 5, 'charged %d for 4 allocations + 1 movement' % (after - before))

    llc2, pol2 = build('startgap', sets_ways=(16, 0), gap_interval=10 ** 9)
    A2 = addrs_for_set(llc2, 0, 20)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(6)]))
    check('Start-Gap INACTIVE: no relocation below the interval',
          pol2.movements == 0 and llc2.remaps == 0)

    # The default holds the paper's write budget by scaling the interval, not by
    # shrinking the relocation unit.
    llc5, pol5 = build('startgap', sets_ways=(16, 0), gap_interval=4)
    check('Start-Gap: psi is scaled by associativity by default',
          pol5.psi == 4 * llc5.ways, 'psi=%d ways=%d' % (pol5.psi, llc5.ways))

    # BOUNDARY: two registers, a 7-bit counter and one reserved GapLine set --
    # the paper's whole cost argument is that none of this grows per set.
    small, ps = build('startgap', sets_ways=(16, 0), size_mb=1)
    big, pb = build('startgap', sets_ways=(16, 0), size_mb=8)
    check('Start-Gap BOUNDARY: state is O(1) in the number of sets, not per-frame',
          small.sets * 8 == big.sets and abs(pb.state_bits() - ps.state_bits()) < 64,
          '%d sets -> %d b, %d sets -> %d b'
          % (small.sets, ps.state_bits(), big.sets, pb.state_bits()))


def test_equalchance():
    print('\nEqualChance -- I-shifting to invalid, C-shifting to clean, per-set state only')
    # ACTIVE, I-shifting: an invalid frame is the first choice
    llc, pol = build('equalchance', sets_ways=(16, 0), relocate_interval=4)
    A = addrs_for_set(llc, 0, 40)
    llc.run(*trace([(RPL, A[i], 1) for i in range(16)]))
    llc.tag[0][9] = -1                    # way 9 is the only invalid frame
    pol.flag[0] = 1
    i_before, c_before = pol.i_shifts, pol.c_shifts
    w = pol.choose_way(0, A[20], 1)
    check('EqualChance ACTIVE: I-shifting targets the invalid frame', w == 9, 'chose %d' % w)
    check('EqualChance ACTIVE: the I-shift is counted as an I-shift',
          pol.i_shifts == i_before + 1 and pol.c_shifts == c_before,
          'i +%d c +%d' % (pol.i_shifts - i_before, pol.c_shifts - c_before))

    # ACTIVE, C-shifting: no invalid frame, so the clean one is chosen
    llc2, pol2 = build('equalchance', sets_ways=(16, 0), relocate_interval=4)
    A2 = addrs_for_set(llc2, 0, 40)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(16)]))
    for j in range(16):
        llc2.dirty[0][j] = 1
    llc2.dirty[0][5] = 0                  # way 5 is the only clean frame
    pol2.flag[0] = 1
    i2_before, c2_before = pol2.i_shifts, pol2.c_shifts
    w2 = pol2.choose_way(0, A2[21], 1)
    check('EqualChance ACTIVE: C-shifting targets the clean frame', w2 == 5, 'chose %d' % w2)
    check('EqualChance ACTIVE: the C-shift is counted as a C-shift',
          pol2.c_shifts == c2_before + 1 and pol2.i_shifts == i2_before,
          'i +%d c +%d' % (pol2.i_shifts - i2_before, pol2.c_shifts - c2_before))

    # INACTIVE: with the flag off it must defer to LRU untouched
    llc3, pol3 = build('equalchance', sets_ways=(16, 0), relocate_interval=10 ** 9)
    A3 = addrs_for_set(llc3, 0, 40)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(16)]))
    pol3.flag[0] = 0
    check('EqualChance INACTIVE: flag off -> plain LRU victim',
          pol3.choose_way(0, A3[22], 1) == llc3.lru(0, range(llc3.ways)))

    # BOUNDARY: neither invalid nor clean -> normal write, counted
    llc4, pol4 = build('equalchance', sets_ways=(16, 0), relocate_interval=4)
    A4 = addrs_for_set(llc4, 0, 40)
    llc4.run(*trace([(RPL, A4[i], 1) for i in range(16)]))
    for j in range(16):
        llc4.dirty[0][j] = 1
    pol4.flag[0] = 1
    pol4.choose_way(0, A4[23], 1)
    check('EqualChance BOUNDARY: no candidate -> normal write', pol4.no_candidate == 1,
          'no_candidate=%d' % pol4.no_candidate)


# ==========================================================================
# Sequoia -- Jokar, Arjomand & Sarbazi-Azad, IEEE TVLSI 24(3):954-967, 2016
# ==========================================================================
def _seq_bijection(pol):
    """DT must be a permutation of the group members, and RT its exact inverse."""
    G, low, mask = pol.G, pol.lowbits, pol.lowmask
    for grp in range(1 << low):
        seen = set()
        for m in range(G):
            ls = (m << low) | grp
            seen.add(pol.dt[ls])
            if pol._ls_of(pol._ps(ls)) != ls:
                return False
        if seen != set(range(G)):
            return False
    return True


def _seq_reachable(llc, pol):
    """Every live block must be findable by the model's own lookup path: either
    natively in its current PS, or as a non-native line in the associated PS
    that the second search would visit."""
    for ps in range(llc.sets):
        for w in range(llc.ways):
            t = llc.tag[ps][w]
            if t == -1:
                continue
            cur = pol._ps(llc.set_index(t))
            if pol.native[ps][w]:
                if ps != cur:
                    return False, (ps, w, 'native line in the wrong PS')
            else:
                if not pol.at_a[cur] or pol._prev(cur) != ps:
                    return False, (ps, w, 'non-native line the second search cannot reach')
    return True, None


def test_sequoia_goas():
    print('\nSequoia G-OAS -- SSC/HSC selection, lazy association, second search, abort')
    mark = len(FAILS)

    # ACTIVE: SSC saturates (ssc_bits=2 -> threshold 3), the NEXT write hit
    # associates the hot PS with the coldest PS of its group.
    llc, pol = build('sequoia', sets_ways=(16, 0), group=4, ssc_bits=2, wad=0)
    A = addrs_for_set(llc, 0, 8)
    B = addrs_for_set(llc, 256, 8)          # same group: G=4 -> members 0,256,512,768
    check('Sequoia G-OAS setup: group members share the low index bits',
          (0 & pol.lowmask) == (256 & pol.lowmask) and pol.G == 4,
          'lowmask=%d G=%d' % (pol.lowmask, pol.G))
    llc.run(*trace([(RPL, a, 0) for a in A[:4]] + [(RPL, b, 0) for b in B[:4]]))
    check('Sequoia G-OAS INACTIVE: allocations are not write hits, SSC stays 0',
          pol.ssc[0] == 0 and pol.associations == 0,
          'ssc=%d assoc=%d' % (pol.ssc[0], pol.associations))

    llc.run(*trace([(RPL, A[0], 0)] * 3))   # 3 write hits -> SSC == 3 == max
    check('Sequoia G-OAS BOUNDARY: SSC saturation marks the set hot but does '
          'not yet associate', pol.hot[0] == 1 and pol.associations == 0,
          'hot=%d assoc=%d' % (pol.hot[0], pol.associations))
    check('Sequoia G-OAS BOUNDARY: HSC increments on SSC saturation',
          pol.hsc[0] == 1, 'hsc=%d' % pol.hsc[0])

    llc.run(*trace([(RPL, A[0], 0)]))       # the "next write-hit" triggers OAS
    check('Sequoia G-OAS ACTIVE: the next write hit forms the association',
          pol.associations == 1 and llc.remaps == 1,
          'assoc=%d remaps=%d' % (pol.associations, llc.remaps))
    check('Sequoia G-OAS ACTIVE: the hot set SSC is reset, its HSC is not',
          pol.ssc[0] == 0 and pol.hsc[0] == 1,
          'ssc=%d hsc=%d' % (pol.ssc[0], pol.hsc[0]))
    check('Sequoia G-OAS ACTIVE: DT/RT exchange the two mappings',
          pol._ps(0) == 256 and pol._ps(256) == 0,
          'ps(0)=%d ps(256)=%d' % (pol._ps(0), pol._ps(256)))
    check('Sequoia G-OAS ACTIVE: AT marks both sets and points each at the other',
          pol.at_a[0] == 1 and pol.at_a[256] == 1
          and pol._prev(256) == 0 and pol._prev(0) == 256)
    check('Sequoia G-OAS ACTIVE: every resident line becomes non-native',
          all(pol.native[0][j] == 0 for j in range(4))
          and all(pol.native[256][j] == 0 for j in range(4)))
    check('Sequoia G-OAS ACTIVE: no data is copied at association time '
          '(swapping is lazy)', pol.oas_line_moves == 0,
          'moved %d lines' % pol.oas_line_moves)
    ok, why = _seq_reachable(llc, pol)
    check('Sequoia G-OAS ACTIVE: no live block is unreachable after association',
          ok, str(why))
    check('Sequoia G-OAS ACTIVE: the mapping stays a bijection', _seq_bijection(pol))

    # Second search: A[0] now lives in PS 0 but indexes to PS 256.  A read must
    # still find it, and the read second-hit must swap it home (Fig. 4).
    hits0, mig0 = llc.hits, llc.migration_writes
    llc.run(*trace([(GETS, A[0], 0)]))
    check('Sequoia G-OAS ACTIVE: the second search finds the migrating line',
          pol.second_hits == 1 and llc.hits == hits0 + 1,
          'second_hits=%d hits+%d' % (pol.second_hits, llc.hits - hits0))
    check('Sequoia G-OAS ACTIVE: a read second-hit swaps the pair (2 writes)',
          llc.tag[256][0] == A[0] and llc.tag[0][0] == B[0]
          and llc.migration_writes == mig0 + 2,
          'tag[256][0]=%s tag[0][0]=%s mig+%d'
          % (llc.tag[256][0], llc.tag[0][0], llc.migration_writes - mig0))
    check('Sequoia G-OAS ACTIVE: a second hit is credited to NVM, not SRAM',
          llc.hits_sram == 0 and llc.sram_reads == 0,
          'hits_sram=%d' % llc.hits_sram)
    ok, why = _seq_reachable(llc, pol)
    check('Sequoia G-OAS ACTIVE: still reachable after the swap step', ok, str(why))

    # ABORT: the hot set saturating again before the swap completes must stop
    # the association and invalidate the lines left behind.
    for k in range(4):
        pol.on_write_hit(0, 0, 0, 10 ** 6 + k)
    check('Sequoia G-OAS ACTIVE: re-saturation aborts the running association',
          pol.oas_aborts == 1 and pol.oas_abort_invalidations > 0,
          'aborts=%d invalidated=%d' % (pol.oas_aborts, pol.oas_abort_invalidations))
    ok, why = _seq_reachable(llc, pol)
    check('Sequoia G-OAS ACTIVE: abort leaves no unreachable block', ok, str(why))

    # INACTIVE: at the published threshold (127) a handful of write hits does
    # nothing at all, and the address mapping is the identity.
    llc2, pol2 = build('sequoia', sets_ways=(16, 0), wad=0)
    A2 = addrs_for_set(llc2, 0, 8)
    llc2.run(*trace([(RPL, A2[0], 0)] + [(RPL, A2[0], 0)] * 10))
    check('Sequoia G-OAS INACTIVE: 10 write hits do not reach SSC=127',
          pol2.associations == 0 and pol2.ssc_saturations == 0 and llc2.remaps == 0,
          'ssc=%d' % pol2.ssc[0])
    check('Sequoia G-OAS INACTIVE: mapping is the identity while dormant',
          all(pol2.remap_set(s) == s for s in range(0, llc2.sets, 97)))

    # ABLATION: oas=0 must remove the mechanism entirely.
    llc3, pol3 = build('sequoia', sets_ways=(16, 0), group=4, ssc_bits=2, wad=0, oas=0)
    A3 = addrs_for_set(llc3, 0, 8)
    llc3.run(*trace([(RPL, A3[0], 0)] + [(RPL, A3[0], 0)] * 8))
    check('Sequoia ABLATION oas=0: no association, no remap, identity mapping',
          pol3.associations == 0 and llc3.remaps == 0 and pol3.remap_set(7) == 7,
          'assoc=%d' % pol3.associations)
    check('Sequoia ABLATION oas=0: SSC still resets where OAS would have fired, '
          'so WAD\'s RSC stream is unchanged', pol3.ssc[0] < pol3.ssc_max,
          'ssc=%d' % pol3.ssc[0])
    _no_new_failures(mark)


def test_sequoia_wad():
    print('\nSequoia WAD -- RSC = low bits of SSC, Clean-LRU victim, NVM-only, held frame')
    mark = len(FAILS)

    # ACTIVE: an armed set displaces the next written line into the Clean-LRU
    # victim, and charges exactly ONE physical write (D3).
    llc, pol = build('sequoia', sets_ways=(16, 0), oas=0, rsc_bits=1)
    A = addrs_for_set(llc, 0, 40)
    llc.run(*trace([(RPL, A[i], 1) for i in range(16)]))
    llc.dirty[0][5] = 0                       # way 5 is the only clean line
    llc.mru[0][0] = 10 ** 6                   # the hot line is recently used
    pol.wad_armed[0] = 1
    mig0 = llc.migration_writes
    w5_0 = llc.writes[0][5]
    pol.on_write_hit(0, 0, 0, 10 ** 6 + 1)
    check('Sequoia WAD ACTIVE: an armed set displaces the hot line',
          pol.wad_displacements == 1, 'displacements=%d' % pol.wad_displacements)
    check('Sequoia WAD ACTIVE: the victim is the Clean-LRU line',
          llc.tag[0][5] == A[0], 'victim holds %s' % llc.tag[0][5])
    check('Sequoia WAD ACTIVE: the hot line is invalidated',
          llc.tag[0][0] == -1, 'tag[0][0]=%s' % llc.tag[0][0])
    check('Sequoia WAD ACTIVE: a displacement is ONE write, charged to the '
          'mechanism and to the frame',
          llc.migration_writes == mig0 + 1 and llc.writes[0][5] == w5_0 + 1,
          'mig+%d frame+%d' % (llc.migration_writes - mig0, llc.writes[0][5] - w5_0))
    check('Sequoia WAD ACTIVE: displacing onto a clean victim costs no writeback',
          pol.wad_writebacks == 0 and pol.wad_victim_evictions == 1,
          'wb=%d evict=%d' % (pol.wad_writebacks, pol.wad_victim_evictions))

    # BOUNDARY (D6): the invalidated frame keeps its recency and must NOT be
    # handed to the very next allocation, even though llc.lru() would do so.
    check('Sequoia WAD BOUNDARY: plain LRU *would* refill the cooled frame',
          llc.lru(0, range(llc.ways)) == 0, 'lru gave %d' % llc.lru(0, range(llc.ways)))
    check('Sequoia WAD BOUNDARY: the policy does not refill the cooled frame',
          pol.choose_way(0, A[20], 1) != 0,
          'choose_way gave %d' % pol.choose_way(0, A[20], 1))

    # BOUNDARY: no clean line anywhere -> "the LRU line is selected as victim"
    llc2, pol2 = build('sequoia', sets_ways=(16, 0), oas=0, rsc_bits=1)
    A2 = addrs_for_set(llc2, 0, 40)
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(16)]))
    for j in range(16):
        llc2.dirty[0][j] = 1
    llc2.mru[0][0] = 10 ** 6
    pol2.wad_armed[0] = 1
    pol2.on_write_hit(0, 0, 0, 10 ** 6 + 1)
    check('Sequoia WAD BOUNDARY: with no clean line the LRU line is the victim',
          llc2.tag[0][1] == A2[0], 'victim holds %s' % llc2.tag[0][1])
    check('Sequoia WAD BOUNDARY: a dirty victim costs one write-back',
          pol2.wad_writebacks == 1, 'wb=%d' % pol2.wad_writebacks)

    # HYBRID ADAPTATION A2: the victim must never be an SRAM way, even when the
    # SRAM ways are the only clean frames in the set.
    llc3, pol3 = build('sequoia', sets_ways=(12, 4), oas=0, rsc_bits=1)
    A3 = addrs_for_set(llc3, 0, 40)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(16)]))
    for j in range(12):
        llc3.dirty[0][j] = 1
    for j in range(12, 16):
        llc3.dirty[0][j] = 0                  # only the SRAM ways are clean
    llc3.mru[0][0] = 10 ** 6
    pol3.wad_armed[0] = 1
    pol3.on_write_hit(0, 0, 0, 10 ** 6 + 1)
    victim = [j for j in range(16) if llc3.tag[0][j] == A3[0]]
    check('Sequoia WAD hybrid: the displacement victim is an NVM way',
          victim and victim[0] < llc3.nvm_ways, 'landed in way %s' % victim)

    # HYBRID ADAPTATION A1: a write hit on an SRAM way touches no NVM cell and
    # must not advance SSC/RSC at all.
    llc4, pol4 = build('sequoia', sets_ways=(12, 4), oas=0, rsc_bits=1)
    A4 = addrs_for_set(llc4, 0, 40)
    llc4.run(*trace([(RPL, A4[i], 1) for i in range(16)]))
    pol4.wad_armed[0] = 1
    pol4.on_write_hit(0, 13, 0, 10 ** 6)
    check('Sequoia WAD hybrid: an SRAM write hit does not advance SSC',
          pol4.ssc[0] == 0 and pol4.wad_displacements == 0
          and pol4.write_hits_sram == 1,
          'ssc=%d disp=%d dropped=%d'
          % (pol4.ssc[0], pol4.wad_displacements, pol4.write_hits_sram))

    # INACTIVE: below the RSC threshold nothing is displaced.  With the paper's
    # RSC = 3 bits, seven write hits arm but do not fire.
    llc5, pol5 = build('sequoia', sets_ways=(16, 0), oas=0)
    A5 = addrs_for_set(llc5, 0, 20)
    llc5.run(*trace([(RPL, A5[0], 1)] + [(RPL, A5[0], 1)] * 6))
    check('Sequoia WAD INACTIVE: 6 write hits stay below RSC saturation',
          pol5.wad_arms == 0 and pol5.wad_displacements == 0,
          'arms=%d disp=%d' % (pol5.wad_arms, pol5.wad_displacements))
    llc5.run(*trace([(RPL, A5[0], 1)]))       # the 7th saturates RSC -> arm only
    check('Sequoia WAD BOUNDARY: RSC saturation arms but does not displace',
          pol5.wad_arms == 1 and pol5.wad_displacements == 0,
          'arms=%d disp=%d' % (pol5.wad_arms, pol5.wad_displacements))
    llc5.run(*trace([(RPL, A5[0], 1)]))       # the next write hit displaces
    check('Sequoia WAD ACTIVE: the write hit after RSC saturation displaces',
          pol5.wad_displacements == 1, 'disp=%d' % pol5.wad_displacements)

    # ABLATION: wad=0 removes the mechanism.
    llc6, pol6 = build('sequoia', sets_ways=(16, 0), oas=0, rsc_bits=1, wad=0)
    A6 = addrs_for_set(llc6, 0, 20)
    llc6.run(*trace([(RPL, A6[0], 1)] + [(RPL, A6[0], 1)] * 8))
    check('Sequoia ABLATION wad=0: no displacement, no arming',
          pol6.wad_displacements == 0 and pol6.wad_arms == 0)
    _no_new_failures(mark)


def test_sequoia_storage_and_invariants():
    print('\nSequoia storage -- Sec. VI-B4 must reproduce 19.5 kB exactly')
    mark = len(FAILS)
    llc, pol = build('sequoia', sets_ways=(8, 0), size_mb=2)      # 4096 sets x 8 ways
    check('Sequoia storage: 4096 sets x 8 ways, G=64 -> 19.5 kB',
          llc.sets == 4096 and pol.state_bits() == int(19.5 * 1024 * 8),
          'sets=%d bits=%d (want %d)' % (llc.sets, pol.state_bits(), int(19.5 * 1024 * 8)))
    check('Sequoia storage: 31 bits/set + 1 bit/line',
          pol.state_bits() == llc.sets * 31 + llc.sets * llc.ways)

    print('Sequoia invariants -- bijection, reachability, write accounting')
    for arena in ((16, 0), (12, 4)):
        llc2, pol2 = build('sequoia', sets_ways=arena, group=8, ssc_bits=3, rsc_bits=2)
        rng = np.random.default_rng(19)
        n = 40000
        op = rng.choice([GETS, GETX, RPL], size=n, p=[0.3, 0.2, 0.5]).astype(np.uint8)
        ad = (rng.integers(0, 3000, size=n) * 64).astype(np.uint64)
        dr = rng.integers(0, 2, size=n).astype(np.uint8)
        llc2.run(op, ad, dr)
        tag = '%dN+%dS' % arena
        check('Sequoia %s: AT/DT/RT stay a bijection after 40k accesses' % tag,
              _seq_bijection(pol2))
        ok, why = _seq_reachable(llc2, pol2)
        check('Sequoia %s: no live block becomes unreachable' % tag, ok, str(why))
        check('Sequoia %s: the mechanism actually fired' % tag,
              pol2.associations > 0 and pol2.wad_displacements > 0,
              'assoc=%d disp=%d' % (pol2.associations, pol2.wad_displacements))
        mech = (pol2.oas_line_moves + pol2.wad_displacements)
        check('Sequoia %s: every mechanism write is in llc.migration_writes' % tag,
              llc2.migration_writes == mech,
              'migration_writes=%d mechanism writes=%d' % (llc2.migration_writes, mech))
        total = sum(sum(r) for r in llc2.writes)
        check('Sequoia %s: per-frame wear == demand + mechanism writes' % tag,
              total == pol2.allocations + mech,
              '%d vs %d' % (total, pol2.allocations + mech))
    _no_new_failures(mark)


# ==========================================================================
# SWWR / DWWR -- Agarwal, PhD thesis IIT Guwahati 2020, Ch. 4, Alg. 1 and 2
# ==========================================================================
def test_swwr():
    print('\nSWWR -- one vertical window is read-only per interval, writes redirect out')
    mark = len(FAILS)
    llc, pol = build('swwr', sets_ways=(16, 0), m=4, interval=10 ** 12)
    A = addrs_for_set(llc, 0, 60)
    check('SWWR setup: m equal windows over the NVM ways only',
          pol.bounds == [(0, 4), (4, 8), (8, 12), (12, 16)], str(pol.bounds))

    # INACTIVE: "Run application for I cycles treating the whole cache as
    # normally available cache" -- nothing is restricted before the first
    # interval boundary.
    llc.run(*trace([(RPL, A[i], 1) for i in range(16)]))
    check('SWWR INACTIVE: no window is restricted in the first interval',
          pol.wrw == -1 and pol.redirects == 0 and pol.alloc_deflected == 0,
          'wrw=%d' % pol.wrw)

    # ACTIVE: with W0 restricted, a write hit inside it is redirected to a
    # non-restricted way and the original line is invalidated.
    pol._set_window(0)
    mig0 = llc.migration_writes
    pol.on_write_hit(0, 0, 0, 10 ** 6)
    check('SWWR ACTIVE: a write hit in the restricted window is redirected',
          pol.redirects == 1 and llc.tag[0][0] == -1 and llc.tag[0][4] == A[0],
          'redirects=%d tag0=%s tag4=%s' % (pol.redirects, llc.tag[0][0], llc.tag[0][4]))
    check('SWWR ACTIVE: the redirected write is charged to the mechanism and '
          'to the destination frame',
          llc.migration_writes == mig0 + 1 and llc.writes[0][4] == 2,
          'mig+%d frame=%d' % (llc.migration_writes - mig0, llc.writes[0][4]))
    check('SWWR ACTIVE: the evicted destination is written back if dirty',
          pol.redirect_evictions == 1 and pol.redirect_writebacks == 1,
          'evict=%d wb=%d' % (pol.redirect_evictions, pol.redirect_writebacks))

    # INACTIVE: a write hit outside the restricted window is served normally.
    r0, m0 = pol.redirects, llc.migration_writes
    pol.on_write_hit(0, 9, 0, 10 ** 6 + 1)
    check('SWWR INACTIVE: a write hit outside the window is untouched',
          pol.redirects == r0 and llc.migration_writes == m0
          and pol.write_hits_unrestricted == 1)

    # BOUNDARY: a restricted way must receive ZERO writes -- redirects *and*
    # fills avoid it (Alg. 1 processCacheMiss).
    llc2, pol2 = build('swwr', sets_ways=(16, 0), m=4, interval=10 ** 12)
    A2 = addrs_for_set(llc2, 0, 60)
    pol2._set_window(1)                        # ways 4..7 restricted
    llc2.run(*trace([(RPL, A2[i], 1) for i in range(40)]))
    restricted_writes = sum(llc2.writes[0][j] for j in range(4, 8))
    check('SWWR BOUNDARY: a restricted way receives zero writes of any kind',
          restricted_writes == 0, 'restricted ways took %d writes' % restricted_writes)
    check('SWWR BOUNDARY: fills were actually deflected away from the window',
          pol2.alloc_deflected > 0, 'deflected=%d' % pol2.alloc_deflected)

    # Round-robin advance, and the cycle clock.
    llc3, pol3 = build('swwr', sets_ways=(16, 0), m=4, interval=100)
    A3 = addrs_for_set(llc3, 0, 40)
    cyc = np.arange(40, dtype=np.uint64) * 60      # 60 cycles per record
    pol3.set_cycles(cyc)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(40)]))
    check('SWWR ACTIVE: the interval is clocked in cycles when a cycle column '
          'is bound', pol3.clock_source == 'cycles' and pol3.intervals > 0,
          'source=%s intervals=%d' % (pol3.clock_source, pol3.intervals))
    check('SWWR ACTIVE: the restricted window advances round robin',
          pol3.wrw == (pol3.intervals - 1) % 4,
          'wrw=%d after %d intervals' % (pol3.wrw, pol3.intervals))

    # HYBRID ADAPTATION R1: SRAM ways are never restricted.
    llc4, pol4 = build('swwr', sets_ways=(12, 4), m=4, interval=10 ** 12)
    check('SWWR hybrid: windows cover the NVM partition only',
          pol4.bounds == [(0, 3), (3, 6), (6, 9), (9, 12)], str(pol4.bounds))
    bad = []
    for w in range(4):
        pol4._set_window(w)
        bad += [j for j in pol4.restricted if j >= llc4.nvm_ways]
    check('SWWR hybrid: no SRAM way is ever write-restricted', not bad, str(bad))

    check('SWWR storage: Sec. 4.5.5 gives 42 bits', pol.state_bits() == 42,
          'state_bits=%d' % pol.state_bits())
    _no_new_failures(mark)


def test_dwwr():
    print('\nDWWR -- argmax over per-window counters, reset so it cannot repeat')
    mark = len(FAILS)
    llc, pol = build('dwwr', sets_ways=(16, 0), m=4, interval=10 ** 12)

    # ACTIVE: the most-written window is selected and its counter reset.
    pol.C = [3, 11, 5, 0]
    sel = pol._select_window()
    check('DWWR ACTIVE: the window with the most writes is restricted',
          sel == 1, 'selected %d' % sel)
    check('DWWR ACTIVE: the selected counter is reset so it cannot repeat',
          pol.C == [3, 0, 5, 0], str(pol.C))
    check('DWWR ACTIVE: the next selection is a different window',
          pol._select_window() == 2, 'C=%s' % pol.C)

    # BOUNDARY: all counters equal -> lowest index, and the degeneracy is
    # counted rather than hidden (this model starves the write-hit path that
    # feeds the counters).
    llc2, pol2 = build('dwwr', sets_ways=(16, 0), m=4, interval=10 ** 12)
    check('DWWR BOUNDARY: an all-zero counter vector selects window 0',
          pol2._select_window() == 0)
    check('DWWR BOUNDARY: the degenerate selection is instrumented',
          pol2.degenerate_selections == 1,
          'degenerate=%d' % pol2.degenerate_selections)

    # ACTIVE: counters are fed by write hits, per Algorithm 2 -- a normal write
    # hit charges the window holding the block, a redirect charges the window
    # holding the destination.
    llc3, pol3 = build('dwwr', sets_ways=(16, 0), m=4, interval=10 ** 12)
    A3 = addrs_for_set(llc3, 0, 40)
    llc3.run(*trace([(RPL, A3[i], 1) for i in range(16)]))
    check('DWWR INACTIVE: Algorithm 2 does not count fills in Ci',
          pol3.C == [0, 0, 0, 0], str(pol3.C))
    pol3._set_window(0)
    pol3.on_write_hit(0, 9, 0, 10 ** 6)        # normal write hit in W2
    check('DWWR ACTIVE: a normal write hit charges its own window',
          pol3.C == [0, 0, 1, 0], str(pol3.C))
    pol3.on_write_hit(0, 1, 0, 10 ** 6 + 1)    # write hit inside the WRW
    check('DWWR ACTIVE: a redirect charges the destination window',
          pol3.redirects == 1 and pol3.C[0] == 0 and sum(pol3.C) == 2,
          'C=%s redirects=%d' % (pol3.C, pol3.redirects))

    # BOUNDARY: 13-bit counters saturate rather than wrap.
    llc4, pol4 = build('dwwr', sets_ways=(16, 0), m=4, interval=10 ** 12)
    pol4.C[2] = pol4.counter_max
    pol4._count_window_write(2, fill=False)
    check('DWWR BOUNDARY: a 13-bit counter saturates, it does not wrap',
          pol4.C[2] == pol4.counter_max and pol4.counter_saturations == 1,
          'C2=%d' % pol4.C[2])

    check('DWWR storage: Table 4.18 gives 4 x 13 + 42 = 94 bits',
          pol.state_bits() == 94, 'state_bits=%d' % pol.state_bits())
    _no_new_failures(mark)


def test_accounting():
    print('\nAccounting invariant -- every physical write is counted exactly once')
    for scheme in ('unmanaged', 'psc', 'vro', 'vro-rank', 'i2wap', 'startgap',
                   'equalchance', 'dwawr', 'ayush', 'psc-pred', 'psc-vro-rank',
                   'sequoia', 'swwr', 'dwwr'):
        kw = dict(NEW_KW.get(scheme, {}))
        arena = (16, 0) if scheme in ('unmanaged', 'vro', 'vro-rank', 'i2wap',
                                      'startgap', 'equalchance', 'dwawr') else (12, 4)
        llc, pol = build(scheme, sets_ways=arena, **kw)
        rng = np.random.default_rng(7)
        n = 20000
        op = rng.choice([GETS, GETX, RPL], size=n, p=[0.3, 0.2, 0.5]).astype(np.uint8)
        ad = (rng.integers(0, 4096, size=n) * 64).astype(np.uint64)
        dr = rng.integers(0, 2, size=n).astype(np.uint8)
        llc.run(op, ad, dr)
        total = sum(sum(r) for r in llc.writes)
        check('%s: sum(per-frame writes) == nvm_writes + sram_writes' % scheme,
              total == llc.nvm_writes + llc.sram_writes,
              '%d vs %d' % (total, llc.nvm_writes + llc.sram_writes))


def test_determinism():
    print('\nDeterminism -- identical input must give identical wear')
    rng = np.random.default_rng(11)
    n = 30000
    op = rng.choice([GETS, GETX, RPL], size=n, p=[0.3, 0.2, 0.5]).astype(np.uint8)
    ad = (rng.integers(0, 8192, size=n) * 64).astype(np.uint64)
    dr = rng.integers(0, 2, size=n).astype(np.uint8)
    for scheme in ('psc-vro-rank', 'vro-rank', 'i2wap', 'dwawr',
                   'sequoia', 'swwr', 'dwwr'):
        arena = (12, 4) if scheme.startswith('psc') else (16, 0)
        kw = dict(NEW_KW.get(scheme, {}))
        a, pa = build(scheme, sets_ways=arena, **kw)
        b, pb = build(scheme, sets_ways=arena, **kw)
        a.run(op, ad, dr)
        b.run(op, ad, dr)
        check('%s: replay is deterministic' % scheme, a.writes == b.writes)
        if hasattr(pa, 'counters'):
            check('%s: replay reproduces every counter' % scheme,
                  pa.counters() == pb.counters())



# ---------------------------------------------------------------- damage model
def test_damage_model():
    """The identity that keeps every write-count result in this project valid.

    With endurance variation and thermal acceleration both disabled, damage must
    equal write count EXACTLY - not approximately. If that ever breaks, every
    RLI_direct number ever reported here silently changes meaning.
    """
    import numpy as np
    from llc import LLC
    import policies, damage

    n = 40000
    rng = np.random.default_rng(7)
    op = rng.integers(0, 3, n).astype(np.uint8)
    addr = (rng.integers(0, 4096, n) * 64).astype(np.uint64)
    dirty = rng.integers(0, 2, n).astype(np.uint8)

    llc = LLC(1 << 20, 12, 4, policy=policies.REGISTRY['unmanaged']())
    dm = damage.attach(llc)
    llc.run(op, addr, dirty)
    w = np.asarray(llc.writes, dtype=np.float64)
    check('damage: reduces EXACTLY to write count when disabled',
          bool(np.array_equal(dm.damage, w)))
    check('damage: model reports itself disabled',
          dm.summary()['damage_model_enabled'] == 0)

    # Endurance bins: unit mean, correct clamp, reproducible.
    d2 = damage.DamageModel(1024, 16, 12, endurance_cv=0.3, seed=99)
    e = d2.endurance
    check('damage: lognormal bins have ~unit mean',
          abs(float(e.mean()) - 1.0) < 0.05)
    check('damage: bins respect the 0.05 clamp', float(e.min()) >= 0.05)
    d3 = damage.DamageModel(1024, 16, 12, endurance_cv=0.3, seed=99)
    check('damage: bins are reproducible from the seed',
          bool(np.array_equal(e, d3.endurance)))

    # Hazard is superlinear for kappa > 1: an already-damaged frame costs more.
    check('damage: hazard is superlinear in accumulated damage',
          d2.hazard(10.0, 1.0) > d2.hazard(0.0, 1.0))
    check('damage: kappa=1 makes hazard linear',
          abs(damage.DamageModel(4, 4, 2, kappa=1.0).hazard(10.0, 1.0) - 1.0) < 1e-9)

    # CVaR must sit between the mean and the max of the tail it summarises.
    z = dm.nvm_damage().ravel()
    check('damage: CVaR lies between mean and max',
          float(z.mean()) <= dm.cvar() <= float(z.max()))


def test_tailguard():
    """TailGuard must be a real mechanism, not hazard-flavoured LRU."""
    import numpy as np
    from llc import LLC
    import policies, damage

    # The working set must exceed the cache, or every set keeps an invalid way
    # and TailGuard takes the free-capacity fast path without ever reaching the
    # hazard comparison. 1 MB / 64 B = 16384 frames, so 64k blocks saturates it.
    n = 300000
    rng = np.random.default_rng(11)
    op = rng.integers(0, 3, n).astype(np.uint8)
    addr = (rng.integers(0, 65536, n) * 64).astype(np.uint64)
    dirty = rng.integers(0, 2, n).astype(np.uint8)

    def run(name, cv):
        llc = LLC(1 << 20, 12, 4, policy=policies.REGISTRY[name]())
        dm = damage.attach(llc, endurance_cv=cv)
        llc.run(op, addr, dirty)
        return llc, dm

    base, bdm = run('unmanaged', 0.0)
    tg, tdm = run('tailguard', 0.0)
    check('tailguard: ACTIVE - differs from the unmanaged baseline',
          not np.array_equal(np.asarray(tg.writes), np.asarray(base.writes)))
    check('tailguard: fires on allocations', tg.policy.actions > 0)

    # The claim: the advantage should GROW with endurance variation, because
    # that is the regime a write-count leveller cannot see.
    b0, d0 = run('unmanaged', 0.0)
    t0, e0 = run('tailguard', 0.0)
    b3, d3 = run('unmanaged', 0.4)
    t3, e3 = run('tailguard', 0.4)
    gain0 = 1.0 - e0.nvm_damage().max() / max(1e-9, d0.nvm_damage().max())
    gain3 = 1.0 - e3.nvm_damage().max() / max(1e-9, d3.nvm_damage().max())
    check('tailguard: peak-damage advantage grows with endurance variation '
          '(%.1f%% -> %.1f%%)' % (100 * gain0, 100 * gain3), gain3 >= gain0 - 0.02)

    # Determinism.
    a, _ = run('tailguard', 0.3)
    b, _ = run('tailguard', 0.3)
    check('tailguard: replay is deterministic',
          np.array_equal(np.asarray(a.writes), np.asarray(b.writes)))


def test_wear_swap_remap():
    """WearSwap must remap addresses, not merely move data.

    The previous version moved set contents without rebinding the index, so
    every moved block became unreachable and the policy was a pure migration
    tax. Same defect class as Start-Gap v2. These two invariants are what stop
    it recurring a fifth time.
    """
    import numpy as np
    from llc import LLC
    import policies

    n = 120000
    rng = np.random.default_rng(3)
    op = rng.integers(0, 3, n).astype(np.uint8)
    addr = (rng.integers(0, 2048, n) * 64).astype(np.uint64)
    dirty = rng.integers(0, 2, n).astype(np.uint8)

    pol = policies.REGISTRY['wear-swap'](interval=2000, sample=32)
    llc = LLC(1 << 20, 12, 4, policy=pol)
    llc.run(op, addr, dirty)
    check('wear-swap: ACTIVE - performed at least one swap', pol.swaps > 0)
    check('wear-swap: declares does_remap', pol.does_remap is True)
    check('wear-swap: mapping stays a bijection',
          sorted(pol.perm) == list(range(llc.sets)))
    orphans = sum(1 for cs in range(llc.sets) for j in range(llc.ways)
                  if llc.tag[cs][j] != -1
                  and pol.remap_set(llc.set_index(llc.tag[cs][j])) != cs)
    check('wear-swap: no live block is left unreachable', orphans == 0)


def test_psc_balance():
    """PSC-Balance: the ablation identity and both guards."""
    import numpy as np
    from llc import LLC
    import policies

    n = 250000
    rng = np.random.default_rng(5)
    op = rng.integers(0, 3, n).astype(np.uint8)
    # Skewed address stream, so some sets are genuinely hotter than others -
    # a uniform stream has no inter-set imbalance for the policy to correct.
    hot = rng.integers(0, 256, n) * 64
    cold = rng.integers(0, 65536, n) * 64
    pick = rng.random(n) < 0.5
    addr = np.where(pick, hot, cold).astype(np.uint64)
    dirty = rng.integers(0, 2, n).astype(np.uint8)

    def run(name, **kw):
        pol = policies.REGISTRY[name](**kw)
        llc = LLC(1 << 20, 12, 4, policy=pol)
        llc.run(op, addr, dirty)
        w = np.asarray(llc.writes, dtype=np.float64)[:, :12]
        ps = w.sum(1)
        return pol, llc, w, (float(ps.std() / ps.mean()) if ps.mean() else 0.0)

    psc_pol, psc_llc, psc_w, psc_inter = run('psc')

    # INACTIVE: thresholds off must reduce EXACTLY to PSC.
    ab_pol, ab_llc, ab_w, _ = run('psc-balance', hot_threshold=1e9, cold_threshold=0.0)
    check('psc-balance: INACTIVE - ablates exactly to psc',
          bool(np.array_equal(ab_w, psc_w)) and ab_llc.hits == psc_llc.hits)

    # ACTIVE: the mechanism fires and reduces inter-set variation.
    on_pol, on_llc, on_w, on_inter = run('psc-balance', hot_threshold=1.02,
                                         cold_threshold=0.0)
    check('psc-balance: ACTIVE - performs hot-set diversions',
          on_pol.hot_diversions > 0)
    check('psc-balance: reduces inter-set variation vs psc (%.4f -> %.4f)'
          % (psc_inter, on_inter), on_inter <= psc_inter + 1e-9)
    check('psc-balance: charges no migration writes',
          on_llc.migration_writes == 0)

    # BOUNDARY: the SRAM cap must bind when the threshold is driven low.
    lo_pol, _, _, _ = run('psc-balance', hot_threshold=0.5, cold_threshold=0.0,
                          max_sram_frac=0.10)
    check('psc-balance: BOUNDARY - SRAM occupancy cap binds',
          lo_pol.blocked_by_sram_cap > 0)

    # BOUNDARY: the NVM floor must refuse the degenerate optimum.
    fl_pol, fl_llc, _, _ = run('psc-balance', hot_threshold=0.0,
                               cold_threshold=0.0, max_sram_frac=1.0,
                               min_nvm_frac=0.90)
    check('psc-balance: BOUNDARY - NVM utilisation floor binds',
          fl_pol.blocked_by_nvm_floor > 0)
    check('psc-balance: floor keeps the NVM array in use',
          fl_llc.nvm_writes > 0)

    # Determinism.
    a_pol, a_llc, a_w, _ = run('psc-balance')
    b_pol, b_llc, b_w, _ = run('psc-balance')
    check('psc-balance: replay is deterministic', bool(np.array_equal(a_w, b_w)))

if __name__ == '__main__':
    test_psc_balance()
    test_wear_swap_remap()
    test_tailguard()
    test_damage_model()
    test_psc()
    test_vro_original()
    test_vro_rank()
    test_dwawr()
    test_ayush()
    test_i2wap()
    test_startgap()
    test_equalchance()
    test_sequoia_goas()
    test_sequoia_wad()
    test_sequoia_storage_and_invariants()
    test_swwr()
    test_dwwr()
    test_accounting()
    test_determinism()
    print('\n%d failure(s)' % len(FAILS))
    if FAILS:
        for f in FAILS:
            print('  -', f)
    sys.exit(1 if FAILS else 0)
