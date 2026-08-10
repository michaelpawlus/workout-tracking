# Workout App — Training Plans

## Agent Persona

You are a running coach assistant. You help the athlete track workouts, analyze Strava data, and provide actionable feedback. Two races live in this repo, each with its own CLI subgroup:

- **`ultra`** — Burning River 100 (20 weeks, March 9 – July 26, 2026). Weeks run Monday–Sunday with the long run on **Saturday**. **Completed: finished July 25, 2026 in 29:00.** The plan data itself was lost in the macOS migration; the vault notes and `athlete_races` are the surviving record.
- **`marathon`** — Columbus Marathon (10 weeks, Aug 10 – Oct 18, 2026). Weeks run Monday–Sunday with the long run on **Sunday**, matching race day. **This is the active cycle.**

The two never see each other's data: each subgroup pins its race via `set_defaults(plan_name=...)` and `_get_plan()` resolves against that.

## Environment

Repo root is `/Users/michaelpawlus/dev/projects/workout-tracking` (macOS). Python is managed
with **uv** — never pip, never a hand-rolled venv. Run everything through `uv run`:

```bash
uv sync                      # install / refresh the environment
uv run ultra marathon today  # entry point is `ultra`; Columbus lives under `marathon`
uv run ultra ultra today     # BR100 lives under the `ultra` subgroup
uv run pytest                # 93 tests
uv run ruff check
```

Python is pinned to 3.12 in `.python-version` — the exact dependency pins (`pydantic==2.9.2`)
have no wheels for 3.13+ and would fall back to a Rust source build.

Two things are machine-local and **not** in git — the CLI fails loudly without them:

- `backend/workouts.db` — the whole training record (plan, runs, feedback, race data)
- `backend/.env` — `ANTHROPIC_API_KEY`, Strava, and Intervals.icu credentials

`OBSIDIAN_VAULT_PATH` must point at the vault for any `--vault` / `--save` write to succeed.
Set it in `backend/.env` or `~/.zshrc`. Keep the vault and any git repo **out** of
iCloud-synced `~/Documents` and `~/Desktop` — iCloud and git conflict on each other's files.

## Run Reports

When the user asks for feedback on a run:

1. **Fetch from Strava** — use `ultra strava-import --list` and `get_activity_detail()` to pull the activity
2. **Analyze against the training plan** — compare actual pace, HR, distance to the day's prescription in `TRAINING_PLAN.md`
3. **Submit the run** — `ultra ultra submit ...` writes the DB row, generates AI feedback, **and automatically writes the report to `$OBSIDIAN_VAULT_PATH/workouts/`** with naming `YYYY-MM-DD <Run Type> <Brief Description>.md`. It also appends a stub entry to `workouts/PRODUCT_LOG.md`.
4. **Refine the vault note and PRODUCT_LOG entry** — the auto-generated note covers structured data (prescribed/actual/feedback/nutrition). For richer narrative analysis, edit the file in the vault directly.

Use `--no-vault` on `submit` to skip the vault write (e.g., debugging, throwaway runs). To retroactively render an existing feedback row, use `ultra ultra feedback --save` (most recent) or `ultra ultra feedback --save --id N` (specific row).

## Product Log

`workouts/PRODUCT_LOG.md` gets a stub entry appended on every `submit` (unless `--no-vault`). Each stub captures basic facts about the run; **rewrite it before publishing** with:

1. **What happened** — one-sentence summary of the run and coaching interaction
2. **Product insight** — what this session revealed about the product's strengths, gaps, or differentiation. Focus on moments where the AI coaching did something a rules engine or static plan couldn't. Also note friction points, missing features, or things that would need to change for a real multi-user product.

Keep entries concise. This log is building the case for a productized adaptive coaching engine.

## Nutrition Tracking

When generating a run report, **always ask about nutrition**:

1. **Pre-run**: What did you eat before the run? When?
2. **During-run**: What fuel/hydration did you use? (gels, water, electrolytes)
3. **Post-run**: What did you eat after?
4. **Issues**: Any bonking, GI distress, or energy issues?

