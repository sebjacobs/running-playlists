# Strides mix transition issues — taxonomy & fixes

Reference for diagnosing bad transitions in generated strides workout mixes
and the toolset for addressing them. Each issue lists the symptom you'd hear,
how to detect it, and how to fix it.

The guiding principle: **prefer data-side fixes** (edits to `music.db` or a
`grid_overrides` table) over Traktor GUI work. Only go into Traktor when
data-side detection isn't enough — typically when a human ear is needed to
place a cue or pick a true downbeat.

---

## Issue 1 — Tempo snap (Traktor rounds BPM to integers)

**What it sounds like:** drums in two adjacent tracks gradually drift apart
across a long crossfade. By the end of a 22 s overlap the kicks are clearly
out of time.

**Why it happens:** Traktor's autodetect snaps to integer BPMs (e.g. 173.0
when the true tempo is 172.3). Over 22 s of crossfade a 0.4% error compounds
to ~95 ms — close to ¼ of a beat.

**Detect:**
- Run `scripts/diag_grid_phase.py --search "<artist>:<title>" --start <s>
  --dur 16` — look for **drift in beat-offset values** (creeping in one
  direction over time) rather than random scatter.
- Catalogue-wide screen: any track with `bpm` to 3+ decimals of an integer
  *and* a non-zero drift slope is a candidate.
- Note: `~–20 ms` median offset is the **noise floor** of the spectral-flux
  onset detector — that's the FD baseline, not a real issue.

**Fix:**
- Manual: tap-tempo in Traktor → Edit → Beat Grid; enable fractional BPM;
  re-export NML; re-run `import_traktor.py`.
- Data-side: `UPDATE tracks SET bpm = <corrected> WHERE id = ?`. Or via a
  proposed `grid_overrides` table that the generator coalesces from first.

**Status:** detection script exists. Auto-correction not built yet.

---

## Issue 2 — Wrong grid anchor (off-phase grid)

**What it sounds like:** drums in one track always feel "behind" or "ahead"
of everything else, regardless of crossfade length. Whole track sits ~50 ms
off from where the ear expects.

**Why it happens:** AutoGrid latches onto the wrong onset — a sub-kick swell
that ramps up before the actual hit, an FX stab, a snare on beat 4 of the
preceding bar.

**Detect:**
- `scripts/diag_grid_phase.py` — **median beat-offset** vs the catalogue
  baseline (~–20 ms). BMT was at –70 ms, ~50 ms off baseline. That's the
  signal. (Also visible by eye in `scripts/diag_phrase_waveform.py` once
  zoomed in tight enough.)
- Watch for offsets that are **NOT** clean musical fractions (½ beat, ¼ beat).
  Those would be wrong-downbeat anchors (Issue 3); something like –70 ms in
  isolation is more likely a misplaced anchor on a swell or pre-attack.

**Fix:**
- Manual: Traktor → drag the AutoGrid anchor onto a clean kick → re-export
  NML → re-import.
- Data-side: `UPDATE tracks SET traktor_beatgrid_ms = <corrected>` or write
  to `grid_overrides`.

**Status:** detection script exists. BMT flagged as a candidate; not yet
re-gridded.

---

## Issue 3 — Odd-structure intro (the Danny Byrd problem)

**What it sounds like:** track is mathematically on the grid but feels
syncopated or "off-beat" against neighbours. Often present in tracks that
were tricky to mix on vinyl.

**Why it happens:** the production deliberately misleads the ear — reverse
cymbals that "land" the 1, half-bar pickups, half-time intros that flip to
full-time at the drop, syncopated kick patterns where the strongest hit
isn't beat 1.

**Detect:**
- **Hard to automate.** Would need a classifier trained on labelled DnB
  intros. Not worth building.
- The calibrator script (proposed) can flag *candidates* for manual review
  — track has clean onset detection but doesn't sit well in mixes.
- Pragmatic test: open in Traktor, look at the AutoGrid vs the visible
  kicks in track view. If kicks land on grid lines but the track still feels
  off, it's this category.

**Fix:**
- Place a `drop` hotcue in Traktor at the genuine downbeat (where your ear
  says "this is bar 1 of the verse"). Re-export NML, re-import.
- Generator already uses `drop_cue + N bars` instead of `grid + 48 bars`
  when a cue named "drop" exists for the track.

