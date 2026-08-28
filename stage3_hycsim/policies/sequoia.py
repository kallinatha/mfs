#!/usr/bin/env python3
"""Sequoia - Jokar, Arjomand & Sarbazi-Azad, "Sequoia: A High-Endurance
NVM-Based Cache Architecture", IEEE TVLSI 24(3):954-967, 2016.

Written against the paper (Sections IV-A, IV-B, VI-B4 and VI-B5 and Figures 3
and 4), not against a summary of it.  Every departure from the text is named
below together with the direction it pushes the measured result: *flatters*
means the deviation makes Sequoia look better than the published scheme would,
*penalises* means the reverse.

================================================================ THE MECHANISM

Sequoia is two independent wear-levelling procedures, and the paper is explicit
(Sec. IV, opening) that **both are triggered on write hits**:

    "Based on this observation, we assume that wear-leveling procedures are
     triggered on write-hit accesses."

G-OAS - on-access inter-set swapping (Sec. IV-A)
------------------------------------------------
Selection (Sec. IV-A1), verbatim:

    "The controller keeps a saturation counter per set, named set saturation
     counter (SSC) ... The SSC increments on each write-hit to the set.  When a
     set's SSC reaches its maximum value, it is considered hot and the next
     write-hit to this set triggers OAS to change its mapping with the coldest
     set ... In addition, the SSC of the hot set is reset.  In order to avoid
     this set to be considered cold in near future ... each set is equipped with
     another counter, named hotness saturation counter (HSC), which increments
     on saturation of SSC.  Thus, the total write-hits to a set is determined by
     concatenating its HSC and SSC, i.e., HSC||SSC ... When the HSC of a set
     saturates, the controller resets all the HSCs in the cache."

Grouping, verbatim:

    "For a group size of G, the sets whose indices differ in log G most
     significant bits, belong to one group."

so the group id is the *low* log2(S)-log2(G) index bits and the member id is the
top log2(G) bits.  Sec. VI-B3 fixes G = 64; Sec. V fixes the SSC threshold at
127 (7 bits) and the HSC at 5 bits.

Tables (Sec. IV-A3), verbatim:

    AT  "one entry per PS ... 1) log G most significant bits of index of the set
         associated with PS i and 2) an association bit (named a)"
    DT  "the i th entry of DT stores log G most significant bits of the set
         index to which i th LS is currently mapped"
    RT  "the i th entry of this table stores log G most significant bits of the
         LS index that is currently mapped to PS i"
    n   "an additional native bit ... indicates whether the line belongs to the
         LS that is mapped to its current PS (n = 1) or to its associated set
         (n = 0).  Searches always begin by examining a set using its current PS
         and for lines with n = 1.  If the line is not found and association bit
         ... is 1, a second search is performed in the previous PS."

Swapping (Sec. IV-A2, Fig. 4) - six cases, implemented one for one:

    Write First-Hit    a hits in the current PS j; normally updated.
    Write Second-Hit   a misses in j, hits in i.  B_victim(j) is written in
                       place of a in set i; a is written in place of B_victim
                       in set j.
    Write Miss         a in neither.  A_victim(i) is invalidated (written back
                       if dirty); B_victim(j) is moved into A_victim's frame in
                       i; a is written in place of B_victim in j.
    Read First-Hit     read from j.
    Read Second-Hit    read a from i, then a and B_victim(j) are swapped.
    Read Miss          as write miss, but the frame in j is filled from memory.

Victim rule, verbatim:

    "Instead, we choose the victims sequentially based on the way number in
     ascending order."

Completion / abort, verbatim:

    "In a K-way cache, swapping two sets is completed after exactly K processor
     accesses to different lines of the two sets that are not first-hit.
     However, the controller may decide to stop associating two sets before
     completing their swap when SSC of one of them saturates ... the controller
     stops an on-going association and invalidates the lines remaining in the
     previous set for correct operation (dirty lines are flushed back to main
     memory)."

WAD - write-back aware intra-set displacement (Sec. IV-B)
---------------------------------------------------------
Verbatim:

    "the controller needs a single and small counter per set, called intraset
     saturation counter (RSC) ... Since SSCs count the write-hits to the sets,
     we consider few least significant bits of the SSC as RSC.  Hence, no
     specific storage is required for intraset counters."

    "On RSC saturation, the next write-hit to a cache line assumes it to be a
     hot line ... First, the controller invalidates the hot line but does not
     change its replacement metadata (e.g., LRU tag) to protect it from next
     writes for a while.  Second, the hot line is displaced to another line as a
     victim and the content of the victim line is flushed back to main memory,
     if it is dirty."

    "we introduce Clean-LRU line as WAD-victim.  Here, we check the LRU stack
     (except MRU line) up to the point a clean line (Clean-LRU line) is found.
     If no line is found, the LRU line is selected as victim."

Sec. V sets the RSC threshold to 7, i.e. RSC is the low 3 bits of SSC.

Storage (Sec. VI-B4), verbatim:

    "each set corresponds to adding 12 bits per set (7 bits for SSC and 5 bits
     for HSC).  An entry in the AT has 6 bits (group size = 64) for index and
     one association bit.  In addition, the DT and RT have an entry of 6 bits
     per set.  Each line has one native bit ... our implementation requires
     19.5 kB storage."

    7 + 5 + (6+1) + 6 + 6 = 31 bits/set, plus 1 bit/line.
    4096 sets x 31 + 4096 x 8 lines = 159744 bits = 19.5 kiB exactly.

`state_bits()` reproduces that arithmetic for the general case.

======================================= REQUIRED HYBRID-ARRAY ADAPTATIONS

Sequoia is an all-NVM scheme.  This arena has ways [0, nvm_ways) in NVM and
[nvm_ways, ways) in SRAM.  Three adaptations, each a deliberate change:

A1. **SSC and RSC count write hits on NVM ways only.**  Sequoia has no notion of
    a wear-exempt way; counting a write hit that landed in an SRAM way would
    declare a set hot on writes that touched no NVM cell and would fire both
    mechanisms on phantom wear.  Direction: *penalises* Sequoia mildly, because
    it fires less often in the hybrid arena than in the all-NVM arena on the
    same trace.  `write_hits_sram` records exactly how many events were dropped
    this way, so the size of the effect is auditable rather than asserted.

A2. **WAD victims are restricted to NVM ways.**  Displacing a hot line into an
    SRAM way would relieve NVM wear for free; that is spare capacity, not wear
    levelling, and reporting it as Sequoia's result would flatter the
    competitor.  Direction: without this restriction Sequoia would be flattered;
    with it, the measurement is of the published mechanism.

A3. **G-OAS set interchangeability holds here.**  Inter-set association assumes
    the two sets are substitutable.  In this model every set has the identical
    technology composition - ways [0, nvm_ways) NVM and [nvm_ways, ways) SRAM in
    *every* set - so swapping the mapping of two sets exchanges two physically
    identical structures and the assumption is satisfied exactly.  It would
    *not* hold in an array where the SRAM ways were concentrated in a subset of
    sets; that configuration is out of scope here and is noted so the assumption
    is not silently inherited.

============================ AMBIGUITIES THE PAPER DOES NOT RESOLVE

Each is resolved one way, implemented, and its direction stated.

D1. **The coldest set in the group is already associated with a third set.**
    The paper never says.  Resolved: such sets are *skipped* as candidates
    (counter `oas_skipped_associated`); the coldest *unassociated* set in the
    group is chosen, and if the group has none the association is abandoned
    (`oas_no_cold_set`).  The alternative - forcibly aborting the third set's
    association - would cascade flushes across a group.  Direction: skipping
    *flatters* Sequoia, because it avoids flush-backs the cascade would cost.
    The hot set's *own* pending association is still aborted, because the paper
    explicitly requires that ("the controller stops an on-going association").

D2. **Do OAS/WAD data movements increment the per-line write counters?**  They
    must, and they do: every one goes through `llc.charge_write`, so it appears
    in `llc.migration_writes` and in the per-frame wear array.  The paper's own
    write-amplification claim (Sec. VI, "~1% write amplification") is a claim
    *about* these writes; not counting them would make the claim unfalsifiable.

D3. **Is a WAD displacement one write or two?**  Resolved: **one**.  The victim
    frame receives the hot line's data (one physical write, charged); the hot
    frame is only invalidated, and clearing a valid bit is not a data write.
    Direction: charging two would *penalise* Sequoia; one is the reading that
    matches the paper's cost model.

D4. **The A_victim selection rule.**  Sec. IV-A2 first describes an LRU-based
    ideal ("prioritizing over moving most recently used lines ... and evicting
    LRU lines when needed") and then discards it because pseudo-LRU cannot order
    the stack, concluding "we choose the victims sequentially based on the way
    number in ascending order".  Resolved: ascending way index among eligible
    (non-native, valid) frames, for **both** A_victim and B_victim, which is the
    rule the paper's own evaluation used.  This codebase has true LRU and could
    implement the discarded ideal; doing so would measure a scheme the paper did
    not evaluate.

D5. **True LRU vs the paper's binary-tree pseudo-LRU.**  This model uses true
    LRU.  Consequently Clean-LRU here walks a fully ordered stack instead of the
    paper's "check the lines in a sequential order by regarding their way
    indices" fallback, and **Table VII's statistics (LRU line clean ~49% of the
    time; a clean line found within three lines ~78% of the time) will not
    reproduce here**.  Direction: true LRU finds a clean victim at least as
    often as the pseudo-LRU scan, so this *flatters* WAD's write-back cost.

D6. **How an invalidated-but-not-demoted WAD line interacts with the
    allocator.**  `llc.lru()` returns any invalid frame immediately, so the
    naive composition sends the very next allocation straight back into the
    frame WAD just cooled - which defeats the stated intent ("does not change
    its replacement metadata ... to protect it from next writes for a while",
    the same argument i2WAP makes for PoLF).  Resolved: a WAD-invalidated frame
    is *held* at its pre-invalidation recency (`wad_hold`) and competes for
    victimhood at that age instead of being taken as a free invalid frame.  The
    hold is released the moment the frame is written.  Direction: the naive
    behaviour would *penalise* Sequoia by deleting WAD's effect; this is the
    faithful reading.

D7. **Are SSC/HSC per logical or per physical set?**  The paper says "per set"
    without disambiguating.  Resolved: **per physical set**.  HSC||SSC is used
    to pick a *cold physical location*; under a per-LS reading the counters
    would follow the data around and could never identify a cold location, and
    "the SSC of the hot set is reset" after remapping would be meaningless.

D8. **Read misses inside an active association.**  The paper's Read Miss case
    ends by filling the current PS from main memory (step 3).  This model never
    fills on a read miss (see below), so step 3 has no referent and steps 1-2
    alone would destroy a line of the migrating set for no placement.  Resolved:
    read misses do **not** advance the association by default
    (`advance_on_read_miss=0`); the parameter exists so the other reading can be
    measured.  Direction: not advancing *flatters* Sequoia by avoiding
    model-artefact flush-backs, and *penalises* it by slowing completion and so
    raising the abort probability.  Read *second-hits* still advance the swap,
    which is the paper's main driver.

D9. **Write second-hits and the counters.**  A write second-hit is a write hit,
    so it increments SSC/HSC and can trigger OAS (including the paper's own
    abort-on-saturation).  It does **not** arm or fire WAD, because the line is
    mid-flight between two physical sets and displacing it in the same step is
    not a case the paper defines.  Counter: `second_hit_writes`.

D10. **Recency of a displaced/migrated line.**  The paper does not say what
    happens to the LRU metadata of moved data.  Resolved: moved data carries its
    recency with it, so a migration does not change what the set would evict
    next.  Direction: neutral for wear, avoids an artificial hit-rate change.

=================================== THIS MODEL STARVES BOTH TRIGGERS

The LLC here is exclusive-ish (HyCSim `BaseHybrid`): the array is filled **only**
by an `L2_Rpl` that misses, a read miss allocates nothing, an `L2_Rpl` to a
resident block performs no data write, `GetS` on a dirty block invalidates and
`GetX` always invalidates.  Therefore **every charged NVM write is an
allocation**, and the only event that looks like Sequoia's "write hit" is the
`L2_Rpl`-to-resident, which by itself charges nothing.

Sequoia counts *write hits*, not allocations.  In this model that trigger is
rare, so SSC barely moves, sets rarely go hot, and both procedures spend most of
the run idle while G-OAS's aborts and WAD's victim evictions still cost
write-backs.  This is not a defect of the re-implementation; it is exactly the
regime the paper itself documents in **Sec. VI-B5** for `swaptions`:

    "~60% of the sets in swaptions have up to 7 write-hits.  Since 7 is the RSC
     saturation level, WAD is not triggered for intraset wear-leveling in most
     of the sets ... for swaptions, ~80% of the sets have up to 127 write-hits
     (i.e., OAS saturation threshold in Sequoia) that cause a small fraction of
     sets (~20%) becoming hot and triggering OAS ... To conclude, WAD and OAS
     algorithms are infrequently called under swaptions workload, leaving a
     small room for InterV and IntraV reduction by Sequoia."

and where the scheme still raised main-memory write-backs by ~9.5x.  Under this
model's semantics that regime is the *expected* one, not the exception.  The
counters below are the instrument that makes it legible: `write_hits_nvm`,
`ssc_saturations`, `associations`, `wad_displacements` and `allocations` are all
reported, so a row where Sequoia does nothing is visibly a row where Sequoia
*could* do nothing, rather than a row where the implementation is broken.

Ablation: `oas=0` disables G-OAS, `wad=0` disables WAD, so each contribution is
measured rather than asserted.  Note that with `oas=0` the SSC is still reset at
the point where OAS would have fired; otherwise the saturating SSC would pin RSC
at its threshold and the ablation would silently change WAD as well.
"""
import math

