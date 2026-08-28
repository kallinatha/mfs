#include "psc_family.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <numeric>
#include <random>

namespace
{
long env_long(const char* k, long d)
{
  const char* v = std::getenv(k);
  return v ? std::strtol(v, nullptr, 10) : d;
}
double env_dbl(const char* k, double d)
{
  const char* v = std::getenv(k);
  return v ? std::strtod(v, nullptr) : d;
}
std::string env_str(const char* k, const std::string& d)
{
  const char* v = std::getenv(k);
  return v ? std::string(v) : d;
}
} // namespace

psc_family::psc_family(CACHE* cache) : psc_family(cache, cache->NUM_SET, cache->NUM_WAY) {}

psc_family::psc_family(CACHE* cache, long sets, long ways)
    : replacement(cache), NUM_SET(sets), NUM_WAY(ways)
{
  read_env();
  if (NVM_WAYS < 0) NVM_WAYS = 0;
  if (NVM_WAYS > NUM_WAY) NVM_WAYS = NUM_WAY;

  /* Start-Gap's N logical sets occupy N+1 physical locations, the extra one
   * being the GapLine (Sec. 4.3).  The spare is *appended* so the Fig. 7
   * mapping stays an exact bijection; folding a logical set away instead would
   * alias two set indices together and charge Start-Gap a hit-rate loss the
   * paper does not have.  Only the module's own wear arrays are widened - the
   * cache still has NUM_SET sets. */
  WEAR_SETS = NUM_SET + ((policy == policy_t::STARTGAP) ? 1 : 0);

  auto n = static_cast<std::size_t>(NUM_SET * NUM_WAY);
  auto pn = static_cast<std::size_t>(WEAR_SETS * NUM_WAY);
  last_used.assign(n, 0);
  writes.assign(pn, 0);
  damage.assign(pn, 0.0);
  pb_sc.assign(static_cast<std::size_t>(WEAR_SETS), 0u);
  coarse.assign(pn, 0);
  ayush_reuse.assign(n, 0);
  dwawr_Z.assign(n, 0);
  dwawr_R.assign(n, 0);
  pred_tab.assign(pred_entries, 0);
  displaced_to.assign(n, -1);
  elba_wcount.assign(n, 0);
  ec_ctr.assign(static_cast<std::size_t>(NUM_SET), 0);
  ec_flag.assign(static_cast<std::size_t>(NUM_SET), 0);

  if (policy == policy_t::SEQUOIA) seq_init();
  if (policy == policy_t::STARTGAP) sg_init();
  if (policy == policy_t::SWWR || policy == policy_t::DWWR) wwr_init();
  if (policy == policy_t::WEAR_SWAP || policy == policy_t::FULL) ws_init();

  banks = std::max(1L, NUM_SET / 512);
  bank_temp.assign(static_cast<std::size_t>(banks), T_amb);
  bank_writes_epoch.assign(static_cast<std::size_t>(banks), 0);

  /* VRO's read-way count.  psc_vro.py uses R = round(M * read_frac); this uses
   * floor, which is what the module has always done and what every VRO number
   * already published from it assumes.  The two agree for M = 12 (8 read ways)
   * and differ only when M*read_frac has a fractional part >= 0.5 - at M = 16,
   * floor gives R = 10 where the replay gives 11.  Stated rather than silently
   * changed, because changing it would move the existing `vro` rows. */
  vro_R = static_cast<long>(static_cast<double>(NVM_WAYS) * vro_read_frac);
  if (vro_R < 1) vro_R = 1;
  if (vro_R > NVM_WAYS - 1) vro_R = std::max(1L, NVM_WAYS - 1);

  init_endurance(static_cast<uint64_t>(env_long("PSC_SEED", 1)));

  std::cout << "psc_family: policy=" << policy_name << " sets=" << NUM_SET << " ways=" << NUM_WAY
            << " nvm_ways=" << NVM_WAYS << " banks=" << banks << std::endl;
}

void psc_family::read_env()
{
  policy_name = env_str("PSC_POLICY", "lru");
  // Accept the hyphenated spellings used everywhere else in the project
  // ("psc-balance", "wear-swap") as well as the underscored ones. The two
  // simulators, the docs and the CSVs all use hyphens; only this parser used
  // underscores, so `PSC_POLICY=psc-balance` matched nothing.
  for (auto& ch : policy_name)
    if (ch == '-') ch = '_';

  if (policy_name == "psc") policy = policy_t::PSC;
  else if (policy_name == "vro") policy = policy_t::VRO;
  else if (policy_name == "vro_rank") policy = policy_t::VRO_RANK;
  else if (policy_name == "psc_pred") policy = policy_t::PSC_PRED;
  else if (policy_name == "psc_vro_rank") policy = policy_t::PSC_VRO_RANK;
  else if (policy_name == "ayush") policy = policy_t::AYUSH;
  else if (policy_name == "dwawr") policy = policy_t::DWAWR;
  else if (policy_name == "i2wap") policy = policy_t::I2WAP;
  else if (policy_name == "tailguard") policy = policy_t::TAILGUARD;
  else if (policy_name == "psc_balance") policy = policy_t::PSC_BALANCE;
  else if (policy_name == "psc_soft") policy = policy_t::PSC_SOFT;
  else if (policy_name == "psc_soft_rank") policy = policy_t::PSC_SOFT_RANK;
  else if (policy_name == "elba") policy = policy_t::ELBA;
  else if (policy_name == "sequoia") policy = policy_t::SEQUOIA;
  else if (policy_name == "startgap") policy = policy_t::STARTGAP;
  else if (policy_name == "equalchance") policy = policy_t::EQUALCHANCE;
  else if (policy_name == "swwr") policy = policy_t::SWWR;
  else if (policy_name == "dwwr") policy = policy_t::DWWR;
  else if (policy_name == "wear_swap") policy = policy_t::WEAR_SWAP;
  else if (policy_name == "full") policy = policy_t::FULL;
  else if (policy_name == "psc_vro") policy = policy_t::PSC_VRO;
  else if (policy_name == "lru") policy = policy_t::LRU;
  else {
    // Do NOT silently fall back to LRU. That is the single most dangerous
    // failure mode in this whole harness: a mistyped or renamed policy runs
    // plain LRU, produces entirely plausible numbers, and the CSV labels them
    // with the policy that never ran. It was found by driving the binary
    // directly with the hyphenated names the rest of the project uses --
    // `psc-vro` and `psc-balance` returned results byte-identical to `lru`.
    // Fail loudly instead; an unknown policy is an experiment design error.
    std::cerr << "psc_family: unknown PSC_POLICY '" << policy_name
              << "'. Refusing to run, because falling back to LRU would "
                 "produce results labelled with a policy that never executed."
              << std::endl;
    std::abort();
  }

  NVM_WAYS = env_long("PSC_NVM_WAYS", 12);
  stats_path = env_str("PSC_STATS", "");

  vro_beta_max = static_cast<uint64_t>(env_long("PSC_BETA_MAX", 1023));
  vro_read_frac = env_dbl("PSC_VRO_READ_FRAC", 2.0 / 3.0);
  rank_pool = env_long("PSC_RANK_POOL", 4);
  rank_trigger = env_long("PSC_RANK_TRIGGER", 4);
  wear_bits = env_long("PSC_WEAR_BITS", 4);

  pred_entries = static_cast<std::size_t>(env_long("PSC_PRED_ENTRIES", 4096));
  pred_bits = static_cast<int>(env_long("PSC_PRED_BITS", 2));
  pred_threshold = static_cast<int>(env_long("PSC_PRED_THRESHOLD", 2));
  pred_decay = static_cast<uint64_t>(env_long("PSC_PRED_DECAY", 100000));
  max_sram_frac = env_dbl("PSC_MAX_SRAM_FRAC", 0.45);

  ayush_thresh = static_cast<int>(env_long("PSC_AYUSH_THRESH", 1));

  dwawr_interval = static_cast<uint32_t>(env_long("PSC_DWAWR_INTERVAL", 4096));
  dwawr_n = env_long("PSC_DWAWR_N", 2);

  polf_threshold = static_cast<uint64_t>(env_long("PSC_POLF", 10));
  ss_interval = static_cast<uint64_t>(env_long("PSC_SS_INTERVAL", 100000));
  ss_shift = 0;

  pb_hot_threshold = env_dbl("PSC_PB_HOT", 1.02);
  pb_cold_threshold = env_dbl("PSC_PB_COLD", 0.0);
  pb_max_extra = env_dbl("PSC_PB_MAX_EXTRA", 0.45);
  pb_min_nvm = env_dbl("PSC_PB_MIN_NVM", 0.40);
  pb_avg_shift = env_long("PSC_PB_AVG_SHIFT", 10);
  pb_cap = static_cast<uint32_t>((1L << env_long("PSC_PB_SET_BITS", 8)) - 1);
  tg_kappa = env_dbl("PSC_TG_KAPPA", 2.0);
  tg_alpha = env_dbl("PSC_TG_ALPHA", 0.99);
  tg_lambda_r = env_dbl("PSC_TG_LAMBDA_R", 1.0);
  tg_lambda_wa = env_dbl("PSC_TG_LAMBDA_WA", 0.1);
  tg_candidates = env_long("PSC_TG_CANDIDATES", 4);

  /* ELBA.  Parameter names mirror stage3_hycsim/policies/elba.py exactly so the
   * ablations are the same experiment in both simulators:
   *   PSC_ELBA_PROMOTE_ON  very large  -> ablates promote-and-release (PR)
   *   PSC_ELBA_ENTRIES     0           -> ablates the absorption buffer (GWAB)
   *   PSC_ELBA_ELASTIC     0           -> ablates the elastic boundary (EB)
   * all three off reduces ELBA to plain LRU on the hybrid array. */
  elba_promote_on = env_long("PSC_ELBA_PROMOTE_ON", 2);
  elba_cap = env_long("PSC_ELBA_ENTRIES", 2048);
  elba_elastic = (env_long("PSC_ELBA_ELASTIC", 1) != 0);
  elba_epoch = static_cast<uint64_t>(env_long("PSC_ELBA_EPOCH", 200000));
  elba_grow = env_dbl("PSC_ELBA_GROW", 1.25);
  elba_shrink = env_dbl("PSC_ELBA_SHRINK", 0.9);
  elba_tol = env_dbl("PSC_ELBA_HIT_TOLERANCE", 0.005);
  elba_min = env_long("PSC_ELBA_MIN_ENTRIES", 256);
  elba_max = env_long("PSC_ELBA_MAX_ENTRIES", 16384);
  elba_release = (env_long("PSC_ELBA_RELEASE", 0) != 0);

  seq_G = env_long("PSC_SEQ_GROUP", 64);
  seq_ssc_bits = static_cast<int>(env_long("PSC_SEQ_SSC_BITS", 7));
  seq_hsc_bits = static_cast<int>(env_long("PSC_SEQ_HSC_BITS", 5));
  seq_rsc_bits = static_cast<int>(env_long("PSC_SEQ_RSC_BITS", 3));
  seq_oas = (env_long("PSC_SEQ_OAS", 1) != 0);
  seq_wad = (env_long("PSC_SEQ_WAD", 1) != 0);

  sg_psi = static_cast<uint64_t>(std::max(1L, env_long("PSC_SG_INTERVAL", 100)));
  sg_scale_by_ways = (env_long("PSC_SG_SCALE_BY_WAYS", 1) != 0);

  ec_interval = std::max(1L, env_long("PSC_EC_INTERVAL", 16));

  /* SWWR / DWWR.  DWWR subclasses SWWR in swwr.py and inherits `m`, `interval`
   * and `tag_buffer_bits`, so the shared knobs keep one name here too - a
   * change to PSC_SWWR_INTERVAL moves both policies, exactly as editing
   * SWWR.PARAMS does in the replay.
   *   PSC_SWWR_M                m,               Sec. 4.4.1  (4)
   *   PSC_SWWR_INTERVAL         I in CYCLES,     Sec. 4.4.1  (2,000,000)
   *   PSC_SWWR_TAG_BUFFER_BITS  Table 4.18                   (42)
   *   PSC_DWWR_COUNTER_BITS     Ci width,        Sec. 4.5.5  (13)
   *   PSC_DWWR_COUNT_FILLS      M3 sensitivity knob; 0 = Algorithm 2 verbatim */
  wwr_m = std::max(1L, env_long("PSC_SWWR_M", 4));
  wwr_interval = static_cast<uint64_t>(std::max(1L, env_long("PSC_SWWR_INTERVAL", 2000000)));
  wwr_tag_buffer_bits = env_long("PSC_SWWR_TAG_BUFFER_BITS", 42);
  wwr_counter_bits = static_cast<int>(std::max(1L, std::min(31L, env_long("PSC_DWWR_COUNTER_BITS", 13))));
  wwr_count_fills = (env_long("PSC_DWWR_COUNT_FILLS", 0) != 0);

  /* WearSwap.  Names mirror novel.py's WearSwap.PARAMS (`interval`, `bits`,
   * `sample`).  `full` composes WearSwap and therefore reads the SAME knobs;
   * Full.PARAMS calls the counter width `set_bits` because it already spends
   * `bits` on the predictor, and PSC_WS_BITS is that parameter here. */
  ws_interval = static_cast<uint64_t>(std::max(1L, env_long("PSC_WS_INTERVAL", 20000)));
  ws_bits = std::max(1L, std::min(31L, env_long("PSC_WS_BITS", 8)));
  ws_sample = std::max(1L, env_long("PSC_WS_SAMPLE", 64));

  /* FULL per-channel ablation.  `full` is a composition, so each constituent
   * must be switchable off independently or the composition claim is not
   * falsifiable:
   *   PSC_FULL_PRED=0  -> drop the PSC-Pred mu channel   => reduces to psc_pred
   *                       with a predictor that never fires
   *   PSC_FULL_RANK=0  -> drop the VRO-Rank intra-set channel
   *   PSC_FULL_SWAP=0  -> drop the WearSwap inter-set channel => psc_vro_rank
   * PSC_FULL_SWAP=0 alone must reproduce `psc_vro_rank` bit for bit, and
   * PSC_FULL_SWAP=0 PSC_FULL_RANK=0 must reproduce `psc_pred`. */
  full_pred = (env_long("PSC_FULL_PRED", 1) != 0);
  full_rank = (env_long("PSC_FULL_RANK", 1) != 0);
  full_swap = (env_long("PSC_FULL_SWAP", 1) != 0);

  thermal_on = (env_long("PSC_THERMAL", 0) != 0);
  T_amb = env_dbl("PSC_T_AMB", 330.0);
  R_th = env_dbl("PSC_R_TH", 0.6);
  tau_th = env_dbl("PSC_TAU_TH", 100000.0);
  E_a = env_dbl("PSC_E_A", 0.35);
}

/* Per-frame endurance bins.  Uniform endurance is the optimistic assumption;
 * real arrays show spatial variation, and a policy that levels raw write counts
 * is not levelling damage when endurance itself varies.  CV is swept via
 * PSC_ENDURANCE_CV (0 disables variation and reproduces the uniform model). */
