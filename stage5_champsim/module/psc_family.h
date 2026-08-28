/*
 * psc_family: hybrid SRAM/NVM last-level cache management for endurance study.
 *
 * One ChampSim replacement module implementing every policy under comparison,
 * selected at run time so that IPC, MPKI and physical wear all come from the
 * SAME simulation.  The manuscript's split methodology (timing from one tool,
 * wear from another) cannot reconcile a 0.31 event/M-access rate with a 0.11%
 * IPC change; measuring both here removes that gap.
 *
 * Array model
 *   ways [0, NVM_WAYS)      SOT-MRAM   -- finite endurance, wear tracked
 *   ways [NVM_WAYS, NUM_WAY) SRAM      -- treated as unlimited endurance
 *
 * A physical write event is a fill into a frame, plus any migration, swap or
 * redirect the policy itself performs.  Every one is charged.
 *
 * Runtime configuration (environment variables, so no rebuild per point):
 *   PSC_POLICY       lru|psc|vro|vro_rank|psc_pred|psc_vro_rank|
 *                    ayush|dwawr|i2wap|tailguard|psc_soft|psc_soft_rank|
 *                    elba|sequoia|startgap|equalchance|
 *                    swwr|dwwr|wear_swap|full|psc_vro
 *   PSC_NVM_WAYS     number of NVM ways            (default 12)
 *   PSC_STATS        path for the per-frame wear dump
 *   plus per-policy knobs, see read_env() in the .cc
 *
 * =====================================================================
 * PROTOCOL NOTE - how this differs from the HyCSim trace replay
 * =====================================================================
 * The Python replay (stage3_hycsim) follows HyCSim's `BaseHybrid`, which is
 * near-exclusive: the LLC is filled ONLY by an L2 writeback that misses, a
 * writeback to a resident block performs NO data write, and `GetS` on a dirty
 * block and `GetX` always invalidate.  Consequence there: every charged NVM
 * write is an allocation, so any mechanism whose trigger is a *write hit* is
 * structurally starved.  Measured in that model: Sequoia's SSC counter never
 * once reached its threshold in 400k records, and ELBA's promote-and-release
 * moved peak wear by 4%.
 *
 * ChampSim's LLC is NOT exclusive.  Write hits happen and they write the array
 * (`try_hit` sets `way->dirty |= (type == WRITE)` on a resident block).  The
 * same mechanisms therefore have real headroom here.  Each ported policy below
 * carries an explicit "TRIGGER MAPPING" comment naming the ChampSim event its
 * Python hook corresponds to and stating where the two models genuinely
 * diverge.  Wear accounting is identical in *definition* in both simulators -
 * per-way write counters, every mechanism-performed write charged, peak and
 * CoV reported - so the numbers are comparable in kind even when they differ
 * in value.
 *
 * =====================================================================
 * STRUCTURAL LIMIT - the replacement interface cannot remap an address
 * =====================================================================
 * `CACHE::get_set_index()` is a private, non-virtual member and no module hook
 * is threaded through it; `handle_fill`/`try_hit` compute the set from the
 * address and only then call the replacement module, which returns a *way*.
 * A replacement module therefore cannot change which set an address indexes
 * to.  Three of the ported schemes (Start-Gap, Sequoia's G-OAS and WearSwap)
 * are inter-set remappings.  They are modelled here as a permutation of the
 * WEAR ACCOUNTING (logical set -> physical set) rather than of the tag array;
 * see `phys_set()` and the per-policy comment blocks for exactly what that
 * does and does not reproduce.  The module never mutates `CACHE::block`.
 *
 * =====================================================================
 * WHAT CHAMPSIM *CAN* DO THAT THE REPLAY COULD NOT - the cycle clock
 * =====================================================================
 * The coverage doc used to excuse SWWR/DWWR as unportable because their
 * interval is clocked in processor cycles.  That reason was wrong.  A
 * replacement module reaches the cache's own clock through its `intern_`
 * binding (`champsim::operable::current_time / clock_period`), so a
 * cycle-clocked interval is DIRECTLY expressible here - and more faithfully
 * than in the trace replay, where the interval had to be approximated from a
 * cycle column bound onto the record stream.  `now_cycles()` is that clock and
 * `swwr`/`dwwr` use it verbatim as the thesis's I.
 */
#ifndef REPLACEMENT_PSC_FAMILY_H
#define REPLACEMENT_PSC_FAMILY_H

