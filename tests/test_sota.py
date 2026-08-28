"""The SOTA comparison is the part a reviewer is most likely to re-run, so its
statistics must be correct without scipy and its fairness audit must not be
able to silently report parity when there is none.
"""
import math, os, subprocess, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, 'results', 'sample')
sys.path.insert(0, os.path.join(ROOT, 'stage4_analysis'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import sota  # noqa: E402


def test_exact_wilcoxon_matches_scipy():
    """The no-scipy fallback must agree with scipy, or a machine without scipy
    silently gets different p-values from the ones in the paper."""
    scipy_stats = pytest.importorskip('scipy.stats')
    cases = [
        [0.5, 0.3, 0.9, 0.1, 0.7, 0.2],
        [-0.4, 0.8, -0.2, 0.6, 0.3, -0.1, 0.9],
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [0.1, -0.2, 0.3, -0.4, 0.5, -0.6, 0.7, -0.8],
    ]
    for d in cases:
        ref = float(scipy_stats.wilcoxon(d).pvalue)
        # force the fallback by hiding scipy from sota.wilcoxon
        real = sys.modules.get('scipy.stats')
        try:
            sys.modules['scipy.stats'] = None
            got, n = sota.wilcoxon(d)
        finally:
            if real is not None:
                sys.modules['scipy.stats'] = real
            else:
                sys.modules.pop('scipy.stats', None)
        assert n == len(d)
        assert abs(got - ref) < 0.02, \
            'exact fallback %.5f vs scipy %.5f on %s' % (got, ref, d)


def test_wilcoxon_refuses_tiny_samples():
    p, n = sota.wilcoxon([0.1, 0.2, 0.3])
    assert p != p, 'must return NaN rather than a p-value from 3 pairs'


def test_wilcoxon_ignores_zero_differences():
    a, _ = sota.wilcoxon([0.5, 0.0, 0.3, 0.0, 0.9, 0.1, 0.7])
    b, _ = sota.wilcoxon([0.5, 0.3, 0.9, 0.1, 0.7])
    assert abs(a - b) < 1e-12


def test_holm_is_monotone_and_stops_at_first_failure():
    """Holm rejects in ascending p order and stops permanently at the first
    non-rejection; a version that kept going would inflate the false positives
    the correction exists to control."""
    out = sota.holm([('a', 0.001), ('b', 0.02), ('c', 0.30), ('d', 0.004)])
    names = [n for n, _, _, _ in out]
    assert names == ['a', 'd', 'b', 'c'], 'not sorted by p'
    rejected = [ok for _, _, _, ok in out]
    assert rejected[0] is True
    # once a comparison fails, no later one may be rejected
    first_false = rejected.index(False) if False in rejected else len(rejected)
    assert not any(rejected[first_false:])


def test_published_table_has_a_citation_for_every_entry():
    for k, v in sota.PUBLISHED.items():
        assert v and any(ch.isdigit() for ch in v), \
            '%s has no year in its citation: %r' % (k, v)


def test_narrative_decision_runs_on_the_sample_tree():
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, 'scripts', 'decide_narrative.py'),
                        '--results', SAMPLE], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'NARRATIVE:' in r.stdout
    # a partial tree must still yield a verdict rather than crashing
    assert any(x in r.stdout for x in ('A - measurement', 'B - mechanism',
                                       'C - cross-stack'))
