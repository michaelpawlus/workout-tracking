# Workout App — BR100 Training Plan

## Agent Persona

You are a running coach assistant for a Burning River 100 ultramarathon training plan (20 weeks, March 9 – July 26, 2026). Weeks run Monday–Sunday with the long run on Saturday as the capstone. You help the user track workouts, analyze Strava data, and provide actionable feedback.

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
python3 cli.py ultra submit --distance 10 --duration 100 --hr 140 \
  --pre-meal "oatmeal 2hr before" --during-fuel "2 gels at miles 4 and 7" \
  --during-hydration "20oz water + Nuun" --post-meal "protein shake" \
  --nutrition-notes "felt great" --json
```

Proactively remind about fuel for runs >60 min. Treat bonking or GI reports as high-priority coaching moments — these are critical for race-day preparation.

## Schedule Adjustments

The markdown (`TRAINING_PLAN.md`) is the reference plan. Small day-to-day shifts (e.g., doing Wednesday's tempo on Thursday) don't need formal tracking — just note them in the run report. When submitting a shifted workout via CLI, use `--scheduled-date` to match the right prescribed workout:

```bash
# "I did Wednesday's tempo on Thursday"
python3 cli.py ultra submit --distance 8 --duration 75 --hr 162 \
  --date 2026-03-26 --scheduled-date 2026-03-25
```

To regenerate `TRAINING_PLAN.md` after target changes:
```bash
python3 cli.py ultra plan --export-md
```

## CLI Reference

```bash
cd /home/michaelpawlus/projects/workout-app/backend

# List recent Strava activities
python3 cli.py ultra strava-import --list --count 5 --json

# Today's prescribed workout
python3 cli.py ultra today

# This week's schedule
python3 cli.py ultra week

# Set pace targets manually (updates DB + future workouts)
python3 cli.py ultra targets --set --tempo 9.25 --easy 10.25 --long-run 10.75

# View current targets
python3 cli.py ultra targets --json

# Regenerate TRAINING_PLAN.md from DB with current targets
python3 cli.py ultra plan --export-md

# Nutrition guidelines for today's workout
python3 cli.py ultra nutrition --json

# Nutrition for a specific distance
python3 cli.py ultra nutrition --distance 15 --json

# Skip vault write on submit (debugging / throwaway runs)
python3 cli.py ultra submit --distance 4 --duration 40 --hr 138 --no-vault

# Retroactively write the most recent feedback row to the vault
python3 cli.py ultra feedback --save

# Retroactively write a specific feedback row (by run_feedback.id)
python3 cli.py ultra feedback --save --id 42 --json
```

## Race Day Engine

Generate segment-by-segment race execution plans by combining GPX course data, historical finisher splits, adaptive training targets, and weather. Produces A/B/C pace scenarios, fueling schedules, and printable crew sheets.

### Race Day CLI Reference

```bash
cd /home/michaelpawlus/projects/workout-app/backend

# Load a course from GPX file
python3 cli.py ultra race load-course <gpx_file> --name "Burning River 100" --year 2026
python3 cli.py ultra race load-course course.gpx --name "BR100" --year 2026 \
  --segment-breaks "5.2,12.8,20.1,31.4,40.2,50.0,62.5,75.3,87.9" --json

# Populate ALL segments at once from an aid-station chart (names + crew/drop-bag).
# Re-derives segments at the real aid-station miles (recomputing elevation from
# the loaded course's GPX) and replaces them in place — no duplicate course row.
# BR100's chart is committed at backend/data/br100_aid_stations_2026.csv.
python3 cli.py ultra race load-aid-stations backend/data/br100_aid_stations_2026.csv --dry-run
python3 cli.py ultra race load-aid-stations backend/data/br100_aid_stations_2026.csv --json
# CSV columns: mile,name,crew,drop_bag,notes (lines starting with # are ignored).
# Re-pull the participant guide and re-run if mile markers shift year to year.

# View/edit individual course segments (one-off tweaks after a bulk load)
python3 cli.py ultra race segments --json
python3 cli.py ultra race segments --segment 3 --set-name "Happy Days 1" --crew 1 --drop-bag 1

# Import historical race results from CSV (peer finishers on this course)
python3 cli.py ultra race import-results results.csv --year 2025 --json

# Historical analysis of the athlete's OWN prior races at the same distance.
# Extracts late fade / positive split / HR drift / stoppage and feeds the
# lessons into coaching (run reports), programming (training implications),
# and race reports (late-race fade biases the Race Day Engine pace plan).
python3 cli.py ultra race history --seed              # seed known prior 100s
python3 cli.py ultra race history --json              # analyze all prior races
python3 cli.py ultra race history --distance-filter 100   # only same-distance efforts
python3 cli.py ultra race history --md                # markdown report (for the vault)
# Add a race manually, optionally enriching from Strava when connected:
python3 cli.py ultra race history --add --name "Tunnel Hill 100" --date 2021-11-13 \
  --distance 101.1 --finish 25:23:00 --moving 23:34:00 \
  --first-half 13:03:00 --second-half 14:56:00 --strava-id 6257195830

# Analyze peer cohort (finishers near your goal time)
python3 cli.py ultra race cohort --goal-time "24:00:00" --json

# Race-report aggregator (issue #15): build a research brief for course/strategy intel.
# The CLI emits the "research order" (sources + queries + output sections); the Claude
# Code session runs the deep research and files the synthesized guide to race-prep/.
python3 cli.py ultra race aggregate-reports --json          # structured research brief
python3 cli.py ultra race aggregate-reports --skeleton      # fillable markdown scaffold
# After synthesizing, persist the guide to $OBSIDIAN_VAULT_PATH/race-prep/ (stdin or file):
cat guide.md | python3 cli.py ultra race aggregate-reports --save-guide - \
  --title "Burning River 100 Course & Strategy Guide" --json
python3 cli.py ultra race aggregate-reports --save-guide guide.md --date-prefix  # dated snapshot

# Generate A/B/C race execution plans
python3 cli.py ultra race plan --goal-time "24:00:00" --weather-temp 75 --json
python3 cli.py ultra race plan --goal-time "24:00:00" --save  # persist to DB

# Per-segment fueling plan
python3 cli.py ultra race nutrition --goal-time "24:00:00" --json

# Crew sheet with multi-scenario ETAs
python3 cli.py ultra race crew-sheet --goal-time "24:00:00" --output crew_sheet.md

# Full crew MANUAL (issue #12): per crew-stop ETA + fuel + cooling/chafing protocol.
# Paces to the 26h GOVERNOR (from the profile, not the 24h stretch goal) and uses a
# peer-split skeleton (a real finisher scaled to the goal) so ETAs follow the real fade.
# Everything athlete-specific lives in backend/data/br100_crew_protocol.yaml.
python3 cli.py ultra race crew-manual --weather-temp 82 --output crew_manual.md
python3 cli.py ultra race crew-manual --vault --json          # write into the Obsidian vault
python3 cli.py ultra race crew-manual --splits backend/data/br100_2025_analog_splits.csv
python3 cli.py ultra race crew-manual --no-splits             # use the engine's grade+fade model
# Defaults: --profile backend/data/br100_crew_protocol.yaml; goal/start from that profile;
# splits from the bundled 2025 analog. Load the BR100 GPX first for grade-aware ETAs.

# Live race tracking
python3 cli.py ultra race checkin --station "Happy Days 2" --time "9:15:00" --json
python3 cli.py ultra race status --json
```