#include <cstdint>
#include <deque>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "cache.h"
#include "modules.h"

class psc_family : public champsim::modules::replacement
{
public:
  enum class policy_t {
    LRU, PSC, VRO, VRO_RANK, PSC_PRED, PSC_VRO_RANK, AYUSH, DWAWR, I2WAP, TAILGUARD, PSC_BALANCE,
    PSC_SOFT, PSC_SOFT_RANK, ELBA, SEQUOIA, STARTGAP, EQUALCHANCE,
    SWWR, DWWR, WEAR_SWAP, FULL, PSC_VRO
  };

private:
  long NUM_SET{}, NUM_WAY{}, NVM_WAYS{};
  long WEAR_SETS{};                     // physical sets tracked; NUM_SET+1 under Start-Gap
  policy_t policy{policy_t::LRU};
  std::string policy_name{"lru"}, stats_path{};

  // ---- replacement state ----------------------------------------------
  std::vector<uint64_t> last_used;      // recency, per frame
  uint64_t cycle{0};

  // ---- wear / damage accounting ---------------------------------------
  std::vector<uint64_t> writes;         // physical writes, per frame
  std::vector<double> damage;           // endurance-normalised damage
  std::vector<double> endurance;        // per-frame endurance bin (relative)
  uint64_t nvm_writes{0}, sram_writes{0}, induced_writes{0};

  // ---- thermal estimator (per bank) -----------------------------------
  long banks{0};
  std::vector<double> bank_temp;        // Kelvin
  std::vector<uint64_t> bank_writes_epoch;
  uint64_t thermal_epoch{0};
  double T_amb{330.0}, R_th{0.6}, tau_th{100000.0}, E_a{0.35};
  bool thermal_on{false};

  // ---- VRO (original, restrictive); shared with PSC_VRO ---------------
  double vro_read_frac{2.0 / 3.0};
  long vro_R{0}, vro_w0{0};
  uint64_t vro_beta{0}, vro_beta_max{1023};
  uint64_t vro_rotations{0};

  // ---- VRO-Rank --------------------------------------------------------
  long rank_pool{4}, rank_trigger{4}, wear_bits{4};
  std::vector<uint8_t> coarse;          // coarse per-frame wear counters
  uint64_t rank_interventions{0};

  // ---- write-intensity predictor --------------------------------------
  std::vector<uint8_t> pred_tab;
  std::size_t pred_entries{4096};
  int pred_bits{2}, pred_threshold{2};
  uint64_t pred_decay{100000}, pred_ctr{0};
  double max_sram_frac{0.45};
  uint64_t steered{0}, allocations{0};

  // ---- AYUSH -----------------------------------------------------------
  std::vector<uint8_t> ayush_reuse;     // per-frame reuse counter
  int ayush_thresh{1};
  uint64_t ayush_migrations{0};

  // ---- DWAWR -----------------------------------------------------------
  std::vector<uint32_t> dwawr_Z;        // per-way write counters, per set
  std::vector<uint8_t> dwawr_R;         // restricted bit, per way per set
  uint32_t dwawr_interval{4096}, dwawr_t{0};
  long dwawr_n{2};
  uint64_t dwawr_redirects{0}, dwawr_intervals{0};

  // ---- i2WAP -----------------------------------------------------------
  uint64_t polf_threshold{10}, polf_ctr{0}, polf_flushes{0};
  uint64_t ss_interval{100000}, ss_ctr{0}, ss_ptr{0}, ss_swaps{0}, ss_shift{0};

  // ---- TailGuard -------------------------------------------------------
  double tg_kappa{2.0}, tg_alpha{0.99}, tg_lambda_r{1.0}, tg_lambda_wa{0.1};
  long tg_candidates{4};
  uint64_t tg_actions{0}, tg_bypass{0};

  /* ---- shared intra-set displacement (Sequoia WAD, EqualChance) --------
   * Both schemes move a hot line to another frame of the SAME set so that its
   * subsequent writes wear a different cell.  The module does not own the tag
   * array, so the block is not physically moved; instead the frame's *write
   * charge* is redirected, which is the wear-domain content of the mechanism.
   * `displaced_to[frame]` is the way that frame's data now lives in, or -1.
   * Cleared whenever either endpoint is filled. */
  std::vector<int8_t> displaced_to;
  uint64_t displacements{0};

