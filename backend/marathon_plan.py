"""Static 10-week Columbus Marathon training plan (Aug 10 - Oct 18, 2026).

Mirrors ultra_plan.py in shape — same tables, same week/day/benchmark structure —
but the training logic is deliberately different, because the athlete arrives here
in an unusual state: 20 weeks of ultra training plus a 100-mile finish on Jul 25,
2026, and only ~10 weeks until race day.

The design thesis:

  The aerobic engine is NOT the limiter; marathon-specific pace is. A 28:00 5K
  (Mar 12, 2026) projects to a ~4:28 marathon, yet the standing PR is 4:51. That
  gap is threshold and pace discipline, not endurance. So this block is
  quality-biased and volume-modest — ~40 mi peak against a 70-80 mi BR100 peak.
  Someone who just ran 100 miles does not need 20-milers to survive 26.2; they
  need to rehearse holding 10:17/mi.

Consequences of that thesis, all of which differ from the ultra plan:

  * Long runs move to SUNDAY to rehearse Columbus's race day (ultra plan: Saturday).
  * Weeks 1-3 are recovery and diagnostics, not base building. There is no base to
    build — there is fatigue to clear. The week-3 time trial is the real start.
  * Marathon-pace ("MP") miles inside long runs are the core stimulus, progressing
    4 -> 5 -> 8 -> 4 across the block.
  * No back-to-back long weekends, no night running, no hill-repeat blocks.
"""

from datetime import datetime, timedelta

from .adapt import (
    apply_targets_to_future_workouts,
    get_current_targets,
    seed_initial_targets,
)
from .ultra_plan import _fmt_pace  # shared pace formatter; identical semantics

RACE_NAME = "Columbus Marathon"
RACE_DATE = "2026-10-18"
PLAN_START = "2026-08-10"  # Monday
TOTAL_WEEKS = 10

# --- Canonical race goal (single source of truth: training_plans.goal) ---
# Sub-4:30 is the governor, and it is not a stretch: it sits almost exactly at the
# Riegel equivalent of the athlete's existing 28:00 5K. Sub-4:00 would require a
# ~25:01 5K — a three-minute improvement in ten weeks starting from a 100-miler —
# so it is explicitly out of scope for this cycle rather than listed as a stretch.
RACE_GOAL = "Sub-4:30 (10:17/mi); PR is 4:51"
RACE_GOAL_NOTES = (
    "10-week plan for the Columbus Marathon (Oct 18, 2026), starting 16 days after "
    "a 29:00 Burning River 100 finish. Quality-biased and volume-modest: the aerobic "
    "base is already deep, so the block targets marathon-specific pace. Sub-4:30 is "
    "the governor; the week-3 5K time trial confirms or recalibrates it."
)

# Provisional goal pace until the week-3 time trial replaces it (4:30:00 / 26.2188).
# Seeded into athlete_targets.marathon_pace so MP workouts have a number on day one;
# adapt_from_5k_tt() overwrites it and retargets every remaining MP session.
PROVISIONAL_MARATHON_PACE = 10.30

# Week definitions: (week_num, week_type, target_miles_low, target_miles_high, focus)
WEEKS = [
    (1,  "recovery", 16, 20, "Post-BR100 recovery. All easy. MAF Test #5"),
    (2,  "base",     26, 30, "Aerobic rebuild. First turnover since the 100"),
    (3,  "base",     28, 32, "5K Time Trial Sat — sets every target for the block"),
    (4,  "build",    32, 36, "Threshold reintroduced. 3x1mi"),
    (5,  "build",    36, 40, "Marathon pace enters the long run"),
    (6,  "peak",     29, 33, "Air Force Half Sat — tune-up race, 4 weeks out"),
    (7,  "peak",     33, 37, "Absorb the race, then the longest steady long run"),
    (8,  "peak",     38, 42, "Key session: 20mi with 8 @ MP"),
    (9,  "taper",    24, 28, "Taper. Sharpen, don't build"),
    (10, "race",     12, 40, "Race week. Oct 18: Columbus Marathon"),
]

