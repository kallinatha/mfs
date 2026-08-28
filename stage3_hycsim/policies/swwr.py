#!/usr/bin/env python3
"""SWWR / DWWR - Sukarn Agarwal, "Techniques for Improving the Lifetime and
Performance of Non-Volatile Last Level Caches", PhD thesis, IIT Guwahati, 2020,
Chapter 4 ("Intra-Set Wear Leveling using Vertical Partitions"), thesis
pp. 95-127 = PDF pp. 125-157, Algorithms 1 and 2.

=========================================================== WHY linovo.py IS GONE

This file replaces `policies/linovo.py`, which has been deleted.  That file was
unfaithful in a way that no parameter change could repair:

  * "LiNoVo" is the **title of the thesis / of the ISVLSI 2020 paper**, not the
    name of a mechanism.  A policy called `linovo` implementing "the LiNoVo
    algorithm" was naming something that does not exist.
  * What linovo.py actually approximated - writes confined to a rotating window
    of consecutive ways, the window advancing on a write counter - is Chapter
    4's **write-restricted rotating window**, i.e. SWWR, with two substantive
    errors: the window was the *permitted* region rather than the *restricted*
    one (SWWR restricts one window out of m and allows the other m-1, which is
    the opposite geometry), and the rotation was clocked in *writes* rather than
    in *cycles*.
  * It also bolted on a fully associative victim buffer shared across sets,
    which appears nowhere in Chapter 4 and which absorbed allocations - so the
    measured "LiNoVo" write reduction was substantially the victim buffer, not
    the write restriction.
  * `docs/RUN_PLAN.md` and `docs/PACKAGE_REVIEW.md` had already flagged the file
    as an approximation that must not be reported under that name.

The correct citation for what is implemented here is Chapter 4 of the thesis
(SWWR: Sec. 4.2.1, Algorithm 1; DWWR: Sec. 4.2.2, Algorithm 2).

================================================================= THE MECHANISM

Both techniques partition the ways of a set-associative cache **vertically**
into m equal windows and make exactly one window write-restricted (read-only)
for a predefined interval I of processor cycles.

Algorithm 1 (SWWR), verbatim on the four cases:

    Read Hit        "The requested block B in the LLC is served normally to
                     higher-level cache irrespective of its location."
    Write Hit and B in Wi
                    "If B belongs to the selected window Wi with the invalid
                     line(s) in the other windows of the same cache set, the
                     request R from ULC is redirected to the first invalid line
                     and the Block B is invalidated from the respective location
                     in write restricted window.  In the other case, when there
                     is no invalid line in the other windows of the same cache
                     set, the LRU victim line v is picked from the Location L
                     and the write-back operation is performed according to its
                     dirty bit ... Once the victim v is evicted from the LLC,
                     the write request from a ULC is redirected to the generated
                     location L and the Block B is invalidated from its
                     respective location in WRW."
    Write Hit and B not in Wi
                    "The requested Block B in the LLC is written normally."
    LLC miss        "the newly arrived Block is placed in the location other
                     than the WRW Wi location."

Window selection:

    SWWR  `WinSelect(i, m): i = (i + 1) % m`  - round robin (Alg. 1, l. 27-30).
    DWWR  `WinSelect(Ci, m): i = max(Ci); reset Ci to zero` (Alg. 2, l. 30-34),
          with Ci "a counter that is used to track the number of writes during
          the past interval to the window (i.e., from ULC to LLC)", incremented
          on a redirected write at its destination window (Alg. 2 l. 19) and on
          a normal write hit at the window holding the block (Alg. 2 l. 22).
          The counter of the *selected* window is reset "to remove the
          possibility of selecting the same window in the consecutive
          intervals".

Sec. 4.4.1: I = 2 million cycles (dual-core), 1 million cycles (quad-core);
m = 4; 16-way L2.  Sec. 4.5.5 / Table 4.18: the Ci counters are 13 bits, the
tag-transfer buffer is 42 bits, "the amount of overheads by SWWR is only 42
bits" and DWWR is 4 x 13 + 42 = 94 bits.  `state_bits()` reproduces both.

Note that the storage is *global*, not per set: one window index is restricted
across the whole cache and DWWR keeps m counters for the whole cache.  That is
why the overhead is tens of bits rather than tens of kilobytes.

============================================ REQUIRED HYBRID-ARRAY ADAPTATION

R1. **Only the NVM partition is windowed.**  Ways [0, nvm_ways) are divided into
    m windows; SRAM ways are never restricted.  Restricting an SRAM way removes
    capacity and levels nothing, since SRAM does not wear; a scheme that did so
    would be paying SWWR's cost for none of its benefit.  Window w covers ways
    [w*M//m, (w+1)*M//m), which handles M not divisible by m (M=12, m=4 gives
    three ways per window; M=16, m=4 gives the thesis's four).

R2. **Redirect and fill targets are every non-restricted location, including
    SRAM ways.**  The thesis's rule is "a location L ... other than the write
    restricted window location", and in this array the non-restricted locations
    include the SRAM ways.  Excluding them would invent a technology-aware
    variant the thesis does not have.  Direction: including them *flatters*
    SWWR/DWWR, because a redirect that lands in SRAM costs no NVM wear at all.
    `redirect_to_sram` and `alloc_to_sram` count exactly how often that happened
    so the size of the flattery is auditable.  DWWR's window counters are only
    incremented for NVM landings, since SRAM ways belong to no window.

R3. **Restricted ways receive no writes of any kind while restricted** - not
    redirected write hits and not fills.  Contrary to the brief this task was
    given, the thesis *does* state the fill rule explicitly: Algorithm 1's
    `processCacheMiss` reads "Keep the newly arrived block in a location other
    than WRW location", and the prose repeats it ("the newly arrived Block is
    placed in the location other than the WRW Wi location").  It is therefore
    implemented as specification, not as an assumption.

========================================================== MODEL ADAPTATIONS

M1. **The interval is in cycles, and `llc.run` does not pass cycles.**  The
    prepared traces carry a `cycle.npy` alongside `op/addr/dirty`; call
    `policy.set_cycles(cycle_array)` before `llc.run` and the interval is
    measured in real cycles, as the thesis specifies.  With no cycle array the
    policy falls back to the record index as a proxy clock, and `clock_source`
    records which was used - a run that silently measured intervals in accesses
    instead of cycles would report an I that is off by ~3 orders of magnitude on
    this trace.

M2. **Where the clock ticks.**  The model gives a policy a timestamp in
    `on_write_hit` and in `aux_lookup`; `aux_lookup` is consulted on every miss,
    which is the overwhelming majority of accesses in this exclusive model, so
    the interval boundary can be late only by a run of consecutive read hits.
    `clock_ticks` counts the ticks actually observed.

M3. **This model starves the write-hit path.**  The array is filled only by an
    `L2_Rpl` that misses; an `L2_Rpl` to a resident block performs no data
    write.  The redirect case therefore fires rarely, while the fill-avoids-WRW
    case fires on every allocation.  In this model SWWR/DWWR are consequently
    almost entirely a *fill placement* restriction.  For **DWWR** this is worse
    than a matter of degree: Algorithm 2 increments Ci only in the two write-hit
    branches, never in `processCacheMiss`, so with write hits starved the
    counters stay near zero, `max(Ci)` degenerates to the lowest-index window
    and DWWR collapses onto a *static* restriction of window 0.
    `degenerate_selections` counts every interval whose argmax was taken over an
    all-zero counter vector, so the collapse is visible rather than inferred.
    The parameter `count_fills=1` (default 0, i.e. thesis-faithful) additionally
    counts allocations in Ci and can be used to measure the sensitivity; it is
    **not** what Algorithm 2 says.

M4. **Tie-breaking.**  `max(Ci)` with equal counters resolves to the lowest
    index.  Deterministic and documented; round-robin tie-breaking would
    silently turn DWWR into SWWR.

M5. **First interval.**  "Run application for I cycles treating the whole cache
    as normally available cache" - no window is restricted until the first
    interval boundary (`wrw = -1`).  SWWR's `i` is initialised to -1 so the
    first selected window is W0, matching Figure 4.2.
"""
from .base import Policy