  // ---- ELBA -------------------------------------------------------------
  struct gwab_entry {
    long set{0};
    long way{0};
    bool dirty{false};
    uint64_t stamp{0};
  };
  std::unordered_map<uint64_t, gwab_entry> elba_buf;   // absorbed blocks, by address
  std::deque<uint64_t> elba_order;                     // FIFO, lazily compacted
  std::vector<uint8_t> elba_wcount;                    // writes to a frame this residency
  long elba_cap{2048}, elba_min{256}, elba_max{16384};
  long elba_promote_on{2};
  bool elba_elastic{true}, elba_release{false};
  uint64_t elba_epoch{200000}, elba_epoch_i{0}, elba_epoch_hits{0}, elba_epoch_acc{0};
  double elba_grow{1.25}, elba_shrink{0.9}, elba_tol{0.005}, elba_last_hr{-1.0};
  std::unordered_set<uint64_t> elba_burst;
  uint64_t elba_promotions{0}, elba_frames_released{0}, elba_buf_hits{0},
      elba_buf_evictions{0}, elba_buf_writebacks{0}, elba_absorbed{0},
      elba_boundary_changes{0}, elba_early_evictions{0};

  // ---- Sequoia ----------------------------------------------------------
  int seq_ssc_bits{7}, seq_hsc_bits{5}, seq_rsc_bits{3};
  uint32_t seq_ssc_max{127}, seq_hsc_max{31}, seq_rsc_mask{7};
  long seq_G{64}, seq_gbits{0}, seq_lowbits{0}, seq_lowmask{0};
  bool seq_oas{true}, seq_wad{true};
  std::vector<uint16_t> seq_ssc, seq_hsc;   // per PHYSICAL set (D7)
  std::vector<uint8_t> seq_hot;             // per physical set
  std::vector<long> seq_dt;                 // DT: logical set -> high bits of PS
  std::vector<long> seq_rt;                 // RT: physical set -> high bits of LS
  std::vector<uint8_t> seq_at_a;            // AT association bit, per PS
  std::vector<long> seq_at_i;               // AT index field, per PS
  std::vector<long> seq_steps;              // migration steps, per PS
  std::vector<uint8_t> seq_native;          // per-line native bit, per LOGICAL frame
  std::vector<uint8_t> seq_wad_armed;       // per physical set
  uint64_t seq_write_hits{0}, seq_write_hits_nvm{0}, seq_write_hits_sram{0},
      seq_ssc_saturations{0}, seq_hsc_resets{0}, seq_hot_marks{0}, seq_associations{0},
      seq_skipped_associated{0}, seq_no_cold_set{0}, seq_completions{0}, seq_aborts{0},
      seq_abort_invalidations{0}, seq_abort_writebacks{0}, seq_line_moves{0},
      seq_steps_taken{0}, seq_wad_arms{0}, seq_wad_displacements{0}, seq_wad_no_victim{0},
      seq_wad_victim_evictions{0}, seq_wad_writebacks{0}, seq_second_searches_elided{0};

  // ---- Start-Gap ---------------------------------------------------------
  long sg_N{0}, sg_gap{0}, sg_start{0};
  uint64_t sg_psi{100}, sg_ctr{0}, sg_movements{0}, sg_rotations{0}, sg_frames_moved{0},
      remapped_writes{0};
  bool sg_scale_by_ways{true};

  // ---- EqualChance -------------------------------------------------------
  std::vector<uint32_t> ec_ctr;   // per set: numWrite
  std::vector<uint8_t> ec_flag;   // per set: FlagBit
  long ec_interval{16};
  uint64_t ec_i_shifts{0}, ec_c_shifts{0}, ec_no_candidate{0}, ec_hit_shifts{0};