void psc_family::init_endurance(uint64_t seed)
{
  double cv = env_dbl("PSC_ENDURANCE_CV", 0.0);
  endurance.assign(static_cast<std::size_t>(WEAR_SETS * NUM_WAY), 1.0);
  if (cv <= 0.0) return;
  std::mt19937_64 rng(seed);
  double sigma = std::sqrt(std::log(1.0 + cv * cv));
  double mu = -0.5 * sigma * sigma; // unit median-ish, unit mean
  std::lognormal_distribution<double> d(mu, sigma);
  for (auto& e : endurance) e = std::max(0.05, d(rng));
}

/* Bank temperature, discrete form of  T(t+D) = Tamb + (T-Tamb)e^{-D/RC} + R*P*(1-e^{-D/RC}).
 * Write activity in the epoch stands in for power.  Used only to rank candidate
 * frames, so absolute accuracy matters less than ordering. */
void psc_family::thermal_tick(long set)
{
  if (!thermal_on) return;   // default: flat thermal map, damage == write count
  auto b = static_cast<std::size_t>(bank_of(set));
  bank_writes_epoch[b]++;
  if (++thermal_epoch < 10000) return;
  thermal_epoch = 0;
  double decay = std::exp(-10000.0 / tau_th);
  for (std::size_t i = 0; i < bank_temp.size(); ++i) {
    double p = static_cast<double>(bank_writes_epoch[i]) / 10000.0;
    bank_temp[i] = T_amb + (bank_temp[i] - T_amb) * decay + R_th * p * 100.0 * (1.0 - decay);
    bank_writes_epoch[i] = 0;
  }
}

/* Damage of one write to a frame, normalised by that frame's endurance bin and
 * accelerated by local temperature (Arrhenius form).  With CV=0 and a flat
 * thermal map this reduces to 1.0, i.e. plain write counting. */
double psc_family::damage_per_write(long set, long way) const
{
  /* With PSC_THERMAL=0 and PSC_ENDURANCE_CV=0 this returns 1.0 exactly, so the
   * damage counter degenerates to a write counter and the uniform-endurance
   * results are directly comparable with the write-count literature.  Enabling
   * either term is what turns "level the writes" into "level the damage". */
  double acc = 1.0;
  if (thermal_on) {
    double T = bank_temp[static_cast<std::size_t>(bank_of(set))];
    acc = std::exp((E_a / 8.617333e-5) * (1.0 / T_amb - 1.0 / T));
  }
  return acc / endurance[idx(set, way)];
}

/* The cache's own cycle count.
 *
 * This is the whole reason SWWR/DWWR are portable at all, and the reason the
 * coverage doc's old excuse ("their interval is cycle-clocked, so it cannot be
 * expressed") was wrong.  `CACHE` derives from `champsim::operable`, whose
 * `current_time` and `clock_period` are public, and a replacement module holds
 * a `CACHE*` in `intern_`.  `operable::current_cycle()` is literally this
 * expression but carries [[deprecated]], which would break the clean build
 * under the project's -Wall -Wextra -Wpedantic flags, so it is spelled out.
 *
 * The clock is the LLC's, which at the configured frequency (4 GHz, same as the
 * core) ticks once per core cycle, so I is in the thesis's units without a
 * conversion.  It advances during warmup as well, which is correct: the thesis
 * runs the first interval with the whole cache available. */
uint64_t psc_family::now_cycles() const
{
  if (intern_ == nullptr) return 0;
  auto period = intern_->clock_period.count();
  if (period <= 0) return 0;
  return static_cast<uint64_t>(intern_->current_time.time_since_epoch().count() / period);
}

long psc_family::bank_of(long set) const
{
  if (banks <= 0 || NUM_SET <= 0) return 0;
  long b = set * banks / NUM_SET;
  return (b >= banks) ? banks - 1 : b;      // Start-Gap's spare set indexes one past the end
}

/* Read-only view of the cache's own tag array for the given set.  Needed for
 * the clean/dirty and valid predicates that EqualChance (C-shifting), Sequoia
 * (Clean-LRU) and Start-Gap (frames actually relocated) are defined over, and
 * which ChampSim does not pass to `update_replacement_state`. */
const champsim::cache_block* psc_family::set_blocks(long set) const
{
  if (intern_ == nullptr) return nullptr;
  if (set < 0 || set >= NUM_SET) return nullptr;
  auto base = static_cast<std::size_t>(set) * static_cast<std::size_t>(intern_->NUM_WAY);
  if (base + static_cast<std::size_t>(intern_->NUM_WAY) > intern_->block.size()) return nullptr;
  return &intern_->block[base];
}

long psc_family::valid_count(long set) const
{
  const champsim::cache_block* b = set_blocks(set);
  if (b == nullptr) return 0;
  long c = 0;
  for (long w = 0; w < NUM_WAY; ++w)
    if (b[w].valid) ++c;
  return c;
}

void psc_family::clear_displacement(long set, long way)
{
  if (displaced_to.empty()) return;
  displaced_to[idx(set, way)] = -1;
  for (long w = 0; w < NUM_WAY; ++w)
    if (displaced_to[idx(set, w)] == static_cast<int8_t>(way)) displaced_to[idx(set, w)] = -1;
}

/* Logical set -> physical set.
 *
 * Identity for every policy that does not remap.  Start-Gap and Sequoia's
 * G-OAS are inter-set remappings, and ChampSim's replacement interface cannot
 * express one (see the header): the CACHE derives the set from the address
 * before the module is called and only asks it for a way.  What is modelled
 * here is the permutation of the *wear accounting*: a write that ChampSim
 * places in logical set L is charged to physical frame (phys_set(L), way).
 *
 * This is exact for the wear distribution, because every set in this array has
 * the identical technology composition - ways [0, NVM_WAYS) NVM and the rest
 * SRAM in *every* set - so the permutation is an isomorphism on the wear
 * state, and because a set remap does not change which lines are accessed.
 * It does NOT reproduce the transient hit-rate effect of the in-flight copy,
 * nor the second lookup a partially migrated set needs.  Both are named at the
 * per-policy comment blocks with the direction they cut. */
long psc_family::phys_set(long set) const
{
  switch (policy) {
  case policy_t::STARTGAP: {
    // Figure 7, verbatim:  PA = (LA + Start) mod N ;  if PA >= GAP: PA = PA + 1
    long pa = (set + sg_start) % sg_N;
    if (pa >= sg_gap) pa += 1;
    return pa;
  }
  case policy_t::SEQUOIA:
    if (!seq_oas) return set;
    return (seq_dt[static_cast<std::size_t>(set)] << seq_lowbits) | (set & seq_lowmask);
  case policy_t::FULL:
    if (!full_swap) return set;
    [[fallthrough]];
  case policy_t::WEAR_SWAP:
    if (ws_map.empty()) return set;
    return ws_map[static_cast<std::size_t>(set)];
  default:
    return set;
  }
}

void psc_family::invalidate(long set, long way)
{
  /* The module does not own the tag array, so an invalidation is modelled by
   * making the frame maximally stale: it becomes the next victim and its
   * recency no longer protects it. Wear state is preserved, because the cell
   * has still been written. */
  last_used[idx(set, way)] = 0;
}

/* Charge one physical write to a *physical* frame.  Every mechanism-performed
 * write - fill, write hit, migration, displacement, relocation - goes through
 * here, so the definition of "a charged write" is the same in this module as
 * in the Python replay's `llc.charge_write`. */
void psc_family::charge_phys(long pset, long way, bool induced)
{
  auto i = idx(pset, way);
  writes[i]++;
  if (is_nvm(way)) {
    damage[i] += damage_per_write(pset, way);
    if (policy == policy_t::PSC_BALANCE) pb_note_write(pset);
    nvm_writes++;
    if (static_cast<int>(coarse[i]) < (1 << wear_bits) - 1) coarse[i]++;
    else for (long w = 0; w < NVM_WAYS; ++w) coarse[idx(pset, w)] = static_cast<uint8_t>(coarse[idx(pset, w)] >> 1);
    thermal_tick(pset);
    /* Start-Gap's psi counts *demand* writes only; the mechanism's own
     * relocation writes are excluded, which is what makes the paper's
     * arithmetic (psi demand + 1 relocation = psi+1 total) come out exactly. */
    if (policy == policy_t::STARTGAP && !induced) sg_tick();
    /* WearSwap's per-set counters are wear counters, so they see EVERY charged
     * NVM write - fill, write hit and the mechanism's own migration traffic -
     * and they are indexed by the PHYSICAL set, which is the thing that wears.
     * `ws_note_write` guards its own re-entrancy while a swap is in flight. */
    if (policy == policy_t::WEAR_SWAP || (policy == policy_t::FULL && full_swap)) ws_note_write(pset, way);
  } else {
    sram_writes++;
  }
  if (induced) induced_writes++;
}

void psc_family::charge(long set, long way, bool induced)
{
  long ps = phys_set(set);
  if (ps != set) remapped_writes++;   // instrument: proves the remap is wired
  charge_phys(ps, way, induced);
}

/* A write that lands on a resident block.  If the block has been displaced
 * inside its set by WAD or EqualChance, its data now lives in another frame,
 * so that is the cell the write wears.  A redirected demand write is still
 * ONE write and is not `induced`: only writes the mechanism adds on top of the
 * demand stream (the displacement copy itself, a migration, a relocation) are
 * write amplification. */
void psc_family::charge_hit(long set, long way)
{
  long target = way;
  if (!displaced_to.empty()) {
    int8_t d = displaced_to[idx(set, way)];
    if (d >= 0 && d < static_cast<int8_t>(NUM_WAY)) target = d;
  }
  charge(set, target, false);
}

long psc_family::lru_in(long set, long lo, long hi, const champsim::cache_block* cs) const
{
  long best = -1;
  uint64_t bt = 0;
  for (long w = lo; w < hi; ++w) {
    if (cs != nullptr && !cs[w].valid) return w;
    uint64_t t = last_used[idx(set, w)];
    if (best < 0 || t < bt) { bt = t; best = w; }
  }
  return best;
}

long psc_family::lru_in_list(long set, const std::vector<long>& ways, const champsim::cache_block* cs) const
{
  long best = -1;
  uint64_t bt = 0;
  for (long w : ways) {
    if (cs != nullptr && !cs[w].valid) return w;
    uint64_t t = last_used[idx(set, w)];
    if (best < 0 || t < bt) { bt = t; best = w; }
  }
  return best;
}

/* VRO-Rank: least-worn frame among the `pool` least-recently-used NVM ways.
 * Restricting the wear choice to LRU-plausible victims is what keeps the hit
 * rate intact; choosing the globally least-worn way costs several points. */
long psc_family::rank_way(long set, const champsim::cache_block* cs)
{
  if (NVM_WAYS <= 1) return -1;
  /* The coarse wear counters belong to PHYSICAL frames - `charge_phys` writes
   * them at the physical set - so they must be read there too.  Identity for
   * every policy that does not remap; it matters for `full`, whose WearSwap
   * channel permutes the mapping underneath the ranking channel.  Recency, in
   * contrast, belongs to the logical frame, because that is where the tag is. */
  long ps = phys_set(set);
  int lo = 255, hi = 0;
  for (long w = 0; w < NVM_WAYS; ++w) {
    int v = coarse[idx(ps, w)];
    lo = std::min(lo, v);
    hi = std::max(hi, v);
  }
  if (hi - lo < rank_trigger) return -1;
  for (long w = 0; w < NVM_WAYS; ++w)
    if (cs != nullptr && !cs[w].valid) return w;

  std::vector<long> order(static_cast<std::size_t>(NVM_WAYS));
  std::iota(order.begin(), order.end(), 0L);
  std::sort(order.begin(), order.end(), [&](long a, long b) { return last_used[idx(set, a)] < last_used[idx(set, b)]; });
  long k = std::min(rank_pool, NVM_WAYS);
  long best = order[0];
  int bw = coarse[idx(ps, order[0])];
  for (long i = 1; i < k; ++i) {
    int v = coarse[idx(ps, order[static_cast<std::size_t>(i)])];
    if (v < bw) { bw = v; best = order[static_cast<std::size_t>(i)]; }
  }
  rank_interventions++;
  return best;
}

bool psc_family::predict_hot(champsim::address addr) const
{
  auto a = addr.to<uint64_t>() >> 6;
  auto i = static_cast<std::size_t>((a ^ (a >> 13) ^ (a >> 23)) & (pred_entries - 1));
  return pred_tab[i] >= pred_threshold;
}

void psc_family::note_write(champsim::address addr)
{
  auto a = addr.to<uint64_t>() >> 6;
  auto i = static_cast<std::size_t>((a ^ (a >> 13) ^ (a >> 23)) & (pred_entries - 1));
  if (pred_tab[i] < (1 << pred_bits) - 1) pred_tab[i]++;
  if (++pred_ctr >= pred_decay) {
    pred_ctr = 0;
    for (auto& c : pred_tab) c = static_cast<uint8_t>(c >> 1);
  }
}

/* ======================================================================
 * SHARED CHANNELS - so that a composite is a composition, not a copy
 * ======================================================================
 * `psc_pred`, `psc_vro_rank`, `tailguard` and `full` all open with the same
 * mu-channel decision, and `vro` and `psc_vro` share the same intra-set
 * rotating window.  Each rule lives in exactly one function below and every
 * policy that claims to contain it calls that function, so editing the rule
 * moves every policy built on it.  That is the property `full` needs: it is
 * defined in novel.py as PSC-Pred + VRO-Rank + WearSwap, and if it restated
 * those rules instead of calling them the three would drift apart silently. */

/* The PSC-Pred mu channel (novel.py `PSCPred.choose_way`, first half).
 * Returns an SRAM way to steer into, or -1 to leave the decision to the
 * caller's own intra-set channel.  The write-intensity table is trained here
 * whether or not the steering fires, exactly as the Python does. */
/* PSC-Balance. `pressure(s) = sc[s] / max(1, avg)`; a set above
 * `pb_hot_threshold` diverts clean traffic to SRAM as well as dirty.
 *
 * Two guards, both load-bearing. The cap bounds the diversion THIS mechanism
 * adds above plain PSC, not total SRAM occupancy - capping the total was the
 * first implementation and it was wrong, because on a write-heavy stream PSC's
 * own dirty-steering already exceeds the budget and the balance arm never
 * engaged at all. The floor refuses any diversion that would push NVM below
 * `pb_min_nvm` of allocations, which is the degenerate optimum a wear-only
 * objective always has. */
