# ultra

A command-line ultramarathon coach. Plans 20 weeks of training, learns from every run, and writes the system of record to Obsidian.

```bash
pip install -e .
```

## What this is

`ultra` is the CLI that drives my Burning River 100 training (March 9 – July 26, 2026). The whole training program — prescribed workouts, actual runs, race-day plan, course data — lives in a small SQLite database and a folder of markdown notes in an Obsidian vault. An AI-coaching loop reads recent runs, weights them against the plan, and proposes adjustments to pace targets and the next microcycle.

The CLI is the front door. There is no app to open. `ultra ultra today` tells me what to run; `ultra ultra submit ...` logs it and writes a structured report to the vault; `ultra ultra adapt` revises the plan when reality has drifted from prescription.

This repo is also a portfolio artifact for an AI-native coaching product. The architecture — narrow CLI surface, durable file-based store, LLM as a reasoning layer over structured data — is the case I'd make for what adaptive coaching software should look like.

## 30-second walkthrough

A Wednesday in the app:

```text
$ ultra ultra today
Wed 2026-05-13 — Week 10/20 · Mid-week tempo
  Distance:  8.0 mi
  Pace:      9:15 / mi (tempo)
  HR target: 155-168
  Fuel:      1 gel @ mi 4, 600ml Flash IV
```

Run the workout. Back at the desk:

```text
$ ultra ultra submit --distance 8.2 --duration 76 --hr 161 \
    --during-fuel "1 NeverSecond C30 at mi 4" \
    --during-hydration "600ml Flash IV" \
    --notes "legs heavy first 2mi, settled in by mi 4"

✓ Run logged (id 87)
✓ Feedback generated
✓ Wrote workouts/2026-05-13 Tempo Mid-Cycle Grind.md to vault
✓ Appended stub to workouts/PRODUCT_LOG.md
```

Friday night, after a string of runs that came in hotter than prescribed:

```text
$ ultra ultra adapt
Reviewing last 10 days (7 runs)…
  Avg tempo HR drift: +4 bpm vs target band
  Easy pace running: 9:48/mi (target 10:15/mi) — too fast
Proposed changes:
  • Bump tempo pace 9:15 → 9:05/mi (+1 step)
  • Hold easy pace; add HR ceiling reminder to today's view
Apply? [y/N]
```

## Install

```bash
git clone https://github.com/michaelpawlus/workout-app
cd workout-app
python3 -m venv venv && source venv/bin/activate
pip install -e .
ultra ultra init                  # seed the 20-week plan
```