  /* ---- SWWR / DWWR - vertical window write restriction -------------------
   * Agarwal, PhD thesis (IIT Guwahati, 2020), Ch. 4, Algorithms 1 and 2.
   * The state is GLOBAL, not per set: one restricted window index for the whole
   * cache, and for DWWR m write counters for the whole cache.  That is why the
   * thesis's overhead is 42 / 94 bits rather than tens of kilobytes, and it is
   * why none of these arrays is dimensioned by NUM_SET. */
  long wwr_m{4};                        // number of vertical windows (Sec. 4.4.1)
  uint64_t wwr_interval{2000000};       // I, in PROCESSOR CYCLES
  long wwr_tag_buffer_bits{42};         // Sec. 4.5.5 / Table 4.18
  int wwr_counter_bits{13};             // DWWR Ci width, Sec. 4.5.5
  bool wwr_count_fills{false};          // M3 sensitivity knob; 0 = Alg. 2 verbatim
  std::vector<int8_t> wwr_win_of;       // way -> window index, -1 for SRAM ways
  std::vector<uint32_t> wwr_C;          // DWWR: per-window write counters
  uint32_t wwr_counter_max{8191};
  long wwr_sel{-1};                     // M5: first WinSelect yields W0
  long wwr_wrw{-1};                     // currently restricted window, -1 = none
  bool wwr_clock_started{false};
  uint64_t wwr_t0{0};
  uint64_t wwr_clock_ticks{0}, wwr_intervals{0}, wwr_degenerate{0},
      wwr_counter_saturations{0}, wwr_alloc_deflected{0}, wwr_alloc_to_sram{0},
      wwr_fill_into_wrw{0}, wwr_write_hits{0}, wwr_write_hits_restricted{0},
      wwr_write_hits_unrestricted{0}, wwr_redirects{0}, wwr_redirect_to_invalid{0},
      wwr_redirect_evictions{0}, wwr_redirect_writebacks{0}, wwr_redirect_to_sram{0},
      wwr_no_dest{0};

  /* ---- WearSwap - measured-wear inter-set swapping -----------------------
   * Shared by `wear_swap` and by `full`, which composes it.  `ws_sc` is a wear
   * counter for a PHYSICAL location, so it does not travel with the mapping;
   * see the comment block in the .cc for why that differs from the replay. */
  long ws_bits{8}, ws_sample{64};
  uint64_t ws_interval{20000};
  uint32_t ws_cap{255};
  std::vector<uint32_t> ws_sc;      // coarse wear counter, per PHYSICAL set
  std::vector<long> ws_map;         // logical set -> physical set
  std::vector<long> ws_inv;         // physical set -> logical set
  uint64_t ws_ctr{0};
  long ws_cursor{0};
  bool ws_busy{false};              // re-entrancy guard: migration writes wear too
  uint64_t ws_swaps{0}, ws_migration_writes{0}, ws_no_candidate{0};

  // ---- FULL: per-channel ablation, so the composition is falsifiable ------
  bool full_pred{true}, full_rank{true}, full_swap{true};

  // ---- helpers ---------------------------------------------------------
  void read_env();
  void init_endurance(uint64_t seed);

  /* ---- PSC-Balance: inter-set levelling with zero data movement ----------
   * PSC removes intra-set variation but leaves inter-set variation untouched.
   * Every other inter-set leveller here (Start-Gap, i2WAP SwS, Sequoia G-OAS)
   * equalises by moving data and pays migration writes for it. PSC-Balance
   * uses the SRAM partition as the levelling channel instead: a set whose NVM
   * ways are wearing faster than the array average diverts more of its
   * allocations - including clean ones - into SRAM. It is a placement
   * decision, so it moves nothing and charges no migration writes. See
   * stage3_hycsim/policies/psc_balance.py for the full argument. */
  std::vector<uint32_t> pb_sc;      // per-set NVM write counter, set_bits wide
  double pb_avg = 1.0;              // exponential mean of per-set wear
  double pb_hot_threshold = 1.02;   // pressure at/above which clean traffic diverts
  double pb_cold_threshold = 0.0;   // <= 0 disables the cold arm entirely
  double pb_max_extra = 0.45;       // cap on THIS mechanism's extra diversion
  double pb_min_nvm = 0.40;         // floor on NVM's share of allocations
  long pb_avg_shift = 10;
  uint32_t pb_cap = 255;
  uint64_t pb_diversions = 0, pb_cold_retentions = 0;
  uint64_t pb_blocked_cap = 0, pb_blocked_floor = 0;
  [[nodiscard]] long pb_victim(long set, const champsim::cache_block* cs, bool is_dirty);
  void pb_note_write(long set);
  /* The cache's own clock, in cycles.  `champsim::operable::current_cycle()`
   * does exactly this but is marked [[deprecated]], and the project builds with
   * -Wall -Wextra -Wpedantic -Wconversion and expects a clean compile, so the
   * two public members it is defined in terms of are read directly. */
  [[nodiscard]] uint64_t now_cycles() const;
  [[nodiscard]] std::size_t idx(long set, long way) const { return (std::size_t)(set * NUM_WAY + way); }
  [[nodiscard]] bool is_nvm(long way) const { return way < NVM_WAYS; }
  [[nodiscard]] long bank_of(long set) const;
  [[nodiscard]] long lru_in(long set, long lo, long hi, const champsim::cache_block* cs) const;
  [[nodiscard]] long lru_in_list(long set, const std::vector<long>& ways, const champsim::cache_block* cs) const;
  [[nodiscard]] long rank_way(long set, const champsim::cache_block* cs);
  [[nodiscard]] bool predict_hot(champsim::address addr) const;
  void note_write(champsim::address addr);
  void invalidate(long set, long way);
  void charge(long set, long way, bool induced);
  void charge_phys(long pset, long way, bool induced);
  void charge_hit(long set, long way);
  void thermal_tick(long set);
  [[nodiscard]] double damage_per_write(long set, long way) const;