long psc_family::pb_victim(long set, const champsim::cache_block* cs, bool is_dirty)
{
  if (NVM_WAYS >= NUM_WAY) return lru_in(set, 0, NVM_WAYS, cs);   // no SRAM side
  bool want_sram = is_dirty;
  double denom = (allocations > 0) ? static_cast<double>(allocations) : 1.0;
  double press = static_cast<double>(pb_sc[static_cast<std::size_t>(set)]) / std::max(1.0, pb_avg);
  double extra = static_cast<double>(pb_diversions) / denom;
  double nvm_share = 1.0 - static_cast<double>(steered) / denom;

  if (!want_sram && press >= pb_hot_threshold) {
    if (extra >= pb_max_extra) pb_blocked_cap++;
    else if (nvm_share <= pb_min_nvm) pb_blocked_floor++;
    else { want_sram = true; pb_diversions++; }
  } else if (want_sram && pb_cold_threshold > 0.0 && press <= pb_cold_threshold) {
    want_sram = false;                       // cold set can absorb this write
    pb_cold_retentions++;
  }
  if (want_sram) {
    long w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
    if (w >= 0) { steered++; return w; }
  }
  long w = lru_in(set, 0, NVM_WAYS, cs);
  if (w >= 0) return w;
  w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
  if (w >= 0) steered++;
  return w;
}

/* Per-set wear counter plus an exponential mean, so the pressure comparison is
 * relative and the thresholds are workload-independent. Counters halve
 * array-wide on saturation - the same normalisation i2WAP uses. */
void psc_family::pb_note_write(long set)
{
  auto& v = pb_sc[static_cast<std::size_t>(set)];
  v++;
  pb_avg += (static_cast<double>(v) - pb_avg) / static_cast<double>(1L << pb_avg_shift);
  if (v >= pb_cap) {
    for (auto& x : pb_sc) x >>= 1;
    pb_avg *= 0.5;
  }
}

long psc_family::steer_sram(long set, const champsim::cache_block* cs, champsim::address addr, bool is_write)
{
  if (is_write) note_write(addr);
  if (NVM_WAYS >= NUM_WAY) return -1;                        // no SRAM partition
  /* Guard against the degenerate optimum: a policy that steers everything into
   * SRAM drives peak NVM writes towards zero and scores an unbounded lifetime
   * gain while turning a 16-way cache into a 4-way one. */
  bool cap_ok = (allocations < 1000) || (static_cast<double>(steered) / static_cast<double>(allocations) < max_sram_frac);
  if (!cap_ok || !predict_hot(addr)) return -1;
  long w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
  if (w >= 0) steered++;
  return w;
}

/* VRO's rotating write-way window (psc_vro.py `_NVMPartition.write_ways`).
 * (M-R) consecutive NVM ways starting at w0 are the write-ways; allocations
 * land only there.  Returns -1 if the window yields no way. */
long psc_family::vro_victim(long set, const champsim::cache_block* cs) const
{
  long n = NVM_WAYS - vro_R;
  if (n < 1) n = 1;
  std::vector<long> win;
  win.reserve(static_cast<std::size_t>(n));
  for (long k = 0; k < n; ++k) win.push_back((vro_w0 + k) % NVM_WAYS);
  return lru_in_list(set, win, cs);
}

/* psc_vro.py `_NVMPartition.tick`: beta saturates, then Swap-Ways and
 * Advance-Partition are applied together as one rotation. */
void psc_family::vro_tick()
{
  if (++vro_beta < vro_beta_max) return;
  vro_beta = 0;
  long n = std::max(1L, NVM_WAYS - vro_R);
  vro_w0 = (vro_w0 + n) % NVM_WAYS;
  vro_rotations++;
}

/* ======================================================================
 * SWWR / DWWR - Agarwal, "Techniques for Improving the Lifetime and
 * Performance of Non-Volatile Last Level Caches", PhD thesis, IIT Guwahati,
 * 2020, Chapter 4, Algorithms 1 and 2
 * ======================================================================
 * Port of stage3_hycsim/policies/swwr.py (which carries both classes).
 *
 * The ways are partitioned VERTICALLY into m equal windows and exactly one
 * window is write-restricted (read-only) for an interval I of processor cycles.
 *   Read hit                : served normally wherever the block is.
 *   Write hit, B in Wi      : redirected to the first invalid line in the other
 *                             windows of the same set; failing that, the LRU
 *                             victim of the non-restricted locations is evicted
 *                             (written back if dirty) and the write goes there.
 *                             B is invalidated from its location in the WRW.
 *   Write hit, B not in Wi  : written normally.
 *   LLC miss                : "the newly arrived Block is placed in the location
 *                             other than the WRW Wi location."
 * WinSelect: SWWR round-robins (Alg. 1 l. 27-30); DWWR takes the argmax over m
 * per-window write counters and resets the chosen one (Alg. 2 l. 30-34).
 *
 * THE INTERVAL IS IN CYCLES, AND THAT IS WHY THIS PORT EXISTS
 * -----------------------------------------------------------
 * docs/POLICY_COVERAGE.md used to list these two as unportable because I is
 * clocked in processor cycles.  That reason was wrong: `now_cycles()` reads the
 * cache's own clock, so the thesis's I is used verbatim, with no proxy and no
 * conversion.  This is in fact MORE faithful than the replay, where the record
 * stream had to have a cycle column bound onto it and a run that forgot to bind
 * it measured I in accesses - off by roughly three orders of magnitude.  The
 * catch-up loop in `wwr_clock` advances as many interval boundaries as the
 * elapsed cycle count implies, so a long gap with no LLC traffic cannot make
 * the schedule drift.
 *
 * `wwr_clock_ticks` and `wwr_intervals` are printed: at the thesis default of
 * I = 2,000,000 a 1M-instruction run of ~1.7M cycles crosses ZERO boundaries,
 * in which case the whole cache stays unrestricted (M5) and the policy is LRU
 * by construction.  If `wwr_intervals` is 0, the row is not a measurement of
 * SWWR; it is a measurement of a run too short to reach the first interval.
 *
 * TRIGGER MAPPING - and how it differs from the replay
 * -----------------------------------------------------
 *   python `aux_lookup(addr, op, now)`  -> the clock tick at the top of
 *       `update_replacement_state`.  Strictly better: the replay only got a
 *       timestamp on misses and on write hits, so a run of read hits could
 *       carry the boundary late; here every tag check ticks.
 *   python `choose_way`                 -> `find_victim`, restricted to the
 *       non-WRW ways.
 *   python `on_write(cs, way)`          -> `replacement_cache_fill`.
 *   python `on_write_hit(cs, way, ...)` -> `update_replacement_state` with
 *       hit==1 and type==WRITE.
 *
 * swwr.py's M3 records that the replay STARVES the write-hit path: the array is
 * filled only by an L2 writeback that misses, so the redirect case almost never
 * fires and SWWR degenerates into a pure fill-placement restriction.  Worse for
 * DWWR: Algorithm 2 increments Ci only in the two write-hit branches, so with
 * write hits starved the counters stay at zero, `max(Ci)` degenerates to the
 * lowest index and DWWR collapses onto a STATIC restriction of window 0 -
 * i.e. onto SWWR with no rotation at all.  ChampSim's LLC is not exclusive and
 * write hits outnumber allocations here, so both mechanisms run as specified.
 * `wwr_degenerate` counts intervals whose argmax was taken over an all-zero
 * counter vector; on ChampSim it should be ~0, and that contrast with the
 * replay is a result, not an inconsistency.
 *
 * REQUIRED HYBRID-ARRAY ADAPTATIONS (carried over verbatim from swwr.py)
 *   R1. Only the NVM partition is windowed.  Ways [0, NVM_WAYS) are divided
 *       into m windows; SRAM ways belong to no window and are never restricted.
 *       Restricting an SRAM way removes capacity and levels nothing.  Window w
 *       covers [w*M/m, (w+1)*M/m), which handles M not divisible by m.
 *   R2. Redirect and fill targets are every non-restricted location, INCLUDING
 *       the SRAM ways - the thesis says "a location L ... other than the write
 *       restricted window location" and in this array that includes them.
 *       Direction: *flatters* SWWR/DWWR, because a redirect into SRAM costs no
 *       NVM wear at all.  `wwr_redirect_to_sram` and `wwr_alloc_to_sram` make
 *       the size of the flattery auditable.  DWWR's counters are incremented
 *       only for NVM landings, since SRAM ways are in no window.
 *   R3. Restricted ways receive no writes of any kind while restricted - not
 *       redirected write hits and not fills.  This is specification, not
 *       assumption: Algorithm 1's `processCacheMiss` says so explicitly.
 *
 * WHAT CHAMPSIM FORCES, stated rather than faked
 *   F1. The fill rule can be VIOLATED by the cache itself.  `CACHE::handle_fill`
 *       takes any invalid way with `find_if_not(valid)` and only calls the
 *       replacement module when the set is full, so a fill into a set with a
 *       free frame inside the WRW lands there without the module ever being
 *       asked.  This is the same structural hole that costs EqualChance its
 *       I-shifting credit.  It is not hidden: `wwr_fill_into_wrw` counts every
 *       such write, so R3's violation rate is measured rather than assumed.
 *   F2. The redirect moves no data, because the module does not own the tag
 *       array.  It is modelled the way WAD and EqualChance already are: the
 *       write is charged to the destination frame and `displaced_to` records
 *       that the line's data now lives there, so its subsequent writes wear the
 *       new cell.  Consequences: the thesis's invalidation of the source line
 *       is NOT performed (invalidating would destroy a block that SWWR keeps
 *       cached, charging it a hit-rate loss it does not have), and the eviction
 *       of the destination's victim is NOT performed either.  The second
 *       omission *flatters* SWWR - it is exactly the extra miss traffic the
 *       redirect buys its wear relief with - so `wwr_redirect_evictions` and
 *       `wwr_redirect_writebacks` count the omitted flushes.
 *   F3. A redirected write is ONE write, not two: it is the demand write
 *       performed at a different cell, so it is charged with induced=false and
 *       does not appear as write amplification.  Same convention as
 *       EqualChance's shift; contrast Sequoia's WAD, where the displacement
 *       copy is a genuine extra write on top of the demand write. */
void psc_family::wwr_init()
{
  if (NVM_WAYS < 1) {
    std::cout << "psc_family: " << policy_name << " needs at least one NVM way; falling back to lru" << std::endl;
    policy = policy_t::LRU;
    policy_name = "lru";
    return;
  }
  wwr_m = std::max(1L, std::min(wwr_m, NVM_WAYS));
  wwr_win_of.assign(static_cast<std::size_t>(NUM_WAY), -1);   // SRAM ways: no window
  for (long w = 0; w < wwr_m; ++w) {
    // R1: window w covers NVM ways [w*M/m, (w+1)*M/m), which handles M not
    // divisible by m (M=12, m=4 -> three ways each; M=16, m=4 -> the thesis's
    // four).  SRAM ways keep the -1 sentinel and are never restricted.
    long lo = w * NVM_WAYS / wwr_m;
    long hi = (w + 1) * NVM_WAYS / wwr_m;
    for (long j = lo; j < hi; ++j) wwr_win_of[static_cast<std::size_t>(j)] = static_cast<int8_t>(w);
  }
  wwr_counter_max = (1u << static_cast<unsigned>(wwr_counter_bits)) - 1u;
  wwr_C.assign(static_cast<std::size_t>(wwr_m), 0);
  wwr_sel = -1;                                 // M5: first WinSelect yields W0
  wwr_wrw = -1;                                 // M5: unrestricted first interval
}

bool psc_family::wwr_is_restricted(long way) const
{
  if (wwr_wrw < 0 || way < 0 || way >= NUM_WAY) return false;
  return wwr_win_of[static_cast<std::size_t>(way)] == static_cast<int8_t>(wwr_wrw);
}

/* Alg. 1 l. 27-30 for SWWR; Alg. 2 l. 30-34 for DWWR.  M4: `max(Ci)` with equal
 * counters resolves to the lowest index - deterministic and documented;
 * round-robin tie-breaking would silently turn DWWR back into SWWR. */
long psc_family::wwr_select_window()
{
  if (policy != policy_t::DWWR) {
    wwr_sel = (wwr_sel + 1) % wwr_m;
    return wwr_sel;
  }
  long best = 0;
  uint32_t bestv = wwr_C[0];
  for (long i = 1; i < wwr_m; ++i) {
    if (wwr_C[static_cast<std::size_t>(i)] > bestv) {
      bestv = wwr_C[static_cast<std::size_t>(i)];
      best = i;
    }
  }
  if (bestv == 0) wwr_degenerate++;             // M3: the replay's collapse mode
  wwr_C[static_cast<std::size_t>(best)] = 0;    // "to remove the possibility of
                                                //  selecting the same window in
                                                //  the consecutive intervals"
  wwr_sel = best;
  return best;
}

void psc_family::wwr_clock()
{
  wwr_clock_ticks++;
  uint64_t c = now_cycles();
  if (!wwr_clock_started) {
    wwr_clock_started = true;
    wwr_t0 = c;
    return;
  }
  if (c < wwr_t0 || c - wwr_t0 < wwr_interval) return;
  uint64_t n = (c - wwr_t0) / wwr_interval;
  wwr_t0 += n * wwr_interval;
  for (uint64_t k = 0; k < n; ++k) {
    wwr_intervals++;
    wwr_wrw = wwr_select_window();
  }
}

/* Alg. 2 l. 19 / l. 22: Ci is incremented at the window the write LANDS in, on
 * a redirected write and on a normal write hit.  A no-op under SWWR, which is
 * how swwr.py factors it (`SWWR._count_window_write` is `pass`). */
void psc_family::wwr_count_window_write(long way, bool fill)
{
  if (policy != policy_t::DWWR) return;
  if (way < 0 || way >= NVM_WAYS) return;                    // R2: SRAM is in no window
  if (fill && !wwr_count_fills) return;                      // Algorithm 2 counts write hits only
  auto w = static_cast<std::size_t>(wwr_win_of[static_cast<std::size_t>(way)]);
  if (wwr_C[w] < wwr_counter_max) wwr_C[w]++;
  else wwr_counter_saturations++;
}

/* Algorithm 1 `processCacheMiss` (R3): allocate anywhere except the WRW. */
long psc_family::wwr_victim(long set, const champsim::cache_block* cs)
{
  long plain = lru_in(set, 0, NUM_WAY, cs);
  if (wwr_wrw < 0) return plain;
  std::vector<long> cand;
  cand.reserve(static_cast<std::size_t>(NUM_WAY));
  for (long w = 0; w < NUM_WAY; ++w)
    if (!wwr_is_restricted(w)) cand.push_back(w);
  if (cand.empty()) return plain;
  long w = lru_in_list(set, cand, cs);
  if (w < 0) return plain;
  if (w != plain) wwr_alloc_deflected++;
  return w;
}