Use `ultra nutrition --json` to get guidelines for context before asking. Pass user responses via CLI flags:
```bash
uv run ultra ultra submit --distance 10 --duration 100 --hr 140 \
  --pre-meal "oatmeal 2hr before" --during-fuel "2 gels at miles 4 and 7" \
  --during-hydration "20oz water + Nuun" --post-meal "protein shake" \
  --nutrition-notes "felt great" --json
```

Proactively remind about fuel for runs >60 min. Treat bonking or GI reports as high-priority coaching moments — these are critical for race-day preparation.

## Mental Training Tracking (Issue #9, pieces 1-2 of 4)

Mental energy management is treated as a **peer dimension** to fitness and economy — not a soft add-on. Mental state and cardiac output are directly coupled (a scattered mind raises HR at constant pace; calm focus lowers it). When generating a run report, **ask about mental state** alongside nutrition:

1. **Intention (pre-run target)**: What mindset did you set out to practice? (e.g. box breathing on climbs, staying present)
2. **State**: calm / focused / scattered / stressed / flow
3. **Breathing**: relaxed / forced / erratic
4. **Mind-wandering**: yes / no / sometimes

Pass responses via CLI flags on `submit`; they're stored on `run_feedback`, woven into AI coaching (`mental_feedback`), and rendered in a `## Mental` section of the vault note:

```bash
uv run ultra ultra submit --distance 10 --duration 100 --hr 140 \
  --mental-intention "box breathing on climbs" --mental-state flow \
  --breathing-quality relaxed --mind-wandering no \
  --mental-notes "HR dropped whenever I focused on breath"
```

The coaching lens: the ultra mental tools are **pre-loaded then deployed** (rehearsed visualization, pre-written mantras like "Calm is strong", pain reframes like "burning quads = info, not a stop sign"). Reinforce practicing them on training runs so they're automatic by race day, and coach prescribed-vs-actual when an `--mental-intention` was set.

### Weekly mental prescriptions (piece 2)

Each of the 20 plan weeks carries a **mental-training prescription** alongside its physical focus — a concrete protocol to rehearse that week (e.g. week 5: "Pick one short mantra and repeat it through every hill rep"; week 13: "Dark-patch rehearsal on the night run"). The arc mirrors the block: base = build the practice, build = deploy under fatigue, peak = rehearse the BR100 dark patches, taper = lock and trust, race week = deploy, don't rehearse. The text lives in `MENTAL_FOCUS` in `ultra_plan.py` and is stored on `training_plan_weeks.mental_focus` (additive migration backfills existing plans).

Surfaced as a **Mental:** line in `ultra today`, `ultra week`, and `TRAINING_PLAN.md`. When coaching a run, tie the athlete's per-run `--mental-intention` back to the week's prescription (prescribed-vs-actual at the week level). After editing prescriptions, re-run `ultra plan --export-md`.

### Race-day mental rehearsal (piece 3)

The **mental race plan** (`ultra race mental`) is the deploy-day counterpart to the crew manual: it maps each Race Day Engine segment to a mental **zone** (launch → settle → grind → dark-patch → deep → closer, by mile fraction), overlays **night** (segments whose ETA lands after sunset) and the peer cohort's high-divergence **danger** segments, and renders a printable per-segment "what you'll likely feel here → what to deploy" sheet with clock ETAs. It paces to the governor via the **same spine as the crew manual** (`race_engine.segment_cumulative_seconds`), so both surfaces put you in the same place at the same time.

All athlete-specific content — mantras, reframes, anchors, the pre-race visualization, and the zone scripts — lives in `backend/data/br100_mental_race_plan.yaml` (hand-editable, like `br100_crew_protocol.yaml`; a second race = a second profile). The tools were **pre-loaded** across the 20-week block (`MENTAL_FOCUS`); race day is **deploy, not rehearse**. After editing the profile, just re-run the command.

```bash
cd /Users/michaelpawlus/dev/projects/workout-tracking
uv run ultra ultra race mental --weather-temp 82              # print the plan
uv run ultra ultra race mental --vault                        # write to vault race/<Race> Mental Race Plan.md
uv run ultra ultra race mental --no-splits                    # engine grade+fade model instead of the 2025 analog
uv run ultra ultra race mental --json                         # structured (zones + per-segment cues)
```

The capstone (`ultra race capstone`) folds a compact mental signal (`signals.mental`: zone arc + dark-patch/night markers + toolkit) into its synthesis order and links the full mental-plan doc, so mental energy is a first-class dimension of the strategy report.

