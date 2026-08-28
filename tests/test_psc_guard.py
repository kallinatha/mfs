"""Plan section 1.6 conformance tests for PSC-Guard (per-set clean reservation).

Model note: LLC.allocate() is reached only from the o==2 (L2_Rpl) branch of
LLC.run and only on a miss, so every stream that must exercise placement is
built from L2_Rpl records.  A stream of GetS/GetX records never allocates and
would silently test nothing -- an earlier version of this file made exactly
that mistake and passed vacuously.
"""
import os,sys,numpy as np,pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'stage3_hycsim'))
from llc import LLC
import policies
SZ=1<<20
def build(scheme,**kw):
    p=policies.REGISTRY[scheme](**kw); return LLC(SZ,12,4,policy=p),p
def run(scheme,st,**kw):
    l,p=build(scheme,**kw); l.run(*st); return l,p
def repl(n,dirty_frac,seed=7,base=0):
    """L2_Rpl stream: op==2 everywhere, so every miss allocates."""
    r=np.random.default_rng(seed)
    op=np.full(n,2,dtype=np.int64)
    addr=(r.integers(0,1<<15,n)*64+base).astype(np.int64)
    dirty=(r.random(n)<dirty_frac).astype(np.int64)
    return op,addr,dirty
def dirty_per_set(llc):
    NW,W=llc.nvm_ways,llc.ways
    return [sum(1 for w in range(NW,W) if llc.tag[s][w]!=-1 and llc.dirty[s][w])
            for s in range(llc.sets)]

def test_1_ablation_reserve0_identical_to_psc():
    st=repl(60000,0.7)
    a,_=run('psc',st); b,pb=run('psc-guard',st,theta=0.75,reserve=0)
    assert np.array_equal(np.array(a.writes),np.array(b.writes)),'wear vectors differ'
    assert (a.hits,a.misses,a.nvm_writes,a.sram_writes)==(b.hits,b.misses,b.nvm_writes,b.sram_writes)
    assert pb.pg_rejected==0

def test_2_reservation_invariant_holds_everywhere():
    """d(s) <= d_max for EVERY set, under a 100%-dirty fill stream."""
    st=repl(120000,1.0,seed=3)
    llc,p=run('psc-guard',st,theta=0.75,reserve=1)
    d=dirty_per_set(llc)
    assert max(d)<=p.d_max, 'set with d=%d exceeds d_max=%d'%(max(d),p.d_max)
    assert p.d_max==3 and llc.sram_ways==4
    assert p.pg_rejected>0,'reservation never bound on an all-dirty stream'

def test_3_plain_psc_actually_saturates():
    """The failure this policy fixes must be reproducible, or the fix is moot."""
    st=repl(120000,1.0,seed=3)
    llc,_=run('psc',st)
    d=dirty_per_set(llc)
    full=sum(1 for x in d if x==llc.sram_ways)
    assert full>0.9*llc.sets,'plain PSC did not saturate (%d/%d sets full)'%(full,llc.sets)

def test_4_clean_blocks_get_a_home():
    st=repl(120000,0.85,seed=5)
    a,_=run('psc',st); b,pb=run('psc-guard',st,theta=0.75,reserve=1)
    assert pb.pg_drained>0,'no clean fill ever reached SRAM'
    assert b.hits>=a.hits,'guard reduced hits: %d < %d'%(b.hits,a.hits)

def test_5_determinism():
    st=repl(60000,0.7,seed=11)
    a,pa=run('psc-guard',st,theta=0.5,reserve=1); b,pb=run('psc-guard',st,theta=0.5,reserve=1)
    assert np.array_equal(np.array(a.writes),np.array(b.writes))
    assert (pa.pg_admitted,pa.pg_rejected,pa.pg_drained)==(pb.pg_admitted,pb.pg_rejected,pb.pg_drained)

def test_6_write_accounting_and_zero_migration():
    st=repl(60000,0.7,seed=3)
    llc,p=run('psc-guard',st,theta=0.25,reserve=1)
    assert int(np.array(llc.writes).sum())==llc.nvm_writes+llc.sram_writes
    assert llc.migration_writes==0
    assert p.state_bits()==0

def test_7_monotone_in_theta():
    """Lower theta => tighter reservation => at least as many diversions."""
    st=repl(80000,1.0,seed=9)
    r=[run('psc-guard',st,theta=t,reserve=1)[1].pg_rejected for t in (0.25,0.5,0.75)]
    assert r[0]>=r[1]>=r[2],'diversions not monotone in theta: %s'%r

def test_8_nvm_wear_not_worse_on_a_realistic_mix():
    """On a realistic fill mix the diverted dirty traffic must not simply move
    the peak into NVM.  27% of fills are clean in the measured traces (plain
    PSC's NVM write share is 0.27 against lru-hybrid's 0.76), so 0.73 is the
    operating point this assertion is about."""
    st=repl(120000,0.73,seed=3)
    g,_=run('psc-guard',st,theta=0.75,reserve=1); l,_=run('lru-hybrid',st)
    gw=np.array(g.writes)[:, :12].max(); lw=np.array(l.writes)[:, :12].max()
    assert gw<=lw*1.05,'guard peak NVM wear %d vs lru-hybrid %d'%(gw,lw)


def test_9_all_dirty_boundary_is_documented_not_hidden():
    """Boundary condition, asserted so it cannot silently change.

    With a 100%-dirty fill stream there are no clean blocks to protect, so
    reserving a way for them can only shrink the dirty-absorbing partition
    from K to d_max.  PSC-Guard is then strictly worse than plain PSC for
    wear, and the paper must say so rather than quoting only the mixed-stream
    result.  The reservation is a hit-rate insurance premium; on a workload
    with nothing to insure, the premium is pure cost."""
    st=repl(120000,1.0,seed=3)
    g,_=run('psc-guard',st,theta=0.75,reserve=1); p,_=run('psc',st)
    gw=np.array(g.writes)[:, :12].max(); pw=np.array(p.writes)[:, :12].max()
    assert gw>=pw, 'boundary changed: guard %d, psc %d'%(gw,pw)
