"""Adaptive pace/HR target management for the BR100 training plan.

Adjusts easy, long, tempo, and threshold paces based on benchmark results
(MAF tests, 5K TTs) and accumulated run data. Each adaptation creates a new
athlete_targets snapshot so history is preserved.
"""

from datetime import datetime


# Guardrails (min/mi)
EASY_PACE_MIN = 8.5
EASY_PACE_MAX = 12.0
TEMPO_PACE_MIN = 7.0
TEMPO_PACE_MAX = 10.0

# Defaults (adjusted from first tempo workout data: 9:13-9:29 at correct HR)
DEFAULT_TARGETS = {
    "easy_pace": 10.25,
    "long_run_pace": 10.75,
    "tempo_pace": 9.25,
    "threshold_pace": None,
    "maf_hr": 137,
    "zone2_ceiling": 137,
    "zone3_ceiling": 155,
    "zone4_ceiling": 170,
}

TREND_STEP = 0.25  # 15 sec = 0.25 min/mi


def get_current_targets(conn, plan_id, as_of_date=None):
    """Return the latest targets effective on or before as_of_date.

    Returns a dict with all target fields, or None if no targets exist.
    """
    if as_of_date is None:
        as_of_date = datetime.now().strftime("%Y-%m-%d")

    row = conn.execute(
        """SELECT * FROM athlete_targets
           WHERE plan_id = ? AND effective_date <= ?
           ORDER BY effective_date DESC, id DESC LIMIT 1""",
        (plan_id, as_of_date),
    ).fetchone()

    return dict(row) if row else None