# Weekly mental-training prescriptions, carrying forward the peer-dimension treatment
# from the BR100 block (issue #9). The marathon arc is a different problem from the
# ultra arc: an ultra is about managing lows, a marathon is about *sustaining a high*.
# For this athlete specifically the deep aerobic base is a trap — marathon pace will
# feel easy at mile 3, and the discipline is refusing to bank time.
MENTAL_FOCUS = {
    1:  "Recovery is training. Practice sitting with 'I should be doing more' — post-100 restlessness is the first discipline of this block.",
    2:  "Rebuild the turnover feel. On strides, focus on quick, light contact — remember what fast feels like after 20 weeks of ultra shuffle.",
    3:  "Time-trial nerves. Rehearse the pre-TT routine you'll reuse on race morning: calm breathing, one cue word, no clock-watching before the gun.",
    4:  "Name the new discomfort. Threshold burn is not ultra fatigue — it's sharp, and it's supposed to be. Label it: 'this is the right kind of hard.'",
    5:  "Marathon pace is a feeling, not a number. On the MP miles, look away from the watch for a minute and learn the effort from the inside.",
    6:  "Race-day rehearsal, and the one honest chance to practice pushing. Run the half with your full marathon routine — then in the last 5K, deliberately stay in it when it starts to hurt. That is the exact thing BR100 said you back away from.",
    7:  "Patience under a big base. Your engine will make MP feel easy at mile 3 of the long run. Refusing to bank time is the discipline — 'easy now is the plan working.'",
    8:  "The 20-mile conversation. In the last MP miles, practice the exact self-talk you'll need at Columbus mile 22. Write down what worked.",
    9:  "Trust the taper. Reduced volume will feel like losing fitness. It isn't. Decide now what you'll say when that doubt shows up.",
    10: "Deploy, don't rehearse. The first 10K should feel too easy — that's the plan working. One mile at a time; the race starts at 20.",
}

# The tune-up race. A real entry on the calendar rather than a generic placeholder,
# because its date drives the week 6/7 structure: the half absorbs week 6's long run
# and the 18-miler shifts to week 7, which keeps 15 days between the race and the
# block's key session (week 8's 20mi w/ 8 @ MP) instead of the 8 a Sep 26 race allowed.
TUNE_UP_RACE = {
    "week": 6,
    "day_offset": 5,                      # Saturday
    "name": "Air Force Half Marathon",
    "where": "Wright-Patterson AFB, Dayton",
    "start_time": "7:15 a.m.",
    "miles": 13.1,
}

# Benchmark schedule: (week_num, name, type, day_offset_from_Monday)
# 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
# Numbering continues the BR100 series (MAF tests #1-#4, 5K TTs #1-#2) so the
# adaptive engine and the vault notes read as one unbroken athlete history.
BENCHMARKS = [
    (1, "MAF Test #5",            "maf_test",       2),  # Wed Aug 12
    (3, "5K Time Trial #3",       "time_trial",     5),  # Sat Aug 29 — the decision gate
    (TUNE_UP_RACE["week"], TUNE_UP_RACE["name"], "race",
     TUNE_UP_RACE["day_offset"]),                        # Sat Sep 19 — 4 weeks out
    (8, "Marathon-Pace Long Run", "endurance_test", 6),  # Sun Oct 4 — key session
]

# Sunday distance by week. Week 6's Sunday is a recovery jog — its long effort was
# Saturday's tune-up race. Week 10's Sunday is the marathon.
LONG_RUNS = {
    1: 8, 2: 10, 3: 11, 4: 13, 5: 15,
    6: 4, 7: 18, 8: 20, 9: 12, 10: 26.2,
}