Requires Python 3.10+. Strava and intervals.icu integrations are optional — see [Device sync](#device-sync).

## Contents

1. [Daily workflow](#daily-workflow)
2. [Command map](#command-map)
3. [Planning](#planning) · [Logging a run](#logging-a-run) · [Progress & data](#progress--data)
4. [Strava sync](#strava-sync) · [intervals.icu / Garmin](#intervalsicu--garmin)
5. [Race day](#race-day)
6. [Export](#export) · [Gym](#gym)
7. [Architecture](#architecture)
8. [Why this exists](#why-this-exists)

## Daily workflow

Four commands handle 90% of the day-to-day. Everything else is configuration, sync, or race-day planning.

**`ultra ultra today`** — print the day's prescribed workout from the active plan.

```bash
ultra ultra today
ultra ultra today --json    # machine-readable
```

**`ultra ultra submit`** — log a run. Writes the SQLite row, generates AI feedback, and renders a markdown report to `$OBSIDIAN_VAULT_PATH/workouts/`.

```bash
ultra ultra submit --distance 8.2 --duration 76 --hr 161 \
  --during-fuel "1 gel at mi 4" --during-hydration "600ml Flash IV"
```

**`ultra ultra feedback`** — recall the coaching feedback for recent runs, or re-render an existing row to the vault.

```bash
ultra ultra feedback                  # most recent
ultra ultra feedback --save --id 42   # retroactively write row 42 to the vault
```

**`ultra ultra progress`** — week-over-week mileage, HR drift, completion %, time-to-race.

```bash
ultra ultra progress
ultra ultra progress --json
```

## Command map

```
DAILY            ▶  ultra ultra today              # what's on tap
                    ultra ultra submit ...         # log the run
                    ultra ultra feedback ...       # recall coaching
                    ultra ultra progress           # how the block is going

PLAN MANAGEMENT  ▶  init  week  upcoming  targets  adapt  nutrition  plan

PROGRESS         ▶  progress  benchmarks

DEVICE SYNC      ▶  strava-{connect, status, import}
                    icu-push  export-fit

RACE DAY         ▶  race {load-course, cohort, plan, nutrition,
                          crew-sheet, checkin, status, segments,
                          import-results, history}

EXPORT           ▶  plan --export-md

GYM              ▶  ultra gym {log, pr, suggest, history, exercises}
```

## Command reference

Every command in `backend/cli.py` appears in exactly one block below. Examples use the actual binary form: top-level entry point is `ultra`, with two subgroups — `ultra ultra ...` for BR100 and `ultra gym ...` for strength work.

<details>
<summary><b>Planning</b> · init  today  week  upcoming  targets  adapt</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra init` | Seed the 20-week BR100 plan into the database. Idempotent — safe to re-run. | `ultra ultra init` |
| `ultra ultra today` | Show today's prescribed workout from the active plan. | `ultra ultra today --json` |
| `ultra ultra week` | Show the full Monday–Sunday week view with prescriptions and actuals. | `ultra ultra week` |
| `ultra ultra upcoming` | Show the next N days of prescribed workouts. | `ultra ultra upcoming --days 10` |
| `ultra ultra targets` | Show or set pace and HR targets. Setting targets updates future workouts in place. | `ultra ultra targets --set --tempo 9.25 --easy 10.25 --long-run 10.75` |
| `ultra ultra adapt` | Read the last block of runs and propose plan adjustments (pace targets, next microcycle). | `ultra ultra adapt` |

</details>

<details>
<summary><b>Logging a run</b> · submit  feedback</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra submit` | Log a run with distance/duration/HR/nutrition. Writes the DB row, generates AI feedback, and writes a structured report to the Obsidian vault. Use `--no-vault` to skip the vault write. Use `--scheduled-date` when the run was a shifted version of a different day's prescription. | `ultra ultra submit --distance 8.2 --duration 76 --hr 161 --during-fuel "1 C30 gel at mi 4"` |
| `ultra ultra feedback` | Show the coaching feedback for recent runs. With `--save`, render a feedback row to the vault retroactively (use `--id` to target a specific row). | `ultra ultra feedback --save` |

</details>

<details>
<summary><b>Progress & data</b> · progress  benchmarks  nutrition</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra progress` | Overall progress dashboard: mileage, HR drift, completion %, time-to-race. | `ultra ultra progress --json` |
| `ultra ultra benchmarks` | Show the benchmark schedule (MAF test, time trials, race rehearsals) and recorded results. | `ultra ultra benchmarks` |
| `ultra ultra nutrition` | Nutrition guidelines for today's workout or a specific distance. Carb/sodium targets per hour, suggested fuel split. | `ultra ultra nutrition --distance 15 --json` |

</details>

<details>
<summary><b>Strava sync</b> · strava-connect  strava-status  strava-import</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra strava-connect` | Seed Strava OAuth tokens (one-time setup). | `ultra ultra strava-connect` |
| `ultra ultra strava-status` | Check Strava connection health and token expiry. | `ultra ultra strava-status` |
| `ultra ultra strava-import` | List or import recent Strava runs. `--list` is the safe read-only mode used to fetch activity metadata before deciding what to submit. | `ultra ultra strava-import --list --count 5 --json` |

</details>

<details>
<summary><b>intervals.icu / Garmin</b> · icu-push  export-fit</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra icu-push` | Push prescribed workouts to intervals.icu, which syncs them to Coros / Garmin. Supports `--as-date` and `--no-replace` for controlled re-pushes. | `ultra ultra icu-push --week` |
| `ultra ultra export-fit` | Export FIT workout files (Coros-compatible) for offline transfer. | `ultra ultra export-fit --days 7` |

</details>

<details>
<summary><b>Race day</b> · load-course  import-results  history  cohort  plan  nutrition  crew-sheet  checkin  status  segments</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra race load-course` | Load a race course from a GPX file. Optionally pass `--segment-breaks` to define aid-station mile markers. | `ultra ultra race load-course course.gpx --name "BR100" --year 2026` |
| `ultra ultra race import-results` | Import historical finisher splits from CSV for peer-cohort analysis. | `ultra ultra race import-results results.csv --year 2025` |
| `ultra ultra race history` | Ingest & analyze the athlete's *own* prior races at the same distance (late fade, positive split, HR drift, stoppage). Feeds coaching, training implications, and the Race Day Engine's late-race fade. `--seed`, `--add`, `--json`, `--md`. | `ultra ultra race history --seed && ultra ultra race history --distance-filter 100` |
| `ultra ultra race cohort` | Build a peer cohort of historical finishers near a goal time. | `ultra ultra race cohort --goal-time 24:00:00 --json` |
| `ultra ultra race plan` | Generate A / B / C race execution plans (pace + fueling) given a goal time and weather. `--save` persists to the DB. | `ultra ultra race plan --goal-time 24:00:00 --weather-temp 75 --save` |
| `ultra ultra race nutrition` | Per-segment fueling plan (carbs/hr, sodium/hr, what goes in each drop bag). | `ultra ultra race nutrition --goal-time 24:00:00` |
| `ultra ultra race crew-sheet` | Printable crew sheet with multi-scenario ETAs, aid-station notes, and drop-bag contents. | `ultra ultra race crew-sheet --goal-time 24:00:00 --output crew_sheet.md` |
| `ultra ultra race checkin` | Log arrival at an aid station during the race. | `ultra ultra race checkin --station "Happy Days 2" --time 9:15:00` |
| `ultra ultra race status` | Show current race status vs plan (ahead / behind, projected finish). | `ultra ultra race status --json` |
| `ultra ultra race segments` | View course segments. Edit aid-station names, crew access, drop-bag flags. | `ultra ultra race segments --segment 3 --set-name "Happy Days 1" --crew 1` |

</details>

<details>
<summary><b>Export</b> · plan</summary>

| Command | What it does | Example |
|---|---|---|
| `ultra ultra plan` | Manage the plan as a unit. `--export-md` regenerates `TRAINING_PLAN.md` from the current DB state and pace targets. | `ultra ultra plan --export-md` |

</details>

<details>
<summary><b>Gym</b> · log  pr  suggest  history  exercises</summary>

The gym subgroup tracks strength sessions in parallel with run training. Lower priority for the BR100 block, but the surface exists.

| Command | What it does | Example |
|---|---|---|
| `ultra gym log` | Log a strength workout. | `ultra gym log` |
| `ultra gym pr` | View or manage personal records by lift. | `ultra gym pr` |
| `ultra gym suggest` | Generate a session with weight suggestions based on recent PRs. | `ultra gym suggest` |
| `ultra gym history` | List past gym sessions. | `ultra gym history` |
| `ultra gym exercises` | Print the available exercise catalog. | `ultra gym exercises` |

</details>

## Architecture

The data store is a single SQLite file (`backend/workouts.db`) holding the prescribed plan, every logged run, AI-generated feedback rows, nutrition events, and race-day course / segment / checkin data. Markdown is the human-facing surface: every `submit` writes a structured report into the configured Obsidian vault (`$OBSIDIAN_VAULT_PATH/workouts/`), and `plan --export-md` re-renders `TRAINING_PLAN.md` from the DB whenever targets change. Strava and intervals.icu live behind thin adapter modules (`backend/strava.py`, `backend/intervals_icu.py`); the LLM coaching layer is a single module (`backend/llm.py`) that reads recent rows and writes feedback / adapted targets back through the same DB. A `frontend/` React app exists from an earlier iteration but is deprecated — the CLI is the front door.

## Why this exists

Off-the-shelf training apps stop being useful the moment a real training block diverges from the prescribed plan — and a 20-week ultramarathon build is almost entirely divergence. `ultra` is the version of that product I wanted: a small, durable CLI that treats my actual runs, nutrition, and race-day intel as primary data and uses an LLM to do the thing a static plan can't — read the last two weeks, notice that I've been running easy days too hot, and adjust. Beyond the personal use case, this repo is a working sketch of what AI-native endurance coaching looks like when the coach has continuous access to structured workout data instead of a once-a-week conversation.
