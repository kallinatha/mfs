#!/usr/bin/env python3
"""Write-recurrence profiler: the experiment that decides whether any
write-absorption mechanism is worth building.

Every hybrid scheme that promotes hot blocks into SRAM rests on an unstated
assumption: that a meaningful share of blocks are written **more than once while
resident**. If almost every block is written once and then evicted, there is no
burst to absorb, promotion has nothing to promote, and the whole family - PSC,
AYUSH, ELBA - collapses to LRU with extra hardware.  The same measurement
bounds every *write-hit-triggered* wear-levelling scheme (Sequoia's G-OAS and
WAD, i2WAP's PoLF, SWWR/DWWR's write redirection): if blocks are written once
and evicted, those triggers never fire.

The last-level cache filters temporal locality by construction, so this is a
real possibility and not a formality. It costs one pass over the trace to find
out, and it should be run before a line of controller code is written.

Reported:

  recurrence      distribution of writes-per-residency, and the share of NVM
                  write traffic attributable to second-and-later writes. That
                  share is the ceiling on what any absorption scheme can remove.
  burst set       number of distinct blocks in a write burst within a window,
                  sampled over time. This is the set point an elastic absorber
                  would have to track, and its variance says whether a fixed
                  partition could have sufficed.
  spread          how the burst set distributes across cache sets. If it is
                  even, per-set SRAM ways are the right shape and a globally
                  pooled buffer buys nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llc import LLC          # noqa: E402
import policies              # noqa: E402

GETS, GETX, RPL = 0, 1, 2


def profile(op, addr, dirty, size_mb, nvm_ways, sram_ways, window, samples):
    """Replay under plain LRU and record write behaviour per residency."""
    llc = LLC(size_mb * (1 << 20), nvm_ways, sram_ways,
              policy=policies.REGISTRY['unmanaged']())
    sets, ways = llc.sets, llc.ways

    tag = [[-1] * ways for _ in range(sets)]
    mru = [[0] * ways for _ in range(sets)]
    wcount = [[0] * ways for _ in range(sets)]      # writes during this residency

    residency_writes = Counter()                    # writes-per-residency histogram
    total_writes = 0
    repeat_writes = 0

    burst_samples = []
    spread_samples = []
    recent_writes = {}                              # addr -> last write index

    n = len(op)
    step = max(1, n // max(1, samples))

    for i in range(n):
        a = int(addr[i])
        cs = llc.set_index(a)
        t = tag[cs]
        way = -1
        for j in range(ways):
            if t[j] == a:
                way = j
                break
        o = op[i]

        if o == RPL:
            if way == -1:
                # allocate: LRU victim, closing that frame's residency
                victim, best = -1, None
                for j in range(ways):
                    if t[j] == -1:
                        victim = j
                        break
                    if best is None or mru[j] < best:
                        best, victim = mru[j], j
                if t[victim] != -1:
                    residency_writes[wcount[cs][victim]] += 1
                t[victim] = a
                mru[cs][victim] = i
                wcount[cs][victim] = 1
                total_writes += 1
            else:
                # write to a resident block: this is the recurrence we care about
                wcount[cs][way] += 1
                mru[cs][way] = i
                total_writes += 1
                repeat_writes += 1
                recent_writes[a] = i
        else:
            if way >= 0:
                mru[cs][way] = i

        if i % step == 0 and i > window:
            live = [ad for ad, last in recent_writes.items() if i - last <= window]
            burst_samples.append(len(live))
            if live:
                per_set = Counter(llc.set_index(ad) for ad in live)
                counts = np.array(sorted(per_set.values(), reverse=True), dtype=float)
                spread_samples.append(dict(
                    distinct_sets=int(len(per_set)),
                    max_per_set=int(counts[0]),
                    mean_per_set=float(counts.mean()),
                    top_decile_share=float(counts[:max(1, len(counts) // 10)].sum() / counts.sum())))
            if len(recent_writes) > 4_000_000:      # bound the tracker
                recent_writes = {ad: last for ad, last in recent_writes.items()
                                 if i - last <= window}

    # close out residencies still open at the end
    for s in range(sets):
        for j in range(ways):
            if tag[s][j] != -1:
                residency_writes[wcount[s][j]] += 1

    hist = dict(sorted(residency_writes.items()))
    residencies = sum(hist.values())
    once = hist.get(1, 0)
    burst = np.array(burst_samples, dtype=float) if burst_samples else np.array([0.0])

    out = dict(
        accesses=int(n),
        sets=sets, ways=ways, nvm_ways=nvm_ways, sram_ways=sram_ways,
        size_MB=size_mb, window=window,
        residencies=int(residencies),
        writes_total=int(total_writes),
        writes_repeat=int(repeat_writes),
        # This is the headline: the share of write traffic an absorber could remove.
        # 'writes' counts write *events* (allocations + repeat writes). Only
        # allocations are charged as wear in this protocol - see verdict().
        repeat_write_share=float(repeat_writes / total_writes) if total_writes else 0.0,
        charged_writes=int(total_writes - repeat_writes),
        wear_headroom_from_absorption=0.0,
        written_once_share=float(once / residencies) if residencies else 0.0,
        residency_write_histogram={str(k): int(v) for k, v in list(hist.items())[:32]},
        burst_set_mean=float(burst.mean()),
        burst_set_p50=float(np.percentile(burst, 50)),
        burst_set_p95=float(np.percentile(burst, 95)),
        burst_set_max=float(burst.max()),
        burst_set_cv=float(burst.std() / burst.mean()) if burst.mean() else 0.0,
        sram_frames_available=int(sets * sram_ways),
    )
    if spread_samples:
        out.update(
            spread_distinct_sets_mean=float(np.mean([s['distinct_sets'] for s in spread_samples])),
            spread_max_per_set_mean=float(np.mean([s['max_per_set'] for s in spread_samples])),
            spread_max_per_set_p95=float(np.percentile([s['max_per_set'] for s in spread_samples], 95)),
            spread_top_decile_share=float(np.mean([s['top_decile_share'] for s in spread_samples])))
    return out


def verdict(p):
    """State plainly what the numbers permit.

    Read this before the percentages. Under HyCSim's BaseHybrid protocol - which
    this model reproduces bit-exactly, see tests/test_hycsim_conformance.py - a
    writeback to a *resident* block performs no data write at all. HyCSim's own
    source asserts such a writeback is never dirty, because GetS on a dirty
    block and GetX both invalidate the LLC copy when it goes up to L2. Every
    charged NVM write is therefore an allocation.

    So `repeat_write_share` counts *events*, not *wear*. The wear headroom
    available to any absorption or promotion mechanism in this protocol is
    exactly zero, however large that percentage looks. The lever that remains is
    allocation placement - which way a fill lands in - which is where psc-soft
    acts, and why it works where ELBA's promote-and-release does not.

    An inclusive LLC (ChampSim's, for instance) does charge write hits, and
    there the same mechanisms have real headroom. Always report which protocol a
    number came from. The two disagree by construction, and that disagreement is
    a finding, not an inconsistency.
    """
    lines = []
    r = p['repeat_write_share']
    lines.append('PROTOCOL: under HyCSim BaseHybrid semantics a repeat write to a '
                 'resident block charges NO array write. Wear headroom for absorption '
                 'is 0% here regardless of the figure below; repeat writes are a '
                 'recency signal, not a wear signal.')
    if r < 0.05:
        lines.append('KILL: only %.1f%% of write events are repeats. There is no burst '
                     'even as a signal, so every write-hit-triggered scheme is inert: '
                     'PSC, AYUSH, ELBA, Sequoia G-OAS/WAD, i2WAP PoLF, SWWR/DWWR.'
                     % (100 * r))
    elif r < 0.20:
        lines.append('WEAK: %.1f%% of write events are repeats. Enough to trigger a '
                     'mechanism, not enough to expect much from it.' % (100 * r))
    else:
        lines.append('SIGNAL PRESENT: %.1f%% of write events are repeats, so write-hit '
                     'triggers will fire. Under this protocol that buys hit rate, not '
                     'lifetime. Measure both; do not conflate them.' % (100 * r))

    b, cap = p['burst_set_p95'], p['sram_frames_available']
    if cap:
        lines.append('Burst working set p95 = %.0f blocks against %d SRAM frames (%.2fx). %s'
                     % (b, cap, b / cap,
                        'Fits: a fixed partition may suffice.' if b <= cap else
                        'Overflows: a fixed partition cannot hold the burst.'))
    cv = p['burst_set_cv']
    lines.append('Burst set CV = %.2f. %s' % (
        cv, 'Stable, so elasticity buys little.' if cv < 0.25 else
            'Variable, so a single fixed split is wrong for some phase.'))
    if 'spread_max_per_set_p95' in p:
        lines.append('Hottest set holds %.0f burst blocks at p95 against %d SRAM ways per set. %s'
                     % (p['spread_max_per_set_p95'], p['sram_ways'],
                        'Per-set ways are the wrong shape; pooling should help.'
                        if p['spread_max_per_set_p95'] > p['sram_ways'] else
                        'Per-set ways are adequately shaped; pooling buys little.'))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('trace', help='prepared .npz or per-mix .npy directory')
    ap.add_argument('--size-mb', type=int, default=1)
    ap.add_argument('--nvm-ways', type=int, default=12)
    ap.add_argument('--sram-ways', type=int, default=4)
    ap.add_argument('--window', type=int, default=100000,
                    help='accesses within which two writes count as one burst')
    ap.add_argument('--samples', type=int, default=200)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    if os.path.isdir(a.trace):
        op = np.load(os.path.join(a.trace, 'op.npy'), mmap_mode='r')
        addr = np.load(os.path.join(a.trace, 'addr.npy'), mmap_mode='r')
        dirty = np.load(os.path.join(a.trace, 'dirty.npy'), mmap_mode='r')
    else:
        z = np.load(a.trace)
        op, addr, dirty = z['op'], z['addr'], z['dirty']
    if a.limit:
        op, addr, dirty = op[:a.limit], addr[:a.limit], dirty[:a.limit]

    p = profile(np.asarray(op), np.asarray(addr), np.asarray(dirty),
                a.size_mb, a.nvm_ways, a.sram_ways, a.window, a.samples)
    p['trace'] = os.path.basename(a.trace.rstrip('/'))
    for line in verdict(p):
        p.setdefault('verdict', []).append(line)

    if a.out:
        with open(a.out, 'w') as f:
            json.dump(p, f, indent=2)
        print('wrote', a.out)
    print('\n=== write recurrence: %s (%d accesses, %d MB) ===' % (p['trace'], p['accesses'], p['size_MB']))
    print('  charged writes (fills)  %d   <- all NVM wear comes from these' % p['charged_writes'])
    print('  repeat write EVENTS     %.1f%%   (%d of %d events, 0 charged wear)'
          % (100 * p['repeat_write_share'], p['writes_repeat'], p['writes_total']))
    print('  residencies written once %.1f%%' % (100 * p['written_once_share']))
    print('  burst set  mean %.0f  p50 %.0f  p95 %.0f  max %.0f  CV %.2f'
          % (p['burst_set_mean'], p['burst_set_p50'], p['burst_set_p95'],
             p['burst_set_max'], p['burst_set_cv']))
    print('  SRAM frames available   %d' % p['sram_frames_available'])
    if 'spread_max_per_set_p95' in p:
        print('  hottest set (p95)       %.0f burst blocks vs %d SRAM ways/set'
              % (p['spread_max_per_set_p95'], p['sram_ways']))
    print()
    for line in p['verdict']:
        print('  ' + line)


if __name__ == '__main__':
    main()
