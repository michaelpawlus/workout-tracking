"""Static 20-week Burning River 100 training plan.

Not LLM-generated — ultra training is too specific for that.
Inserts day-by-day workouts, weekly structure, and benchmark schedule.
"""

from datetime import datetime, timedelta
from database import get_db
from adapt import seed_initial_targets


# Week definitions: (week_num, phase/week_type, target_miles_low, target_miles_high, focus)
WEEKS = [
    (1,  "base",     25, 30, "Baseline week. Easy running, 5K TT, MAF test"),
    (2,  "base",     30, 35, "Endurance baseline. Strides 2x"),
    (3,  "base",     35, 40, "First tempo segments. Trail run"),
    (4,  "recovery", 25, 28, "~70% volume cutback"),
    (5,  "build",    40, 45, "Tempo 4mi. Hill repeats"),
    (6,  "build",    45, 50, "Back-to-back long runs: 16 Sat / 8 Sun"),
    (7,  "build",    45, 50, "MAF retest #2. Trail long run"),
    (8,  "recovery", 30, 35, "Cutback. Light strides"),
    (9,  "build",    50, 55, "B2B: 14/8. Tempo 5mi"),
    (10, "build",    55, 60, "Nutrition practice. Hill workout"),
    (11, "build",    55, 65, "Benchmark: 50K training race"),
    (12, "recovery", 35, 40, "MAF test #3. Recovery/absorb"),
    (13, "peak",     65, 70, "B2B: 26/10. Night running practice"),
    (14, "peak",     70, 80, "Peak week. Longest run"),
    (15, "peak",     65, 70, "B2B: 24/12. Race logistics practice"),
    (16, "recovery", 40, 45, "Final benchmarks: MAF #4, 5K TT #2"),
    (17, "taper",    45, 50, "Last hard tempo. Begin taper"),
    (18, "taper",    35, 40, "~50% of peak. Short strides"),
    (19, "taper",    20, 25, "Very easy. Gear check"),
    (20, "race",     10, 110, "Shakeouts Mon-Fri. Jul 25: Burning River 100"),
]

# Benchmark schedule: (week_num, name, type, day_offset_from_week_start)
# Week starts on Monday, so: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
BENCHMARKS = [
    (1,  "5K Time Trial",       "time_trial",     2),  # Wednesday
    (1,  "MAF Test #1",         "maf_test",       5),  # Saturday (during long run)
    (2,  "Long Run Baseline",   "endurance_test", 5),  # Saturday
    (7,  "MAF Test #2",         "maf_test",       2),  # Wednesday
    (11, "50K Training Race",   "race",           5),  # Saturday
    (12, "MAF Test #3",         "maf_test",       2),  # Wednesday
    (16, "5K Time Trial #2",    "time_trial",     5),  # Saturday
    (16, "MAF Test #4",         "maf_test",       2),  # Wednesday
]


def _week_day_map(start_date):
    """Map workout roles to calendar dates. Week starts Monday, ends Sunday.

    Long run lands on Saturday (capstone), recovery/B2B on Sunday (close).
    """
    # start_date should be a Monday
    day_map = {
        "rest":     start_date,                        # Monday
        "easy_1":   start_date + timedelta(days=1),    # Tuesday
        "quality":  start_date + timedelta(days=2),    # Wednesday
        "easy_2":   start_date + timedelta(days=3),    # Thursday
        "shakeout": start_date + timedelta(days=4),    # Friday
        "long_run": start_date + timedelta(days=5),    # Saturday
        "recovery": start_date + timedelta(days=6),    # Sunday
    }
    return day_map