/* Algorithm 1, lines 15-20. */
void psc_family::wwr_write_hit(long set, long way)
{
  wwr_write_hits++;

  /* Resolve where the line's data actually lives: an earlier redirect may
   * already have moved it out of the restricted window, in which case this is
   * an ordinary write hit and nothing more happens. */
  long eff = way;
  int8_t d = displaced_to[idx(set, way)];
  if (d >= 0 && d < static_cast<int8_t>(NUM_WAY)) eff = d;

  if (!wwr_is_restricted(eff)) {
    wwr_write_hits_unrestricted++;
    wwr_count_window_write(eff, false);
    charge_hit(set, way);                       // routes through displaced_to itself
    return;
  }
  wwr_write_hits_restricted++;

  const champsim::cache_block* b = set_blocks(set);
  long dest = -1;
  // "... is redirected to the first invalid line" in the other windows (R2)
  for (long w = 0; w < NUM_WAY && dest < 0; ++w)
    if (w != eff && !wwr_is_restricted(w) && b != nullptr && !b[w].valid) dest = w;

  if (dest >= 0) {
    wwr_redirect_to_invalid++;
  } else {
    // "... the LRU victim line v is picked from the Location L and the
    //  write-back operation is performed according to its dirty bit"
    std::vector<long> cand;
    cand.reserve(static_cast<std::size_t>(NUM_WAY));
    for (long w = 0; w < NUM_WAY; ++w)
      if (w != eff && !wwr_is_restricted(w)) cand.push_back(w);
    // nullptr: the invalid case was handled above, so this is the LRU search
    dest = cand.empty() ? -1 : lru_in_list(set, cand, nullptr);
    if (dest < 0) {                             // nowhere to go: write normally
      wwr_no_dest++;
      wwr_count_window_write(eff, false);
      charge_hit(set, way);
      return;
    }
    if (b != nullptr && b[dest].valid) {
      if (b[dest].dirty) wwr_redirect_writebacks++;
      wwr_redirect_evictions++;                 // F2: counted, not performed
    }
  }

  charge(set, dest, false);                     // F3: one write, at a different cell
  displaced_to[idx(set, way)] = static_cast<int8_t>(dest);
  displacements++;
  wwr_redirects++;
  if (dest >= NVM_WAYS) wwr_redirect_to_sram++;
  else wwr_count_window_write(dest, false);
}

/* Sec. 4.5.5 / Table 4.18.  SWWR: "the amount of overheads by SWWR is only 42
 * bits" - the tag-transfer buffer that carries a tag to its redirected
 * location.  DWWR: m counters of 13 bits plus the same buffer, 4*13+42 = 94.
 * The thesis charges nothing for the window pointer or the interval counter;
 * this reproduces its arithmetic rather than correcting it. */
long psc_family::wwr_state_bits() const
{
  if (policy == policy_t::DWWR) return wwr_m * wwr_counter_bits + wwr_tag_buffer_bits;
  if (policy == policy_t::SWWR) return wwr_tag_buffer_bits;
  return 0;
}

/* ======================================================================
 * WEARSWAP - this project's own inter-set scheme (novel.py `WearSwap`)
 * ======================================================================
 * Start-Gap and i2WAP's Swap-Shift rotate the set mapping blindly: they move
 * lines whether or not the source set is hot.  Measured in the replay, both
 * cost more in migration writes than they returned (Start-Gap: +6.4% write
 * amplification, -3.7% RLI on the hybrid array).  WearSwap only acts on
 * evidence: coarse per-set wear counters are kept, and every `interval` NVM
 * writes the hottest set in a sampled window is swapped with the coldest, with
 * the migration writes charged.  Sampling keeps the search cost constant
 * instead of O(sets).  Parameters and the sampling stride (7919) are novel.py's.
 *
 * TRIGGER MAPPING
 *   python `on_write(cs, way)` -> every charged NVM write, which in ChampSim
 *   means fills, write hits AND the mechanism's own migration writes.  In the
 *   replay every charged write was an allocation, so the counters saw only the
 *   allocation stream; here they see the write-hit stream too, which is the
 *   larger of the two at an LLC.  WearSwap therefore reaches its interval far
 *   sooner and acts on a wear signal that includes in-place rewriting - which
 *   is the traffic an NVM endurance scheme most needs to see.
 *
 * ONE PLACE WHERE THIS PORT DELIBERATELY DIVERGES FROM THE REPLAY
 * ----------------------------------------------------------------
 * novel.py calls `llc.swap_sets(hot, cold)`, which exchanges the CONTENTS of
 * the two sets but does NOT rebind the set index (WearSwap does not set
 * `does_remap`, so `llc.run` keeps indexing by `set_index(a)`).  A block moved
 * to the other set is therefore never found again: in the replay the swap is
 * effectively an invalidation of both sets plus the migration write tax, and
 * the inter-set levelling it is supposed to deliver is not delivered - the
 * writes simply reappear in the same hot physical set on the next allocation.
 * That is precisely the deleted-mechanism failure `startgap.py` documents in
 * its v2, and the policy's own next line - swapping its two per-set counters -
 * shows that a MAPPING exchange is what was intended.
 *
 * This port implements the mapping exchange, using the same wear-accounting
 * permutation as Start-Gap and Sequoia's G-OAS (`phys_set()`), because that is
 * the only reading under which the scheme does anything.  Exact for the wear
 * distribution: every set here has identical technology composition, so two
 * sets are physically substitutable, and a set remap does not change which
 * lines are accessed.  `remapped_writes` is the liveness instrument; if it is
 * 0 while `ws_swaps` is non-zero the mapping has been disconnected and only the
 * tax remains.
 *
 * WHAT IS NOT MODELLED, and which way it cuts
 *   - The in-flight copy's bandwidth and occupancy.  Victim selection is
 *     untouched, so IPC is identical to `lru` BY CONSTRUCTION; a 0.00% IPC
 *     delta is a model artefact, not a finding that WearSwap is free.
 *     *Flatters* WearSwap on timing.
 *   - No hit-rate loss: under a genuine mapping exchange the data follows the
 *     mapping, so there is none to model.  That is a property of the mechanism,
 *     not of the port.
 *
 * The counters are attached to PHYSICAL sets and do not travel with the
 * mapping, because they measure how worn a location is and wear does not move.
 * (novel.py swaps them, which is coherent only in a model where logical and
 * physical are the same thing - which, given the missing remap, is what the
 * replay has.) */
void psc_family::ws_init()
{
  auto S = static_cast<std::size_t>(NUM_SET);
  ws_cap = (1u << static_cast<unsigned>(ws_bits)) - 1u;
  ws_sc.assign(S, 0);
  ws_map.resize(S);
  ws_inv.resize(S);
  for (std::size_t i = 0; i < S; ++i) ws_map[i] = ws_inv[i] = static_cast<long>(i);
  ws_sample = std::min(ws_sample, NUM_SET);
}

void psc_family::ws_note_write(long pset, long way)
{
  if (way >= NVM_WAYS || ws_sc.empty()) return;
  auto p = static_cast<std::size_t>(pset);
  if (p >= ws_sc.size()) return;
  if (++ws_sc[p] >= ws_cap)
    for (auto& v : ws_sc) v >>= 1;

  /* A swap's own migration writes wear real cells and are counted above, but
   * they must not re-trigger the interval from inside the swap. */
  if (ws_busy) return;
  if (++ws_ctr < ws_interval) return;
  ws_ctr = 0;

  long hot = ws_cursor, cold = ws_cursor;
  uint32_t hv = ws_sc[static_cast<std::size_t>(ws_cursor)];
  uint32_t cv = hv;
  for (long k = 1; k < ws_sample; ++k) {
    long i = (ws_cursor + k * 7919) % NUM_SET;
    uint32_t v = ws_sc[static_cast<std::size_t>(i)];
    if (v > hv) { hv = v; hot = i; }
    if (v < cv) { cv = v; cold = i; }
  }
  ws_cursor = (ws_cursor + ws_sample) % NUM_SET;
  if (hot != cold && (hv - cv) >= 2) {
    ws_swap(hot, cold);
    ws_swaps++;
  } else {
    ws_no_candidate++;
  }
}

/* Exchange the logical->physical bindings of the two logical sets currently
 * living at physical sets `pa` and `pb`, and charge one migration write per
 * valid frame on each side - novel.py's `swap_sets` cost model exactly. */
void psc_family::ws_swap(long pa, long pb)
{
  long la = ws_inv[static_cast<std::size_t>(pa)];
  long lb = ws_inv[static_cast<std::size_t>(pb)];
  ws_map[static_cast<std::size_t>(la)] = pb;
  ws_map[static_cast<std::size_t>(lb)] = pa;
  ws_inv[static_cast<std::size_t>(pa)] = lb;
  ws_inv[static_cast<std::size_t>(pb)] = la;

  const champsim::cache_block* ba = set_blocks(la);
  const champsim::cache_block* bb = set_blocks(lb);
  ws_busy = true;
  for (long w = 0; w < NUM_WAY; ++w) {
    if (ba == nullptr || ba[w].valid) { charge_phys(pb, w, true); ws_migration_writes++; }
    if (bb == nullptr || bb[w].valid) { charge_phys(pa, w, true); ws_migration_writes++; }
  }
  ws_busy = false;
}

/* ======================================================================
 * ELBA - Elastic Boundary Write Absorption
 * ======================================================================
 * Port of stage3_hycsim/policies/elba.py.  Three separable mechanisms, each
 * ablatable by parameter (see read_env): promote-and-release (PR), the global
 * write-absorption buffer (GWAB), the elastic boundary (EB).
 *
 * TRIGGER MAPPING
 *   python `on_write_hit(cs, way, ...)`   -> `update_replacement_state` with
 *                                            hit==1 and type==WRITE
 *   python `aux_lookup(addr, op, now)`    -> the GWAB probe at the top of
 *                                            `update_replacement_state`
 *   python `on_write(cs, way)`            -> `replacement_cache_fill`
 *   python `on_hit` / `choose_way` epoch  -> every `update_replacement_state`
 *
 * The protocol difference is the whole point.  In the HyCSim replay an
 * `L2_Rpl` onto a resident block charges no array write, so PR's trigger fires
 * on an event that costs nothing and the mechanism moved peak wear by 4%.  In
 * ChampSim a write hit really does write the array, so PR is removing writes
 * that were actually being charged.  Expect a materially different result;
 * that difference is the finding, not an inconsistency.
 *
 * HOW THE BUFFER PARTICIPATES IN LOOKUP - and why it must
 * -------------------------------------------------------
 * The Python version originally kept the absorbed blocks out of the lookup
 * path, so they served nothing and ELBA measured as pure capacity loss; the
 * fix (`aux_lookup`) is documented in elba.py.  ChampSim will not let a
 * replacement module turn a tag miss into a hit - `try_hit` decides hit/miss
 * before the module is called and there is no hook to override it.  Repeating
 * the Python bug is therefore not an option, so GWAB is realised here as a
 * DATA-SIDE buffer with the tag left in place: the absorbed block keeps its
 * frame in the set (so ChampSim's own tag scan finds it, and the buffer
 * genuinely participates in lookup), while its data - and therefore every
 * subsequent write to it - lives in the shared SRAM buffer.  This is a real
 * hybrid-cache organisation (NVM data array, absorbed lines held SRAM-side),
 * not a modelling dodge, and it preserves GWAB's defining property: capacity
 * is a single global pool demand-allocated across sets rather than pinned to
 * per-set geometry.
 *
 * WHAT THIS COSTS.  PR's "release the frame" half is a capacity return that
 * the port cannot make while also keeping the block findable: freeing the
 * frame in ChampSim means the block is gone at the next fill to that set,
 * because there is no buffer the array can be asked to consult.  Default is
 * therefore keep-the-frame (`PSC_ELBA_RELEASE=0`), which measures the wear
 * benefit without the capacity benefit and so *penalises* ELBA on capacity and
 * *flatters* it on hit rate relative to the Python reading.  Set
 * `PSC_ELBA_RELEASE=1` for the Python reading: the frame is released (made
 * maximally stale, so the next fill in the set reclaims it) and the absorbed
 * block dies with it - `elba_early_evictions` counts exactly how often that
 * happened, so the size of the effect is auditable rather than asserted. */
void psc_family::elba_buf_trim()
{
  while (static_cast<long>(elba_buf.size()) > elba_cap && !elba_order.empty()) {
    uint64_t victim = elba_order.front();
    elba_order.pop_front();
    auto it = elba_buf.find(victim);
    if (it == elba_buf.end()) continue;          // lazily deleted, skip
    /* Dirty on eviction: it goes to memory, NOT back into an NVM frame.
     * Backfilling is what makes a swap cost NVM endurance; releasing is the
     * point of the mechanism. */
    if (it->second.dirty) elba_buf_writebacks++;
    // the line's frame is given up: it holds no live data any more
    invalidate(it->second.set, it->second.way);
    elba_buf.erase(it);
    elba_buf_evictions++;
  }
}

void psc_family::elba_buf_insert(long set, long way, uint64_t addr, bool dirty)
{
  auto it = elba_buf.find(addr);
  if (it != elba_buf.end()) {
    it->second.dirty = it->second.dirty || dirty;
    it->second.stamp = cycle;
    it->second.set = set;
    it->second.way = way;
    return;
  }
  gwab_entry e;
  e.set = set;
  e.way = way;
  e.dirty = dirty;
  e.stamp = cycle;
  elba_buf.emplace(addr, e);
  elba_order.push_back(addr);
  sram_writes++;            // the promoted line is written into SRAM
  elba_buf_trim();
}

void psc_family::elba_buf_drop(uint64_t addr, bool count_early)
{
  auto it = elba_buf.find(addr);
  if (it == elba_buf.end()) return;
  if (it->second.dirty) elba_buf_writebacks++;
  elba_buf.erase(it);
  if (count_early) elba_early_evictions++;
}

void psc_family::elba_write_hit(long set, long way, uint64_t addr)
{
  // GWAB hit on a write: absorbed in SRAM, the NVM cell is not touched.
  auto it = elba_buf.find(addr);
  if (it != elba_buf.end()) {
    it->second.dirty = true;
    it->second.stamp = cycle;
    it->second.set = set;
    it->second.way = way;
    elba_absorbed++;
    elba_buf_hits++;
    sram_writes++;
    return;
  }
  if (!is_nvm(way) || elba_cap <= 0) {          // SRAM side, or GWAB ablated
    charge(set, way, false);
    return;
  }
  elba_burst.insert(addr);
  auto i = idx(set, way);
  if (elba_wcount[i] < 255) elba_wcount[i]++;
  if (static_cast<long>(elba_wcount[i]) < elba_promote_on) {
    charge(set, way, false);                    // not hot enough yet
    return;
  }
  /* Promote and RELEASE.  AYUSH writes the displaced SRAM resident into the
   * vacated NVM frame, spending one NVM write per promotion; ELBA spends none.
   * The triggering write is merged into the promotion copy - one physical SRAM
   * write moves the new data into the buffer - so exactly one write is
   * charged, and none of it is charged to NVM. */
  elba_buf_insert(set, way, addr, true);
  elba_promotions++;
  elba_wcount[i] = 0;
  if (elba_release) {
    invalidate(set, way);
    elba_frames_released++;
  }
}

void psc_family::elba_access(bool hit)
{
  elba_epoch_acc++;
  if (hit) elba_epoch_hits++;
  if (!elba_elastic) return;
  if (++elba_epoch_i >= elba_epoch) elba_retune();
}

