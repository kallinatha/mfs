#!/usr/bin/env python3
"""Resolve an exported trace filename to its canonical workload-mix label.

Exported HyCSim traces are named by concatenating their constituent benchmarks
in arbitrary order, e.g. `zeusmpgobmkdealIIbzip2BaseHybrid_trace.txt`. The
manuscript refers to the same workload as MIX1. Joining results to the paper by
hand is exactly the kind of step that produces a table nobody can regenerate.

A mix is matched by the *set* of benchmarks detected in the filename, so name
ordering does not matter and a renamed export still resolves. The mapping itself
lives in a JSON data file, not in this module: no benchmark name is hardcoded in
code.

Ambiguity is an error, never a guess. If a filename matches no mix, or more than
one, the caller is told and the raw stem is used.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

DEFAULT_LABELS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'config', 'mix_labels.suscom.json')


class MixLabeller:
    def __init__(self, spec_path: Optional[str] = None):
        self.path = spec_path or DEFAULT_LABELS
        self.enabled = os.path.exists(self.path)
        self.vocabulary: List[str] = []
        self.mixes: Dict[str, frozenset] = {}
        if not self.enabled:
            return
        with open(self.path) as src:
            spec = json.load(src)
        # Longest first so that e.g. `mcf17` is consumed before a bare `mcf`.
        self.vocabulary = sorted(spec.get('benchmark_vocabulary', []), key=len, reverse=True)
        self.mixes = {name: frozenset(members) for name, members in spec.get('mixes', {}).items()}

    def detect(self, filename: str) -> frozenset:
        stem = os.path.basename(filename)
        for suffix in ('.txt', '.npz'):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        stem = re.sub(r'(BaseHybrid|LHybrid|CMPHybrid|CMPLHybrid)?_?trace$', '', stem)
        found, remaining = [], stem
        for token in self.vocabulary:
            if token.lower() in remaining.lower():
                found.append(token)
                idx = remaining.lower().index(token.lower())
                remaining = remaining[:idx] + remaining[idx + len(token):]
        return frozenset(found)

    def label(self, filename: str) -> Tuple[str, str]:
        """Return (label, note). `note` is empty on a clean unique match."""
        stem = os.path.splitext(os.path.basename(filename))[0]
        if not self.enabled:
            return stem, 'no label spec at %s' % self.path
        found = self.detect(filename)
        if not found:
            return stem, 'no benchmarks recognised'
        hits = [name for name, members in self.mixes.items() if members == found]
        if len(hits) == 1:
            return hits[0], ''
        if len(hits) > 1:
            return stem, 'ambiguous: matches %s' % ', '.join(sorted(hits))
        # No exact set match: report the closest so a typo is obvious.
        best, best_score = None, -1
        for name, members in self.mixes.items():
            score = len(members & found)
            if score > best_score:
                best, best_score = name, score
        return stem, ('no exact mix match; detected {%s}, closest %s (%d/%d shared)'
                      % (', '.join(sorted(found)), best, best_score,
                         len(self.mixes.get(best, ()))))


if __name__ == '__main__':
    import sys
    lab = MixLabeller()
    for name in sys.argv[1:]:
        label, note = lab.label(name)
        print('%-58s -> %-6s %s' % (os.path.basename(name), label, note))
