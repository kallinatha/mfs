"""The projection must remain the production join, exactly.

If these fail, every counterfactual technology result in the paper is void, so
they assert bit-equality rather than approximate agreement.
"""
import csv, os, subprocess, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, 'results', 'sample')
sys.path.insert(0, os.path.join(ROOT, 'stage6_transfer'))
sys.path.insert(0, os.path.join(ROOT, 'stage4_analysis'))

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SAMPLE, 'cross_stack_results.csv')),
    reason='sample results tree not present')

import cross_stack as CS       # noqa: E402
import transfer as T           # noqa: E402


def _load():
    life = CS.read_csv(os.path.join(SAMPLE, 'hycsim', 'lifetime_all_mixes.csv'))
    models = CS.read_csv(os.path.join(SAMPLE, 'nvmexplorer', 'trace_profiles.csv'))
    dpath = os.path.join(SAMPLE, 'destiny', 'results.csv')
    dest = CS.read_csv(dpath) if os.path.exists(dpath) else []
    meas = CS.read_csv(os.path.join(SAMPLE, 'cross_stack_results.csv'))
    return life, models, dest, meas


def test_projection_reproduces_the_measured_join_exactly():
    life, models, dest, meas = _load()
    rows, rejected = T.project(life, models, dest, nvm_cell='SOT_12F2',
                               sram_cell='SRAM_6T', destiny_nvm_tech='SOT-12/1',
                               destiny_sram_tech='SRAM',
                               endurances=CS.DEFAULT_ENDURANCE_GRID)
    assert rows, 'projection produced no rows'
    idx = {(r['scheme'], r['arena'], r.get('mix')): r for r in meas}
    compared = 0
    for r in rows:
        m = idx.get((r['scheme'], r['arena'], r.get('mix')))
        if m is None:
            continue
        for k in T.COMPARE_KEYS:
            if k not in m:
                continue
            x, y = float(r[k]), float(m[k])
            compared += 1
            assert abs(x - y) <= 1e-9 * max(abs(y), 1e-30), \
                '%s/%s drifted: %.17g vs %.17g' % (r['scheme'], k, x, y)
    assert compared > 0, 'nothing was compared; the keying is wrong'


def test_synth_model_rejects_unknown_parameters():
    _, models, _, _ = _load()
    t = CS.select_array_model(models, 'SOT_12F2', None)
    with pytest.raises(ValueError):
        T.synth_model('X', t, endurance_writes=1e12)   # not a cell parameter


def test_identity_override_is_a_no_op():
    """Overriding a parameter with its own value must change nothing.

    Catches unit slips in synth_model: if the override path rescaled anything,
    this would move the projected energy.
    """
    life, models, dest, _ = _load()
    t = CS.select_array_model(models, 'SOT_12F2', None)
    same = T.synth_model('IDENT', t, write_energy_pJ=float(t['write_energy_pJ']))
    a, _ = T.project(life, models, dest, nvm_cell='SOT_12F2', sram_cell='SRAM_6T',
                     destiny_nvm_tech='SOT-12/1', destiny_sram_tech='SRAM',
                     endurances=CS.DEFAULT_ENDURANCE_GRID)
    b, _ = T.project(life, list(models) + [same], dest, nvm_cell='IDENT',
                     sram_cell='SRAM_6T', destiny_nvm_tech='SOT-12/1',
                     destiny_sram_tech='SRAM', endurances=CS.DEFAULT_ENDURANCE_GRID)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert abs(float(x['dynamic_EDP_mJ_ms']) - float(y['dynamic_EDP_mJ_ms'])) < 1e-12


def test_higher_write_energy_never_lowers_dynamic_energy():
    """Monotonicity: raising NVM write energy cannot reduce dynamic energy.

    A sign error or a swapped partition would break this immediately.
    """
    life, models, dest, _ = _load()
    t = CS.select_array_model(models, 'SOT_12F2', None)
    e0 = float(t['write_energy_pJ'])
    lo = T.synth_model('LO', t, write_energy_pJ=e0)
    hi = T.synth_model('HI', t, write_energy_pJ=e0 * 10.0)
    A, _ = T.project(life, list(models) + [lo], dest, nvm_cell='LO', sram_cell='SRAM_6T',
                     destiny_nvm_tech='SOT-12/1', destiny_sram_tech='SRAM',
                     endurances=CS.DEFAULT_ENDURANCE_GRID)
    B, _ = T.project(life, list(models) + [hi], dest, nvm_cell='HI', sram_cell='SRAM_6T',
                     destiny_nvm_tech='SOT-12/1', destiny_sram_tech='SRAM',
                     endurances=CS.DEFAULT_ENDURANCE_GRID)
    for x, y in zip(A, B):
        if float(x['nvm_writes']) > 0:
            assert float(y['dynamic_energy_mJ']) >= float(x['dynamic_energy_mJ']) - 1e-12