def _daily_workouts_for_week(week_num, week_type, start_date, miles_low, miles_high):
    """Generate daily workout list for a given week."""
    if week_type == "race" and week_num == 20:
        return _race_week(start_date, start_date)

    dm = _week_day_map(start_date)
    days = []

    long_run = _long_run_for_week(week_num)
    easy_pace_range = "10:00-10:30"
    mid_week_quality = _quality_session(week_num, week_type)

    # Monday — Rest or cross-train
    days.append(_rest_or_cross(dm["rest"], week_type))

    # Tuesday — Easy run
    tue_dist = _easy_distance(week_num, week_type, "tue")
    days.append({
        "scheduled_date": dm["easy_1"].strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": f"{tue_dist}mi Easy Run",
        "description": f"Easy pace ({easy_pace_range}/mi). Keep HR under 145.",
        "target_distance_miles": tue_dist,
        "target_pace_min_per_mile": 10.25,
        "target_hr_zone": "Zone 2 (MAF, <137 bpm ideal)",
        "intensity": "easy",
    })

    # Wednesday — Quality session
    days.append({
        "scheduled_date": dm["quality"].strftime("%Y-%m-%d"),
        **mid_week_quality,
    })

    # Thursday — Easy run
    thu_dist = _easy_distance(week_num, week_type, "thu")
    days.append({
        "scheduled_date": dm["easy_2"].strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": f"{thu_dist}mi Easy Run",
        "description": f"Easy pace ({easy_pace_range}/mi). Recovery focus.",
        "target_distance_miles": thu_dist,
        "target_pace_min_per_mile": 10.25,
        "target_hr_zone": "Zone 2 (MAF, <137 bpm ideal)",
        "intensity": "easy",
    })

    # Friday — Rest or short shakeout
    days.append(_friday(dm["shakeout"], week_type, week_num))

    # Saturday — Long run
    days.append({
        "scheduled_date": dm["long_run"].strftime("%Y-%m-%d"),
        "workout_type": "long_run",
        "title": f"{long_run}mi Long Run",
        "description": _long_run_description(week_num, long_run),
        "target_distance_miles": long_run,
        "target_pace_min_per_mile": 10.75,
        "target_hr_zone": "Zone 2, allow drift to Zone 3 in final third",
        "intensity": "easy" if long_run <= 14 else "moderate",
    })

    # Sunday — Recovery or B2B
    days.append(_sunday(dm["recovery"], week_num, week_type))

    # Sort by date so DB insertion is chronological
    days.sort(key=lambda w: w["scheduled_date"])

    return days


def _rest_or_cross(date, week_type):
    if week_type in ("recovery", "taper"):
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


def _easy_distance(week_num, week_type, day):
    base = {
        "base": 5, "build": 6, "peak": 7, "recovery": 4, "taper": 4, "race": 3,
    }.get(week_type, 5)
    if day == "thu":
        base -= 1
    return max(3, base)