def get_targets_history(conn, plan_id):
    """Return full timeline of target snapshots."""
    rows = conn.execute(
        "SELECT * FROM athlete_targets WHERE plan_id = ? ORDER BY effective_date, id",
        (plan_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def seed_initial_targets(conn, plan_id, start_date):
    """Insert the first targets row with plan defaults."""
    existing = conn.execute(
        "SELECT id FROM athlete_targets WHERE plan_id = ? LIMIT 1",
        (plan_id,),
    ).fetchone()
    if existing:
        return existing["id"]

    cursor = conn.execute(
        """INSERT INTO athlete_targets
           (plan_id, effective_date, easy_pace, long_run_pace, tempo_pace,
            threshold_pace, maf_hr, zone2_ceiling, zone3_ceiling, zone4_ceiling,
            source, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, start_date,
         DEFAULT_TARGETS["easy_pace"], DEFAULT_TARGETS["long_run_pace"],
         DEFAULT_TARGETS["tempo_pace"], DEFAULT_TARGETS["threshold_pace"],
         DEFAULT_TARGETS["maf_hr"], DEFAULT_TARGETS["zone2_ceiling"],
         DEFAULT_TARGETS["zone3_ceiling"], DEFAULT_TARGETS["zone4_ceiling"],
         "initial", "Plan defaults"),
    )
    return cursor.lastrowid


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _insert_targets(conn, plan_id, effective_date, targets, source,
                     benchmark_id=None, notes=None):
    """Insert a new targets snapshot. Returns the new row id."""
    cursor = conn.execute(
        """INSERT INTO athlete_targets
           (plan_id, effective_date, easy_pace, long_run_pace, tempo_pace,
            threshold_pace, maf_hr, zone2_ceiling, zone3_ceiling, zone4_ceiling,
            source, trigger_benchmark_id, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, effective_date,
         targets["easy_pace"], targets["long_run_pace"],
         targets["tempo_pace"], targets.get("threshold_pace"),
         targets["maf_hr"], targets["zone2_ceiling"],
         targets["zone3_ceiling"], targets["zone4_ceiling"],
         source, benchmark_id, notes),
    )
    return cursor.lastrowid


def adapt_from_maf(conn, plan_id, benchmark_id, maf_distance, duration=30):
    """Adapt easy/long pace from a MAF test result.

    maf_distance: miles covered in `duration` minutes at MAF HR.
    Formula: easy_pace = maf_pace + 0.5, long_pace = maf_pace + 1.0
    """
    if maf_distance <= 0:
        return None

    maf_pace = duration / maf_distance  # min/mi
    current = get_current_targets(conn, plan_id)
    if not current:
        return None

    new = dict(current)
    new["easy_pace"] = _clamp(round(maf_pace + 0.5, 2), EASY_PACE_MIN, EASY_PACE_MAX)
    new["long_run_pace"] = _clamp(round(maf_pace + 1.0, 2), EASY_PACE_MIN + 0.5, EASY_PACE_MAX + 0.5)

    bm = conn.execute("SELECT scheduled_date FROM plan_benchmarks WHERE id = ?",
                       (benchmark_id,)).fetchone()
    effective = bm["scheduled_date"] if bm else datetime.now().strftime("%Y-%m-%d")

    row_id = _insert_targets(
        conn, plan_id, effective, new, "maf_test", benchmark_id,
        f"MAF: {maf_distance:.2f}mi in {duration}min → {maf_pace:.2f} min/mi",
    )
    return {"id": row_id, "targets": new, "maf_pace": round(maf_pace, 2)}


def adapt_from_5k_tt(conn, plan_id, benchmark_id, tt_time_seconds):
    """Adapt tempo/threshold pace from a 5K time trial.

    tt_time_seconds: total time for 3.1 miles.
    Formula (Daniels-style): threshold = 5k_pace + 0.5, tempo = 5k_pace + 0.75
    Guardrail: tempo/threshold must be faster (lower) than easy pace.
    """
    if tt_time_seconds <= 0:
        return None

    pace_5k = (tt_time_seconds / 60) / 3.1  # min/mi
    current = get_current_targets(conn, plan_id)
    if not current:
        return None

    new = dict(current)
    easy = current["easy_pace"]
    tempo_raw = round(pace_5k + 0.75, 2)
    threshold_raw = round(pace_5k + 0.5, 2)
    new["tempo_pace"] = _clamp(min(tempo_raw, easy - 0.1), TEMPO_PACE_MIN, TEMPO_PACE_MAX)
    new["threshold_pace"] = min(threshold_raw, new["tempo_pace"] - 0.1)

    bm = conn.execute("SELECT scheduled_date FROM plan_benchmarks WHERE id = ?",
                       (benchmark_id,)).fetchone()
    effective = bm["scheduled_date"] if bm else datetime.now().strftime("%Y-%m-%d")

    row_id = _insert_targets(
        conn, plan_id, effective, new, "5k_tt", benchmark_id,
        f"5K TT: {tt_time_seconds}s → {pace_5k:.2f} min/mi",
    )
    return {"id": row_id, "targets": new, "five_k_pace": round(pace_5k, 2)}


def adapt_from_trends(conn, plan_id):
    """Check last 10 easy runs for systematic pace/HR patterns.

    If 7+/10 easy runs are faster than target with HR in zone → tighten 15s.
    If 5+/10 easy runs have elevated HR (above zone2_ceiling) → loosen 15s.
    Returns adaptation result or None if no change warranted.
    """
    current = get_current_targets(conn, plan_id)
    if not current:
        return None

    rows = conn.execute(
        """SELECT rf.actual_pace, rf.avg_heart_rate, dw.workout_type, dw.intensity
           FROM run_feedback rf
           JOIN daily_workouts dw ON dw.id = rf.daily_workout_id
           WHERE rf.plan_id = ? AND dw.intensity = 'easy'
             AND rf.actual_pace IS NOT NULL
           ORDER BY rf.created_at DESC LIMIT 10""",
        (plan_id,),
    ).fetchall()

    if len(rows) < 5:
        return {"change": None, "reason": "Insufficient data (need 5+ easy runs)"}

    target_easy = current["easy_pace"]
    z2_ceiling = current["zone2_ceiling"]

    faster_in_zone = 0
    hr_elevated = 0
    for r in rows:
        pace = r["actual_pace"]
        hr = r["avg_heart_rate"]
        if pace and pace < target_easy and hr and hr <= z2_ceiling:
            faster_in_zone += 1
        if hr and hr > z2_ceiling:
            hr_elevated += 1

    total = len(rows)
    new = dict(current)
    change = None

    if faster_in_zone >= 7:
        new["easy_pace"] = _clamp(round(target_easy - TREND_STEP, 2), EASY_PACE_MIN, EASY_PACE_MAX)
        new["long_run_pace"] = _clamp(round(current["long_run_pace"] - TREND_STEP, 2),
                                       EASY_PACE_MIN + 0.5, EASY_PACE_MAX + 0.5)
        change = "tighten"
    elif hr_elevated >= 5:
        new["easy_pace"] = _clamp(round(target_easy + TREND_STEP, 2), EASY_PACE_MIN, EASY_PACE_MAX)
        new["long_run_pace"] = _clamp(round(current["long_run_pace"] + TREND_STEP, 2),
                                       EASY_PACE_MIN + 0.5, EASY_PACE_MAX + 0.5)
        change = "loosen"

    if not change:
        return {
            "change": None,
            "reason": f"No trend: {faster_in_zone}/{total} faster-in-zone, {hr_elevated}/{total} HR elevated",
            "faster_in_zone": faster_in_zone,
            "hr_elevated": hr_elevated,
            "total_runs": total,
        }

    today = datetime.now().strftime("%Y-%m-%d")
    row_id = _insert_targets(
        conn, plan_id, today, new, "trend", None,
        f"Trend {change}: {faster_in_zone}/{total} faster, {hr_elevated}/{total} HR elevated",
    )
    return {
        "id": row_id,
        "change": change,
        "targets": new,
        "faster_in_zone": faster_in_zone,
        "hr_elevated": hr_elevated,
        "total_runs": total,
    }


def apply_targets_to_future_workouts(conn, plan_id, targets, from_date=None):
    """Update uncompleted daily_workouts with new pace targets.

    Only modifies rows that haven't been completed yet.
    Returns count of updated rows.
    """
    if from_date is None:
        from_date = datetime.now().strftime("%Y-%m-%d")

    easy_types = ("easy_run", "back_to_back")
    long_types = ("long_run",)
    tempo_types = ("tempo", "hills")

    updated = 0

    # Easy runs
    result = conn.execute(
        """UPDATE daily_workouts
           SET target_pace_min_per_mile = ?
           WHERE plan_id = ? AND scheduled_date >= ? AND completed = 0
             AND workout_type IN (?, ?)""",
        (targets["easy_pace"], plan_id, from_date, *easy_types),
    )
    updated += result.rowcount

    # Long runs
    result = conn.execute(
        """UPDATE daily_workouts
           SET target_pace_min_per_mile = ?
           WHERE plan_id = ? AND scheduled_date >= ? AND completed = 0
             AND workout_type IN (?)""",
        (targets["long_run_pace"], plan_id, from_date, *long_types),
    )
    updated += result.rowcount

    # Tempo
    if targets.get("tempo_pace"):
        result = conn.execute(
            """UPDATE daily_workouts
               SET target_pace_min_per_mile = ?
               WHERE plan_id = ? AND scheduled_date >= ? AND completed = 0
                 AND workout_type IN (?, ?)""",
            (targets["tempo_pace"], plan_id, from_date, *tempo_types),
        )
        updated += result.rowcount

    return updated


def format_adaptation_report(old, new, source):
    """Create a structured diff between old and new targets."""
    changes = []
    fields = [
        ("easy_pace", "Easy Pace", "min/mi"),
        ("long_run_pace", "Long Run Pace", "min/mi"),
        ("tempo_pace", "Tempo Pace", "min/mi"),
        ("threshold_pace", "Threshold Pace", "min/mi"),
        ("maf_hr", "MAF HR", "bpm"),
        ("zone2_ceiling", "Zone 2 Ceiling", "bpm"),
        ("zone3_ceiling", "Zone 3 Ceiling", "bpm"),
        ("zone4_ceiling", "Zone 4 Ceiling", "bpm"),
    ]

    for key, label, unit in fields:
        old_val = old.get(key) if old else None
        new_val = new.get(key)
        if old_val != new_val and new_val is not None:
            changes.append({
                "field": key,
                "label": label,
                "old": old_val,
                "new": new_val,
                "unit": unit,
            })

    return {
        "source": source,
        "changes": changes,
        "changed": len(changes) > 0,
    }


def set_manual_targets(conn, plan_id, easy=None, long_run=None, tempo=None,
                       threshold=None, maf_hr=None, notes=None):
    """Set targets manually from CLI. Merges provided values with current targets."""
    current = get_current_targets(conn, plan_id)
    if not current:
        return None

    new = dict(current)
    if easy is not None:
        new["easy_pace"] = _clamp(easy, EASY_PACE_MIN, EASY_PACE_MAX)
    if long_run is not None:
        new["long_run_pace"] = _clamp(long_run, EASY_PACE_MIN + 0.5, EASY_PACE_MAX + 0.5)
    if tempo is not None:
        new["tempo_pace"] = _clamp(tempo, TEMPO_PACE_MIN, TEMPO_PACE_MAX)
    if threshold is not None:
        new["threshold_pace"] = threshold
    if maf_hr is not None:
        new["maf_hr"] = maf_hr
        new["zone2_ceiling"] = maf_hr

    effective = datetime.now().strftime("%Y-%m-%d")
    row_id = _insert_targets(conn, plan_id, effective, new, "manual", notes=notes)
    return {"id": row_id, "targets": new}


def find_unprocessed_benchmarks(conn, plan_id):
    """Find completed benchmarks that haven't triggered a target adaptation."""
    rows = conn.execute(
        """SELECT pb.* FROM plan_benchmarks pb
           WHERE pb.plan_id = ? AND pb.completed = 1
             AND pb.id NOT IN (
                 SELECT trigger_benchmark_id FROM athlete_targets
                 WHERE plan_id = ? AND trigger_benchmark_id IS NOT NULL
             )
           ORDER BY pb.scheduled_date""",
        (plan_id, plan_id),
    ).fetchall()
    return [dict(r) for r in rows]