**Status:** `traktor_cues` table + cue-aware phrase selection built. Manual
hotcue labelling required per track.

---

## Issue 4 — Phasing-heavy production (Marcus Intalex et al)

**What it sounds like:** in a long crossfade between two phasing-heavy
tracks, the drums turn into a mushy, smeared, comb-filtered mess —
regardless of grid alignment.

**Why it happens:** flanging/phasing on the drum bus modulates transient
peak positions over time. Two phased tracks overlapped = comb-filter on
comb-filter. Audibly chaotic even with perfect grids.

**Detect:**
- **By ear** is the reliable signal. The track will have an obvious sweep on
  the drums.
- Auto-detection is possible (look for moving spectral notches in HF) but
  unreliable; not worth automating.

**Fix:**
- Manual: `UPDATE tracks SET phasing_heavy = 1 WHERE artist = '...' AND
  title = '...'`
- Generator's `select_tracks` skips picking a phasing-heavy track if the
  previous pick was also phasing-heavy — guarantees a non-phased separator.

**Status:** `phasing_heavy` column + spread-apart constraint built. BMT and
Commix Marcus Intalex remixes flagged.

---

## Issue 5 — Phrase lands on a breakdown / atmospheric section

**What it sounds like:** the chosen 32-bar window contains pads / vocals /
atmospherics rather than full drums. Crossfading it against a drum-heavy
track creates a "drums dropping out" feel mid-overlap.

**Why it happens:** the generator uses `grid_anchor + 48 bars` as a blunt
intro-skip heuristic. Knows nothing about song structure. For a track with
a long breakdown around bar 48, the cut lands in the wrong place.

**Detect:**
- Render `scripts/diag_phrase_waveform.py` for a track — if the RMS
  envelope inside the phrase window is flat and quiet compared to the
  surrounding track, the phrase is in a low-energy section.
- Could automate: average RMS in the phrase window vs overall track RMS;
  flag if phrase RMS is significantly below the track average.

**Fix:**
- Same as Issue 3: place a `drop` hotcue at the genuine drum entry.
  Generator's cue-aware phrase selector picks `drop + N bars` instead of
  `grid + 48 bars`.
- Alternative: per-track `intro_bars` override in a config table.

**Status:** cue-aware selection built. Auto-detection of low-energy phrase
windows not built.

---

## Issue 6 — Key clash (not yet investigated)

**What it sounds like:** two adjacent tracks fight harmonically — one's bass
note clashes with the other's pad/vocal.

**Detect:**
- `traktor_key` is already imported (0–23 integer encoding). Adjacent picks
  could be checked for Camelot-wheel compatibility (same key, perfect 4th /
  5th, or relative major/minor).

**Fix:**
- `select_tracks` could prefer key-compatible adjacencies. Not built yet.
  Lowest-priority issue — DnB tonal content is usually sparse enough that
  key clashes are mild compared to grid/timing problems.

**Status:** data available, no constraint built.

---

## Issue 7 — Mixing environment / monitoring chain

Documented separately in `docs/traktor-setup.md`. In short: bad monitoring
(BT HFP fallback, latency-mismatched aggregate, no proper bass response)
makes it impossible to diagnose any of the above. The wired Z1 + laptop
speakers aggregate is the baseline for everything else here.

---

## Tooling map

| Tool | Purpose |
|---|---|
| `scripts/import_traktor.py` | NML → music.db (BPM, key, beatgrid, cues) |
| `scripts/diag_phrase_waveform.py` | Visual: waveform + grid + phrase window + cues |
| `scripts/diag_grid_phase.py` | Numerical: onset offsets vs grid, drift detection |
| `scripts/generate_strides_workout.py` | Mix generator (cue-aware, phasing-aware) |
| `scripts/calibrate_grids.py` | **Proposed** — catalogue-wide triage of grid issues |
| `grid_overrides` table | **Proposed** — data-side BPM/grid corrections |

## Workflow

1. Generate a mix; listen.
2. Note timestamp of any bad transition; identify the two tracks involved.
3. Run `diag_grid_phase.py` and `diag_phrase_waveform.py` on each suspect.
4. Map symptoms to issue categories above.
5. Apply the appropriate fix (data-side first; Traktor only when needed).
6. Regenerate; verify.