# Marathon-pace miles embedded in that week's long run. This is the core stimulus
# of the block — everything else exists to let these miles happen well. Week 6 has
# none: the race is the stimulus that week.
MP_IN_LONG_RUN = {5: 4, 7: 5, 8: 8, 9: 4}


def _week_day_map(start_date):
    """Map workout roles to calendar dates. Week starts Monday, ends Sunday.

    Long run lands on SUNDAY — Columbus is a Sunday race, so every long run is a
    dress rehearsal for race-day timing. (The BR100 plan puts it on Saturday.)
    """
    return {
        "rest":      start_date,                        # Monday
        "easy_1":    start_date + timedelta(days=1),    # Tuesday
        "quality":   start_date + timedelta(days=2),    # Wednesday
        "easy_2":    start_date + timedelta(days=3),    # Thursday
        "shakeout":  start_date + timedelta(days=4),    # Friday
        "pre_long":  start_date + timedelta(days=5),    # Saturday
        "long_run":  start_date + timedelta(days=6),    # Sunday
    }


def _easy_distance(week_type, day):
    base = {
        "recovery": 4, "base": 5, "build": 6, "peak": 6, "taper": 4, "race": 3,
    }.get(week_type, 5)
    if day == "thu":
        base -= 1
    return max(3, base)


def _quality_session(week_num):
    """Wednesday quality session. Weeks 1-3 stay deliberately soft: the athlete is
    16-30 days off a 100-miler and connective-tissue recovery lags how the legs feel."""
    sessions = {
        1: {"workout_type": "benchmark", "title": "MAF Test #5",
            "description": ("30 min at HR ~137 bpm on flat ground after a 10 min easy warmup. "
                            "Record distance. Compare to BR100-block tests #1-#4 (11:38 → 11:23 → "
                            "11:17/mi) — this reads post-100 aerobic retention, not fitness gain. "
                            "Do not chase the pace; hold the HR."),
            "target_duration_minutes": 30, "target_hr_zone": "MAF (137 bpm)",
            "intensity": "easy", "is_benchmark": True},
        2: {"workout_type": "easy_run", "title": "5mi Easy + 6 Strides",
            "description": ("5mi easy with 6x100m strides at the end. First real turnover since "
                            "the 100. Strides are form work, not speed work — relaxed and quick, "
                            "full recovery between."),
            "target_distance_miles": 5, "intensity": "easy"},
        3: {"workout_type": "easy_run", "title": "4mi Easy + 4 Strides",
            "description": ("Deliberately light — the 5K time trial is Saturday and it sets every "
                            "target for the next seven weeks. Arrive fresh."),
            "target_distance_miles": 4, "intensity": "easy"},
        4: {"workout_type": "tempo", "title": "Threshold: 3x1mi",
            "description": ("2mi warmup, 3x1mi at threshold with 2min jog recovery, 1mi cooldown. "
                            "First hard session of the block. Threshold burn is sharp and localised "
                            "— a different sensation from ultra fatigue. Expect it."),
            "target_distance_miles": 8, "intensity": "threshold"},
        5: {"workout_type": "tempo", "title": "Tempo: 4mi Continuous",
            "description": "2mi warmup, 4mi continuous at tempo, 1mi cooldown.",
            "target_distance_miles": 7, "intensity": "threshold"},
        6: {"workout_type": "easy_run", "title": "4mi Easy + 4 Strides",
            "description": ("Race week for the tune-up half. 4mi easy with 4x100m strides — enough "
                            "to stay sharp, nothing that costs anything on Saturday. The cruise "
                            "intervals that normally live here come out; the race is the workout."),
            "target_distance_miles": 4, "intensity": "easy"},
        7: {"workout_type": "easy_run", "title": "4mi Easy",
            "description": ("Four days after a raced half — all easy, no quality. Sunday's 18 with "
                            "5 at marathon pace is the week's only hard effort, and it wants fresh "
                            "legs more than this session wants to exist."),
            "target_distance_miles": 4, "intensity": "easy"},
        8: {"workout_type": "tempo", "title": "Tempo: 5mi",
            "description": ("2mi warmup, 5mi at tempo, 1mi cooldown. Last big midweek effort — "
                            "Sunday's 20 with 8 @ MP is the priority, so hold something back."),
            "target_distance_miles": 8, "intensity": "threshold"},
        9: {"workout_type": "marathon_pace", "title": "MP Sharpener: 3mi",
            "description": ("2mi warmup, 3mi at goal marathon pace, 1mi cooldown. Taper work: "
                            "enough to stay sharp, not enough to cost anything."),
            "target_distance_miles": 6, "intensity": "moderate"},
        10: {"workout_type": "easy_run", "title": "3mi Easy + 4 Strides",
             "description": ("Race week. 3mi very easy with 4x100m strides. Run in race-day shoes "
                             "and kit — final gear check."),
             "target_distance_miles": 3, "intensity": "easy"},
    }
    return sessions.get(week_num, {
        "workout_type": "easy_run", "title": "5mi Easy",
        "description": "Easy run.", "target_distance_miles": 5, "intensity": "easy",
    })