Remaining piece of #9 (still to build): HR-at-pace × mental-state correlation analysis in run reports (piece 4, needs ≥~6 runs of piece-1 data).

## Columbus Marathon (active cycle)

Ten weeks, Aug 10 – Oct 18, 2026, starting 16 days after the BR100 finish. Everything lives in `backend/marathon_plan.py`; the reference doc is `MARATHON_PLAN.md`.

**The coaching thesis — read this before adjusting anything.** The aerobic engine is not the limiter. A 28:00 5K (Mar 12) projects to a ~4:28 marathon while the PR is 4:51, so the gap is threshold and pace discipline, not endurance. Someone this fresh off a 100-miler doesn't need 20-milers to survive 26.2 — they need to rehearse 10:17/mi. The block is therefore **quality-biased and volume-modest**: ~40 mi peak against BR100's 70–80. Resist requests to add volume; add specificity instead.

Structural differences from the ultra plan:

- **Long runs on Sunday**, rehearsing Columbus's race-day timing (BR100 used Saturday).
- **Weeks 1–3 are recovery and diagnostics**, not base building. There is no base to build, only fatigue to clear.
- **Marathon-pace miles inside long runs** are the core stimulus: 4 → 5 → 8 → 4.
- No back-to-backs, night running, or hill blocks. No `race` subgroup — the Race Day Engine is BR100-specific.

**The week-3 decision gate.** The 5K TT on Sat Aug 29 sets the real goal, and `adapt_from_5k_tt()` propagates it through `athlete_targets.marathon_pace` to every remaining MP session:

| TT result | Target |
|---|---|
| ≤ 27:30 | sub-4:30, reach for 4:20 |
| 27:30–28:30 | sub-4:30 |
| 28:30–29:30 | ~4:35–4:40 |
| > 30:00 | ~4:45, execution focus |

Every branch is still a PR over 4:51. Sub-4:00 needs a ~25:01 5K and is explicitly out of scope this cycle — say so plainly if asked.

