#!/usr/bin/env python3
"""Turn published device parameters into validated DESTINY `.cell` files.

Why this exists. Hand-written cell files are the weakest link in every NVM
architecture paper, including this one: a reviewer cannot check a number that
appears only as a literal in a config file with no citation attached. The three
SOT cells this project started with (12 F^2, 24 F^2) were hand-set geometries
carrying DESTINY's sample electricals, and there was nothing in the file to say
so. This module makes the provenance structural instead of optional.

    sources.json    one record per published parameter set. Every field carries
                    a `provenance` string and a `kind` of reported / derived /
                    assumed. Data, not code.
    from_paper.py   validates a record against DESTINY's grammar and against
                    physics, then emits a .cell whose header states, per line,
                    where the number came from.

A generated cell that contains an `assumed` value says so in its header, next to
the value, so the assumption travels with the artefact into anyone's review.

Usage
-----
    python3 from_paper.py --list
    python3 from_paper.py --emit sot_wu2020_2t1sot_45nm -o generated/
    python3 from_paper.py --emit-all -o generated/
    python3 from_paper.py --emit sot_wu2020_2t1sot_45nm --mlc 2 -o generated/
    python3 from_paper.py --check            # validate every record, emit nothing
    python3 from_paper.py --compare CellArea # cross-paper spread on one field

MLC
---
`--mlc N` synthesises an N-level MRAM cell from an SLC record. DESTINY models
MLC MRAM as a *hard* and a *soft* transition (`-SoftSetCurrent` and friends):
the two stacked free layers switch at different currents, and the soft layer is
the one written for the low-order bit. Almost no device paper publishes both, so
the soft transition is derived from the hard one by an explicit, exposed ratio
and marked `assumed`. That is a modelling choice, not a measurement, and the
generated header says so in those words. Do not report an MLC number without
a sensitivity sweep over `--soft-ratio`; the default is a placeholder.

For MLC PCM/RRAM, DESTINY instead uses `-AverageIterations` (write-verify
loops). Set it from the paper if the paper has it; there is no defensible way to
guess it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, 'sources.json')

# ---------------------------------------------------------------- grammar
# Exactly the keys MemCell.cpp parses. Anything not in here is silently ignored
# by DESTINY, which is the failure mode this table exists to prevent: a
# misspelled key does not error, it just does nothing.
VALID_KEYS = {
    'MemCellType', 'MemCellLevel', 'ProcessNode', 'Temperature (K)',
    'CellArea (F^2)', 'CellAspectRatio', 'Stitching',
    'ResistanceOn (ohm)', 'ResistanceOff (ohm)',
    'ResistanceOnAtSetVoltage (ohm)', 'ResistanceOffAtSetVoltage (ohm)',
    'ResistanceOnAtResetVoltage (ohm)', 'ResistanceOffAtResetVoltage (ohm)',
    'ResistanceOnAtReadVoltage (ohm)', 'ResistanceOffAtReadVoltage (ohm)',
    'ResistanceOnAtHalfReadVoltage (ohm)', 'ResistanceOffAtHalfReadVoltage (ohm)',
    'ResistanceOnAtHalfResetVoltage (ohm)',
    'CapacitanceOn (F)', 'CapacitanceOff (F)',
    'ReadMode', 'ReadCurrent (uA)', 'ReadVoltage (V)', 'ReadPower (uW)',
    'ReadPulse (ns)', 'ReadFloating', 'MinSenseVoltage (mV)',
    'ResetMode', 'ResetCurrent (uA)', 'ResetVoltage (V)', 'ResetPulse (ns)',
    'ResetEnergy (pJ)',
    'SetMode', 'SetCurrent (uA)', 'SetVoltage (V)', 'SetPulse (ns)',
    'SetEnergy (pJ)',
    'SoftResetCurrent (uA)', 'SoftResetVoltage (V)', 'SoftResetPulse (ns)',
    'SoftResetEnergy (pJ)', 'ResetVoltageSoft (V)',
    'SoftSetCurrent (uA)', 'SoftSetVoltage (V)', 'SoftSetPulse (ns)',
    'SoftSetEnergy (pJ)',
    'AverageIterations', 'Interval (ns)',
    'AccessType', 'AccessCMOSWidth (F)', 'VoltageDropAccessDevice (V)',
    'LeakageCurrentAccessDevice (uA)',
    'SRAMCellNMOSWidth (F)', 'SRAMCellPMOSWidth (F)',
    'SOIDeviceWidth (F)', 'GateCouplingRatio', 'GateOxThicknessFactor',
    'RetentionTime (us)', 'DRAMCellCapacitance (F)',
    'FlashEraseTime (ms)', 'FlashProgramTime (us)', 'FlashEraseVoltage (V)',
    'FlashProgramVoltage (V)', 'FlashPassVoltage (V)', 'WordlineBoostRatio',
    'ShiftCurrent (uA)', 'ShiftPulse (ns)', 'ShiftEnergy (pJ)',
    'TapeLength (bit)', 'PortDistance (bit)', 'TapePerGroup',
}

# Order matters only for readability of the emitted file.
EMIT_ORDER = [
    'MemCellType', 'MemCellLevel', 'ProcessNode', 'Temperature (K)',
    'CellArea (F^2)', 'CellAspectRatio',
    'ResistanceOn (ohm)', 'ResistanceOff (ohm)',
    'ReadMode', 'ReadCurrent (uA)', 'ReadVoltage (V)', 'ReadPower (uW)',
    'ReadPulse (ns)', 'MinSenseVoltage (mV)',
    'ResetMode', 'ResetCurrent (uA)', 'ResetVoltage (V)', 'ResetPulse (ns)',
    'ResetEnergy (pJ)',
    'SetMode', 'SetCurrent (uA)', 'SetVoltage (V)', 'SetPulse (ns)',
    'SetEnergy (pJ)',
    'SoftResetCurrent (uA)', 'SoftResetVoltage (V)', 'SoftResetPulse (ns)',
    'SoftResetEnergy (pJ)',
    'SoftSetCurrent (uA)', 'SoftSetVoltage (V)', 'SoftSetPulse (ns)',
    'SoftSetEnergy (pJ)',
    'AverageIterations',
    'AccessType', 'AccessCMOSWidth (F)', 'VoltageDropAccessDevice (V)',
    'LeakageCurrentAccessDevice (uA)',
    'SRAMCellNMOSWidth (F)', 'SRAMCellPMOSWidth (F)',
]

REQUIRED = {
    'MRAM': ['MemCellType', 'CellArea (F^2)', 'CellAspectRatio',
             'ResistanceOn (ohm)', 'ResistanceOff (ohm)', 'ReadMode',
             'AccessType'],
    'PCRAM': ['MemCellType', 'CellArea (F^2)', 'CellAspectRatio',
              'ResistanceOn (ohm)', 'ResistanceOff (ohm)', 'ReadMode',
              'AccessType'],
    'memristor': ['MemCellType', 'CellArea (F^2)', 'CellAspectRatio',
                  'ResistanceOn (ohm)', 'ResistanceOff (ohm)', 'ReadMode',
                  'AccessType'],
    'SRAM': ['MemCellType', 'CellArea (F^2)', 'CellAspectRatio',
             'SRAMCellNMOSWidth (F)', 'SRAMCellPMOSWidth (F)',
             'AccessCMOSWidth (F)'],
}

# A DESTINY cell file spells the MLC soft keys without the "Soft" prefix for
# reset voltage. Mapping kept explicit so the emitter cannot drift from
# MemCell.cpp.
KEY_ALIASES = {'SoftResetVoltage (V)': 'ResetVoltageSoft (V)'}

# An MTJ is read at a bias far below its switching threshold, or the read
# disturbs the bit. 0.2 V is the conventional ceiling and is what DESTINY's own
# sample cells sit under (25 uA x 5 kohm = 0.125 V).
READ_BIAS_MAX_V = 0.2


# ---------------------------------------------------------------- loading
def load(path=SOURCES):
    with open(path) as f:
        records = json.load(f)
    seen = set()
    for r in records:
        if r['id'] in seen:
            raise ValueError('duplicate source id: %s' % r['id'])
        seen.add(r['id'])
    return records


def _val(rec, key):
    f = rec.get('fields', {}).get(key)
    return None if f is None else f.get('value')


def _kind(rec, key):
    f = rec.get('fields', {}).get(key)
    return None if f is None else f.get('kind', 'reported')


# ------------------------------------------------------------- validation
def validate(rec):
    """Return (errors, warnings). Errors block emission; warnings do not.

    The warnings are the interesting part. Every one of them corresponds to a
    mistake that has actually been shipped in a published cell file, including
    DESTINY's own: an access transistor that cannot fit inside the cell it is
    supposed to sit in, a TMR that exceeds anything measured at room
    temperature, or a switching energy inconsistent with the current and pulse
    that are supposed to produce it.
    """
    err, warn = [], []
    fields = rec.get('fields', {})

    for key in fields:
        base = key.replace(' (nm)', '')      # ProcessNode is bare in the file
        if key not in VALID_KEYS and base not in VALID_KEYS:
            warn.append('key not in DESTINY grammar, will be ignored: %r' % key)

    mtype = _val(rec, 'MemCellType')
    if mtype is None:
        err.append('MemCellType missing')
    elif mtype in REQUIRED:
        for k in REQUIRED[mtype]:
            if _val(rec, k) is None:
                err.append('required for %s but absent: %s' % (mtype, k))

    ron, roff = _val(rec, 'ResistanceOn (ohm)'), _val(rec, 'ResistanceOff (ohm)')
    if _num(ron) and _num(roff):
        if roff <= ron:
            err.append('ResistanceOff (%g) must exceed ResistanceOn (%g)' % (roff, ron))
        else:
            tmr = roff / ron - 1.0
            if tmr > 6.0:
                warn.append('TMR = %.0f%%, above anything measured at 300 K; '
                            'check whether the paper quotes a low-temperature '
                            'or an optimistic projected value' % (100 * tmr))
            if tmr < 0.3:
                warn.append('TMR = %.0f%%; sensing margin will be thin and '
                            'DESTINY may not converge on a sense amplifier'
                            % (100 * tmr))

    # Geometry vs the access device. A transistor of width W F needs roughly
    # W x 3 F^2 of footprint (gate plus two contacted diffusions at ~1.5 F
    # each). DESTINY's shipped STT cell fails this by a factor of eight and the
    # tool says nothing.
    area = _val(rec, 'CellArea (F^2)')
    w = _val(rec, 'AccessCMOSWidth (F)')
    if _num(area) and _num(w) and w > 0:
        ntx = _transistor_count(rec)
        need = 3.0 * w * ntx
        if area < need:
            warn.append('CellArea %g F^2 cannot hold %d access transistor(s) of '
                        'width %g F (needs about %.0f F^2). Either the area is '
                        'a cell-stack area excluding the transistor, or the two '
                        'numbers come from different designs.' % (area, ntx, w, need))

    # Energy consistency: E ~ I^2 R t for a current-driven switch.
    for pre in ('Set', 'Reset'):
        i = _val(rec, '%sCurrent (uA)' % pre)
        t = _val(rec, '%sPulse (ns)' % pre)
        e = _val(rec, '%sEnergy (pJ)' % pre)
        if _num(i) and _num(t) and _num(e) and _num(ron) and e > 0:
            pred = (i * 1e-6) ** 2 * ron * (t * 1e-9) * 1e12   # pJ
            if pred > 0 and not (0.1 <= e / pred <= 10.0):
                warn.append('%sEnergy %.4g pJ is %.0fx the I^2*R_on*t estimate '
                            '(%.4g pJ). One of current, pulse or energy is from '
                            'a different operating point.' % (pre, e, e / pred, pred))

    # Read bias. DESTINY solves for a precharge voltage and gives up with
    # "Read current too large or too small that no reasonable precharge voltage
    # existing" when I_read * R_on lands outside a sane window. Several
    # published parameter sets fail this: they quote one current for both read
    # and write, which is fine as a device statement and impossible as a
    # sensing scheme. Catching it here explains the failure instead of leaving
    # DESTINY to print "No valid solutions" a hundred times.
    iread = _val(rec, 'ReadCurrent (uA)')
    if _num(iread) and _num(ron) and iread > 0:
        v_bias = iread * 1e-6 * ron
        if v_bias > READ_BIAS_MAX_V:
            warn.append('read bias I_read*R_on = %.3f V exceeds %.2f V. MTJ '
                        'reads are biased well below the switching threshold to '
                        'avoid read disturb, and DESTINY will fail to find a '
                        'precharge voltage. Usually means the paper quotes one '
                        'current for read and write. Use --auto-read.'
                        % (v_bias, READ_BIAS_MAX_V))
        if _num(roff):
            dv = iread * 1e-6 * (roff - ron) * 1e3      # mV
            need = _val(rec, 'MinSenseVoltage (mV)') or 25
            if dv < need:
                warn.append('sense margin I_read*(R_off-R_on) = %.1f mV is below '
                            'MinSenseVoltage %.0f mV; the sense amplifier cannot '
                            'resolve the state. Use --auto-read.' % (dv, need))

    lvl = _val(rec, 'MemCellLevel')
    if lvl == 'MLC':
        if mtype == 'MRAM':
            for k in ('SoftSetCurrent (uA)', 'SoftResetCurrent (uA)'):
                if _val(rec, k) is None:
                    err.append('MLC MRAM requires %s' % k)
        elif mtype in ('PCRAM', 'memristor'):
            if _val(rec, 'AverageIterations') is None:
                err.append('MLC %s requires AverageIterations '
                           '(write-verify loop count)' % mtype)

    if _val(rec, 'ProcessNode') is None and rec.get('process_node_nm') is None:
        warn.append('no process node; F^2 areas cannot be checked against any '
                    'absolute dimension and the sweep must set the node itself')

    assumed = [k for k in fields if _kind(rec, k) == 'assumed']
    if assumed:
        warn.append('assumed (not measured) values: %s' % ', '.join(sorted(assumed)))
    return err, warn


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _transistor_count(rec):
    """Read the transistor count out of the geometry note.

    SOT needs separate read and write paths, so it is 2T or 3T; STT is 1T1M.
    Getting this wrong is the single most common error in SOT area claims, so
    it is parsed from the recorded geometry rather than assumed from the
    technology name.
    """
    g = (rec.get('device_geometry') or '').upper()
    for n, marks in ((3, ('3T', 'THREE TRANSISTOR')),
                     (2, ('2T', 'TWO TRANSISTOR')),
                     (1, ('1T1M', '1T-1M', 'ONE TRANSISTOR'))):
        if any(m in g for m in marks):
            return n
    return 2 if 'SOT' in (rec.get('technology') or '').upper() else 1


def auto_read(rec, bias_v=None, margin_mV=None):
    """Solve for a read current the array model can actually sense.

    Constraint pair:
        I * R_on            <= bias_v        (no read disturb, and DESTINY
                                              can find a precharge voltage)
        I * (R_off - R_on)  >= margin_mV     (the sense amp can resolve it)

    Both are satisfiable only when TMR is high enough:
        bias_v / R_on * (R_off - R_on) >= margin  <=>  TMR >= margin / bias_v.
    At 0.2 V and 25 mV that means TMR >= 12.5%, which every MTJ here clears.
    The chosen current is the largest that respects the bias ceiling, since
    that maximises sense margin and read speed.

    Marked `derived`, with the arithmetic, because it is a sensing design
    choice this project made - not a number the paper published.
    """
    bias_v = READ_BIAS_MAX_V if bias_v is None else bias_v
    margin = margin_mV if margin_mV is not None else (
        _val(rec, 'MinSenseVoltage (mV)') or 25)
    ron, roff = _val(rec, 'ResistanceOn (ohm)'), _val(rec, 'ResistanceOff (ohm)')
    if not (_num(ron) and _num(roff)) or roff <= ron:
        return rec, 'no usable R_on/R_off; cannot solve for a read current'
    i_max = bias_v / ron * 1e6                                   # uA
    dv = i_max * 1e-6 * (roff - ron) * 1e3                       # mV
    if dv < margin:
        return rec, ('TMR %.0f%% too low: at the %.2f V bias ceiling the sense '
                     'margin is %.1f mV against %.0f mV required. This cell '
                     'cannot be read at a safe bias; report it as such.'
                     % (100 * (roff / ron - 1), bias_v, dv, margin))
    out = json.loads(json.dumps(rec))
    prev = _val(rec, 'ReadCurrent (uA)')
    out['fields']['ReadCurrent (uA)'] = dict(
        value=round(i_max, 4), unit='uA', kind='derived',
        provenance='solved: %.2f V bias ceiling / R_on %g ohm = %.4g uA, giving '
                   '%.1f mV sense margin against %.0f mV required%s'
                   % (bias_v, ron, i_max, dv, margin,
                      ('; replaces the paper\'s %g uA, which biases the junction '
                       'at %.2f V' % (prev, prev * 1e-6 * ron)) if _num(prev) else ''))
    out['fields'].pop('ReadVoltage (V)', None)
    out['fields'].pop('ReadPower (uW)', None)
    out.setdefault('warnings', []).append(
        'ReadCurrent solved by --auto-read; the paper\'s value did not admit a '
        'valid sense scheme at a safe MTJ read bias.')
    return out, None


# ------------------------------------------------------------- completion
# DESTINY refuses to run without a handful of fields that most *device* papers
# never print, because they are layout or sensing choices rather than device
# physics. Filling them silently is how an uncheckable cell file gets made, so
# completion is explicit, tagged, and reported.
#
# Two sources, in order of preference:
#   1. the paper itself, when the number is recoverable from something it does
#      print (aspect ratio from a stated layout, R_on from an RA product);
#   2. a named donor record, which the emitted header names outright.
#
# Nothing is ever completed from a bare default without saying so on the line.

DESTINY_SAMPLE_MRAM = {          # destiny_v2 sample_STTRAM.cell, shipped values
    'CellAspectRatio': 1.0,
    'ReadMode': 'voltage',
    'MinSenseVoltage (mV)': 25,
    'AccessType': 'CMOS',
    'VoltageDropAccessDevice (V)': 0.15,
}
DESTINY_SAMPLE_SRAM = {
    'SRAMCellNMOSWidth (F)': 2.08,
    'SRAMCellPMOSWidth (F)': 1.23,
    'AccessCMOSWidth (F)': 1.31,
    'CellAspectRatio': 1.46,
}

_LAYOUT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*F?\s*(?:wide)?\s*[x\u00d7]\s*'
                        r'(\d+(?:\.\d+)?)\s*F?\s*(?:tall|high)?', re.I)
_RA_RE = re.compile(r'RA[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(?:ohm|\u03a9)[\s.\u00b7*]*'
                    r'(?:um|\u00b5m)\s*\^?2', re.I)
_TMR_RE = re.compile(r'TMR[^0-9]{0,20}(\d+(?:\.\d+)?)\s*%', re.I)


def _derive_aspect_ratio(rec):
    """Recover the cell aspect ratio from a stated layout, e.g. '4F x 5F'."""
    g = rec.get('device_geometry') or ''
    m = _LAYOUT_RE.search(g)
    if not m:
        return None
    w, h = float(m.group(1)), float(m.group(2))
    if not w or not h:
        return None
    area = _val(rec, 'CellArea (F^2)')
    if _num(area) and abs(w * h - area) / max(area, 1e-9) > 0.25:
        return None                      # layout does not reconcile with the area
    return dict(value=round(h / w, 4), unit=None, kind='derived',
                provenance='layout %gF x %gF stated in device_geometry -> %g/%g'
                           % (w, h, h, w))


def complete(rec, donor=None, allow_defaults=True):
    """Fill required-but-missing fields. Returns (record, list-of-notes)."""
    out = json.loads(json.dumps(rec))
    f = out.setdefault('fields', {})
    notes = []
    mtype = _val(out, 'MemCellType')
    if mtype is None and 'MRAM' in (out.get('technology') or '').upper():
        f['MemCellType'] = dict(value='MRAM', unit=None, kind='derived',
                                provenance='from the technology field of the record')
        mtype = 'MRAM'
    if mtype is None:
        return out, ['no MemCellType and none inferable; cannot complete']

    if 'CellAspectRatio' not in f:
        ar = _derive_aspect_ratio(out)
        if ar:
            f['CellAspectRatio'] = ar
            notes.append('CellAspectRatio derived from the stated layout')

    for key in REQUIRED.get(mtype, []):
        if _val(out, key) is not None:
            continue
        if donor and _val(donor, key) is not None:
            src = donor['fields'][key]
            f[key] = dict(value=src['value'], unit=src.get('unit'), kind='assumed',
                          provenance='borrowed from %s (%s); the source paper does '
                                     'not report it' % (donor['id'],
                                                        src.get('provenance', '')[:60]))
            notes.append('%s borrowed from %s' % (key, donor['id']))
            continue
        table = DESTINY_SAMPLE_SRAM if mtype == 'SRAM' else DESTINY_SAMPLE_MRAM
        if allow_defaults and key in table:
            f[key] = dict(value=table[key], unit=None, kind='assumed',
                          provenance='DESTINY v2 shipped sample value; NOT from '
                                     'the source paper')
            notes.append('%s taken from the DESTINY sample cell' % key)
        else:
            notes.append('%s STILL MISSING - no donor and no defensible default' % key)
    if notes:
        out.setdefault('warnings', []).append(
            'completed by from_paper.py: ' + '; '.join(notes))
    return out, notes


# ---------------------------------------------------------------- MLC
def make_mlc(rec, levels=4, soft_ratio=0.5, pulse_ratio=1.0):
    """Synthesise an N-level MRAM record from an SLC one.

    DESTINY's MLC MRAM model wants a second, weaker switching event: the stacked
    soft free layer flips at a lower current than the hard one. No device paper
    in `sources.json` publishes both, so the soft transition is scaled from the
    hard one and every synthesised field is marked `assumed`. Sweep
    `--soft-ratio` before quoting any MLC number; the default is a placeholder,
    not a measurement.

    Levels above 4 are refused. Two stacked free layers give four states, and
    representing more would need a device model DESTINY does not have.
    """
    if _val(rec, 'MemCellType') != 'MRAM':
        raise ValueError('MLC synthesis here covers MRAM only; for PCM/RRAM set '
                         'AverageIterations from the paper instead')
    if levels not in (2, 4):
        raise ValueError('MRAM MLC supports 2 (=SLC) or 4 levels, not %d' % levels)
    out = json.loads(json.dumps(rec))
    out['id'] = '%s_mlc%d' % (rec['id'], levels)
    out['cell_levels'] = 'MLC' if levels > 2 else 'SLC'
    f = out['fields']
    f['MemCellLevel'] = dict(value='MLC' if levels > 2 else 'SLC', unit=None,
                             provenance='synthesised by from_paper.py --mlc',
                             kind='assumed')
    note = ('scaled from the hard transition by soft_ratio=%g; the source paper '
            'reports no soft-layer switching parameter' % soft_ratio)
    for pre in ('Set', 'Reset'):
        for suffix, ratio in (('Current (uA)', soft_ratio),
                              ('Pulse (ns)', pulse_ratio),
                              ('Voltage (V)', soft_ratio)):
            base = f.get('%s%s' % (pre, suffix))
            if base and _num(base.get('value')):
                f['Soft%s%s' % (pre, suffix)] = dict(
                    value=base['value'] * ratio, unit=base.get('unit'),
                    provenance=note, kind='assumed')
        # Let DESTINY recompute soft energies from current and pulse.
        f.pop('Soft%sEnergy (pJ)' % pre, None)
    out.setdefault('warnings', []).append(
        'MLC synthesised, not measured. Every Soft* value is assumed. Sweep '
        'soft_ratio and report the range, or drop the MLC row.')
    return out


# ---------------------------------------------------------------- emitting
def emit(rec, path=None, node_nm=None):
    err, warn = validate(rec)
    if err:
        raise ValueError('%s: %s' % (rec['id'], '; '.join(err)))

    fields = rec['fields']
    L = []
    L.append('// %s' % rec['id'])
    L.append('//')
    L.append('// GENERATED by from_paper.py from cells/sources.json.')
    L.append('// Edit sources.json, not this file; regenerate to change a value.')
    L.append('//')
    L.append('// Source: %s' % rec.get('citation', 'UNCITED'))
    if rec.get('doi'):
        L.append('// DOI:    %s' % rec['doi'])
    L.append('// Tech:   %s' % rec.get('technology', '?'))
    if rec.get('process_node_nm'):
        L.append('// Node:   %s nm' % rec['process_node_nm'])
    geo = rec.get('device_geometry')
    if geo:
        L.append('//')
        L.append('// Geometry (verbatim from the paper):')
        for chunk in _wrap(geo, 72):
            L.append('//   %s' % chunk)
    kinds = {}
    for k in fields:
        kinds.setdefault(_kind(rec, k), []).append(k)
    L.append('//')
    L.append('// Provenance: %d reported, %d derived, %d assumed.'
             % (len(kinds.get('reported', [])), len(kinds.get('derived', [])),
                len(kinds.get('assumed', []))))
    if kinds.get('assumed'):
        L.append('// WARNING - these values are NOT from the paper:')
        for k in sorted(kinds['assumed']):
            L.append('//   %s = %s  (%s)'
                     % (k, _val(rec, k), fields[k].get('provenance', 'no note')))
    for w in warn:
        for chunk in _wrap('CHECK: ' + w, 72):
            L.append('// %s' % chunk)
    for w in rec.get('warnings', []) or []:
        for chunk in _wrap('NOTE: ' + w, 72):
            L.append('// %s' % chunk)
    L.append('')

    for key in EMIT_ORDER:
        if key not in fields:
            continue
        v = fields[key]['value']
        if v is None:
            continue
        out_key = KEY_ALIASES.get(key, key)
        if out_key == 'ProcessNode' and node_nm:
            v = node_nm
        line = '-%s: %s' % (out_key, _fmt(v))
        prov = fields[key].get('provenance', '')
        kind = _kind(rec, key)
        if kind != 'reported' or prov:
            line = '%-46s // [%s] %s' % (line, kind, prov[:90])
        L.append(line)
    # Anything valid but outside EMIT_ORDER still has to be written.
    for key in fields:
        if key in EMIT_ORDER or key.replace(' (nm)', '') not in VALID_KEYS:
            continue
        v = fields[key]['value']
        if v is not None:
            L.append('-%s: %s' % (KEY_ALIASES.get(key, key), _fmt(v)))
    text = '\n'.join(L) + '\n'

    if path:
        with open(path, 'w') as f:
            f.write(text)
    return text, warn


def _fmt(v):
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, float):
        return ('%g' % v)
    return str(v)


def _wrap(s, n):
    words, line, out = str(s).split(), '', []
    for w in words:
        if len(line) + len(w) + 1 > n:
            out.append(line)
            line = w
        else:
            line = (line + ' ' + w).strip()
    if line:
        out.append(line)
    return out or ['']


# ------------------------------------------------------- NVMExplorer
# NVMExplorer takes cells as Python/JSON dicts with its own key names, and it
# has no MRAM class distinct from STT: a SOT device is expressed through the
# same custom-STT interface. That substitution is real and must be declared -
# NVMExplorer will model the *array* around a two-terminal cell whatever the
# device physics, so the SOT write path's separate read/write terminals are not
# represented. The generated record carries that statement with it.
NVMX_KEYS = {
    'CellArea (F^2)': 'cell_size_F2',
    'CellAspectRatio': 'cell_aspect_ratio',
    'ResistanceOn (ohm)': 'r_on',
    'ResistanceOff (ohm)': 'r_off',
    'ReadVoltage (V)': 'read_voltage',
    'ReadPower (uW)': 'read_power',
    'SetPulse (ns)': 'set_pulse',
    'ResetPulse (ns)': 'reset_pulse',
    'SetEnergy (pJ)': 'set_energy',
    'ResetEnergy (pJ)': 'reset_energy',
    'AccessCMOSWidth (F)': 'access_CMOS_width',
}
NVMX_TYPE = {'MRAM': 'STT', 'PCRAM': 'PCM', 'SRAM': 'SRAM', 'memristor': 'RRAM'}
NVMX_DEFAULTS = {'read_voltage': 0.25, 'read_power': 30}


def to_nvmexplorer(rec):
    """Emit one NVMExplorer custom-cell dict, with provenance alongside.

    Returns (cell_dict, meta_dict). The cell goes into the NVMExplorer config;
    the meta travels beside it so the citation is not lost at the tool boundary,
    which is exactly where provenance normally evaporates.
    """
    mtype = _val(rec, 'MemCellType')
    if mtype not in NVMX_TYPE:
        raise ValueError('%s: no NVMExplorer class for MemCellType %r'
                         % (rec['id'], mtype))
    cell = {'cell_type': NVMX_TYPE[mtype], 'name': rec['id']}
    prov, missing = {}, []
    for dkey, nkey in NVMX_KEYS.items():
        v = _val(rec, dkey)
        if v is None:
            if nkey in NVMX_DEFAULTS:
                cell[nkey] = NVMX_DEFAULTS[nkey]
                prov[nkey] = ('NVMExplorer default (%s); not from the paper'
                              % NVMX_DEFAULTS[nkey])
                missing.append(nkey)
            continue
        cell[nkey] = v
        prov[nkey] = '[%s] %s' % (_kind(rec, dkey),
                                  rec['fields'][dkey].get('provenance', '')[:120])
    if mtype == 'SRAM':
        for dkey, nkey in (('SRAMCellNMOSWidth (F)', 'nmos_width'),
                           ('SRAMCellPMOSWidth (F)', 'pmos_width')):
            v = _val(rec, dkey)
            if v is not None:
                cell[nkey] = v
    meta = {
        'id': rec['id'],
        'citation': rec.get('citation'),
        'doi': rec.get('doi'),
        'technology': rec.get('technology'),
        'process_node_nm': rec.get('process_node_nm'),
        'device_geometry': rec.get('device_geometry'),
        'field_provenance': prov,
        'fields_defaulted_not_from_paper': missing,
        'substitutions': [],
        'warnings': list(rec.get('warnings') or []),
    }
    if 'SOT' in (rec.get('technology') or '').upper():
        meta['substitutions'].append(
            'Modelled through NVMExplorer\'s custom **STT** interface: the tool '
            'has no three-terminal SOT class. Array energy and latency therefore '
            'assume a two-terminal cell with a shared read/write path, so SOT\'s '
            'separate write path is NOT represented. Treat NVMExplorer SOT rows '
            'as an array-model approximation and cross-check against DESTINY.')
    if _val(rec, 'MemCellLevel') == 'MLC':
        meta['substitutions'].append(
            'MLC is not expressible here; NVMExplorer receives the hard '
            'transition only, i.e. this row is effectively SLC.')
    return cell, meta


# ---------------------------------------------------------------- CLI
def cmd_list(records):
    print('%-40s %-14s %6s %8s %8s %8s  %s'
          % ('id', 'tech', 'node', 'area F2', 'Iw uA', 'tw ns', 'prov r/d/a'))
    for r in sorted(records, key=lambda x: x['id']):
        kinds = [_kind(r, k) for k in r.get('fields', {})]
        print('%-40s %-14s %6s %8s %8s %8s  %d/%d/%d'
              % (r['id'][:40], (r.get('technology') or '')[:14],
                 r.get('process_node_nm') or '-',
                 _show(_val(r, 'CellArea (F^2)')),
                 _show(_val(r, 'SetCurrent (uA)')),
                 _show(_val(r, 'SetPulse (ns)')),
                 kinds.count('reported'), kinds.count('derived'),
                 kinds.count('assumed')))


def _show(v):
    return '-' if v is None else (('%g' % v) if _num(v) else str(v)[:8])


def cmd_compare(records, field):
    key = field if field in VALID_KEYS else next(
        (k for k in VALID_KEYS if k.lower().startswith(field.lower())), field)
    rows = [(r['id'], r.get('technology', ''), _val(r, key), _kind(r, key))
            for r in records if _num(_val(r, key))]
    if not rows:
        print('no record reports %r' % key)
        return
    print('\n%s across %d papers\n' % (key, len(rows)))
    for tech_pre in ('SOT', 'STT', 'SRAM', ''):
        sel = [x for x in rows if (x[1] or '').upper().startswith(tech_pre)]
        if tech_pre == '':
            sel = [x for x in rows
                   if not any((x[1] or '').upper().startswith(t)
                              for t in ('SOT', 'STT', 'SRAM'))]
        if not sel:
            continue
        vals = [x[2] for x in sel]
        print('  %-5s n=%d  min %g  median %g  max %g  spread %.1fx'
              % (tech_pre or 'other', len(sel), min(vals),
                 sorted(vals)[len(vals) // 2], max(vals),
                 max(vals) / min(vals) if min(vals) else float('inf')))
        for i, t, v, k in sorted(sel, key=lambda x: x[2]):
            print('        %-40s %10g  [%s]' % (i[:40], v, k))
    print('\nA spread of more than about 3x means the quantity is a design '
          'choice, not a device constant. Report the range, not a point.')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sources', default=SOURCES)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--compare', metavar='FIELD')
    ap.add_argument('--emit', metavar='ID')
    ap.add_argument('--emit-all', action='store_true')
    ap.add_argument('--mlc', type=int, default=0, metavar='LEVELS')
    ap.add_argument('--soft-ratio', type=float, default=0.5)
    ap.add_argument('--node', type=int, default=0, help='override ProcessNode (nm)')
    ap.add_argument('--complete', action='store_true',
                    help='fill required-but-missing fields, tagging every one')
    ap.add_argument('--complete-from', metavar='ID', default='',
                    help='donor record for missing fields, named in the header')
    ap.add_argument('--auto-read', action='store_true',
                    help='solve ReadCurrent from R_on/R_off when the published '
                         'value cannot be sensed at a safe bias')
    ap.add_argument('--read-bias', type=float, default=READ_BIAS_MAX_V,
                    metavar='V', help='MTJ read-bias ceiling (default %.2f V)'
                                      % READ_BIAS_MAX_V)
    ap.add_argument('--no-defaults', action='store_true',
                    help='with --complete, refuse the DESTINY sample fallback')
    ap.add_argument('-o', '--out', default=os.path.join(HERE, 'generated'))
    ap.add_argument('--format', choices=('destiny', 'nvmexplorer', 'both'),
                    default='destiny',
                    help='destiny writes .cell files; nvmexplorer writes '
                         'nvmexplorer_cells.json plus a provenance sidecar')
    a = ap.parse_args()

    records = load(a.sources)
    by_id = {r['id']: r for r in records}

    if a.list:
        cmd_list(records)
        return 0
    if a.compare:
        cmd_compare(records, a.compare)
        return 0
    if a.check:
        bad = 0
        for r in sorted(records, key=lambda x: x['id']):
            err, warn = validate(r)
            if err or warn:
                print('\n%s' % r['id'])
                for e in err:
                    print('   ERROR   %s' % e)
                    bad += 1
                for w in warn:
                    print('   warn    %s' % w)
        print('\n%d record(s), %d error(s)' % (len(records), bad))
        return 1 if bad else 0

    targets = list(records) if a.emit_all else (
        [by_id[a.emit]] if a.emit in by_id else [])
    if not targets:
        print('unknown id %r; try --list' % a.emit, file=sys.stderr)
        return 2

    donor = by_id.get(a.complete_from) if a.complete_from else None
    if a.complete_from and donor is None:
        print('unknown donor %r' % a.complete_from, file=sys.stderr)
        return 2

    os.makedirs(a.out, exist_ok=True)
    written = skipped = 0
    nvmx_cells, nvmx_meta = [], []
    for r in targets:
        rec = r
        if a.complete or donor:
            rec, notes = complete(rec, donor, allow_defaults=not a.no_defaults)
            for n in notes:
                print('   complete  %-38s %s' % (r['id'][:38], n))
        if a.auto_read:
            rec, why = auto_read(rec, a.read_bias)
            if why:
                print('   auto-read  %-38s %s' % (r['id'][:38], why))
        if a.mlc:
            rec = make_mlc(rec, a.mlc, a.soft_ratio)
        path = os.path.join(a.out, rec['id'] + '.cell')
        try:
            _, warn = emit(rec, path, a.node or None)
        except ValueError as e:
            print('SKIP  %s' % e)
            skipped += 1
            continue
        if a.format in ('nvmexplorer', 'both'):
            try:
                cell, meta = to_nvmexplorer(rec)
                nvmx_cells.append(cell)
                nvmx_meta.append(meta)
            except ValueError as e:
                print('SKIP nvmexplorer  %s' % e)
        written += 1
        print('wrote %s%s' % (path, '   (%d check(s))' % len(warn) if warn else ''))

    if a.format in ('nvmexplorer', 'both') and nvmx_cells:
        cp = os.path.join(a.out, 'nvmexplorer_cells.json')
        mp = os.path.join(a.out, 'nvmexplorer_cells.provenance.json')
        with open(cp, 'w') as f:
            json.dump(nvmx_cells, f, indent=1)
        with open(mp, 'w') as f:
            json.dump(nvmx_meta, f, indent=1)
        print('wrote %s (%d cells) and its provenance sidecar'
              % (cp, len(nvmx_cells)))
    print('\n%d written, %d skipped' % (written, skipped))
    return 0


if __name__ == '__main__':
    sys.exit(main())