def _long_run_description(week_num, distance, mp_miles):
    if week_num == 10:
        return ("RACE DAY — Columbus Marathon. Goal pace throughout. The first 10K should feel "
                "too easy; that is the plan working, not a reason to speed up. Fuel every 30-40 "
                "min from the start. The race starts at mile 20.")
    if week_num == TUNE_UP_RACE["week"]:
        return (f"{distance}mi very easy, the morning after the tune-up half. Loosen the legs and "
                f"nothing more — no pace, no strides. Skip it entirely if anything hurts.")
    if mp_miles:
        return (f"{distance}mi long run with {mp_miles}mi at goal marathon pace in the back half. "
                f"Warm up easy, settle into MP once you're loose, finish easy. The MP miles are "
                f"the point of the session — run them on tired legs, exactly as they'll come at "
                f"Columbus. Practice race-day fueling throughout.")
    if distance >= 16:
        return (f"{distance}mi steady long run. Conversational effort, no marathon-pace work. "
                f"Practice race-day fueling (~200 cal/hr after 60 min) and race-morning timing.")
    return (f"{distance}mi long run at easy/long-run pace. Rebuilding, not testing. "
            f"Conversational the whole way.")


def _rest_or_cross(date, week_type):
    if week_type in ("recovery", "taper", "race"):
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "rest",
            "title": "Rest Day",
            "description": "Full rest. Walk, foam roll, stretch.",
            "intensity": "easy",
        }
    return {
        "scheduled_date": date.strftime("%Y-%m-%d"),
        "workout_type": "cross_train",
        "title": "Rest / Cross-Train",
        "description": "Rest or light cross-training (yoga, swimming, cycling). No running.",
        "intensity": "easy",
    }


def _friday(date, week_type):
    """Friday is rest in every week — Saturday carries the pre-long-run shakeout."""
    return {
        "scheduled_date": date.strftime("%Y-%m-%d"),
        "workout_type": "rest",
        "title": "Rest Day",
        "description": "Rest. Stretch, foam roll, hydrate — Sunday is the long run.",
        "intensity": "easy",
    }