class SWWR(Policy):
    """Static Window Write Restriction - Chapter 4, Sec. 4.2.1, Algorithm 1."""
    name = 'swwr'
    PARAMS = dict(
        m=4,                    # number of vertical windows, Sec. 4.4.1
        interval=2_000_000,     # I, cycles: 2M dual-core / 1M quad-core
        tag_buffer_bits=42,     # Sec. 4.5.5 / Table 4.18
    )

    # ------------------------------------------------------------------ setup
    def setup(self):
        llc = self.llc
        self.M = llc.nvm_ways
        m = int(self.params['m'])
        if self.M < 1:
            raise ValueError('%s needs at least one NVM way' % self.name)
        self.m = max(1, min(m, self.M))
        # window w covers NVM ways [w*M//m, (w+1)*M//m)  (R1)
        self.bounds = [(w * self.M // self.m, (w + 1) * self.M // self.m)
                       for w in range(self.m)]
        self.win_of = [0] * llc.ways
        for w, (lo, hi) in enumerate(self.bounds):
            for j in range(lo, hi):
                self.win_of[j] = w
        for j in range(self.M, llc.ways):
            self.win_of[j] = -1              # SRAM ways belong to no window

        self.wrw = -1                        # M5: unrestricted first interval
        self.wsel = -1                       # M5: first WinSelect yields W0
        self.restricted = ()
        self._set_window(-1)

        self.interval = int(self.params['interval'])
        self.cycles = None
        self.clock_source = 'record-index'   # M1
        self.t0 = None

        # ------------------------------------------------------ instrumentation
        self.clock_ticks = 0
        self.intervals = 0
        self.allocations = 0
        self.alloc_deflected = 0             # fill that avoided the WRW
        self.alloc_to_sram = 0
        self.write_hits = 0
        self.write_hits_restricted = 0
        self.write_hits_unrestricted = 0
        self.redirects = 0
        self.redirect_to_invalid = 0
        self.redirect_evictions = 0
        self.redirect_writebacks = 0
        self.redirect_to_sram = 0
        self.degenerate_selections = 0

    # ------------------------------------------------------------------- clock
    def set_cycles(self, arr):
        """Bind the trace's cycle column so I is measured in cycles (M1)."""
        self.cycles = arr
        self.clock_source = 'cycles'
        return self

    def _clock(self, now):
        self.clock_ticks += 1
        c = int(self.cycles[now]) if self.cycles is not None else int(now)
        if self.t0 is None:
            self.t0 = c
            return
        if c - self.t0 < self.interval:
            return
        n = (c - self.t0) // self.interval
        self.t0 += n * self.interval
        for _ in range(int(n)):
            self.intervals += 1
            self._set_window(self._select_window())

    def _set_window(self, w):
        self.wrw = w
        if w < 0:
            self.restricted = ()
        else:
            lo, hi = self.bounds[w]
            self.restricted = tuple(range(lo, hi))
        rs = set(self.restricted)
        self.cand = [j for j in range(self.llc.ways) if j not in rs]

    def _select_window(self):
        """Algorithm 1, WinSelect(i, m): round robin."""
        self.wsel = (self.wsel + 1) % self.m
        return self.wsel

    # ------------------------------------------------------------------- cost
    def state_bits(self):
        """Sec. 4.5.5: "The amount of overheads by SWWR is only 42 bits" - the
        tag-transfer buffer used to carry a tag to its redirected location.  The
        thesis charges nothing for the window pointer or the interval counter;
        this reproduces its arithmetic rather than correcting it."""
        return int(self.params['tag_buffer_bits'])

    # ------------------------------------------------------------------ hooks
    def aux_lookup(self, addr, op, now):
        """Not an auxiliary storage structure - the only per-access hook the
        model offers on the miss path, used here purely to advance the cycle
        clock (M2).  It never serves a block."""
        self._clock(now)
        return False

    def choose_way(self, cs, addr, is_dirty):
        """Algorithm 1, `processCacheMiss`: place the arriving block anywhere
        except the write-restricted window (R3)."""
        llc = self.llc
        if self.wrw < 0:
            return llc.lru(cs, range(llc.ways))
        plain = llc.lru(cs, range(llc.ways))
        w = llc.lru(cs, self.cand)
        if w != plain:
            self.alloc_deflected += 1
        return w

    def on_write(self, cs, way):
        self.allocations += 1
        if way >= self.M:
            self.alloc_to_sram += 1
        else:
            self._count_window_write(self.win_of[way], fill=True)

    def on_write_hit(self, cs, way, is_dirty, now):
        """Algorithm 1, lines 15-20.  A write hit inside the WRW is redirected
        to a non-restricted location in the same set and the original line is
        invalidated; a write hit outside the WRW is served normally."""
        self._clock(now)
        self.write_hits += 1
        llc = self.llc
        if self.wrw < 0 or way not in self.restricted:
            self.write_hits_unrestricted += 1
            if way < self.M:
                self._count_window_write(self.win_of[way], fill=False)
            return
        self.write_hits_restricted += 1

        # "redirected to the first invalid line" in the other windows ...
        dest = -1
        for j in self.cand:
            if llc.tag[cs][j] == -1:
                dest = j
                break
        if dest >= 0:
            self.redirect_to_invalid += 1
        else:
            # ... otherwise the LRU victim of the non-restricted locations is
            # evicted and written back according to its dirty bit.
            dest = llc.lru(cs, self.cand)
            if dest < 0:
                return
            if llc.tag[cs][dest] != -1:
                if llc.dirty[cs][dest]:
                    self.redirect_writebacks += 1
                llc.flushes += 1
                self.redirect_evictions += 1

        llc.tag[cs][dest] = llc.tag[cs][way]
        llc.dirty[cs][dest] = is_dirty
        llc.mru[cs][dest] = now
        llc.charge_write(cs, dest)        # the redirected write is a real write
        llc.tag[cs][way] = -1             # "the Block B is invalidated from WRW"
        llc.dirty[cs][way] = 0
        self.redirects += 1
        if dest >= self.M:
            self.redirect_to_sram += 1
        else:
            self._count_window_write(self.win_of[dest], fill=False)

    # ------------------------------------------------------- DWWR hook (no-op)
    def _count_window_write(self, w, fill):
        pass

    def counters(self):
        keys = ('clock_source', 'clock_ticks', 'intervals', 'wrw', 'allocations',
                'alloc_deflected', 'alloc_to_sram', 'write_hits',
                'write_hits_restricted', 'write_hits_unrestricted', 'redirects',
                'redirect_to_invalid', 'redirect_evictions',
                'redirect_writebacks', 'redirect_to_sram',
                'degenerate_selections')
        return {k: getattr(self, k) for k in keys}


class DWWR(SWWR):
    """Dynamic Window Write Restriction - Chapter 4, Sec. 4.2.2, Algorithm 2.

    Identical to SWWR except that the restricted window is the argmax over m
    per-window write counters, and that counter is reset when its window is
    selected so it cannot be selected twice in a row.
    """
    name = 'dwwr'
    PARAMS = dict(
        m=4,
        interval=2_000_000,
        tag_buffer_bits=42,
        counter_bits=13,        # Sec. 4.5.5: "the size of the counter Ci ... 13 bit"
        count_fills=0,          # M3: 0 = Algorithm 2 verbatim
    )

    def setup(self):
        SWWR.setup(self)
        self.counter_bits = int(self.params['counter_bits'])
        self.counter_max = (1 << self.counter_bits) - 1
        self.C = [0] * self.m
        self.counter_saturations = 0

    def state_bits(self):
        """Table 4.18: m window counters of `counter_bits` plus the 42-bit tag
        buffer.  With m = 4 and 13-bit counters this is 4*13 + 42 = 94 bits."""
        return self.m * self.counter_bits + int(self.params['tag_buffer_bits'])

    def _select_window(self):
        """Algorithm 2, WinSelect(Ci, m): i = max(Ci); reset Ci; return i."""
        best, bestv = 0, self.C[0]
        for i in range(1, self.m):
            if self.C[i] > bestv:            # M4: ties resolve to lowest index
                best, bestv = i, self.C[i]
        if bestv == 0:
            self.degenerate_selections += 1  # M3
        self.C[best] = 0
        return best

    def _count_window_write(self, w, fill):
        if w < 0:
            return
        if fill and not int(self.params['count_fills']):
            return                            # Algorithm 2 counts write hits only
        if self.C[w] < self.counter_max:
            self.C[w] += 1
        else:
            self.counter_saturations += 1

    def counters(self):
        d = SWWR.counters(self)
        d['C'] = list(self.C)
        d['counter_saturations'] = self.counter_saturations
        return d
