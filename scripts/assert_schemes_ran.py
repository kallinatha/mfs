#!/usr/bin/env python3
"""Every scheme the config asks for must appear in the results.

A scheme can be implemented, registered, unit-tested and still never run,
because the only thing that decides is the config's `experiment.schemes` list.
That happened to six schemes in this study and was invisible for weeks: the
results looked complete, they were simply missing rows nobody counted.
"""
import csv, json, sys

def main():
    results, config = sys.argv[1], sys.argv[2]
    rows = list(csv.DictReader(open(results + '/hycsim/lifetime_all_mixes.csv')))
    cfg = json.load(open(config))
    ran = {r['scheme'] for r in rows}
    want = set(cfg['experiment']['schemes'])
    missing = sorted(want - ran)
    if missing:
        print('configured but absent from the results: %s' % missing)
        return 1
    print('all %d schemes present across %d rows, %d mixes'
          % (len(ran), len(rows), len({r['mix'] for r in rows})))
    return 0

if __name__ == '__main__':
    sys.exit(main())
