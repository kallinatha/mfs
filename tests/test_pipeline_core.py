import json
import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'stage3_hycsim'))
from prep_trace import convert
from llc import LLC
import policies


class PipelineCoreTest(unittest.TestCase):
    def test_bounded_trace_conversion_and_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, 'mix.txt')
            out = os.path.join(tmp, 'prepared')
            with open(raw, 'w') as dst:
                dst.write('1 0 GetS 40\n')
                dst.write('WARMUP COMPLETED\n')
                dst.write('10 0 GetS 80\n')
                dst.write('12 1 GetX c0\n')
                dst.write('14 0 L2_Rpl 100 64 1 0\n')
                dst.write('bad 0 L2_Rpl nope\n')
            profile = convert(raw, out, clock_hz=2.0)
            self.assertEqual(profile['records'], 3)
            self.assertEqual(profile['counts'], {'GetS': 1, 'GetX': 1, 'L2_Rpl': 1})
            self.assertEqual(profile['duration_s'], 2.0)
            self.assertEqual(np.load(os.path.join(out, 'cycle.npy')).tolist(), [10, 12, 14])
            with open(os.path.join(out, 'profile.json')) as src:
                self.assertEqual(json.load(src)['source_region_sha256'],
                                 profile['source_region_sha256'])

    def test_llc_reports_tier_physical_events(self):
        op = np.array([2, 0, 2, 0], dtype=np.uint8)
        addr = np.array([0x1000, 0x1000, 0x2000, 0x2000], dtype=np.uint64)
        dirty = np.array([0, 0, 1, 0], dtype=np.uint8)
        llc = LLC(1 << 20, 12, 4, policy=policies.PSC())
        llc.run(op, addr, dirty)
        stats = llc.stats()
        self.assertEqual(stats['nvm_writes'], 1)
        self.assertEqual(stats['sram_writes'], 1)
        self.assertEqual(stats['nvm_reads'], 1)
        self.assertEqual(stats['sram_reads'], 1)


if __name__ == '__main__':
    unittest.main()