from .base import Policy


def _log2(x):
    return int(round(math.log(x, 2)))


class Sequoia(Policy):
    name = 'sequoia'
    does_remap = True
    PARAMS = dict(
        group=64,                 # G, Sec. VI-B3
        ssc_bits=7,               # threshold 127, Sec. V
        hsc_bits=5,               # Sec. VI-B2 ("its size to 5 bits")
        rsc_bits=3,               # RSC threshold 7, Sec. V
        oas=1,                    # ablation: G-OAS on/off
        wad=1,                    # ablation: WAD on/off
        advance_on_read_miss=0,   # D8
    )

    # ------------------------------------------------------------------ setup
    def setup(self):
        llc = self.llc
        S, W = llc.sets, llc.ways
        self.S, self.W = S, W
        self.NW = llc.nvm_ways
        self.oas = int(self.params['oas'])
        self.wad = int(self.params['wad'])
        self.ssc_bits = int(self.params['ssc_bits'])
        self.hsc_bits = int(self.params['hsc_bits'])
        self.rsc_bits = int(self.params['rsc_bits'])
        self.ssc_max = (1 << self.ssc_bits) - 1
        self.hsc_max = (1 << self.hsc_bits) - 1
        self.rsc_mask = (1 << self.rsc_bits) - 1

        G = int(self.params['group'])
        if G > S:
            G = S
        if G < 1 or (G & (G - 1)):
            raise ValueError('Sequoia group size must be a power of two')
        self.G = G
        self.gbits = _log2(G)
        self.lowbits = _log2(S) - self.gbits
        self.lowmask = (1 << self.lowbits) - 1

        # per-physical-set write-intensity state (D7)
        self.ssc = [0] * S
        self.hsc = [0] * S
        self.hot = [0] * S
        # DT is indexed by logical set, RT and AT by physical set
        self.dt = [i >> self.lowbits for i in range(S)]
        self.rt = [i >> self.lowbits for i in range(S)]
        self.at_a = [0] * S
        self.at_i = [0] * S
        self.steps = [0] * S
        self.native = [[1] * W for _ in range(S)]
        self.wad_hold = [[-1] * W for _ in range(S)]
        self.wad_armed = [0] * S

        # ------------------------------------------------------ instrumentation
        self.allocations = 0
        self.write_hits = 0            # L2_Rpl to a resident block
        self.write_hits_nvm = 0        # ... that landed on an NVM way (counted)
        self.write_hits_sram = 0       # ... that did not (dropped, see A1)
        self.ssc_saturations = 0
        self.hsc_resets = 0
        self.hot_marks = 0
        self.associations = 0
        self.oas_skipped_associated = 0
        self.oas_no_cold_set = 0
        self.oas_completions = 0
        self.oas_aborts = 0
        self.oas_abort_invalidations = 0
        self.oas_abort_writebacks = 0
        self.oas_avictim_evictions = 0
        self.oas_avictim_writebacks = 0
        self.oas_bvictim_dropped = 0
        self.oas_line_moves = 0        # physical lines relocated by G-OAS
        self.second_searches = 0
        self.second_hits = 0
        self.second_hit_writes = 0
        self.second_hit_reads = 0
        self.read_miss_advances = 0
        self.wad_arms = 0
        self.wad_displacements = 0
        self.wad_no_victim = 0
        self.wad_victim_evictions = 0
        self.wad_writebacks = 0
        self.wad_holds_honoured = 0

    # ------------------------------------------------------------------ cost
    def state_bits(self):
        """Sec. VI-B4 arithmetic, generalised.

        per set : SSC + HSC + AT(index + association bit) + DT + RT
        per line: native bit
        With S=4096, W=8, G=64 this is 4096*31 + 4096*8 = 159744 b = 19.5 kiB.
        """
        g = self.gbits
        per_set = self.ssc_bits + self.hsc_bits + (g + 1) + g + g
        return self.S * per_set + self.S * self.W

    # -------------------------------------------------------------- mapping
    def _ps(self, ls):
        """Decoding table: logical set -> physical set (Sec. IV-A3)."""
        return (self.dt[ls] << self.lowbits) | (ls & self.lowmask)

    def _ls_of(self, ps):
        """Reverse table: physical set -> logical set currently mapped to it."""
        return (self.rt[ps] << self.lowbits) | (ps & self.lowmask)

    def _prev(self, ps):
        """Association table: the PS that holds this PS's not-yet-migrated data."""
        return (self.at_i[ps] << self.lowbits) | (ps & self.lowmask)

    def remap_set(self, cs):
        if not self.oas:
            return cs
        return (self.dt[cs] << self.lowbits) | (cs & self.lowmask)

    # ------------------------------------------------- replacement with D6 hold
    def _age(self, s, j):
        h = self.wad_hold[s][j]
        return self.llc.mru[s][j] if h < 0 else h

    def _victim(self, s, cand):
        """`llc.lru` semantics, except that a WAD-held frame is not treated as a
        free invalid frame (D6): it competes at its pre-invalidation recency."""
        llc = self.llc
        tag, hold = llc.tag[s], self.wad_hold[s]
        for j in cand:
            if tag[j] == -1 and hold[j] < 0:
                return j
        best, victim = None, -1
        for j in cand:
            m = self._age(s, j)
            if best is None or m < best:
                best, victim = m, j
        if victim >= 0 and tag[victim] == -1 and hold[victim] >= 0:
            self.wad_holds_honoured += 1
        return victim

    def _first_invalid(self, s):
        tag, hold = self.llc.tag[s], self.wad_hold[s]
        for j in range(self.W):
            if tag[j] == -1 and hold[j] < 0:
                return j
        for j in range(self.W):
            if tag[j] == -1:
                return j
        return -1

    def _first_non_native(self, s):
        """Ascending way order among valid non-native frames (D4)."""
        tag, nat = self.llc.tag[s], self.native[s]
        for j in range(self.W):
            if tag[j] != -1 and not nat[j]:
                return j
        return -1

    def _has_non_native(self, s):
        return self._first_non_native(s) >= 0

    # ------------------------------------------------------- write-hit engine
    def on_write_hit(self, cs, way, is_dirty, now):
        """Sequoia's only trigger.  In this model that is an `L2_Rpl` that finds
        its block resident - which charges no array write, hence the starvation
        documented above."""
        self.write_hits += 1
        self._note_write_hit(cs, way, now, allow_wad=True)

    def _note_write_hit(self, ps, way, now, allow_wad):
        if way >= self.NW:
            self.write_hits_sram += 1      # A1: no NVM cell was touched
            return
        self.write_hits_nvm += 1

        # --- SSC / HSC, Sec. IV-A1 -----------------------------------------
        s = self.ssc[ps]
        just_saturated = False
        if s < self.ssc_max:
            s += 1
            self.ssc[ps] = s
            if s == self.ssc_max:
                just_saturated = True
                self.ssc_saturations += 1
                self.hot[ps] = 1
                self.hot_marks += 1
                h = self.hsc[ps] + 1
                if h >= self.hsc_max:
                    # "When the HSC of a set saturates, the controller resets
                    #  all the HSCs in the cache."
                    self.hsc = [0] * self.S
                    self.hsc_resets += 1
                else:
                    self.hsc[ps] = h

        # --- OAS trigger: "the next write-hit to this set" -------------------
        if self.hot[ps] and not just_saturated:
            self.hot[ps] = 0
            self.ssc[ps] = 0               # "the SSC of the hot set is reset"
            if self.oas:
                self._associate(ps)
            # with oas=0 the reset still happens, so WAD's RSC stream is
            # unchanged by the ablation (see module docstring).

        # --- WAD, Sec. IV-B --------------------------------------------------
        if self.wad and allow_wad:
            if self.wad_armed[ps]:
                self.wad_armed[ps] = 0
                self._wad_displace(ps, way)
            if (self.ssc[ps] & self.rsc_mask) == self.rsc_mask:
                self.wad_armed[ps] = 1
                self.wad_arms += 1

    # -------------------------------------------------------------- G-OAS
    def _associate(self, hot_ps):
        llc = self.llc
        if self.at_a[hot_ps]:
            # "the controller stops an on-going association and invalidates the
            #  lines remaining in the previous set"
            self._abort(hot_ps, self._prev(hot_ps))

        grp = hot_ps & self.lowmask
        best, bestval = -1, None
        for m in range(self.G):
            ps = (m << self.lowbits) | grp
            if ps == hot_ps:
                continue
            if self.at_a[ps]:
                self.oas_skipped_associated += 1     # D1
                continue
            v = (self.hsc[ps] << self.ssc_bits) | self.ssc[ps]   # HSC||SSC
            if bestval is None or v < bestval:
                bestval, best = v, ps
        if best < 0:
            self.oas_no_cold_set += 1
            return

        i, j = hot_ps, best
        A, B = self._ls_of(i), self._ls_of(j)
        # exchange the mappings: A -> j, B -> i  (DT and RT stay inverses)
        self.dt[A] = j >> self.lowbits
        self.dt[B] = i >> self.lowbits
        self.rt[i], self.rt[j] = self.rt[j], self.rt[i]
        # AT: each side records the other as its previous PS (Fig. 3)
        self.at_a[i] = self.at_a[j] = 1
        self.at_i[i] = j >> self.lowbits
        self.at_i[j] = i >> self.lowbits
        # every resident line is now sitting in the wrong PS
        for s in (i, j):
            tg, nat = llc.tag[s], self.native[s]
            for w in range(self.W):
                nat[w] = 0 if tg[w] != -1 else 1
        self.steps[i] = self.steps[j] = 0
        self.associations += 1
        llc.remaps += 1
        self._maybe_complete(i, j)

    def _move_line(self, src, sway, dst, dway):
        """Relocate one line, charging the physical write (D2)."""
        llc = self.llc
        llc.tag[dst][dway] = llc.tag[src][sway]
        llc.dirty[dst][dway] = llc.dirty[src][sway]
        llc.mru[dst][dway] = llc.mru[src][sway]          # D10
        self.native[dst][dway] = 1
        self.wad_hold[dst][dway] = -1
        llc.tag[src][sway] = -1
        llc.dirty[src][sway] = 0
        self.native[src][sway] = 1
        llc.charge_write(dst, dway)
        self.oas_line_moves += 1

    def _drop_line(self, s, way, counter_attr):
        llc = self.llc
        if llc.tag[s][way] == -1:
            return
        if llc.dirty[s][way]:
            setattr(self, counter_attr, getattr(self, counter_attr) + 1)
        llc.tag[s][way] = -1
        llc.dirty[s][way] = 0
        self.native[s][way] = 1
        llc.flushes += 1

    def _step(self, i, j):
        self.steps[i] += 1
        self.steps[j] = self.steps[i]
        if self.steps[i] >= self.W:
            # "swapping two sets is completed after exactly K processor accesses
            #  ... that are not first-hit"
            if self._has_non_native(i) or self._has_non_native(j):
                self._abort(i, j)
            else:
                self._complete(i, j)
            return
        self._maybe_complete(i, j)

    def _maybe_complete(self, i, j):
        if not self._has_non_native(i) and not self._has_non_native(j):
            self._complete(i, j)

    def _complete(self, i, j):
        self.at_a[i] = self.at_a[j] = 0
        self.steps[i] = self.steps[j] = 0
        self.oas_completions += 1

    def _abort(self, i, j):
        llc = self.llc
        for s in (i, j):
            for w in range(self.W):
                if llc.tag[s][w] != -1 and not self.native[s][w]:
                    if llc.dirty[s][w]:
                        self.oas_abort_writebacks += 1
                    llc.tag[s][w] = -1
                    llc.dirty[s][w] = 0
                    llc.flushes += 1
                    self.oas_abort_invalidations += 1
                self.native[s][w] = 1
        self.at_a[i] = self.at_a[j] = 0
        self.steps[i] = self.steps[j] = 0
        self.oas_aborts += 1

    # -------------------------------------------------------------- Fig. 4
    def choose_way(self, cs, addr, is_dirty):
        """Allocation.  Inside an active association this is Fig. 4's Write
        Miss: A_victim is evicted from the previous PS, B_victim is moved home
        into its frame, and the incoming line takes B_victim's frame."""
        if self.oas and self.at_a[cs]:
            w = self._write_miss_step(cs)
            if w >= 0:
                return w
        return self._victim(cs, range(self.W))

    def _write_miss_step(self, ps):
        llc = self.llc
        prev = self._prev(ps)
        b = self._first_non_native(ps)          # a line of B still sitting in ps
        if b < 0:
            self._maybe_complete(ps, prev)
            return -1
        a_victim = self._first_non_native(prev)  # a line of A still sitting in prev
        slot = -1
        if a_victim >= 0:
            self._drop_line(prev, a_victim, 'oas_avictim_writebacks')
            self.oas_avictim_evictions += 1
            slot = a_victim
        else:
            slot = self._first_invalid(prev)
        if slot >= 0:
            self._move_line(ps, b, prev, slot)
        else:
            # prev holds only native lines and is full: B_victim has nowhere to
            # go, so it is invalidated in place (dirty -> written back).
            self._drop_line(ps, b, 'oas_abort_writebacks')
            self.oas_bvictim_dropped += 1
        self.wad_hold[ps][b] = -1
        self.native[ps][b] = 1
        self._step(ps, prev)
        return b

    def aux_lookup(self, addr, op, now):
        """The paper's *second search* (Sec. IV-A3).  `llc.run` calls this
        whenever the current PS misses, which is precisely the point at which
        Sequoia consults the associated PS for lines with n = 0."""
        if not self.oas:
            return False
        llc = self.llc
        ls = llc.set_index(addr)
        ps = (self.dt[ls] << self.lowbits) | (ls & self.lowmask)
        if not self.at_a[ps]:
            return False
        prev = self._prev(ps)
        self.second_searches += 1
        tg, nat = llc.tag[prev], self.native[prev]
        w = -1
        for j in range(self.W):
            if tg[j] == addr and not nat[j]:
                w = j
                break
        if w < 0:
            if op != 2 and int(self.params['advance_on_read_miss']):
                self._read_miss_step(ps, prev)     # D8
            return False

        self.second_hits += 1
        if op == 2:
            return self._write_second_hit(ps, prev, w, addr, now)
        return self._read_second_hit(ps, prev, w, op, now)

    def _read_miss_step(self, ps, prev):
        b = self._first_non_native(ps)
        if b < 0:
            self._maybe_complete(ps, prev)
            return
        a_victim = self._first_non_native(prev)
        slot = a_victim
        if a_victim >= 0:
            self._drop_line(prev, a_victim, 'oas_avictim_writebacks')
            self.oas_avictim_evictions += 1
        else:
            slot = self._first_invalid(prev)
        if slot >= 0:
            self._move_line(ps, b, prev, slot)
            self.read_miss_advances += 1
            self._step(ps, prev)

    def _write_second_hit(self, ps, prev, w, addr, now):
        """Fig. 4, Write Second-Hit.  B_victim(ps) is written in place of a in
        `prev`; a is written in place of B_victim in `ps`.  In the unmodified
        model this access charges *zero* array writes; under G-OAS it charges
        the relocations, and every one is a `charge_write`.

        The model never tells a policy the dirty flag of an `L2_Rpl` that hits,
        so the resident block's own dirty state is carried across the move;
        HyCSim asserts such a writeback is never dirty, so nothing is lost."""
        llc = self.llc
        self.second_hit_writes += 1
        self.write_hits += 1          # it is still an L2_Rpl onto a resident block
        was_dirty = llc.dirty[prev][w]
        dest = self._first_non_native(ps)
        if dest >= 0:
            self._move_line(ps, dest, prev, w)      # B_victim -> a's old frame
        else:
            dest = self._first_invalid(ps)
            if dest < 0:
                dest = self._victim(ps, range(self.W))
                self._drop_line(ps, dest, 'oas_avictim_writebacks')
            llc.tag[prev][w] = -1
            llc.dirty[prev][w] = 0
            self.native[prev][w] = 1
        # line a (from the processor side) is written into its new PS
        llc.tag[ps][dest] = addr
        llc.dirty[ps][dest] = was_dirty
        llc.mru[ps][dest] = now
        self.native[ps][dest] = 1
        self.wad_hold[ps][dest] = -1
        llc.charge_write(ps, dest)
        self.oas_line_moves += 1
        self._step(ps, prev)
        self._note_write_hit(ps, dest, now, allow_wad=False)   # D9
        return True

    def _read_second_hit(self, ps, prev, w, op, now):
        """Fig. 4, Read Second-Hit, composed with this model's read semantics:
        `GetS` on a dirty block invalidates it, `GetX` hits only if dirty and
        always invalidates."""
        llc = self.llc
        self.second_hit_reads += 1
        d = llc.dirty[prev][w]
        nvm_side = w < self.NW
        if op == 0:                       # GetS
            served, keep = True, not d
        else:                             # GetX
            served, keep = bool(d), False

        if keep:
            dest = self._first_non_native(ps)
            if dest >= 0:
                addr = llc.tag[prev][w]
                dirty = llc.dirty[prev][w]
                self._move_line(ps, dest, prev, w)         # B_victim -> prev
                llc.tag[ps][dest] = addr                   # a -> ps
                llc.dirty[ps][dest] = dirty
                llc.mru[ps][dest] = now
                self.native[ps][dest] = 1
                self.wad_hold[ps][dest] = -1
                llc.charge_write(ps, dest)
                self.oas_line_moves += 1
                self._step(ps, prev)
            else:
                llc.mru[prev][w] = now
                self._maybe_complete(ps, prev)
        else:
            # a is invalidated by the access itself; its frame in `prev` is the
            # slot B_victim needs, so the association still advances.
            llc.tag[prev][w] = -1
            llc.dirty[prev][w] = 0
            self.native[prev][w] = 1
            b = self._first_non_native(ps)
            if b >= 0:
                self._move_line(ps, b, prev, w)
                self._step(ps, prev)
            else:
                self._maybe_complete(ps, prev)

        if served and nvm_side:
            # `llc.run` credits an aux_lookup hit to SRAM; this second hit was
            # served by an NVM frame, so the technology attribution is corrected
            # in advance of run's own increments.
            llc.hits_sram -= 1
            llc.sram_reads -= 1
            llc.hits_nvm += 1
            llc.nvm_reads += 1
        return served

    # ---------------------------------------------------------------- WAD
    def _wad_displace(self, ps, hot_way):
        """Sec. IV-B.  Invalidate the hot line without demoting it (D6) and
        write its data into the Clean-LRU victim, restricted to NVM ways (A2)."""
        llc = self.llc
        if llc.tag[ps][hot_way] == -1:
            return
        cand = [j for j in range(self.NW) if j != hot_way]
        if not cand:
            self.wad_no_victim += 1
            return
        mru_way = max(range(self.W), key=lambda j: (self._age(ps, j), j))
        pool = [j for j in cand if j != mru_way] or cand
        pool.sort(key=lambda j: (self._age(ps, j), j))     # LRU first
        victim = -1
        for j in pool:
            if llc.tag[ps][j] == -1 or not llc.dirty[ps][j]:
                victim = j
                break
        if victim < 0:
            victim = pool[0]                                # "the LRU line"
        if llc.tag[ps][victim] != -1:
            if llc.dirty[ps][victim]:
                self.wad_writebacks += 1
            llc.flushes += 1
            self.wad_victim_evictions += 1
        llc.tag[ps][victim] = llc.tag[ps][hot_way]
        llc.dirty[ps][victim] = llc.dirty[ps][hot_way]
        llc.mru[ps][victim] = llc.mru[ps][hot_way]          # D10
        self.native[ps][victim] = self.native[ps][hot_way]
        self.wad_hold[ps][victim] = -1
        llc.charge_write(ps, victim)                        # D3: exactly one
        # hold the hot frame at its pre-invalidation recency
        self.wad_hold[ps][hot_way] = llc.mru[ps][hot_way]
        llc.tag[ps][hot_way] = -1
        llc.dirty[ps][hot_way] = 0
        self.native[ps][hot_way] = 1
        self.wad_displacements += 1

    # -------------------------------------------------------------- bookkeeping
    def on_write(self, cs, way):
        self.allocations += 1
        self.wad_hold[cs][way] = -1
        self.native[cs][way] = 1

    def counters(self):
        keys = ('allocations', 'write_hits', 'write_hits_nvm', 'write_hits_sram',
                'ssc_saturations', 'hsc_resets', 'hot_marks', 'associations',
                'oas_skipped_associated', 'oas_no_cold_set', 'oas_completions',
                'oas_aborts', 'oas_abort_invalidations', 'oas_abort_writebacks',
                'oas_avictim_evictions', 'oas_avictim_writebacks',
                'oas_bvictim_dropped', 'oas_line_moves', 'second_searches',
                'second_hits', 'second_hit_writes', 'second_hit_reads',
                'read_miss_advances', 'wad_arms', 'wad_displacements',
                'wad_no_victim', 'wad_victim_evictions', 'wad_writebacks',
                'wad_holds_honoured')
        return {k: getattr(self, k) for k in keys}
