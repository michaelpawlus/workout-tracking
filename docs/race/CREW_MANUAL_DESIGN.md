# Crew Manual Generator — design doc (issue #12)

Design for `ultra race crew-manual`: a **governor-based crew manual** that turns the
loaded course + a reusable protocol profile into a printable, durable race-day document.
The draft manual lives at `docs/race/BR100_crew_manual_draft.md`, the protocol data at
`backend/data/br100_crew_protocol.yaml`, and the peer-split skeleton at
`backend/data/br100_2025_analog_splits.csv`.

**Status:** first pass **implemented** — `load_crew_protocol`, `load_split_skeleton`,
`eta_seconds_from_skeleton`, `generate_crew_manual`, `crew_manual_to_markdown`
(`race_engine.py`), `cmd_race_crew_manual` (`cli.py`), `write_crew_manual_to_vault`
(`vault.py`), tests in `tests/test_crew_manual.py`. Remaining: load the real BR100 GPX
for grade-aware ETAs and swap in official 2026 splits (#14); `--interview` / `--research`.

## 1. What it is (and how it differs from `crew-sheet`)

`race crew-sheet` already emits A/B/C ETAs per crew stop with a generic decision tree.
The crew **manual** extends it into the full race-day document the athlete actually wants:

| | `crew-sheet` (today) | `crew-manual` (this issue) |
|---|---|---|
| Headline pacing | A/B/C equally | **26h governor** headline; A/B/C secondary |
| Start time | hardcoded `05:00` (**bug** — BR100 starts 4:00 AM) | from profile `meta.start_time` |
| Fuel per stop | none | gels-per-leg + sodium/fluid rates, from the fueling engine |
| Cooling / chafing | none | **full playbook**, per-stop, escalated by weather |
| Drop bags / night kit | drop-bag flag only | manifests + sunset-triggered night-kit handoff |
| Aid cutoffs / food | none | surfaced from segment `terrain_notes` (the CSV) |
| Source of truth | DB only | DB **+ checked-in protocol profile** |

## 2. Architecture & data flow

```
br100_crew_protocol.yaml ─┐
                          ├─► crew-manual ─► markdown (printable) ─► --output / vault
loaded course (DB):       │       │
  segments + grade        │       ├─ generate_race_plan(goal=26h, start=04:00, weather)
  crew/drop flags         │       │     → per-segment cumulative time + ETA
  terrain_notes (cutoffs, │       ├─ generate_fueling_plan(target segments)
  food, ice)              │       │     → carb/sodium/fluid; aggregate BETWEEN crew stops
historical lessons (DB) ──┘       └─ merge per crew stop:
                                       ETA + "on pace if before X" + cutoff cushion
                                       + gels-to-hand + cooling + chafing
                                       + drop-bag/night-kit + decision tree
```

### Command surface
```
ultra race crew-manual
  --goal-time 26:00:00         # default from profile.meta.governor_goal_time
  --start-time 04:00           # default from profile.meta.start_time (NOT 05:00)
  --weather-temp 82            # escalates cooling language + sodium when > cooling.hot_threshold_f
  --profile <path>             # default backend/data/br100_crew_protocol.yaml
  --output crew_manual.md      # write to a path
  --vault                      # write into the Obsidian vault like run reports (vault.py)
  --scenarios                  # also print A/B/C columns under the 26h headline
  --json                       # structured output
```

### Module layout
- `race_engine.py`: add `load_crew_protocol(path)`, `generate_crew_manual(conn, course_id, protocol, goal, start, weather)`, `crew_manual_to_markdown(manual)`. Reuse `generate_race_plan` and `generate_fueling_plan` — **no pacing/fueling logic duplicated.**
- `cli.py`: `cmd_race_crew_manual` + subparser + registry entry; mirror `cmd_race_crew_sheet`.
- `vault.py`: small `write_crew_manual_to_vault(...)` paralleling the run-report writer (reuse `oj` path + direct fallback).
- Fuel aggregation **between** crew stops (sum segment fuel from one crew stop to the next, convert carbs→gels via `fueling.gel_carb_g`) is the one genuinely new calc.

## 3. Durability / reusability / robustness (the explicit ask)

- **Data, not code.** Everything race- or athlete-specific lives in the YAML profile;
  a second race = a second profile, zero Python changes. Aid-station facts come from the
  already-loaded CSV/DB (single source of truth), not duplicated in the manual.
- **Fix the start-time bug.** `start_time` is a profile/course attribute; stop defaulting to 05:00.
  Default the governor to **26h**, not the 24h stretch goal.
- **Validate on load.** Schema-check the profile (required keys, time formats, ranges) and
  fail with a clear message — robust against a half-filled profile from the interview step.
- **No new heavy deps.** Night/sunset is a configurable `meta.sunset`/`meta.sunrise` value
  (avoids an `astral`-type dependency that isn't installed); weather is a flag, with a
  documented seam for a later forecast fetch.
- **Idempotent & regenerable.** Re-run any time targets/weather/profile change; output is a
  pure function of (course, profile, flags).
- **Graceful degradation.** No GPX loaded → even-split fallback with a visible banner
  (exactly what the draft does) instead of an error.
- **Tested.** Unit tests in the `tests/` style of `test_aid_stations.py`: profile loader +
  validation, per-leg gel aggregation, governor ETA from 4:00 AM, sunset → night-kit trigger,
  weather → cooling/sodium escalation, no-GPX fallback.

## 4. Generalization roadmap — research · interview · historical

Designed now, built BR100-first. Three reusable inputs feed the profile/manual:

### a) Interview component  → *populates the profile*
A guided Q&A that writes/updates the YAML so the protocol is captured durably and reused.
- **MVP (now):** the agent conducts the interview conversationally and fills the YAML
  (this draft's profile came from exactly that). The fixed question set is the schema:
  heat tolerance & must-haves (ice bandana, cold water), known chafe spots, gut-trained
  fuel & rates, crew size/roles, drop-bag preferences, night fears, governor target.
- **Later:** `crew-manual --interview` (or `race interview`) prompts through the set and
  writes the profile; re-runnable to update before race day.

### b) Research component  → *enriches the profile (`research:` block)*
A step that researches the specific race and the athlete's themes, captured into the
profile's (currently empty) `research:` block, which the manual renders if present.
- Targets: historic race-day weather for the date/location (drives default `--weather-temp`),
  course heat-exposure / shade, aid-station ice availability, gear/product recommendations
  (cooling methods, anti-chafe products).
- **Seam:** `research:` is already in the YAML; the manual reads it. A `--research` step
  (web search / deep-research harness) can fill it. For BR100 this can pre-load typical
  late-July NE-Ohio temp+humidity so the heat plan isn't guesswork.

### c) Historical data  → *frames the manual + sharpens ETAs*
- **Athlete's own history** (`historical.py`, already built): surface his documented
  limiters — late positive split, heat/weather DNFs — in the manual intro and bias the
  governor's back-half ETAs (already wired into `generate_race_plan`'s fade).
- **Peer splits** (issue #14): **built as the default ETA source.** A finisher's
  cumulative splits load via `load_split_skeleton` and scale to the governor goal
  (`eta_seconds_from_skeleton`), so ETAs follow the real positive-split fade instead of an
  even split. The bundled `br100_2025_analog_splits.csv` (a 26:39:43 M40-44 finisher) is the
  current skeleton; `--splits PATH` overrides it and `--no-splits` falls back to the engine
  model. The skeleton is mapped by course *fraction*, so a 100.5 mi analog transfers onto the
  101.8 mi course. Swap in official 2026 splits / `race cohort` output when available.

## 5. Build order (after this draft is approved)
1. `load_crew_protocol` + schema validation + tests.
2. `generate_crew_manual` (reuse plan+fueling; add between-stop fuel aggregation; sunset/weather logic).
3. `crew_manual_to_markdown` + `--output`/`--vault`; CLI wiring + README/CLAUDE.md docs.
4. Fix the 05:00→profile start-time default and 26h governor default.
5. Tests; regenerate the real manual once the BR100 GPX is loaded.
6. Follow-ups: `--interview`, `--research`, peer-split skeleton (#14).

## 6. Known dependencies / open items
- **BR100 GPX must be loaded** (`race load-course` + `load-aid-stations`) for real grade/ETAs — issue #12 flags this; the GPX file isn't in the repo yet.
- Confirm gel carb count (assumed 24 g) and Flash IV sodium per serving.
- Confirm crew roster / which members cover which stops (could become a `crew:` roster block).