def _saturday(date, week_num, week_type):
    if week_num == 3:
        # The decision gate. Every target for the remaining seven weeks is derived
        # from this number, which is why weeks 1-3 exist at all.
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "benchmark",
            "title": "5K Time Trial #3",
            "description": (
                "1.5mi easy warmup + strides, then 5K all-out on flat ground, then 1.5mi "
                "cooldown. Same course and conditions as the Mar 12 baseline (28:00) if you "
                "can manage it. THIS SETS THE GOAL:  <=27:30 -> sub-4:30 with room to reach "
                "for 4:20 | 27:30-28:30 -> sub-4:30 | 28:30-29:30 -> recalibrate to ~4:35-4:40 "
                "| >30:00 -> ~4:45 and focus on execution. Every branch is still a PR over 4:51. "
                "Run it honestly — a soft number here mis-sets seven weeks of training."
            ),
            "target_distance_miles": 6,
            "intensity": "hard",
            "is_benchmark": True,
        }
    if week_num == TUNE_UP_RACE["week"]:
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "race",
            "title": TUNE_UP_RACE["name"],
            "description": (
                f"TUNE-UP RACE — {TUNE_UP_RACE['where']}, {TUNE_UP_RACE['start_time']} start. "
                "Race it honestly; do not run it at marathon pace. You already have the 5K for "
                "targeting — what this buys is an updated prediction and, more importantly, "
                "practice at sustaining discomfort with nowhere to hide. Full race-day rehearsal: "
                "same breakfast, same kit, same first-mile restraint. Conversion: 2:09:30 projects "
                "to a 4:30 marathon, 2:15 to 4:41, 2:20 to 4:52. Result re-sets every target."
            ),
            "target_distance_miles": TUNE_UP_RACE["miles"],
            "intensity": "hard",
            "is_benchmark": True,
        }
    if week_num == 10:
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "rest",
            "title": "Rest — Race Eve",
            "description": ("Full rest. Expo, packet pickup, feet up. Lay out kit tonight. "
                            "Nothing you do today makes you faster; plenty could make you slower."),
            "intensity": "easy",
        }
    if week_type in ("recovery", "taper"):
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "rest",
            "title": "Rest Day",
            "description": "Rest ahead of tomorrow's long run.",
            "intensity": "easy",
        }
    return {
        "scheduled_date": date.strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": "3mi Shakeout",
        "description": "Very short, very easy. Loosening the legs before tomorrow's long run.",
        "target_distance_miles": 3,
        "intensity": "easy",
    }


def _daily_workouts_for_week(week_num, week_type, start_date):
    dm = _week_day_map(start_date)
    days = []

    long_run = LONG_RUNS.get(week_num, 10)
    mp_miles = MP_IN_LONG_RUN.get(week_num, 0)

    # Monday — rest / cross-train
    days.append(_rest_or_cross(dm["rest"], week_type))

    # Tuesday — easy
    tue = _easy_distance(week_type, "tue")
    days.append({
        "scheduled_date": dm["easy_1"].strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": f"{tue}mi Easy Run",
        "description": "Easy pace. Conversational — you should be able to speak in full sentences.",
        "target_distance_miles": tue,
        "target_hr_zone": "Zone 2 (MAF, <137 bpm ideal)",
        "intensity": "easy",
    })

    # Wednesday — quality
    days.append({"scheduled_date": dm["quality"].strftime("%Y-%m-%d"), **_quality_session(week_num)})

    # Thursday — easy
    thu = _easy_distance(week_type, "thu")
    days.append({
        "scheduled_date": dm["easy_2"].strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": f"{thu}mi Easy Run",
        "description": "Easy pace. Recovery focus — slower than Tuesday if anything.",
        "target_distance_miles": thu,
        "target_hr_zone": "Zone 2 (MAF, <137 bpm ideal)",
        "intensity": "easy",
    })

    # Friday — rest
    days.append(_friday(dm["shakeout"], week_type))

    # Saturday — shakeout or rest
    days.append(_saturday(dm["pre_long"], week_num, week_type))

    # Sunday — long run, or the race itself
    if week_num == 10:
        workout_type, title, intensity = "race", "Columbus Marathon", "hard"
    elif week_num == TUNE_UP_RACE["week"]:
        # The long effort was yesterday. Sunday is a shakeout, not a session.
        workout_type = "easy_run"
        title = f"{long_run}mi Recovery Jog"
        intensity = "easy"
    elif mp_miles:
        workout_type = "marathon_pace"
        title = f"{long_run}mi Long Run w/ {mp_miles} @ MP"
        intensity = "moderate"
    else:
        workout_type = "long_run"
        title = f"{long_run}mi Long Run"
        intensity = "easy" if long_run <= 13 else "moderate"

    days.append({
        "scheduled_date": dm["long_run"].strftime("%Y-%m-%d"),
        "workout_type": workout_type,
        "title": title,
        "description": _long_run_description(week_num, long_run, mp_miles),
        "target_distance_miles": long_run,
        "target_hr_zone": "Zone 2, allow Zone 3 during MP miles" if mp_miles else "Zone 2",
        "intensity": intensity,
        "is_benchmark": week_num == 8,
    })

    days.sort(key=lambda w: w["scheduled_date"])
    return days