Benchmarks continue the BR100 numbering (MAF #5, 5K TT #3) so the athlete history reads as unbroken.

```bash
uv run ultra marathon init            # create the plan (--force to rebuild)
uv run ultra marathon today
uv run ultra marathon week 5
uv run ultra marathon submit --distance 15 --duration 155 --hr 148
uv run ultra marathon adapt           # process benchmarks -> retarget the block
uv run ultra marathon plan --export-md  # regenerate MARATHON_PLAN.md
```

## Schedule Adjustments

The markdown (`TRAINING_PLAN.md` for BR100, `MARATHON_PLAN.md` for Columbus) is the reference plan. Small day-to-day shifts (e.g., doing Wednesday's tempo on Thursday) don't need formal tracking — just note them in the run report. When submitting a shifted workout via CLI, use `--scheduled-date` to match the right prescribed workout:

```bash
# "I did Wednesday's tempo on Thursday"
uv run ultra ultra submit --distance 8 --duration 75 --hr 162 \
  --date 2026-03-26 --scheduled-date 2026-03-25
```

To regenerate the plan markdown after target changes:
```bash
uv run ultra marathon plan --export-md   # MARATHON_PLAN.md
uv run ultra ultra plan --export-md      # TRAINING_PLAN.md
```

## CLI Reference

```bash
cd /Users/michaelpawlus/dev/projects/workout-tracking

# List recent Strava activities
uv run ultra ultra strava-import --list --count 5 --json

# Today's prescribed workout
uv run ultra ultra today

# This week's schedule
uv run ultra ultra week

# Set pace targets manually (updates DB + future workouts)
uv run ultra ultra targets --set --tempo 9.25 --easy 10.25 --long-run 10.75

# View current targets
uv run ultra ultra targets --json

# Regenerate TRAINING_PLAN.md from DB with current targets
uv run ultra ultra plan --export-md

# Nutrition guidelines for today's workout
uv run ultra ultra nutrition --json

# Nutrition for a specific distance
uv run ultra ultra nutrition --distance 15 --json

# Skip vault write on submit (debugging / throwaway runs)
uv run ultra ultra submit --distance 4 --duration 40 --hr 138 --no-vault

# Retroactively write the most recent feedback row to the vault
uv run ultra ultra feedback --save

# Retroactively write a specific feedback row (by run_feedback.id)
uv run ultra ultra feedback --save --id 42 --json
```

## Race Day Engine

Generate segment-by-segment race execution plans by combining GPX course data, historical finisher splits, adaptive training targets, and weather. Produces A/B/C pace scenarios, fueling schedules, and printable crew sheets.

### Race Day CLI Reference

```bash
cd /Users/michaelpawlus/dev/projects/workout-tracking

# Load a course from GPX file
uv run ultra ultra race load-course <gpx_file> --name "Burning River 100" --year 2026
uv run ultra ultra race load-course course.gpx --name "BR100" --year 2026 \
  --segment-breaks "5.2,12.8,20.1,31.4,40.2,50.0,62.5,75.3,87.9" --json

# Populate ALL segments at once from an aid-station chart (names + crew/drop-bag).
# Re-derives segments at the real aid-station miles (recomputing elevation from
# the loaded course's GPX) and replaces them in place — no duplicate course row.
# BR100's chart is committed at backend/data/br100_aid_stations_2026.csv.
uv run ultra ultra race load-aid-stations backend/data/br100_aid_stations_2026.csv --dry-run
uv run ultra ultra race load-aid-stations backend/data/br100_aid_stations_2026.csv --json
# CSV columns: mile,name,crew,drop_bag,notes (lines starting with # are ignored).
# Re-pull the participant guide and re-run if mile markers shift year to year.

# View/edit individual course segments (one-off tweaks after a bulk load)
uv run ultra ultra race segments --json
uv run ultra ultra race segments --segment 3 --set-name "Happy Days 1" --crew 1 --drop-bag 1

# Import historical race results from CSV (peer finishers on this course)
uv run ultra ultra race import-results results.csv --year 2025 --json

# Historical analysis of the athlete's OWN prior races at the same distance.
# Extracts late fade / positive split / HR drift / stoppage and feeds the
# lessons into coaching (run reports), programming (training implications),
# and race reports (late-race fade biases the Race Day Engine pace plan).
uv run ultra ultra race history --seed              # seed known prior 100s
uv run ultra ultra race history --json              # analyze all prior races
uv run ultra ultra race history --distance-filter 100   # only same-distance efforts
uv run ultra ultra race history --md                # markdown report (for the vault)
# Add a race manually, optionally enriching from Strava when connected:
uv run ultra ultra race history --add --name "Tunnel Hill 100" --date 2021-11-13 \
  --distance 101.1 --finish 25:23:00 --moving 23:34:00 \
  --first-half 13:03:00 --second-half 14:56:00 --strava-id 6257195830

# Analyze peer cohort (finishers near your goal time)
uv run ultra ultra race cohort --goal-time "24:00:00" --json

# Peer split comparison (issue #14): acquire & learn from BR100 finishers near the target.
# Agent-driven (mirrors aggregate-reports): the CLI emits a RESEARCH ORDER (which results
# to pull, which timing mats to read, the exact CSV schema); the Claude Code session does
# the fetch. Official results/splits are agentic (RunSignup/UltraSignup/RTRT); arbitrary
# athletes' Strava is hybrid (user drops a link/export, agent parses it).
uv run ultra ultra race peer-splits --goal-time "26:00:00" --json       # research order
uv run ultra ultra race peer-splits --skeleton                          # fillable CSV scaffold
# Ingest a filled LONG CSV (one row per runner+timing-mat; elapsed = cumulative HH:MM:SS).
# Sparse mats are mapped to segments and each leg's pace is spread across the segments it
# covers, so cohort analysis sees a full per-segment curve. The 2025 cohort is committed at
# backend/data/br100_2025_cohort_splits.csv (8 finishers near 26h, pulled from the RunSignup API).
uv run ultra ultra race peer-splits --import backend/data/br100_2025_cohort_splits.csv --year 2025 --json
# Render/persist the cohort learnings (back-half fade %, highest-divergence segments, pacing skeleton):
uv run ultra ultra race peer-splits --goal-time "26:00:00" --window 60 --learnings --vault

# Race-report aggregator (issue #15): build a research brief for course/strategy intel.
# The CLI emits the "research order" (sources + queries + output sections); the Claude
# Code session runs the deep research and files the synthesized guide to race-prep/.
uv run ultra ultra race aggregate-reports --json          # structured research brief
uv run ultra ultra race aggregate-reports --skeleton      # fillable markdown scaffold
# After synthesizing, persist the guide to $OBSIDIAN_VAULT_PATH/race-prep/ (stdin or file):
cat guide.md | uv run ultra ultra race aggregate-reports --save-guide - \
  --title "Burning River 100 Course & Strategy Guide" --json
uv run ultra ultra race aggregate-reports --save-guide guide.md --date-prefix  # dated snapshot

# Generate A/B/C race execution plans
uv run ultra ultra race plan --goal-time "24:00:00" --weather-temp 75 --json
uv run ultra ultra race plan --goal-time "24:00:00" --save  # persist to DB

# Per-segment fueling plan
uv run ultra ultra race nutrition --goal-time "24:00:00" --json

# Crew sheet with multi-scenario ETAs
uv run ultra ultra race crew-sheet --goal-time "24:00:00" --output crew_sheet.md

# Full crew MANUAL (issue #12): per crew-stop ETA + fuel + cooling/chafing protocol.
# Paces to the 26h GOVERNOR (from the profile, not the 24h stretch goal) and uses a
# peer-split skeleton (a real finisher scaled to the goal) so ETAs follow the real fade.
# Everything athlete-specific lives in backend/data/br100_crew_protocol.yaml.
uv run ultra ultra race crew-manual --weather-temp 82 --output crew_manual.md
uv run ultra ultra race crew-manual --vault --json          # write into the Obsidian vault
uv run ultra ultra race crew-manual --splits backend/data/br100_2025_analog_splits.csv
uv run ultra ultra race crew-manual --no-splits             # use the engine's grade+fade model
# Defaults: --profile backend/data/br100_crew_protocol.yaml; goal/start from that profile;
# splits from the bundled 2025 analog. Load the BR100 GPX first for grade-aware ETAs.

# Mental race plan (issue #9, piece 3): per-segment dark-patch / rehearsal script.
# Maps each segment to a mental zone (by mile fraction), overlays night (ETA past
# sunset) + the peer cohort's high-divergence segments, and renders a printable
# "what you'll feel → what to deploy" sheet with clock ETAs. Paces to the governor
# via the same spine as the crew manual. Athlete content lives in the YAML profile.
uv run ultra ultra race mental --weather-temp 82
uv run ultra ultra race mental --vault --json           # write into the Obsidian vault (race/)
uv run ultra ultra race mental --no-splits              # engine grade+fade model, not the 2025 analog
# Defaults: --profile backend/data/br100_mental_race_plan.yaml; goal/start/sunset from it;
# ETAs from the bundled 2025 analog skeleton. Load the BR100 GPX + aid stations first.

# Capstone strategy report (issue #16): the meta-synthesis everything feeds into.
# Agent-driven (mirrors aggregate-reports/peer-splits): the CLI gathers EVERY internal
# signal — adaptive targets, own-history fade, the 26h peer cohort, the A/B/C plan, the
# per-segment fueling math, crew/drop-bag flags, and the training block — into one JSON
# "synthesis order"; the Claude Code session writes/updates the comprehensive strategy
# report and files it to race-prep/. It is a LIVING document: re-run after each new long
# run and it updates the SAME vault file in place (stable filename + Revision Log).
uv run ultra ultra race capstone --json                 # the synthesis dossier
uv run ultra ultra race capstone --weather-temp 82      # human-readable signal inventory
uv run ultra ultra race capstone --skeleton             # fillable section scaffold
# After synthesizing, persist (stable filename = living doc; re-running updates in place):
cat report.md | uv run ultra ultra race capstone --save-guide - --json
uv run ultra ultra race capstone --save-guide report.md --date-prefix   # dated snapshot
# Defaults: goal 26:00:00 governor, start 04:00, title "Burning River 100 Race Strategy".
# When the report already exists, the dossier's method flips to "update in place" and asks
# you to append a dated Revision Log entry noting what new data moved which numbers.

# Live race tracking
uv run ultra ultra race checkin --station "Happy Days 2" --time "9:15:00" --json
uv run ultra ultra race status --json
```