/* Move the boundary towards the measured burst working set.
 *
 * Two-sided by construction: the wear signal asks for growth, the hit-rate
 * guard can refuse it.  A one-sided controller would discover the degenerate
 * optimum - stop using NVM entirely - which scores an unbounded lifetime gain
 * while destroying the cache.  ChampSim gives a better hit-rate signal than
 * the replay did: `update_replacement_state` is called on every tag check with
 * the real hit flag, where the Python version had to approximate the
 * denominator from `choose_way` calls. */
void psc_family::elba_retune()
{
  elba_epoch_i = 0;
  double hr = elba_epoch_acc ? static_cast<double>(elba_epoch_hits) / static_cast<double>(elba_epoch_acc) : 0.0;
  auto burst = static_cast<long>(elba_burst.size());
  long old = elba_cap;

  if (elba_last_hr >= 0.0 && hr < elba_last_hr - elba_tol)
    elba_cap = std::max(elba_min, static_cast<long>(static_cast<double>(elba_cap) * elba_shrink));
  else if (burst > elba_cap)
    elba_cap = std::min(elba_max, static_cast<long>(static_cast<double>(elba_cap) * elba_grow));
  else if (static_cast<double>(burst) < static_cast<double>(elba_cap) * 0.5)
    elba_cap = std::max(elba_min, static_cast<long>(static_cast<double>(elba_cap) * elba_shrink));

  elba_buf_trim();
  if (elba_cap != old) elba_boundary_changes++;
  elba_last_hr = hr;
  elba_epoch_hits = elba_epoch_acc = 0;
  elba_burst.clear();
}

/* ======================================================================
 * SEQUOIA - Jokar, Arjomand & Sarbazi-Azad, IEEE TVLSI 24(3):954-967, 2016
 * ======================================================================
 * Port of stage3_hycsim/policies/sequoia.py.  G-OAS (inter-set association,
 * Sec. IV-A) plus WAD (intra-set displacement, Sec. IV-B), with the AT/DT/RT
 * tables and the per-line native bit.
 *
 * TRIGGER MAPPING
 *   python `on_write_hit` -> `update_replacement_state`, hit==1, type==WRITE.
 *   The paper is explicit (Sec. IV opening) that "wear-leveling procedures are
 *   triggered on write-hit accesses".  In the HyCSim replay a write hit is an
 *   `L2_Rpl` onto a resident block, which charges no array write; SSC never
 *   once reached its 127 threshold in 400k records, so BOTH procedures were
 *   structurally dead and the row measured Sequoia doing nothing.  In ChampSim
 *   a write hit is a real array write, so the trigger has real headroom - this
 *   is the clearest case in the study of a mechanism killed by one protocol and
 *   alive under the other.
 *   python `choose_way` / `on_write` -> `find_victim` / `replacement_cache_fill`.
 *
 * HYBRID-ARRAY ADAPTATIONS (carried over verbatim from sequoia.py)
 *   A1. SSC and RSC count write hits on NVM ways ONLY.  Sequoia has no notion
 *       of a wear-exempt way; counting a write hit that landed in an SRAM way
 *       would declare a set hot on writes that touched no NVM cell and fire
 *       both mechanisms on phantom wear.  Direction: *penalises* Sequoia
 *       mildly.  `seq_write_hits_sram` records how many events were dropped.
 *   A2. WAD victims are restricted to NVM ways.  Displacing a hot line into an
 *       SRAM way would relieve NVM wear for free; that is spare capacity, not
 *       wear levelling, and reporting it as Sequoia's result would flatter the
 *       competitor.
 *   A3. Every set here has identical technology composition, so two sets are
 *       physically substitutable and G-OAS's interchangeability assumption
 *       holds exactly.  It would NOT hold if the SRAM ways were concentrated
 *       in a subset of sets; that geometry is out of scope.
 *
 * AMBIGUITIES, resolved as in sequoia.py
 *   D1  coldest set already associated -> skipped (`seq_skipped_associated`);
 *       the hot set's own pending association is still aborted, per the paper.
 *       Skipping *flatters* Sequoia by avoiding cascade flushes.
 *   D2  OAS/WAD data movements DO increment the per-frame write counters; the
 *       paper's ~1% write-amplification claim is a claim about these writes and
 *       not counting them would make it unfalsifiable.
 *   D3  a WAD displacement is ONE write: the victim frame receives the hot
 *       line's data; clearing a valid bit is not a data write.
 *   D4  A_victim/B_victim chosen by ascending way index among eligible frames -
 *       "we choose the victims sequentially based on the way number in
 *       ascending order" - not by the LRU ideal the paper discards.
 *   D5  true LRU, so Table VII's Clean-LRU statistics will not reproduce; true
 *       LRU finds a clean victim at least as often, which *flatters* WAD.
 *   D7  SSC/HSC are per PHYSICAL set: HSC||SSC must identify a cold physical
 *       location, and "the SSC of the hot set is reset" is meaningless under a
 *       per-logical-set reading.
 *   D10 moved data carries its recency, so a migration does not change what the
 *       set would evict next.
 *
 * WHAT CHAMPSIM CANNOT EXPRESS, stated rather than faked
 *   G-OAS is an inter-set remap and the replacement interface has no way to
 *   change an address's set (see the header).  The permutation is therefore
 *   applied to the WEAR ACCOUNTING through `phys_set()`: once sets i and j
 *   exchange mappings, writes ChampSim places in logical set A are charged to
 *   physical frames of j.  That reproduces the inter-set wear levelling
 *   exactly, because the array is set-homogeneous (A3) and a remap does not
 *   change which lines are accessed.  It does NOT reproduce:
 *     - the *second search* (Sec. IV-A3).  The array never physically moves, so
 *       no line is ever in the "wrong" set and there is nothing to search for.
 *       `seq_second_searches_elided` counts the accesses that would have needed
 *       one in real hardware.  Direction: *flatters* Sequoia, which pays a
 *       second tag lookup for those accesses.
 *     - the six Fig. 4 cases as distinct hit paths.  Write/Read Second-Hit have
 *       no referent for the same reason; the association advances on fills into
 *       an associated set (the paper's Write Miss / Read Miss cases), which is
 *       the only "access that is not a first-hit" the port can observe.
 *   The abort path IS modelled with its real cost: remaining non-native lines
 *   of both sets are invalidated (a genuine hit-rate loss here, since
 *   `invalidate` makes them the next victims) and dirty ones counted as
 *   write-backs. */
void psc_family::seq_init()
{
  auto S = static_cast<std::size_t>(NUM_SET);
  seq_ssc_max = (1u << seq_ssc_bits) - 1u;
  seq_hsc_max = (1u << seq_hsc_bits) - 1u;
  seq_rsc_mask = (1u << seq_rsc_bits) - 1u;

  if (seq_G > NUM_SET) seq_G = NUM_SET;
  bool pow2_sets = (NUM_SET > 0) && ((NUM_SET & (NUM_SET - 1)) == 0);
  bool pow2_grp = (seq_G > 0) && ((seq_G & (seq_G - 1)) == 0);
  if (!pow2_sets || !pow2_grp) {
    /* The grouping rule - "the sets whose indices differ in log G most
     * significant bits belong to one group" - is only defined for power-of-two
     * geometries.  Rather than invent a rule the paper does not have, G-OAS is
     * disabled and the fact is printed, so a row without inter-set levelling is
     * visibly a row where it could not run. */
    std::cout << "psc_family: sequoia G-OAS disabled, sets=" << NUM_SET << " group=" << seq_G
              << " (both must be powers of two); WAD still active" << std::endl;
    seq_oas = false;
    seq_G = 1;
  }
  seq_gbits = 0;
  for (long g = seq_G; g > 1; g >>= 1) seq_gbits++;
  long sbits = 0;
  for (long s = NUM_SET; s > 1; s >>= 1) sbits++;
  seq_lowbits = sbits - seq_gbits;
  seq_lowmask = (1L << seq_lowbits) - 1L;

  seq_ssc.assign(S, 0);
  seq_hsc.assign(S, 0);
  seq_hot.assign(S, 0);
  seq_at_a.assign(S, 0);
  seq_at_i.assign(S, 0);
  seq_steps.assign(S, 0);
  seq_wad_armed.assign(S, 0);
  seq_dt.resize(S);
  seq_rt.resize(S);
  for (std::size_t i = 0; i < S; ++i) {
    seq_dt[i] = static_cast<long>(i) >> seq_lowbits;
    seq_rt[i] = static_cast<long>(i) >> seq_lowbits;
  }
  seq_native.assign(static_cast<std::size_t>(NUM_SET * NUM_WAY), 1);
}

long psc_family::seq_ls_of(long ps) const
{
  return (seq_rt[static_cast<std::size_t>(ps)] << seq_lowbits) | (ps & seq_lowmask);
}

long psc_family::seq_prev(long ps) const
{
  return (seq_at_i[static_cast<std::size_t>(ps)] << seq_lowbits) | (ps & seq_lowmask);
}

/* Ascending way order among valid non-native frames (D4). */
long psc_family::seq_first_non_native(long ls) const
{
  const champsim::cache_block* b = set_blocks(ls);
  for (long w = 0; w < NUM_WAY; ++w)
    if (!seq_native[idx(ls, w)] && (b == nullptr || b[w].valid)) return w;
  return -1;
}

void psc_family::seq_write_hit(long set, long way)
{
  seq_write_hits++;
  long ps = phys_set(set);
  if (seq_oas && seq_at_a[static_cast<std::size_t>(ps)]) seq_second_searches_elided++;

  if (!is_nvm(way)) {
    seq_write_hits_sram++;      // A1: no NVM cell was touched
    charge(set, way, false);
    return;
  }
  seq_write_hits_nvm++;

  // --- SSC / HSC, Sec. IV-A1 ------------------------------------------
  auto p = static_cast<std::size_t>(ps);
  bool just_saturated = false;
  if (seq_ssc[p] < seq_ssc_max) {
    seq_ssc[p] = static_cast<uint16_t>(seq_ssc[p] + 1);
    if (seq_ssc[p] == seq_ssc_max) {
      just_saturated = true;
      seq_ssc_saturations++;
      seq_hot[p] = 1;
      seq_hot_marks++;
      uint32_t h = static_cast<uint32_t>(seq_hsc[p]) + 1u;
      if (h >= seq_hsc_max) {
        // "When the HSC of a set saturates, the controller resets all the
        //  HSCs in the cache."
        std::fill(seq_hsc.begin(), seq_hsc.end(), static_cast<uint16_t>(0));
        seq_hsc_resets++;
      } else {
        seq_hsc[p] = static_cast<uint16_t>(h);
      }
    }
  }

  // --- OAS trigger: "the next write-hit to this set" -------------------
  if (seq_hot[p] && !just_saturated) {
    seq_hot[p] = 0;
    seq_ssc[p] = 0;                       // "the SSC of the hot set is reset"
    if (seq_oas) seq_associate(ps);
    /* With PSC_SEQ_OAS=0 the reset still happens, so the saturating SSC does
     * not pin RSC at its threshold and the ablation does not silently change
     * WAD as well. */
  }

  /* Charge the demand write BEFORE WAD may displace the line, so the
   * displacement's own write (D3: exactly one) is charged separately and the
   * two are not conflated. */
  charge_hit(set, way);

  /* --- WAD, Sec. IV-B --------------------------------------------------
   * Re-read the physical set: an association above may just have moved this
   * logical set onto a different physical location, and WAD's counter (RSC is
   * the low bits of SSC) and its displacement write both belong to the set the
   * data now lives in, not the one it came from. */
  if (seq_wad) {
    auto q = static_cast<std::size_t>(phys_set(set));
    if (seq_wad_armed[q]) {
      seq_wad_armed[q] = 0;
      seq_wad_displace(set, way);
    }
    // "we consider few least significant bits of the SSC as RSC"
    if ((static_cast<uint32_t>(seq_ssc[q]) & seq_rsc_mask) == seq_rsc_mask) {
      seq_wad_armed[q] = 1;
      seq_wad_arms++;
    }
  }
}

void psc_family::seq_associate(long hot_ps)
{
  auto hp = static_cast<std::size_t>(hot_ps);
  if (seq_at_a[hp]) {
    // "the controller stops an on-going association and invalidates the lines
    //  remaining in the previous set"
    seq_abort(hot_ps, seq_prev(hot_ps));
  }

  long grp = hot_ps & seq_lowmask;
  long best = -1;
  uint32_t bestval = 0;
  for (long m = 0; m < seq_G; ++m) {
    long ps = (m << seq_lowbits) | grp;
    if (ps == hot_ps) continue;
    if (seq_at_a[static_cast<std::size_t>(ps)]) { seq_skipped_associated++; continue; }   // D1
    uint32_t v = (static_cast<uint32_t>(seq_hsc[static_cast<std::size_t>(ps)]) << seq_ssc_bits)
                 | static_cast<uint32_t>(seq_ssc[static_cast<std::size_t>(ps)]);           // HSC||SSC
    if (best < 0 || v < bestval) { bestval = v; best = ps; }
  }
  if (best < 0) { seq_no_cold_set++; return; }

  long i = hot_ps, j = best;
  long A = seq_ls_of(i), B = seq_ls_of(j);
  // exchange the mappings: A -> j, B -> i  (DT and RT stay inverses)
  seq_dt[static_cast<std::size_t>(A)] = j >> seq_lowbits;
  seq_dt[static_cast<std::size_t>(B)] = i >> seq_lowbits;
  std::swap(seq_rt[static_cast<std::size_t>(i)], seq_rt[static_cast<std::size_t>(j)]);
  // AT: each side records the other as its previous PS (Fig. 3)
  seq_at_a[static_cast<std::size_t>(i)] = seq_at_a[static_cast<std::size_t>(j)] = 1;
  seq_at_i[static_cast<std::size_t>(i)] = j >> seq_lowbits;
  seq_at_i[static_cast<std::size_t>(j)] = i >> seq_lowbits;
  // every resident line is now sitting in the wrong PS
  for (long ls : {A, B}) {
    const champsim::cache_block* b = set_blocks(ls);
    for (long w = 0; w < NUM_WAY; ++w)
      seq_native[idx(ls, w)] = static_cast<uint8_t>((b != nullptr && b[w].valid) ? 0 : 1);
  }
  seq_steps[static_cast<std::size_t>(i)] = seq_steps[static_cast<std::size_t>(j)] = 0;
  seq_associations++;
  if (seq_first_non_native(A) < 0 && seq_first_non_native(B) < 0) seq_complete(i, j);
}

void psc_family::seq_complete(long i, long j)
{
  seq_at_a[static_cast<std::size_t>(i)] = seq_at_a[static_cast<std::size_t>(j)] = 0;
  seq_steps[static_cast<std::size_t>(i)] = seq_steps[static_cast<std::size_t>(j)] = 0;
  seq_completions++;
}

