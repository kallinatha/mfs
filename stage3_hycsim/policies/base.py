#!/usr/bin/env python3
class Policy(object):
    name = 'policy'
    does_remap = False
    PARAMS = {}

    def __init__(self, **kw):
        self.params = dict(self.PARAMS)
        self.params.update(kw)
        self.llc = None

    def attach(self, llc):
        self.llc = llc
        self.setup()

    def setup(self):
        pass

    def choose_way(self, cs, addr, is_dirty):
        return self.llc.lru(cs, range(self.llc.ways))

    def on_write(self, cs, way):
        pass

    def remap_set(self, cs):
        return cs

    def describe(self):
        return '%s(%s)' % (self.name, ','.join('%s=%s' % kv for kv in sorted(self.params.items())))


class Unmanaged(Policy):
    """All-NVM LLC, plain LRU. Baseline configuration A0."""
    name = 'unmanaged'


class LRUHybrid(Policy):
    """Hybrid SRAM/NVM array with technology-blind LRU (HyCSim BaseHybrid).

    Included to separate the effect of *having* SRAM ways from the effect of
    *steering writes into them*, which is what PSC actually does.
    """
    name = 'lru-hybrid'
