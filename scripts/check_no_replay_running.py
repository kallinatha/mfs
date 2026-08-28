#!/usr/bin/env python3
"""Refuse to start when another replay is already writing the results tree.

Two concurrent `run_mixes.py` processes are the hardest failure in this
pipeline to detect after the fact: both write complete, internally consistent
CSVs, and whichever finishes last wins.  Nothing downstream can tell, and the
provenance record silently attests a tree that had a second writer.

`pgrep -f` is the obvious check and it is wrong here: it matches whole command
lines, so it also matches the shell that is running the check, any editor with
the filename open, and any pipeline whose text happens to contain the pattern.
During development it fired on its own invocation.  Scanning /proc directly
makes the predicate explicit: a *python* process, whose argv contains the
replay script, which is neither this process nor one of its ancestors.

Exit 0 when the coast is clear, 1 otherwise (printing the offending pids).
"""
import os
import sys

TARGET = 'run_mixes.py'


def ancestors(pid):
    """PIDs from `pid` up to init, so the caller's own tree never self-matches."""
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        try:
            with open('/proc/%d/stat' % pid) as f:
                # comm may contain spaces and parentheses; ppid is the field
                # after the closing paren, so split there rather than on space.
                pid = int(f.read().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return seen


def offenders():
    mine = ancestors(os.getpid())
    out = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in mine:
            continue
        try:
            with open('/proc/%d/cmdline' % pid, 'rb') as f:
                argv = [a.decode('utf-8', 'replace') for a in f.read().split(b'\0') if a]
        except OSError:
            continue
        if not argv:
            continue
        exe = os.path.basename(argv[0])
        if not exe.startswith('python'):
            continue
        if any(TARGET in a for a in argv[1:]):
            out.append((pid, ' '.join(argv)[:160]))
    return out


def main():
    bad = offenders()
    for pid, cmd in bad:
        print('  pid %d: %s' % (pid, cmd))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