void psc_family::seq_abort(long i, long j)
{
  for (long ps : {i, j}) {
    long ls = seq_ls_of(ps);
    /* After the mapping exchange, the lines still owed to `ps` are the ones of
     * the logical set that now maps AWAY from it, i.e. the logical set of its
     * associated partner.  Both logical sets are covered because the loop runs
     * over both physical sides. */
    const champsim::cache_block* b = set_blocks(ls);
    for (long w = 0; w < NUM_WAY; ++w) {
      if (!seq_native[idx(ls, w)]) {
        if (b != nullptr && b[w].valid && b[w].dirty) seq_abort_writebacks++;
        invalidate(ls, w);                    // "invalidates the lines remaining"
        seq_abort_invalidations++;
      }
      seq_native[idx(ls, w)] = 1;
    }
  }
  seq_at_a[static_cast<std::size_t>(i)] = seq_at_a[static_cast<std::size_t>(j)] = 0;
  seq_steps[static_cast<std::size_t>(i)] = seq_steps[static_cast<std::size_t>(j)] = 0;
  seq_aborts++;
}

void psc_family::seq_step(long i, long j)
{
  seq_steps_taken++;
  seq_steps[static_cast<std::size_t>(i)]++;
  seq_steps[static_cast<std::size_t>(j)] = seq_steps[static_cast<std::size_t>(i)];
  bool pending = (seq_first_non_native(seq_ls_of(i)) >= 0) || (seq_first_non_native(seq_ls_of(j)) >= 0);
  if (seq_steps[static_cast<std::size_t>(i)] >= NUM_WAY) {
    // "swapping two sets is completed after exactly K processor accesses ...
    //  that are not first-hit"
    if (pending) seq_abort(i, j);
    else seq_complete(i, j);
    return;
  }
  if (!pending) seq_complete(i, j);
}

/* Fig. 4's Write Miss / Read Miss, as far as ChampSim exposes them: a fill into
 * an associated set advances the migration by one line.  The relocated line's
 * write is charged to the DESTINATION physical frame (D2). */
void psc_family::seq_fill_step(long set)
{
  long ps = phys_set(set);
  if (!seq_oas || !seq_at_a[static_cast<std::size_t>(ps)]) return;
  long prev = seq_prev(ps);
  long b = seq_first_non_native(set);
  if (b < 0) {
    if (seq_first_non_native(seq_ls_of(prev)) < 0) seq_complete(ps, prev);
    return;
  }
  seq_native[idx(set, b)] = 1;
  charge_phys(ps, b, true);       // one line relocated into its new PS
  seq_line_moves++;
  seq_step(ps, prev);
}

/* Sec. IV-B.  "the hot line is displaced to another line as a victim and the
 * content of the victim line is flushed back to main memory, if it is dirty",
 * the victim being the Clean-LRU line (the first clean line down the LRU stack
 * excluding the MRU line), else the LRU line.  Restricted to NVM ways (A2).
 *
 * D6 - "the controller invalidates the hot line but does not change its
 * replacement metadata to protect it from next writes for a while" - needs no
 * special handling here.  The Python model had to add an explicit hold because
 * `llc.lru()` hands back any invalid frame immediately, which sent the very
 * next allocation straight into the frame WAD had just cooled.  This module
 * cannot invalidate ChampSim's tag array at all, so the frame keeps its
 * recency by construction and is never offered as a free invalid frame.
 *
 * What the port cannot do is flush the victim line: it does not own the tags.
 * The victim's eviction is therefore NOT modelled, only its write.  Direction:
 * *flatters* WAD by omitting exactly the main-memory write-back traffic the
 * paper measures at ~9.5x on swaptions; `seq_wad_victim_evictions` and
 * `seq_wad_writebacks` count the omitted flushes so the size of the omission is
 * auditable rather than asserted. */
void psc_family::seq_wad_displace(long ls, long hot_way)
{
  if (NVM_WAYS <= 1) { seq_wad_no_victim++; return; }
  const champsim::cache_block* b = set_blocks(ls);

  // MRU way over the whole set, excluded from the candidate pool
  long mru_way = 0;
  for (long w = 1; w < NUM_WAY; ++w)
    if (last_used[idx(ls, w)] > last_used[idx(ls, mru_way)]) mru_way = w;

  std::vector<long> pool;
  pool.reserve(static_cast<std::size_t>(NVM_WAYS));
  for (long w = 0; w < NVM_WAYS; ++w)
    if (w != hot_way && w != mru_way) pool.push_back(w);
  if (pool.empty())
    for (long w = 0; w < NVM_WAYS; ++w)
      if (w != hot_way) pool.push_back(w);
  if (pool.empty()) { seq_wad_no_victim++; return; }
  std::sort(pool.begin(), pool.end(), [&](long x, long y) { return last_used[idx(ls, x)] < last_used[idx(ls, y)]; });

  long victim = -1;
  for (long w : pool)
    if (b == nullptr || !b[w].valid || !b[w].dirty) { victim = w; break; }   // Clean-LRU
  if (victim < 0) victim = pool.front();                                     // "the LRU line"

  if (b != nullptr && b[victim].valid) {
    if (b[victim].dirty) seq_wad_writebacks++;
    seq_wad_victim_evictions++;
  }
  charge(ls, victim, true);                 // D3: exactly one write, routed through the current mapping
  displaced_to[idx(ls, hot_way)] = static_cast<int8_t>(victim);
  displacements++;
  seq_wad_displacements++;
}

/* ======================================================================
 * START-GAP - Qureshi et al., MICRO 2009, at set granularity
 * ======================================================================
 * Port of stage3_hycsim/policies/startgap.py.
 *
 * READ THIS BEFORE READING THE NUMBERS.  Start-Gap *is* an algebraic
 * remapping; the copy is only the bookkeeping that keeps data consistent with
 * the remap.  A previous Python version deleted the mechanism by never wiring
 * the remap - it wrote `start` without ever reading it - leaving a 1/(psi+1)
 * write tax with no benefit, under which inter-set variation got monotonically
 * *worse* as psi shrank and the best configuration was the scheme switched off.
 *
 * ChampSim's replacement interface CANNOT express an address remap.
 * `CACHE::get_set_index()` is private and non-virtual; `handle_fill` and
 * `try_hit` compute the set from the address and then ask the module only for
 * a way.  A faithful transplant of the tag-array remap is therefore
 * impossible, and a stub that charges the relocation writes while leaving the
 * mapping fixed would be exactly the deleted-mechanism failure above, which is
 * worse than an absent policy.
 *
 * What is implemented instead is the remap applied to the WEAR ACCOUNTING:
 * `phys_set()` is Fig. 7 verbatim, and every charged write is routed through
 * it, so a write ChampSim places in logical set LA wears physical frame
 * ((LA+Start) mod N, +1 if >= Gap).  This delivers BOTH halves - the
 * relocation cost and the levelling benefit - and is exact for the wear
 * distribution, because the array is set-homogeneous and a set remap does not
 * change which lines are accessed.  `remapped_writes` is printed as the
 * instrument that proves the mapping is live; if it is ever 0 while
 * `sg_movements` is non-zero, the mechanism has been deleted again.
 *
 * WHAT IT DOES NOT MODEL, and the direction each cuts:
 *   - The in-flight copy's bandwidth and occupancy.  ChampSim's IPC for
 *     `startgap` is therefore identical to `lru` BY CONSTRUCTION (victim
 *     selection is untouched).  A 0.00% IPC delta in the results table is an
 *     artefact of the model, NOT a finding that Start-Gap is free.  This
 *     *flatters* Start-Gap on timing.
 *   - Randomized Start-Gap (Sec. 5) is not implemented.  Plain Start-Gap
 *     reaches 53% of ideal endurance against the randomized variant's 97%,
 *     because "in typical programs heavily written lines tend to be located
 *     spatially close to each other" and plain Start-Gap can only move a line
 *     to its neighbour.  Report this row as plain Start-Gap and cite 53%.
 *
 * Cost model.  One gap movement relocates one SET, costing up to `ways` writes,
 * because in a set-indexed cache the set index is the only part of the
 * placement function that is an algebraic function of the address - a way is
 * chosen dynamically by LRU, so rotating ways permutes nothing.  The paper's
 * <= 1/(psi+1) overhead budget is held by scaling the interval,
 * psi_effective = ways * psi, not by shrinking the relocation unit until it
 * means nothing. */
void psc_family::sg_init()
{
  sg_N = NUM_SET;
  if (sg_N < 3) {
    std::cout << "psc_family: startgap needs at least 3 sets; falling back to lru" << std::endl;
    policy = policy_t::LRU;
    policy_name = "lru";
    return;
  }
  sg_gap = sg_N;                  // the spare starts at the top
  sg_start = 0;
  sg_psi = std::max<uint64_t>(1, sg_psi * static_cast<uint64_t>(sg_scale_by_ways ? NUM_WAY : 1));
}

/* Inverse of Fig. 7.  Physical location -> logical set, or -1 for the GapLine. */
long psc_family::sg_inverse(long pa) const
{
  if (pa == sg_gap) return -1;
  long pa0 = (pa > sg_gap) ? pa - 1 : pa;
  return ((pa0 - sg_start) % sg_N + sg_N) % sg_N;
}

void psc_family::sg_tick()
{
  if (++sg_ctr < sg_psi) return;         // Sec. 4.3: "once every psi writes"
  sg_ctr = 0;
  /* Figure 6.  The three actions of the wrap branch are simultaneous; splitting
   * them breaks the bijection in Fig. 7 for one movement, so they are applied
   * together here. */
  if (sg_gap == 0) {
    sg_move_set(sg_N, 0);
    sg_start = (sg_start + 1) % sg_N;
    sg_gap = sg_N;
    sg_rotations++;
  } else {
    sg_move_set(sg_gap - 1, sg_gap);
    sg_gap -= 1;
  }
  sg_movements++;
}

/* Relocate the contents of one physical set into the GapLine, charging one
 * write per frame that actually holds data. */
void psc_family::sg_move_set(long src_phys, long dst_phys)
{
  long ls = sg_inverse(src_phys);
  if (ls < 0) return;
  const champsim::cache_block* b = set_blocks(ls);
  for (long w = 0; w < NUM_WAY; ++w) {
    if (b != nullptr && !b[w].valid) continue;
    charge_phys(dst_phys, w, true);
    sg_frames_moved++;
  }
}

/* ======================================================================
 * EQUALCHANCE - Mittal & Vetter, INFLOW 2014, Section 3.2 / Algorithm 1
 * ======================================================================
 * Port of stage3_hycsim/policies/equalchance.py.
 *
 * State: one `numWrite` counter and one `FlagBit` PER SET, and nothing else.
 * The scheme has no per-way write counters, and an earlier Python version that
 * selected the globally least-written way was giving EqualChance state it does
 * not have, which flatters it.  `ec_ctr` and `ec_flag` are the only new arrays.
 *
 * Target: Algorithm 1 redirects to an INVALID block first (I-shifting) and to a
 * CLEAN block otherwise (C-shifting); if neither exists a normal write is
 * performed.  It is in-cache data movement, never invalidation, and the LRU age
 * is not updated when a shift happens.
 *
 * TRIGGER MAPPING - and one real behavioural difference
 *   python `on_write(cs, way)`  -> every charged NVM write in the set, which in
 *   ChampSim means BOTH a fill and a write hit.  In the HyCSim replay every
 *   write was an allocation, so `numWrite` counted allocations only; here it
 *   counts the write-hit stream too, which is the larger of the two at an LLC.
 *   EqualChance therefore fires far more often under ChampSim, and the shift
 *   has somewhere useful to go.
 *
 *   python `choose_way` -> `find_victim` when the flag is armed.  I-shifting is
 *   UNREACHABLE through this path: `CACHE::handle_fill` takes any invalid way
 *   itself with `find_if_not(valid)` and only calls the replacement module when
 *   the set is full.  So ChampSim already performs I-shifting on every fill, for
 *   every policy, and EqualChance gets no credit for it here.  `ec_i_shifts`
 *   counts only the shifts on the write-hit path, where an invalid frame is
 *   still reachable.
 *
 *   The write-hit shift has no Python counterpart, because in the replay a
 *   write hit charged nothing and there was nothing to redirect.  It is
 *   modelled the same way as Sequoia's WAD: the write is charged to the target
 *   frame and `displaced_to` records that the line's data now lives there, so
 *   its subsequent writes wear the new cell.  The block is not physically moved
 *   because the module does not own the tag array. */
void psc_family::ec_note_write(long set, long way)
{
  if (!is_nvm(way)) return;             // only NVM wears, so only NVM writes count
  auto s = static_cast<std::size_t>(set);
  if (++ec_ctr[s] >= static_cast<uint32_t>(ec_interval)) {
    ec_ctr[s] = 0;
    ec_flag[s] = 1;
  }
}

/* I-shifting then C-shifting, over the NVM ways, excluding `avoid_way`. */
long psc_family::ec_shift_target(long set, long avoid_way) const
{
  const champsim::cache_block* b = set_blocks(set);
  if (b == nullptr) return -1;
  for (long w = 0; w < NVM_WAYS; ++w)
    if (w != avoid_way && !b[w].valid) return w;
  for (long w = 0; w < NVM_WAYS; ++w)
    if (w != avoid_way && !b[w].dirty) return w;
  return -1;
}