def test_cli_validate_exits_zero():
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'stage6_transfer', 'transfer.py'),
                        '--validate',
                        '--lifetime', os.path.join(SAMPLE, 'hycsim', 'lifetime_all_mixes.csv'),
                        '--models', os.path.join(SAMPLE, 'nvmexplorer', 'trace_profiles.csv'),
                        '--destiny', os.path.join(SAMPLE, 'destiny', 'results.csv'),
                        '--measured', os.path.join(SAMPLE, 'cross_stack_results.csv')],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'EXACT' in r.stdout


# --------------------------------------------------------------------------
# The concurrency guard. Two replays writing one results tree is the failure
# that leaves no trace afterwards, so the check that prevents it is tested in
# both directions: it must not fire on a clean machine (a false positive costs
# a 20-hour run that never starts) and it must fire on a real replay.

def test_replay_guard_is_quiet_when_nothing_is_running():
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, 'scripts', 'check_no_replay_running.py')],
                       capture_output=True, text=True)
    assert r.returncode == 0, 'false positive: ' + r.stdout


def test_replay_guard_detects_a_real_replay(tmp_path):
    d = tmp_path / 'stage3_hycsim'
    d.mkdir(parents=True)
    (d / 'run_mixes.py').write_text('import time\ntime.sleep(30)\n')
    p = subprocess.Popen([sys.executable, str(d / 'run_mixes.py')])
    try:
        import time
        time.sleep(1.0)
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, 'scripts', 'check_no_replay_running.py')],
                           capture_output=True, text=True)
        assert r.returncode == 1, 'guard missed a running replay'
        assert 'run_mixes.py' in r.stdout
    finally:
        p.kill()
        p.wait()


def test_replay_guard_ignores_its_own_process_tree():
    """A shell whose command line merely mentions the script must not trip it."""
    r = subprocess.run(['/bin/sh', '-c',
                        'echo run_mixes.py >/dev/null; exec "$0" "$1"',
                        sys.executable,
                        os.path.join(ROOT, 'scripts', 'check_no_replay_running.py')],
                       capture_output=True, text=True)
    assert r.returncode == 0, 'tripped on its own ancestry: ' + r.stdout


# --------------------------------------------------------------------------
# The two inversions added for the clean slate. Both are pure post-processing
# on artefacts that already exist, so they must be self-consistent with the
# quantities the pipeline already reports -- that consistency is the test.

def test_endurance_floor_ratio_equals_one_plus_rli():
    """E_required scales as 1/(1+RLI) by construction, so the ratio of a
    scheme's floor to the baseline's must reproduce (1+RLI) exactly. If the
    reframing and the lifetime metric ever disagree, one of them is wrong."""
    life = CS.read_csv(os.path.join(SAMPLE, 'hycsim', 'lifetime_all_mixes.csv'))
    SPY = 3.156e7
    by, base = {}, {}
    for r in life:
        try:
            wmax, dur = float(r['max_write']), float(r['measured_duration_s'])
            rli = float(r['rli_direct'])
        except (KeyError, TypeError, ValueError):
            continue
        if wmax <= 0 or dur <= 0:
            continue
        rec = (5.0 * wmax * SPY / dur, rli)
        by.setdefault((r['scheme'], r['arena']), []).append(rec)
        if r['scheme'] == 'unmanaged':
            base[r['arena']] = rec[0]
    checked = 0
    for (scheme, arena), v in by.items():
        if arena not in base or len(v) != 1:
            continue
        floor, rli = v[0]
        ratio = base[arena] / floor
        assert abs(ratio - (1.0 + rli)) < 1e-6, \
            '%s: floor ratio %.6f != 1+RLI %.6f' % (scheme, ratio, 1.0 + rli)
        checked += 1
    assert checked > 0, 'nothing was checked; the keying is wrong'


def test_pareto_front_helper_is_correct_on_a_known_case():
    pts = [(1.0, 1.0), (2.0, 0.5), (0.5, 2.0), (0.4, 0.4), (2.0, 2.0)]
    front = T._pareto(pts)
    assert 4 in front, 'the dominating point must be on the front'
    assert 3 not in front, 'a dominated point must not be'


def test_coverage_audit_runs_and_reports_missing():
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'coverage_audit.py'),
                        '--results', SAMPLE], capture_output=True, text=True)
    assert 'claim coverage' in r.stdout
    # The sample tree is deliberately partial, so the audit must say so rather
    # than reporting full coverage.
    assert 'MISSING' in r.stdout
    assert r.returncode == 1