  // read-only introspection of the cache's own tag array.  The module never
  // writes to it: `try_hit` applies `way->dirty |= ...` through an iterator it
  // captured *before* calling the module, so permuting the array from inside a
  // hook would apply that update to an unrelated block.
  [[nodiscard]] const champsim::cache_block* set_blocks(long set) const;
  [[nodiscard]] long valid_count(long set) const;
  void clear_displacement(long set, long way);

  // logical set -> physical set.  Identity for every policy except Start-Gap
  // (Fig. 7) and Sequoia G-OAS (decoding table).
  [[nodiscard]] long phys_set(long set) const;

  // ELBA
  void elba_access(bool hit);
  void elba_retune();
  void elba_buf_insert(long set, long way, uint64_t addr, bool dirty);
  void elba_buf_drop(uint64_t addr, bool count_early);
  void elba_buf_trim();
  void elba_write_hit(long set, long way, uint64_t addr);

  // Sequoia
  void seq_init();
  [[nodiscard]] long seq_ls_of(long ps) const;
  [[nodiscard]] long seq_prev(long ps) const;
  [[nodiscard]] long seq_first_non_native(long ls) const;
  void seq_write_hit(long set, long way);
  void seq_associate(long hot_ps);
  void seq_step(long i, long j);
  void seq_complete(long i, long j);
  void seq_abort(long i, long j);
  void seq_fill_step(long set);
  void seq_wad_displace(long ls, long hot_way);

  // Start-Gap
  void sg_init();
  [[nodiscard]] long sg_inverse(long pa) const;
  void sg_tick();
  void sg_move_set(long src_phys, long dst_phys);

  // EqualChance
  void ec_note_write(long set, long way);
  [[nodiscard]] long ec_shift_target(long set, long avoid_way) const;

  /* Shared channels.  `full` composes these rather than restating them, so a
   * change to the PSC-Pred steering rule or to the VRO-Rank ranking rule
   * propagates into `full` (and into `psc_vro_rank`) automatically. */
  [[nodiscard]] long steer_sram(long set, const champsim::cache_block* cs, champsim::address addr, bool is_write);
  [[nodiscard]] long vro_victim(long set, const champsim::cache_block* cs) const;
  void vro_tick();

  // SWWR / DWWR
  void wwr_init();
  void wwr_clock();
  [[nodiscard]] long wwr_select_window();
  [[nodiscard]] bool wwr_is_restricted(long way) const;
  void wwr_count_window_write(long way, bool fill);
  [[nodiscard]] long wwr_victim(long set, const champsim::cache_block* cs);
  void wwr_write_hit(long set, long way);
  [[nodiscard]] long wwr_state_bits() const;

  // WearSwap (also the inter-set channel of FULL)
  void ws_init();
  void ws_note_write(long pset, long way);
  void ws_swap(long pa, long pb);

public:
  explicit psc_family(CACHE* cache);
  psc_family(CACHE* cache, long sets, long ways);

  long find_victim(uint32_t triggering_cpu, uint64_t instr_id, long set, const champsim::cache_block* current_set, champsim::address ip,
                   champsim::address full_addr, access_type type);
  void replacement_cache_fill(uint32_t triggering_cpu, long set, long way, champsim::address full_addr, champsim::address ip,
                              champsim::address victim_addr, access_type type);
  void update_replacement_state(uint32_t triggering_cpu, long set, long way, champsim::address full_addr, champsim::address ip,
                                champsim::address victim_addr, access_type type, uint8_t hit);
  void replacement_final_stats();
};

#endif