long psc_family::find_victim(uint32_t /*cpu*/, uint64_t /*instr_id*/, long set, const champsim::cache_block* cs, champsim::address /*ip*/,
                             champsim::address full_addr, access_type type)
{
  const bool is_write = (type == access_type::WRITE);
  allocations++;
  const bool have_sram = (NVM_WAYS < NUM_WAY);

  switch (policy) {
  case policy_t::LRU:
  default:
    return lru_in(set, 0, NUM_WAY, cs);

  case policy_t::PSC: {
    if (is_write && have_sram) {
      long w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
      if (w >= 0) { steered++; return w; }
    }
    long w = lru_in(set, 0, NVM_WAYS, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  case policy_t::VRO: {
    // original: allocations confined to the rotating (M-R) write-way window
    long w = vro_victim(set, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  /* PSC-VRO (psc_vro.py `PSCVRO`, configuration A3): the technology-aware split
   * for the mu channel plus VRO's rotating window for the intra-set channel.
   * Composed from the same two pieces the standalone policies use - the SRAM
   * preference of `psc` and `vro_victim()` - so it cannot drift away from them.
   *
   * TRIGGER MAPPING.  python `choose_way(cs, addr, is_dirty)`'s `is_dirty` is
   * "this arriving block came from an L2 writeback", which is `type == WRITE`
   * at a ChampSim fill.  The measured defect of that rule at a real LLC applies
   * here in full and is why `psc_soft` exists: on 619.lbm essentially every LLC
   * WRITE access HITS, so the dirty->SRAM arm almost never fires on an
   * allocation while VRO's window restriction fires on all of them.  Expect
   * PSC-VRO to behave close to VRO on this hierarchy, and read the `steered`
   * counter before concluding anything about the split.
   *
   * The fallback chain is psc_vro.py's, not `vro`'s: window -> rest of NVM ->
   * SRAM.  `vro` falls back across all ways instead.  Kept distinct on purpose;
   * each reproduces its own source. */
  case policy_t::PSC_VRO: {
    if (is_write && have_sram) {
      long w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
      if (w >= 0) { steered++; return w; }
    }
    long w = vro_victim(set, cs);
    if (w >= 0) return w;
    w = lru_in(set, 0, NVM_WAYS, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  case policy_t::VRO_RANK: {
    long w = rank_way(set, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  case policy_t::PSC_PRED: {
    long w = steer_sram(set, cs, full_addr, is_write);
    if (w >= 0) return w;
    w = lru_in(set, 0, NVM_WAYS, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  /* PSC-VRO-Rank and FULL share one allocation path, because FULL is defined in
   * novel.py as `class Full(PSCVRORank)` - it adds the inter-set channel and
   * changes nothing about placement.  Expressing that as a shared case rather
   * than a copied one is what makes the reduction checks below true by
   * construction instead of by coincidence:
   *   PSC_FULL_SWAP=0                 -> identical to `psc_vro_rank`
   *   PSC_FULL_SWAP=0 PSC_FULL_RANK=0 -> identical to `psc_pred`
   * With all three channels off the residue is the NVM-preferring LRU base that
   * PSCPred.choose_way falls back to, i.e. `psc_pred` with a predictor that
   * never fires - NOT plain LRU, because the base of this family is PSC's
   * placement, not the baseline's. */
  case policy_t::PSC_VRO_RANK:
  case policy_t::FULL: {
    const bool use_pred = (policy != policy_t::FULL) || full_pred;
    const bool use_rank = (policy != policy_t::FULL) || full_rank;
    if (use_pred) {
      long w = steer_sram(set, cs, full_addr, is_write);
      if (w >= 0) return w;
    }
    if (use_rank) {
      long w = rank_way(set, cs);
      if (w >= 0) return w;
    }
    long w = lru_in(set, 0, NVM_WAYS, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  /* WearSwap touches neither partition choice nor way choice: it is purely an
   * inter-set remap driven by measured wear, so allocation is plain LRU and any
   * difference from `lru` in the results is the remap and its migration tax,
   * nothing else. */
  case policy_t::WEAR_SWAP:
    return lru_in(set, 0, NUM_WAY, cs);

  /* SWWR / DWWR share this path: Algorithm 1's `processCacheMiss` is identical
   * in Algorithm 2, only WinSelect differs. */
  case policy_t::SWWR:
  case policy_t::DWWR:
    return wwr_victim(set, cs);

  /* AYUSH (Mittal & Vetter, IEEE CAL 2015): keep blocks with demonstrated reuse
   * in SRAM; a block with no observed reuse is not worth an SRAM frame.  Reuse
   * is tracked per resident frame and consulted at victim selection. */
  case policy_t::AYUSH: {
    if (have_sram && is_write) {
      // prefer an SRAM frame whose resident block has shown no reuse
      long best = -1;
      uint64_t bt = 0;
      for (long w = NVM_WAYS; w < NUM_WAY; ++w) {
        if (cs != nullptr && !cs[w].valid) { best = w; break; }
        if (ayush_reuse[idx(set, w)] <= ayush_thresh) {
          uint64_t t = last_used[idx(set, w)];
          if (best < 0 || t < bt) { bt = t; best = w; }
        }
      }
      if (best >= 0) { ayush_migrations++; return best; }
    }
    long w = lru_in(set, 0, NVM_WAYS, cs);
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  /* DWAWR (Agarwal & Kapoor): each interval, the n most-written ways of a set
   * are marked restricted; writes selecting a restricted way are redirected to
   * an eligible unrestricted way, and the redirect is charged. */
  case policy_t::DWAWR: {
    long w = lru_in(set, 0, NUM_WAY, cs);
    if (w >= 0 && is_nvm(w) && dwawr_R[idx(set, w)]) {
      std::vector<long> ok;
      for (long j = 0; j < NVM_WAYS; ++j)
        if (!dwawr_R[idx(set, j)]) ok.push_back(j);
      if (!ok.empty()) {
        long r = lru_in_list(set, ok, cs);
        if (r >= 0) { dwawr_redirects++; return r; }
      }
    }
    return w;
  }

  case policy_t::I2WAP:
    return lru_in(set, 0, NUM_WAY, cs);

  /* ELBA allocates by plain LRU over all ways, exactly as elba.py's
   * `choose_way` does (`llc.lru(cs, range(llc.ways))`).  All three mechanisms
   * act on the write path, not the allocation path; a released frame is
   * already maximally stale and so is picked up here for free. */
  case policy_t::ELBA:
    return lru_in(set, 0, NUM_WAY, cs);

  /* Start-Gap does not touch victim selection: the remap is a function of the
   * address, orthogonal to which way inside a set is replaced. */
  case policy_t::STARTGAP:
    return lru_in(set, 0, NUM_WAY, cs);

  /* Sequoia's allocator is plain LRU.  D6's "hold" needs no expression here:
   * the module cannot invalidate ChampSim's tag array, so a WAD-cooled frame
   * keeps its recency and is never handed back as a free invalid frame. */
  case policy_t::SEQUOIA:
    return lru_in(set, 0, NUM_WAY, cs);

  /* EqualChance, Algorithm 1 on the allocation path.  Only C-shifting is
   * reachable here: ChampSim takes an invalid way itself before ever calling
   * find_victim, so I-shifting on a fill is already done by the cache. */
  case policy_t::EQUALCHANCE: {
    if (!ec_flag[static_cast<std::size_t>(set)]) return lru_in(set, 0, NUM_WAY, cs);
    ec_flag[static_cast<std::size_t>(set)] = 0;
    for (long w = 0; w < NVM_WAYS; ++w) {
      if (cs != nullptr && !cs[w].valid) { ec_i_shifts++; return w; }
    }
    for (long w = 0; w < NVM_WAYS; ++w) {
      // C-shifting: a clean frame, overwritable without a write-back
      if (cs != nullptr && !cs[w].dirty) { ec_c_shifts++; return w; }
    }
    ec_no_candidate++;
    return lru_in(set, 0, NUM_WAY, cs);
  }

  /* PSC-Soft: the partition is a *preference*, not a wall.
   *
   * Measured defect in PSC as specified: at the LLC of a real hierarchy the
   * writeback stream almost never causes a fill - on 619.lbm, 293,487 of
   * 293,488 LLC WRITE accesses hit.  So "if dirty -> SRAM" essentially never
   * fires on an allocation, while its companion rule "else -> NVM only" sends
   * ~100% of fills into the NVM partition instead of the 12/16 = 75% that plain
   * LRU sends there.  The SRAM ways starve and NVM wear rises: 635,490 NVM
   * writes under LRU against 817,131 under PSC on the same trace.
   *
   * PSC-Soft keeps the intent (write-carrying blocks belong in the
   * high-endurance partition) and removes the harmful half (clean blocks are
   * not banned from SRAM).  Predicted-hot or dirty blocks prefer SRAM; every
   * other block is placed by ordinary LRU across all ways, exactly as the
   * baseline would. */
  case policy_t::PSC_SOFT: {
    if (is_write) note_write(full_addr);
    bool cap_ok = (allocations < 1000) || (static_cast<double>(steered) / static_cast<double>(allocations) < max_sram_frac);
    if (have_sram && cap_ok && (is_write || predict_hot(full_addr))) {
      long w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
      if (w >= 0) { steered++; return w; }
    }
    return lru_in(set, 0, NUM_WAY, cs);     // all ways, not NVM only
  }

  case policy_t::PSC_SOFT_RANK: {
    if (is_write) note_write(full_addr);
    bool cap_ok = (allocations < 1000) || (static_cast<double>(steered) / static_cast<double>(allocations) < max_sram_frac);
    if (have_sram && cap_ok && (is_write || predict_hot(full_addr))) {
      long w = lru_in(set, NVM_WAYS, NUM_WAY, cs);
      if (w >= 0) { steered++; return w; }
    }
    long w = rank_way(set, cs);             // wear-ranked inside the LRU pool
    return (w >= 0) ? w : lru_in(set, 0, NUM_WAY, cs);
  }

  /* TailGuard: choose among a small candidate set by marginal Weibull hazard on
   * endurance-normalised damage, not by raw write count.  The objective is tail
   * risk; a frame with a weak endurance bin or a hot bank is avoided even if its
   * raw write count is low. */
  case policy_t::PSC_BALANCE:
    return pb_victim(set, cs, is_write);

  case policy_t::TAILGUARD: {
    {
      long w = steer_sram(set, cs, full_addr, is_write);   // same mu channel as psc_pred
      if (w >= 0) return w;
    }
    std::vector<long> order(static_cast<std::size_t>(NVM_WAYS));
    std::iota(order.begin(), order.end(), 0L);
    std::sort(order.begin(), order.end(), [&](long a, long b) { return last_used[idx(set, a)] < last_used[idx(set, b)]; });
    long k = std::min(tg_candidates, NVM_WAYS);
    long best = -1;
    double bcost = 0.0;
    for (long i = 0; i < k; ++i) {
      long w = order[static_cast<std::size_t>(i)];
      if (cs != nullptr && !cs[w].valid) { best = w; break; }
      double z = damage[idx(set, w)];
      double dz = damage_per_write(set, w);
      double hazard = std::pow(z + dz, tg_kappa) - std::pow(z, tg_kappa);
      double recency = static_cast<double>(i) / static_cast<double>(k); // 0 = coldest
      double cost = tg_lambda_r * hazard + tg_lambda_wa * recency;
      if (best < 0 || cost < bcost) { bcost = cost; best = w; }
    }
    tg_actions++;
    if (best < 0) best = lru_in(set, 0, NUM_WAY, cs);
    return best;
  }
  }
}

void psc_family::replacement_cache_fill(uint32_t /*cpu*/, long set, long way, champsim::address full_addr, champsim::address /*ip*/,
                                        champsim::address victim_addr, access_type /*type*/)
{
  last_used[idx(set, way)] = cycle++;
  ayush_reuse[idx(set, way)] = 0;
  elba_wcount[idx(set, way)] = 0;
  clear_displacement(set, way);              // the frame's data is new; any WAD / EqualChance redirect through it is void

  if (policy == policy_t::ELBA) {
    /* The absorbed copy cannot outlive the frame: ChampSim has no way to
     * consult a module-side buffer on a tag miss, so a block evicted from the
     * array is gone even though real ELBA would still be holding it.
     * `elba_early_evictions` is the size of that shortfall. */
    elba_buf_drop(victim_addr.to<uint64_t>(), true);
    elba_buf_drop(full_addr.to<uint64_t>(), false);
  }
  if (policy == policy_t::SEQUOIA) {
    /* Order matters: the migration step must pick its line before the filled
     * frame is marked native, because in Fig. 4's Write Miss the incoming line
     * takes exactly the frame the migrated line vacated. */
    seq_fill_step(set);
    seq_native[idx(set, way)] = 1;
  }

  /* SWWR/DWWR `on_write`.  R3 says a restricted way receives no writes at all,
   * but F1: `handle_fill` takes any invalid way itself and only asks the module
   * when the set is full, so a fill can land inside the WRW without the policy
   * ever being consulted.  Counted, not hidden. */
  if (policy == policy_t::SWWR || policy == policy_t::DWWR) {
    if (wwr_is_restricted(way)) wwr_fill_into_wrw++;
    if (way >= NVM_WAYS) wwr_alloc_to_sram++;
  }

  charge(set, way, false);
  if (policy == policy_t::EQUALCHANCE) ec_note_write(set, way);
  if (policy == policy_t::SWWR || policy == policy_t::DWWR) wwr_count_window_write(way, true);

  if (is_nvm(way)) {
    if (policy == policy_t::VRO || policy == policy_t::PSC_VRO) vro_tick();
    if (policy == policy_t::DWAWR) {
      dwawr_Z[idx(set, way)]++;
      if (++dwawr_t >= dwawr_interval) {
        dwawr_t = 0;
        dwawr_intervals++;
        // recompute the restricted set for every set: n largest counters
        for (long s = 0; s < NUM_SET; ++s) {
          std::vector<long> o(static_cast<std::size_t>(NVM_WAYS));
          std::iota(o.begin(), o.end(), 0L);
          std::sort(o.begin(), o.end(), [&](long a, long b) { return dwawr_Z[idx(s, a)] > dwawr_Z[idx(s, b)]; });
          for (long j = 0; j < NVM_WAYS; ++j) dwawr_R[idx(s, j)] = 0;
          for (long j = 0; j < std::min(dwawr_n, NVM_WAYS); ++j) {
            dwawr_R[idx(s, o[static_cast<std::size_t>(j)])] = 1;
            dwawr_Z[idx(s, o[static_cast<std::size_t>(j)])] = 0;
          }
        }
      }
    }
    if (policy == policy_t::I2WAP) {
      /* Swap-Shift, per Wang et al. Sec. 5.2: the swap "only exchanges the set
       * IDs, and the data stored in these two sets are simply invalidated".
       * It is explicitly data-invalidation, not data movement, so no migration
       * writes are charged. Charging them - as an earlier version did - invents
       * write amplification the scheme does not have. */
      if (++ss_ctr >= ss_interval) {
        ss_ctr = 0;
        long a = static_cast<long>(ss_ptr % static_cast<uint64_t>(NUM_SET));
        long b = (a + 1) % static_cast<long>(NUM_SET);
        ss_ptr++;
        if (ss_ptr >= static_cast<uint64_t>(NUM_SET - 1)) { ss_ptr = 0; ss_shift++; }
        for (long j = 0; j < NUM_WAY; ++j) {
          invalidate(a, j);
          invalidate(b, j);
        }
        ss_swaps++;
      }
    }
  }
}

void psc_family::update_replacement_state(uint32_t /*cpu*/, long set, long way, champsim::address full_addr, champsim::address /*ip*/,
                                          champsim::address /*victim_addr*/, access_type type, uint8_t hit)
{
  /* ELBA's elastic controller needs the hit rate over EVERY access, so it is
   * sampled before the miss early-return.  This is a strictly better signal
   * than the replay had: there, the denominator was approximated from
   * `choose_way` calls; here ChampSim reports the real hit flag on every tag
   * check.  The two-sided guard depends on that denominator being honest,
   * because a wear-only objective has a degenerate optimum - stop using NVM
   * entirely - which scores an unbounded lifetime gain while destroying the
   * cache. */
  if (policy == policy_t::ELBA) elba_access(hit != 0);

  /* SWWR/DWWR: the interval clock, ticked on EVERY tag check (hit or miss)
   * before any early return.  This is where the port beats the replay - the
   * Python could only tick in `aux_lookup` (misses) and `on_write_hit`, so a
   * run of read hits could carry an interval boundary late.  The tick reads the
   * cache's cycle counter, so I is in the thesis's units. */
  if (policy == policy_t::SWWR || policy == policy_t::DWWR) wwr_clock();

  if (hit == 0) return;
  if (way < 0 || way >= NUM_WAY) return;
  if (type != access_type::WRITE) {
    last_used[idx(set, way)] = cycle++;
    if (ayush_reuse[idx(set, way)] < 255) ayush_reuse[idx(set, way)]++;
    if (policy == policy_t::ELBA) {
      // a read served out of the absorption buffer; the block is still in the
      // set, so ChampSim found it for us -- credit the buffer with the hit
      auto it = elba_buf.find(full_addr.to<uint64_t>());
      if (it != elba_buf.end()) { it->second.stamp = cycle; elba_buf_hits++; }
    }
  } else {
    /* A write hit rewrites the frame in place: a physical write event, and the
     * only place most LLC write traffic ever appears.  At the LLC of a real
     * hierarchy the writeback stream overwhelmingly *hits* - 293,487 of 293,488
     * LLC WRITE accesses on 619.lbm - so a predictor trained only at fill time
     * sees almost nothing.  Training here, on the real address, is what lets a
     * write-intensity predictor learn which blocks deserve an SRAM frame when
     * they are next filled. */
    note_write(full_addr);

    /* PoLF (Wang et al. Sec. 6.3) counts write hits over the whole cache and,
     * on saturation, sends the new data to the write-back buffer "instead of
     * writing it to the hit data block", invalidating the line. The array write
     * is AVOIDED, which is the entire point of the mechanism; performing the
     * write and then invalidating - as an earlier version did - removes the
     * benefit and charges the cost. The LRU age is deliberately left alone so
     * the line is not immediately refilled with the same data. */
    if (policy == policy_t::I2WAP && is_nvm(way)) {
      if (++polf_ctr >= polf_threshold) {
        polf_ctr = 0;
        polf_flushes++;
        invalidate(set, way);
        return;                                   // write suppressed, not charged
      }
    }

    switch (policy) {
    case policy_t::ELBA:
      elba_write_hit(set, way, full_addr.to<uint64_t>());
      break;

    case policy_t::SEQUOIA:
      // Sequoia's only trigger, and the one the replay could not deliver.
      seq_write_hit(set, way);
      break;

    /* Algorithm 1 lines 15-20.  In the replay this branch was nearly dead (M3);
     * here it is the majority of the write traffic, which is also what keeps
     * DWWR's Ci counters non-degenerate. */
    case policy_t::SWWR:
    case policy_t::DWWR:
      wwr_write_hit(set, way);
      break;

    /* EqualChance on the write-hit path.  Algorithm 1's redirection target is
     * an invalid frame first, else a clean frame, else a normal write.  The
     * flag is consumed whether or not a target was found, exactly as the
     * Python `choose_way` consumes it. */
    case policy_t::EQUALCHANCE: {
      long target = way;
      if (ec_flag[static_cast<std::size_t>(set)] && is_nvm(way)) {
        ec_flag[static_cast<std::size_t>(set)] = 0;
        const champsim::cache_block* b = set_blocks(set);
        long t = ec_shift_target(set, way);
        if (t >= 0) {
          if (b != nullptr && !b[t].valid) ec_i_shifts++;
          else ec_c_shifts++;
          displaced_to[idx(set, way)] = static_cast<int8_t>(t);
          displacements++;
          ec_hit_shifts++;
          target = t;
        } else {
          ec_no_candidate++;
        }
      }
      // a shifted write is the same single write at a different cell, so it is
      // not counted as write amplification
      if (target != way) charge(set, target, false);
      else charge_hit(set, way);
      ec_note_write(set, target);
      break;
    }

    default:
      charge_hit(set, way);
      break;
    }
  }
}

void psc_family::replacement_final_stats()
{
  // NVM frames only: SRAM ways are treated as unlimited endurance.
  std::vector<double> w;
  std::vector<double> z;
  // WEAR_SETS == NUM_SET everywhere except Start-Gap, which owns one extra
  // physical set (the GapLine) that must appear in the distribution.
  w.reserve(static_cast<std::size_t>(WEAR_SETS * NVM_WAYS));
  for (long s = 0; s < WEAR_SETS; ++s)
    for (long j = 0; j < NVM_WAYS; ++j) {
      w.push_back(static_cast<double>(writes[idx(s, j)]));
      z.push_back(damage[idx(s, j)]);
    }
  if (w.empty()) return;

  double total = std::accumulate(w.begin(), w.end(), 0.0);
  double mu = total / static_cast<double>(w.size());
  double mx = *std::max_element(w.begin(), w.end());
  double zmax = *std::max_element(z.begin(), z.end());

  // InterV and IntraV are reported separately, never summed: they are
  // coefficients of variation at different nesting levels and their sum has no
  // probabilistic meaning (law of total variance combines variances, not CVs).
  double interv = 0.0, intrav = 0.0;
  if (mu > 0) {
    std::vector<double> setmean(static_cast<std::size_t>(WEAR_SETS), 0.0);
    for (long s = 0; s < WEAR_SETS; ++s) {
      double sm = 0.0;
      for (long j = 0; j < NVM_WAYS; ++j) sm += static_cast<double>(writes[idx(s, j)]);
      setmean[static_cast<std::size_t>(s)] = sm / static_cast<double>(NVM_WAYS);
    }
    double m2 = 0.0;
    for (double v : setmean) m2 += (v - mu) * (v - mu);
    interv = std::sqrt(m2 / static_cast<double>(WEAR_SETS - 1)) / mu;

    /* Intra-set variation is undefined for a single NVM way: the sample
     * standard deviation over one observation has no denominator.  Report 0
     * rather than a NaN, which would silently poison the results CSV. */
    if (NVM_WAYS > 1) {
      double acc = 0.0;
      for (long s = 0; s < WEAR_SETS; ++s) {
        double sm = setmean[static_cast<std::size_t>(s)], v2 = 0.0;
        for (long j = 0; j < NVM_WAYS; ++j) {
          double d = static_cast<double>(writes[idx(s, j)]) - sm;
          v2 += d * d;
        }
        acc += std::sqrt(v2 / static_cast<double>(NVM_WAYS - 1));
      }
      intrav = acc / (static_cast<double>(WEAR_SETS) * mu);
    }
  }

  // CVaR of normalised damage: the mean of the worst (1-alpha) tail.
  auto cvar = [&](double a) {
    std::vector<double> t = z;
    std::sort(t.begin(), t.end());
    auto k = static_cast<std::size_t>(std::ceil(a * static_cast<double>(t.size())));
    if (k >= t.size()) k = t.size() - 1;
    double s = 0.0;
    std::size_t n = 0;
    for (std::size_t i = k; i < t.size(); ++i) { s += t[i]; ++n; }
    return n ? s / static_cast<double>(n) : 0.0;
  };
  std::vector<double> sorted = w;
  std::sort(sorted.begin(), sorted.end());
  auto pct = [&](double p) { return sorted[static_cast<std::size_t>(p * static_cast<double>(sorted.size() - 1))]; };

  std::cout << "\n=== psc_family (" << policy_name << ") NVM wear ===\n"
            << "nvm_frames " << w.size() << "\n"
            << "nvm_writes " << nvm_writes << "\n"
            << "sram_writes " << sram_writes << "\n"
            << "induced_writes " << induced_writes << "\n"
            << "mu " << mu << "\n"
            << "max_writes " << mx << "\n"
            << "p99_writes " << pct(0.99) << "\n"
            << "p999_writes " << pct(0.999) << "\n"
            << "interV " << interv << "\n"
            << "intraV " << intrav << "\n"
            << "max_damage " << zmax << "\n"
            << "cvar99 " << cvar(0.99) << "\n"
            << "cvar999 " << cvar(0.999) << "\n"
            << "steered " << steered << "\n"
            << "allocations " << allocations << "\n"
            << "vro_rotations " << vro_rotations << "\n"
            << "rank_interventions " << rank_interventions << "\n"
            << "ayush_migrations " << ayush_migrations << "\n"
            << "dwawr_redirects " << dwawr_redirects << "\n"
            << "polf_flushes " << polf_flushes << "\n"
            << "ss_swaps " << ss_swaps << "\n"
            << "tg_actions " << tg_actions << "\n"
            << "pb_diversions " << pb_diversions << "\n"
            << "pb_cold_retentions " << pb_cold_retentions << "\n"
            << "pb_blocked_cap " << pb_blocked_cap << "\n"
            << "pb_blocked_floor " << pb_blocked_floor << "\n"
            << "displacements " << displacements << "\n"
            << "remapped_writes " << remapped_writes << "\n"
            // ELBA
            << "elba_promotions " << elba_promotions << "\n"
            << "elba_frames_released " << elba_frames_released << "\n"
            << "elba_absorbed " << elba_absorbed << "\n"
            << "elba_buf_hits " << elba_buf_hits << "\n"
            << "elba_buf_evictions " << elba_buf_evictions << "\n"
            << "elba_buf_writebacks " << elba_buf_writebacks << "\n"
            << "elba_early_evictions " << elba_early_evictions << "\n"
            << "elba_boundary_changes " << elba_boundary_changes << "\n"
            << "elba_cap " << elba_cap << "\n"
            // Sequoia
            << "seq_write_hits " << seq_write_hits << "\n"
            << "seq_write_hits_nvm " << seq_write_hits_nvm << "\n"
            << "seq_write_hits_sram " << seq_write_hits_sram << "\n"
            << "seq_ssc_saturations " << seq_ssc_saturations << "\n"
            << "seq_hsc_resets " << seq_hsc_resets << "\n"
            << "seq_associations " << seq_associations << "\n"
            << "seq_skipped_associated " << seq_skipped_associated << "\n"
            << "seq_no_cold_set " << seq_no_cold_set << "\n"
            << "seq_completions " << seq_completions << "\n"
            << "seq_aborts " << seq_aborts << "\n"
            << "seq_abort_invalidations " << seq_abort_invalidations << "\n"
            << "seq_abort_writebacks " << seq_abort_writebacks << "\n"
            << "seq_line_moves " << seq_line_moves << "\n"
            << "seq_second_searches_elided " << seq_second_searches_elided << "\n"
            << "seq_wad_arms " << seq_wad_arms << "\n"
            << "seq_wad_displacements " << seq_wad_displacements << "\n"
            << "seq_wad_no_victim " << seq_wad_no_victim << "\n"
            << "seq_wad_victim_evictions " << seq_wad_victim_evictions << "\n"
            << "seq_wad_writebacks " << seq_wad_writebacks << "\n"
            // Start-Gap
            << "sg_movements " << sg_movements << "\n"
            << "sg_rotations " << sg_rotations << "\n"
            << "sg_frames_moved " << sg_frames_moved << "\n"
            // EqualChance
            << "ec_i_shifts " << ec_i_shifts << "\n"
            << "ec_c_shifts " << ec_c_shifts << "\n"
            << "ec_hit_shifts " << ec_hit_shifts << "\n"
            << "ec_no_candidate " << ec_no_candidate << "\n"
            /* SWWR / DWWR.  Read `wwr_intervals` FIRST: at the thesis default
             * of I = 2,000,000 cycles a short run crosses no boundary at all,
             * the cache stays unrestricted, and the row is LRU rather than a
             * measurement of SWWR. */
            << "wwr_intervals " << wwr_intervals << "\n"
            << "wwr_clock_ticks " << wwr_clock_ticks << "\n"
            << "wwr_cycles " << now_cycles() << "\n"
            << "wwr_wrw " << wwr_wrw << "\n"
            << "wwr_degenerate " << wwr_degenerate << "\n"
            << "wwr_counter_saturations " << wwr_counter_saturations << "\n"
            << "wwr_write_hits " << wwr_write_hits << "\n"
            << "wwr_write_hits_restricted " << wwr_write_hits_restricted << "\n"
            << "wwr_write_hits_unrestricted " << wwr_write_hits_unrestricted << "\n"
            << "wwr_redirects " << wwr_redirects << "\n"
            << "wwr_redirect_to_invalid " << wwr_redirect_to_invalid << "\n"
            << "wwr_redirect_evictions " << wwr_redirect_evictions << "\n"
            << "wwr_redirect_writebacks " << wwr_redirect_writebacks << "\n"
            << "wwr_redirect_to_sram " << wwr_redirect_to_sram << "\n"
            << "wwr_no_dest " << wwr_no_dest << "\n"
            << "wwr_alloc_deflected " << wwr_alloc_deflected << "\n"
            << "wwr_alloc_to_sram " << wwr_alloc_to_sram << "\n"
            << "wwr_fill_into_wrw " << wwr_fill_into_wrw << "\n"
            << "wwr_state_bits " << wwr_state_bits() << "\n"
            // WearSwap (also the inter-set channel of `full`)
            << "ws_swaps " << ws_swaps << "\n"
            << "ws_migration_writes " << ws_migration_writes << "\n"
            << "ws_no_candidate " << ws_no_candidate << "\n"
            << "=== end psc_family ===\n";

  /* Start-Gap self-check.  The failure this guards against is the one
   * startgap.py documents: a version that charged the relocation writes but
   * never wired the remap, leaving a pure write tax with no levelling benefit.
   * If movements happened and no write was ever charged to a remapped physical
   * set, the mechanism has been deleted again. */
  if (policy == policy_t::STARTGAP && sg_movements > 0 && remapped_writes == 0)
    std::cout << "psc_family: WARNING startgap performed " << sg_movements
              << " gap movements but remapped no write - the remap is not wired\n";

  /* Same guard for WearSwap, whose replay version fails exactly this test: it
   * swaps set contents without rebinding the index, so it pays the migration
   * tax and delivers no levelling.  If this fires, the port has regressed to
   * that. */
  if ((policy == policy_t::WEAR_SWAP || policy == policy_t::FULL) && ws_swaps > 0 && remapped_writes == 0)
    std::cout << "psc_family: WARNING " << policy_name << " performed " << ws_swaps
              << " set swaps but remapped no write - the remap is not wired\n";

  /* And for SWWR/DWWR: a run too short to cross one interval boundary leaves
   * the whole cache unrestricted, which is LRU.  That is the thesis's own
   * behaviour for the first interval, not a bug, but a results table that
   * silently contained it would be reporting LRU under another name. */
  if ((policy == policy_t::SWWR || policy == policy_t::DWWR) && wwr_intervals == 0)
    std::cout << "psc_family: WARNING " << policy_name << " never reached an interval boundary (I="
              << wwr_interval << " cycles, run was " << now_cycles()
              << ") - no window was ever restricted, so this row is LRU\n";

  if (!stats_path.empty()) {
    std::ofstream f(stats_path);
    f << "set,way,writes,damage,endurance\n";
    for (long s = 0; s < WEAR_SETS; ++s)
      for (long j = 0; j < NVM_WAYS; ++j)
        f << s << ',' << j << ',' << writes[idx(s, j)] << ',' << damage[idx(s, j)] << ',' << endurance[idx(s, j)] << '\n';
  }
}