def create_columbus_plan(conn=None, start_date=PLAN_START):
    """Create the 10-week Columbus Marathon training plan in the database."""
    should_close = False
    if conn is None:
        from .database import get_connection
        conn = get_connection()
        should_close = True

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = start + timedelta(weeks=TOTAL_WEEKS) - timedelta(days=1)

        cursor = conn.execute(
            """INSERT INTO training_plans
               (name, goal, start_date, end_date, total_weeks, mesocycle_weeks, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (RACE_NAME, RACE_GOAL, start_date, end.strftime("%Y-%m-%d"),
             TOTAL_WEEKS, 4, "active", RACE_GOAL_NOTES),
        )
        plan_id = cursor.lastrowid

        for week_num, week_type, miles_low, miles_high, focus in WEEKS:
            week_start = start + timedelta(weeks=week_num - 1)

            week_cursor = conn.execute(
                """INSERT INTO training_plan_weeks
                   (plan_id, week_number, week_type, focus, mental_focus, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (plan_id, week_num, week_type, focus, MENTAL_FOCUS.get(week_num),
                 f"Target: {miles_low}-{miles_high} miles"),
            )
            week_id = week_cursor.lastrowid

            workouts = _daily_workouts_for_week(week_num, week_type, week_start)
            for w in workouts:
                conn.execute(
                    """INSERT INTO daily_workouts
                       (plan_id, week_id, scheduled_date, workout_type, title, description,
                        target_distance_miles, target_duration_minutes, target_pace_min_per_mile,
                        target_hr_zone, intensity, notes, is_benchmark)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (plan_id, week_id, w["scheduled_date"], w["workout_type"], w["title"],
                     w.get("description"), w.get("target_distance_miles"),
                     w.get("target_duration_minutes"), w.get("target_pace_min_per_mile"),
                     w.get("target_hr_zone"), w.get("intensity"),
                     w.get("notes"), w.get("is_benchmark", False)),
                )

            for bm_week, bm_name, bm_type, bm_dow in BENCHMARKS:
                if bm_week == week_num:
                    bm_date = week_start + timedelta(days=bm_dow)
                    conn.execute(
                        """INSERT INTO plan_benchmarks
                           (plan_id, week_id, benchmark_name, benchmark_type, scheduled_date)
                           VALUES (?, ?, ?, ?, ?)""",
                        (plan_id, week_id, bm_name, bm_type, bm_date.strftime("%Y-%m-%d")),
                    )

            conn.execute(
                """INSERT INTO weekly_summaries (plan_id, week_number, target_miles, runs_planned)
                   VALUES (?, ?, ?, ?)""",
                (plan_id, week_num, (miles_low + miles_high) / 2,
                 sum(1 for w in workouts if w["workout_type"] not in ("rest", "cross_train"))),
            )

        # Seed targets with a provisional goal pace so MP sessions have a number
        # before the week-3 time trial; adapt_from_5k_tt() replaces it afterwards.
        seed_initial_targets(conn, plan_id, start_date,
                             marathon_pace=PROVISIONAL_MARATHON_PACE)

        # Stamp those targets onto every workout in the plan, using the same helper
        # the adaptive engine calls after each benchmark — so the paces a workout
        # shows on day one come from exactly the path that later updates them.
        targets = get_current_targets(conn, plan_id, as_of_date=start_date)
        apply_targets_to_future_workouts(conn, plan_id, targets, from_date=start_date)

        conn.commit()
        return plan_id

    except Exception:
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()


def generate_marathon_plan_markdown(conn, plan_id):
    """Generate MARATHON_PLAN.md content from the database with current targets."""
    from .adapt import get_current_targets

    plan = conn.execute("SELECT * FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        return None

    targets = get_current_targets(conn, plan_id)
    easy = _fmt_pace(targets["easy_pace"]) if targets else "10:15/mi"
    long_run = _fmt_pace(targets["long_run_pace"]) if targets else "10:45/mi"
    tempo = _fmt_pace(targets["tempo_pace"]) if targets else "9:15/mi"
    mp = _fmt_pace(targets["marathon_pace"]) if targets and targets.get("marathon_pace") \
        else _fmt_pace(PROVISIONAL_MARATHON_PACE)

    weeks = conn.execute(
        "SELECT * FROM training_plan_weeks WHERE plan_id = ? ORDER BY week_number",
        (plan_id,),
    ).fetchall()

    lines = [
        f"# {RACE_NAME} — {TOTAL_WEEKS}-Week Training Plan",
        "",
        f"**Goal:** {plan['goal']}",
        "**Race Date:** October 18, 2026",
        f"**Start:** {plan['start_date']} | **End:** {plan['end_date']}",
        "",
        "> Built 16 days after a 29:00 Burning River 100. The aerobic base is deep;",
        "> the limiter is marathon-specific pace. Quality-biased, volume-modest.",
        "> Weeks 1-3 are recovery and diagnostics — the block really starts at the",
        "> week-3 time trial.",
        "",
        "## Current Pace Targets",
        "",
        "| Zone | Target |",
        "|------|--------|",
        f"| Easy | {easy} |",
        f"| Long Run | {long_run} |",
        f"| Marathon Pace | {mp} |",
        f"| Tempo | {tempo} |",
        f"| MAF HR | {targets['maf_hr'] if targets else 137} bpm |",
        "",
        "*Targets update automatically from benchmark results (MAF tests, 5K TTs).*",
        "",
    ]

    for week_row in weeks:
        wn = week_row["week_number"]
        wtype = week_row["week_type"].upper()
        focus = week_row["focus"] or ""
        mental = (week_row["mental_focus"] if "mental_focus" in week_row.keys() else "") or ""
        notes = week_row["notes"] or ""

        workouts = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_workouts WHERE week_id = ? ORDER BY scheduled_date",
            (week_row["id"],),
        ).fetchall()]
        if not workouts:
            continue

        lines.append("---")
        lines.append("")
        lines.append(f"## Week {wn} ({wtype}) — {workouts[0]['scheduled_date']} "
                     f"to {workouts[-1]['scheduled_date']}")
        lines.append("")
        lines.append(f"**Focus:** {focus}")
        if mental:
            lines.append("")
            lines.append(f"**Mental:** {mental}")
        lines.append(f"{notes}")
        lines.append("")

        lines.append("| Date | Workout | Distance | Pace | Intensity |")
        lines.append("|------|---------|----------|------|-----------|")
        for w in workouts:
            dist = f"{w['target_distance_miles']}mi" if w.get("target_distance_miles") else "—"
            pace = _fmt_pace(w["target_pace_min_per_mile"]) \
                if w.get("target_pace_min_per_mile") else "—"
            lines.append(f"| {w['scheduled_date']} | {w['title']} | {dist} | {pace} "
                         f"| {w.get('intensity', '')} |")
        lines.append("")

        for w in workouts:
            if w["workout_type"] in ("rest", "cross_train"):
                continue
            if w.get("description"):
                lines.append(f"**{w['scheduled_date']} — {w['title']}**")
                lines.append(f"> {w['description']}")
                lines.append("")

    lines.append("")
    return "\n".join(lines)