def _quality_session(week_num, week_type):
    sessions = {
        1: {"workout_type": "benchmark", "title": "MAF Test #1",
            "description": "30 min at HR ~137 bpm on flat ground. Record distance covered. Warm up 10 min easy first.",
            "target_duration_minutes": 30, "target_hr_zone": "MAF (137 bpm)", "intensity": "easy",
            "is_benchmark": True, "target_distance_miles": None},
        2: {"workout_type": "easy_run", "title": "6mi Easy + Strides",
            "description": "6mi easy with 6x100m strides at end. Focus on quick turnover, relaxed form.",
            "target_distance_miles": 6, "intensity": "easy"},
        3: {"workout_type": "tempo", "title": "Tempo Segments: 3x1mi",
            "description": "2mi warmup, 3x1mi at current tempo target with 2min jog recovery, 1mi cooldown.",
            "target_distance_miles": 8, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        4: {"workout_type": "easy_run", "title": "4mi Easy + Strides",
            "description": "Recovery week. 4mi easy with 4x100m strides.",
            "target_distance_miles": 4, "intensity": "easy"},
        5: {"workout_type": "hills", "title": "Hill Repeats: 8x90sec",
            "description": "2mi warmup, 8x90sec hill repeats at hard effort, jog down recovery, 1mi cooldown.",
            "target_distance_miles": 7, "intensity": "hard"},
        6: {"workout_type": "tempo", "title": "Tempo Run: 4mi",
            "description": "2mi warmup, 4mi at current tempo target, 1mi cooldown.",
            "target_distance_miles": 7, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        7: {"workout_type": "benchmark", "title": "MAF Test #2",
            "description": "30 min at HR ~137 bpm on flat ground. Compare distance to Test #1.",
            "target_duration_minutes": 30, "target_hr_zone": "MAF (137 bpm)", "intensity": "easy",
            "is_benchmark": True, "target_distance_miles": None},
        8: {"workout_type": "easy_run", "title": "5mi Easy + Light Strides",
            "description": "Recovery week. Easy effort with 4x100m strides.",
            "target_distance_miles": 5, "intensity": "easy"},
        9: {"workout_type": "tempo", "title": "Tempo Run: 5mi",
            "description": "2mi warmup, 5mi at current tempo target, 1mi cooldown.",
            "target_distance_miles": 8, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        10: {"workout_type": "hills", "title": "Hill Workout: 10x90sec",
             "description": "2mi warmup, 10x90sec hill repeats, jog down recovery, 1mi cooldown. Practice race-day climbing.",
             "target_distance_miles": 8, "intensity": "hard"},
        11: {"workout_type": "easy_run", "title": "5mi Easy (Pre-Race)",
             "description": "Easy shakeout before Saturday 50K. Stay loose, don't push.",
             "target_distance_miles": 5, "intensity": "easy"},
        12: {"workout_type": "benchmark", "title": "MAF Test #3",
             "description": "30 min at HR ~137 bpm. Compare to previous tests.",
             "target_duration_minutes": 30, "target_hr_zone": "MAF (137 bpm)", "intensity": "easy",
             "is_benchmark": True, "target_distance_miles": None},
        13: {"workout_type": "tempo", "title": "Tempo: 4mi + Trail Segments",
             "description": "2mi warmup, 4mi at current tempo target on trails if possible, 1mi cooldown.",
             "target_distance_miles": 7, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        14: {"workout_type": "tempo", "title": "Tempo: 5mi",
             "description": "Peak week quality. 2mi warmup, 5mi at current tempo target, 1mi cooldown.",
             "target_distance_miles": 8, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        15: {"workout_type": "tempo", "title": "Tempo: 3mi",
             "description": "Reduced quality volume. 2mi warmup, 3mi at current tempo target, 1mi cooldown.",
             "target_distance_miles": 6, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        16: {"workout_type": "benchmark", "title": "MAF Test #4",
             "description": "Final MAF test. 30 min at HR ~137 bpm. Compare all 4 tests.",
             "target_duration_minutes": 30, "target_hr_zone": "MAF (137 bpm)", "intensity": "easy",
             "is_benchmark": True, "target_distance_miles": None},
        17: {"workout_type": "tempo", "title": "Last Tempo: 3mi",
             "description": "Final hard session. 2mi warmup, 3mi at current tempo target, 1mi cooldown. After this, all easy.",
             "target_distance_miles": 6, "target_pace_min_per_mile": 9.25, "intensity": "threshold"},
        18: {"workout_type": "easy_run", "title": "5mi Easy + 4 Strides",
             "description": "Taper. Stay sharp with short strides but keep effort very easy.",
             "target_distance_miles": 5, "intensity": "easy"},
        19: {"workout_type": "easy_run", "title": "3mi Easy Shakeout",
             "description": "Very easy. Just move the legs. Gear check — run in race-day shoes/kit.",
             "target_distance_miles": 3, "intensity": "easy"},
    }
    s = sessions.get(week_num, {
        "workout_type": "easy_run", "title": "5mi Easy",
        "description": "Easy run.", "target_distance_miles": 5, "intensity": "easy",
    })
    return s


def _long_run_for_week(week_num):
    mapping = {
        1: 8, 2: 10, 3: 12, 4: 8, 5: 14, 6: 16, 7: 18, 8: 10,
        9: 20, 10: 22, 11: 31, 12: 12, 13: 26, 14: 30, 15: 24,
        16: 14, 17: 18, 18: 13, 19: 9, 20: 0,
    }
    return mapping.get(week_num, 10)


def _long_run_description(week_num, distance):
    if distance >= 20:
        return (f"{distance}mi long run. Carry nutrition — practice race-day fueling "
                f"(~200 cal/hr after 60 min). Run by effort, not pace. Allow walking on hills.")
    if distance >= 14:
        return (f"{distance}mi long run. Steady effort. Practice carrying water/nutrition. "
                f"Keep HR in Zone 2-3.")
    return f"{distance}mi long run. Comfortable, conversational pace. Build your aerobic base."


def _friday(date, week_type, week_num):
    if week_type in ("build", "peak") and week_num not in (8, 12, 16):
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "easy_run",
            "title": "3mi Shakeout",
            "description": "Very short, very easy. Loosening up before tomorrow's long run.",
            "target_distance_miles": 3,
            "intensity": "easy",
        }
    return {
        "scheduled_date": date.strftime("%Y-%m-%d"),
        "workout_type": "rest",
        "title": "Rest Day",
        "description": "Rest. Stretch, foam roll, hydrate well for tomorrow.",
        "intensity": "easy",
    }


def _sunday(date, week_num, week_type):
    b2b_weeks = {6: 8, 9: 8, 13: 10, 15: 12}
    if week_num in b2b_weeks:
        dist = b2b_weeks[week_num]
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "back_to_back",
            "title": f"{dist}mi B2B Long Run",
            "description": (f"Back-to-back run on tired legs. {dist}mi easy. "
                            "This simulates late-race fatigue. Keep effort easy."),
            "target_distance_miles": dist,
            "target_pace_min_per_mile": 10.75,
            "intensity": "moderate",
        }
    if week_type in ("recovery", "taper"):
        return {
            "scheduled_date": date.strftime("%Y-%m-%d"),
            "workout_type": "rest",
            "title": "Rest / Easy Walk",
            "description": "Full rest or easy 30-min walk. Let the body absorb training.",
            "intensity": "easy",
        }
    dist = _easy_distance(week_num, week_type, "thu")
    return {
        "scheduled_date": date.strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": f"{dist}mi Recovery Run",
        "description": "Easy recovery run. If legs are trashed from yesterday, walk instead.",
        "target_distance_miles": dist,
        "target_pace_min_per_mile": 10.75,
        "intensity": "easy",
    }


def _race_week(start_date, d):
    """Race week: Mon Jul 20 - Sun Jul 26. Shakeouts, race Sat Jul 25, recovery Sun."""
    race_date = datetime(2026, 7, 25)
    days = []
    for i in range(7):
        day = d + timedelta(days=i)
        if day < race_date - timedelta(days=1):
            # Mon-Thu: shakeouts
            days.append({
                "scheduled_date": day.strftime("%Y-%m-%d"),
                "workout_type": "easy_run",
                "title": "2mi Shakeout",
                "description": "Very easy 2mi. Stay loose. Hydrate well.",
                "target_distance_miles": 2,
                "intensity": "easy",
            })
        elif day == race_date - timedelta(days=1):
            # Fri: race eve rest
            days.append({
                "scheduled_date": day.strftime("%Y-%m-%d"),
                "workout_type": "rest",
                "title": "Rest — Race Eve",
                "description": "Full rest. Gear check. Eat well. Sleep early. Trust your training.",
                "intensity": "easy",
            })
        elif day == race_date:
            # Sat: RACE DAY
            days.append({
                "scheduled_date": day.strftime("%Y-%m-%d"),
                "workout_type": "race",
                "title": "BURNING RIVER 100",
                "description": ("100 miles. Sub-24 goal = ~14:24/mi avg including all stops. "
                                "Start conservative. Walk all uphills. Eat early and often. "
                                "The race starts at mile 60."),
                "target_distance_miles": 100,
                "intensity": "hard",
                "is_benchmark": True,
            })
        else:
            # Sun: post-race
            days.append({
                "scheduled_date": day.strftime("%Y-%m-%d"),
                "workout_type": "rest",
                "title": "Post-Race Recovery",
                "description": "You did it. Rest. Celebrate. Eat everything.",
                "intensity": "easy",
            })
    return days


# --- Special Week 1 override for March 9 (Monday) ---

def _week1_workouts(start_date):
    """Week 1 starts Mon March 9. Baseline week with 5K TT and MAF test."""
    d = start_date  # Monday
    days = []

    # Mon Mar 9 — Rest (ease into the plan)
    days.append({
        "scheduled_date": d.strftime("%Y-%m-%d"),
        "workout_type": "rest",
        "title": "Rest Day",
        "description": "Welcome to BR100 training! Rest today, we start running tomorrow.",
        "intensity": "easy",
    })

    # Tue Mar 10 — 4mi Easy (first run)
    days.append({
        "scheduled_date": (d + timedelta(days=1)).strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": "4mi Easy Run",
        "description": "First run of the plan. Easy effort, find your rhythm.",
        "target_distance_miles": 4,
        "target_pace_min_per_mile": 10.25,
        "target_hr_zone": "Zone 2 (MAF, <137 bpm ideal)",
        "intensity": "easy",
    })

    # Wed Mar 11 — 5K Time Trial
    days.append({
        "scheduled_date": (d + timedelta(days=2)).strftime("%Y-%m-%d"),
        "workout_type": "benchmark",
        "title": "5K Time Trial",
        "description": "1mi warmup, all-out 5K (3.1mi), 1mi cooldown. Record time, pace, HR. This establishes pace zones.",
        "target_distance_miles": 5.1,
        "intensity": "hard",
        "is_benchmark": True,
    })

    # Thu Mar 12 — 4mi Easy
    days.append({
        "scheduled_date": (d + timedelta(days=3)).strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": "4mi Easy Run",
        "description": "Easy pace. Recovery focus after yesterday's time trial.",
        "target_distance_miles": 4,
        "target_pace_min_per_mile": 10.25,
        "target_hr_zone": "Zone 2",
        "intensity": "easy",
    })

    # Fri Mar 13 — Rest
    days.append({
        "scheduled_date": (d + timedelta(days=4)).strftime("%Y-%m-%d"),
        "workout_type": "rest",
        "title": "Rest Day",
        "description": "Rest. Stretch, foam roll, hydrate well for tomorrow's long run.",
        "intensity": "easy",
    })

    # Sat Mar 14 — 8mi Long Run
    days.append({
        "scheduled_date": (d + timedelta(days=5)).strftime("%Y-%m-%d"),
        "workout_type": "long_run",
        "title": "8mi Long Run",
        "description": "First long run. Comfortable, conversational pace. Build your aerobic base.",
        "target_distance_miles": 8,
        "target_pace_min_per_mile": 10.75,
        "target_hr_zone": "Zone 2",
        "intensity": "easy",
    })

    # Sun Mar 15 — Recovery Run
    days.append({
        "scheduled_date": (d + timedelta(days=6)).strftime("%Y-%m-%d"),
        "workout_type": "easy_run",
        "title": "4mi Recovery Run",
        "description": "Easy recovery run after yesterday's long run.",
        "target_distance_miles": 4,
        "target_pace_min_per_mile": 10.75,
        "target_hr_zone": "Zone 2",
        "intensity": "easy",
    })

    return days


def create_br100_plan(conn=None, start_date="2026-03-09"):
    """Create the 20-week Burning River 100 training plan in the database."""
    should_close = False
    if conn is None:
        from database import get_connection
        conn = get_connection()
        should_close = True

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = start + timedelta(weeks=20) - timedelta(days=1)

        # Create training plan
        cursor = conn.execute(
            """INSERT INTO training_plans (name, goal, start_date, end_date, total_weeks, mesocycle_weeks, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("Burning River 100", "Sub-24 hour finish", start_date,
             end.strftime("%Y-%m-%d"), 20, 4, "active",
             "20-week plan for Burning River 100 (July 25, 2026). Target: sub-24 hours."),
        )
        plan_id = cursor.lastrowid

        # Create weeks, daily workouts, and benchmarks
        for week_num, week_type, miles_low, miles_high, focus in WEEKS:
            week_start = start + timedelta(weeks=week_num - 1)

            week_cursor = conn.execute(
                """INSERT INTO training_plan_weeks (plan_id, week_number, week_type, focus, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (plan_id, week_num, week_type, focus,
                 f"Target: {miles_low}-{miles_high} miles"),
            )
            week_id = week_cursor.lastrowid

            # Daily workouts — week 1 is special
            if week_num == 1:
                workouts = _week1_workouts(week_start)
            else:
                workouts = _daily_workouts_for_week(week_num, week_type, week_start, miles_low, miles_high)

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

            # Benchmarks
            for bm_week, bm_name, bm_type, bm_dow in BENCHMARKS:
                if bm_week == week_num:
                    bm_date = week_start + timedelta(days=bm_dow)
                    conn.execute(
                        """INSERT INTO plan_benchmarks
                           (plan_id, week_id, benchmark_name, benchmark_type, scheduled_date)
                           VALUES (?, ?, ?, ?, ?)""",
                        (plan_id, week_id, bm_name, bm_type, bm_date.strftime("%Y-%m-%d")),
                    )

            # Weekly summary placeholder
            conn.execute(
                """INSERT INTO weekly_summaries (plan_id, week_number, target_miles, runs_planned)
                   VALUES (?, ?, ?, ?)""",
                (plan_id, week_num, (miles_low + miles_high) / 2,
                 sum(1 for w in workouts if w["workout_type"] not in ("rest", "cross_train"))),
            )

        # Seed initial athlete targets
        seed_initial_targets(conn, plan_id, start_date)

        conn.commit()
        return plan_id

    except Exception:
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()


def _fmt_pace(pace):
    if pace is None:
        return "N/A"
    return f"{int(pace)}:{int((pace % 1) * 60):02d}/mi"


def generate_training_plan_markdown(conn, plan_id):
    """Generate TRAINING_PLAN.md content from the database with current targets."""
    from adapt import get_current_targets

    plan = conn.execute("SELECT * FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        return None

    targets = get_current_targets(conn, plan_id)
    tempo_pace_str = _fmt_pace(targets["tempo_pace"]) if targets else "9:15/mi"
    easy_pace_str = _fmt_pace(targets["easy_pace"]) if targets else "10:15/mi"
    long_pace_str = _fmt_pace(targets["long_run_pace"]) if targets else "10:45/mi"

    weeks = conn.execute(
        "SELECT * FROM training_plan_weeks WHERE plan_id = ? ORDER BY week_number",
        (plan_id,),
    ).fetchall()

    lines = [
        "# Burning River 100 — 20-Week Training Plan",
        "",
        f"**Goal:** {plan['goal']}",
        f"**Race Date:** July 25, 2026",
        f"**Start:** {plan['start_date']} | **End:** {plan['end_date']}",
        "",
        "## Current Pace Targets",
        "",
        f"| Zone | Target |",
        f"|------|--------|",
        f"| Easy | {easy_pace_str} |",
        f"| Long Run | {long_pace_str} |",
        f"| Tempo | {tempo_pace_str} |",
        f"| MAF HR | {targets['maf_hr'] if targets else 137} bpm |",
        "",
        "*Targets update automatically from benchmark results (MAF tests, 5K TTs).*",
        "",
    ]

    for week_row in weeks:
        wn = week_row["week_number"]
        wtype = week_row["week_type"].upper()
        focus = week_row["focus"] or ""
        notes = week_row["notes"] or ""

        workouts = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_workouts WHERE week_id = ? ORDER BY scheduled_date",
            (week_row["id"],),
        ).fetchall()]

        if not workouts:
            continue

        first_date = workouts[0]["scheduled_date"]
        last_date = workouts[-1]["scheduled_date"]

        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## Week {wn} ({wtype}) — {first_date} to {last_date}")
        lines.append(f"")
        lines.append(f"**Focus:** {focus}")
        lines.append(f"{notes}")
        lines.append(f"")

        # Week schedule table
        lines.append(f"| Date | Workout | Distance | Pace | Intensity |")
        lines.append(f"|------|---------|----------|------|-----------|")
        for w in workouts:
            dist = f"{w['target_distance_miles']}mi" if w.get("target_distance_miles") else "—"
            pace = _fmt_pace(w["target_pace_min_per_mile"]) if w.get("target_pace_min_per_mile") else "—"
            lines.append(f"| {w['scheduled_date']} | {w['title']} | {dist} | {pace} | {w.get('intensity', '')} |")

        lines.append(f"")

        # Workout details for non-trivial sessions
        for w in workouts:
            if w["workout_type"] in ("rest", "cross_train"):
                continue
            if w.get("description"):
                lines.append(f"**{w['scheduled_date']} — {w['title']}**")
                lines.append(f"> {w['description']}")
                lines.append(f"")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    from database import init_db
    init_db()
    plan_id = create_br100_plan()
    print(f"Created BR100 plan with id={plan_id}")
