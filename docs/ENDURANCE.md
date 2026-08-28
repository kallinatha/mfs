# Endurance: what each number is, who says so, and what survives the sweep

Years-to-failure scales **linearly** with assumed per-cell endurance. An uncited
endurance figure is therefore the easiest way to make a lifetime claim
unfalsifiable, and it is the first thing a hostile reviewer should attack.
`config/endurance.json` fixes each technology's budget with the citation
attached, and sweeps the one that is genuinely unsettled.

---

## 1. The numbers and their sources

| technology | used here | swept | source |
|---|---|---|---|
| **STT-MRAM** | **4 × 10¹²** | fixed | Mittal, Vetter & Li, *LastingNVCache* (2014): "write endurance value … **4 × 10¹²** and 10⁸, respectively" for STT-RAM and PCM. Reused verbatim by Agarwal, PhD thesis, IIT Guwahati 2020, §2.2.2. Jokar et al., *Sequoia*, IEEE TVLSI 24(3), 2016, independently assume ~10¹². |
| **SOT-MRAM** | swept | **10¹², 10¹³, 10¹⁴, 10¹⁵** | Upper anchor: [imec, 13 December 2023](https://www.imec-int.com/en/press/imecs-extremely-scaled-sot-mram-devices-show-record-low-switching-energy-and-virtually) — scaled SOT-MRAM at ~50 nm CD, sub-100 fJ/bit, "**>10¹⁵ program/erase cycles**". Lower anchor: the STT-class ~10¹², on the argument that a SOT device sharing an MTJ stack inherits its tunnel-barrier wear-out. |
| PCM | 10⁸ | fixed | Mittal et al., same sentence as the STT figure. |
| SRAM | 10¹⁶ | fixed | Mittal & Vetter, *AYUSH*, MASCOTS 2015: "endurance of SRAM is more than 10¹⁵". Not wear-limited here. |
| ReRAM | 10¹¹ | 10⁸–10¹¹ | *AYUSH*: "for ReRAM is only 10¹¹"; Agarwal thesis: "10⁵ to 10¹¹". Six published decades. Not used in the main comparison. |

**You were right that 4 × 10¹² is the community standard** — it traces to
LastingNVCache and propagates from there through most of the comparison
literature. Two caveats you should state rather than have raised at you.

**The STT caveat is severe.** Agarwal's very next sentence after adopting
4 × 10¹² reads: *"Intel report the write endurance value of STT to **10⁶** write
cycles."* That is a **six order of magnitude** gap between the number
architecture papers use and the number a shipped product is rated at. 4 × 10¹²
is defensible as the *community-comparable* point — it is what the competitors'
numbers were computed with, so using anything else breaks the comparison — but
any **absolute** years figure built on it is a research-device projection.
`endurance.json` carries the 10⁶ point as `pessimistic` so it can be quoted.

**The SOT caveat cuts the other way.** imec's 10¹⁵ is a **measurement floor, not
a failure point**: they state they "experimentally validated our assumption that
SOT-MRAM cells can have unlimited endurance", meaning testing stopped without
the devices failing. It is a *lower bound on the truth* and an *upper bound on
what may honestly be claimed*. Quoting 10¹⁵ as a fact is not supportable.

The argument worth making in the paper is structural rather than numerical: in
SOT the write current passes through the heavy-metal track, **not through the
tunnel barrier**, so the barrier never sees the switching stress that limits
STT. That mechanism is why imec measured what they measured, and it is a claim
that survives whatever the eventual number turns out to be.

---

## 2. What the sweep shows

Cross-stack join, hybrid arena, indicative dry-run numbers:

| scheme | STT @ 4×10¹² | SOT @ 10¹² | SOT @ 10¹³ | SOT @ 10¹⁴ | SOT @ 10¹⁵ |
|---|---|---|---|---|---|
| unmanaged | 5.73 yr | 1.43 | 14.3 | 143 | 1,433 |
| i2wap | 5.73 | 1.43 | 14.3 | 143 | 1,433 |
| psc | **13.19** | **3.30** | **33.0** | 330 | 3,296 |
| psc-vro | 12.56 | 3.14 | 31.4 | 314 | 3,139 |
| swwr | 5.49 | 1.37 | 13.7 | 137 | 1,373 |

Two things fall out, and they point in opposite directions.

**The policy ranking is decade-invariant.** Years scale linearly with endurance,
so every column is the same ordering rescaled. Which mechanism wins does *not*
depend on the endurance assumption — only the absolute lifetime does. That is
the reassuring half: the wear-levelling comparison is robust to the biggest
open uncertainty in the paper, and you can say so.

**The viability threshold sits between 10¹² and 10¹³.** At 10¹² an unmanaged SOT
LLC lasts **1.4 years**, which is not a product. PSC lifts it to 3.3 years —
better, still not a product. At 10¹³ the unmanaged case reaches 14 years and PSC
reaches 33. So:

> Wear levelling cannot rescue the 10¹² case. If SOT endurance is STT-class, no
> policy in this study makes an LLC viable; the technology argument requires at
> least 10¹³, and the imec result is what makes that plausible.

That sentence is worth more to the paper than any single lifetime figure,
because it says exactly what the result depends on. Do not report one SOT
lifetime number anywhere. Report the band, and state which decade each
conclusion survives.

---

## 3. How it is wired

```bash
python3 mfs.py integrate --config config/suscom_ten_mixes.json
#   experiment.nvm_technology selects the sweep from config/endurance.json

python3 stage4_analysis/cross_stack.py ... \
        --endurance-table config/endurance.json --nvm-technology STT-MRAM
```

`cross_stack.load_endurance()` raises if a technology has no entry rather than
defaulting — adding a technology means adding its citation. The chosen sweep is
recorded in `assumptions.json` beside every result set.

The array configuration is now **16 MB, 12 NVM + 4 SRAM ways, 64 B lines**,
which is what the HyCSim authors ship in their own `config.json` and therefore
the configuration these traces were exported under. Previously the config ran
1 MB, which is a different design point from the one the traces describe.
