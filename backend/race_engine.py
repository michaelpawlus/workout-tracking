"""Race Day Engine — course loading, pacing, fueling, and crew sheet generation.

Combines GPX course data, historical finisher splits, athlete training data,
and weather to produce segment-by-segment race execution plans (A/B/C scenarios).
"""

import csv
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path

import gpxpy

from .adapt import get_current_targets


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

METERS_TO_MILES = 0.000621371
METERS_TO_FEET = 3.28084
# Heat slowdown: ~1.5% per degree F above 60F (research consensus)
HEAT_SLOWDOWN_PER_DEGREE = 0.015
HEAT_BASELINE_F = 60
# Runner weight assumption for calorie calculations (lbs) — configurable later
DEFAULT_WEIGHT_LBS = 170
# Calorie burn: ~100 cal/mile on flat, adjusted for grade
BASE_CAL_PER_MILE = 100


# ---------------------------------------------------------------------------
# 1. Course Profile Loader (GPX parsing)
# ---------------------------------------------------------------------------

def parse_gpx(gpx_file_path):
    """Parse a GPX file into a list of trackpoints with cumulative distance and elevation.

    Returns list of dicts: [{lat, lon, elevation_m, cumulative_distance_m}, ...]
    """
    with open(gpx_file_path, "r") as f:
        gpx = gpxpy.parse(f)

    points = []
    cumulative_m = 0.0

    for track in gpx.tracks:
        for segment in track.segments:
            for i, pt in enumerate(segment.points):
                if i > 0:
                    prev = segment.points[i - 1]
                    cumulative_m += pt.distance_2d(prev)

                points.append({
                    "lat": pt.latitude,
                    "lon": pt.longitude,
                    "elevation_m": pt.elevation or 0.0,
                    "cumulative_distance_m": cumulative_m,
                })

    return points


def build_segments_from_gpx(points, segment_breaks_miles=None):
    """Build race segments from GPX trackpoints.

    segment_breaks_miles: list of mile markers where segments end (aid stations).
        If None, creates segments every 5 miles.
    """
    total_distance_m = points[-1]["cumulative_distance_m"] if points else 0
    total_distance_miles = total_distance_m * METERS_TO_MILES

    if segment_breaks_miles is None:
        segment_breaks_miles = []
        mile = 5.0
        while mile < total_distance_miles:
            segment_breaks_miles.append(mile)
            mile += 5.0
        segment_breaks_miles.append(total_distance_miles)

    # Ensure final segment ends at course end
    if not segment_breaks_miles or segment_breaks_miles[-1] < total_distance_miles - 0.1:
        segment_breaks_miles.append(total_distance_miles)

    segments = []
    seg_start_mile = 0.0
    seg_num = 1

    for end_mile in sorted(segment_breaks_miles):
        start_m = seg_start_mile / METERS_TO_MILES
        end_m = end_mile / METERS_TO_MILES

        seg_points = [p for p in points
                      if start_m <= p["cumulative_distance_m"] <= end_m]

        if len(seg_points) < 2:
            seg_start_mile = end_mile
            continue

        gain_ft = 0.0
        loss_ft = 0.0
        max_grade = 0.0
        grade_samples = []

        for i in range(1, len(seg_points)):
            elev_diff_m = seg_points[i]["elevation_m"] - seg_points[i - 1]["elevation_m"]
            dist_diff_m = (seg_points[i]["cumulative_distance_m"]
                           - seg_points[i - 1]["cumulative_distance_m"])

            if elev_diff_m > 0:
                gain_ft += elev_diff_m * METERS_TO_FEET
            else:
                loss_ft += abs(elev_diff_m) * METERS_TO_FEET

            if dist_diff_m > 0:
                grade = (elev_diff_m / dist_diff_m) * 100
                grade_samples.append(grade)
                max_grade = max(max_grade, abs(grade))

        distance_miles = end_mile - seg_start_mile
        avg_grade = statistics.mean(grade_samples) if grade_samples else 0.0

        segments.append({
            "segment_number": seg_num,
            "start_mile": round(seg_start_mile, 2),
            "end_mile": round(end_mile, 2),
            "distance_miles": round(distance_miles, 2),
            "elevation_gain_ft": round(gain_ft, 1),
            "elevation_loss_ft": round(loss_ft, 1),
            "avg_grade_pct": round(avg_grade, 2),
            "max_grade_pct": round(max_grade, 2),
        })

        seg_start_mile = end_mile
        seg_num += 1

    total_gain = sum(s["elevation_gain_ft"] for s in segments)
    return segments, round(total_distance_miles, 2), round(total_gain, 1)


def load_course(conn, gpx_file_path, name, year, segment_breaks_miles=None):
    """Parse GPX and store course + segments in the database.

    Returns the course_id and segment list.
    """
    points = parse_gpx(gpx_file_path)
    segments, total_dist, total_gain = build_segments_from_gpx(points, segment_breaks_miles)

    cursor = conn.execute(
        """INSERT INTO race_courses (name, year, total_distance_miles,
           total_elevation_gain_ft, gpx_file_path)
           VALUES (?, ?, ?, ?, ?)""",
        (name, year, total_dist, total_gain, str(gpx_file_path)),
    )
    course_id = cursor.lastrowid

    for seg in segments:
        conn.execute(
            """INSERT INTO race_segments
               (course_id, segment_number, start_mile, end_mile, distance_miles,
                elevation_gain_ft, elevation_loss_ft, avg_grade_pct, max_grade_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course_id, seg["segment_number"], seg["start_mile"], seg["end_mile"],
             seg["distance_miles"], seg["elevation_gain_ft"], seg["elevation_loss_ft"],
             seg["avg_grade_pct"], seg["max_grade_pct"]),
        )

    return course_id, segments, total_dist, total_gain


def update_segment_metadata(conn, segment_id, name=None, terrain_notes=None,
                            crew_accessible=None, drop_bag=None):
    """Update segment metadata (name, terrain, crew access, drop bags)."""
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if terrain_notes is not None:
        updates.append("terrain_notes = ?")
        params.append(terrain_notes)
    if crew_accessible is not None:
        updates.append("crew_accessible = ?")
        params.append(1 if crew_accessible else 0)
    if drop_bag is not None:
        updates.append("drop_bag = ?")
        params.append(1 if drop_bag else 0)

    if updates:
        params.append(segment_id)
        conn.execute(
            f"UPDATE race_segments SET {', '.join(updates)} WHERE id = ?",
            params,
        )


def get_course(conn, course_id=None, name=None):
    """Get course by ID or name (latest if multiple)."""
    if course_id:
        return dict(conn.execute("SELECT * FROM race_courses WHERE id = ?",
                                 (course_id,)).fetchone())
    if name:
        row = conn.execute(
            "SELECT * FROM race_courses WHERE name = ? ORDER BY year DESC, id DESC LIMIT 1",
            (name,),
        ).fetchone()
        return dict(row) if row else None
    # Default: latest course
    row = conn.execute(
        "SELECT * FROM race_courses ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_segments(conn, course_id):
    """Get all segments for a course, ordered by segment_number."""
    rows = conn.execute(
        "SELECT * FROM race_segments WHERE course_id = ? ORDER BY segment_number",
        (course_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 2. Historical Finisher Analysis
# ---------------------------------------------------------------------------

def import_historical_results(conn, course_id, csv_file_path, year):
    """Import historical race results from CSV.

    Expected CSV columns: runner_name, finish_time, dnf
    finish_time format: "HH:MM:SS" or seconds (int).
    Optional segment split columns: split_1, split_2, ... (in HH:MM:SS or seconds).
    """
    imported = 0
    segments = get_segments(conn, course_id)

    with open(csv_file_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            finish_seconds = _parse_time(row.get("finish_time", "0"))
            is_dnf = row.get("dnf", "0").strip().lower() in ("1", "true", "yes", "dnf")

            cursor = conn.execute(
                """INSERT INTO historical_results
                   (course_id, year, runner_name, finish_time_seconds, dnf)
                   VALUES (?, ?, ?, ?, ?)""",
                (course_id, year, row.get("runner_name", "").strip(),
                 finish_seconds, 1 if is_dnf else 0),
            )
            result_id = cursor.lastrowid
            imported += 1

            # Import segment splits if present
            for seg in segments:
                col = f"split_{seg['segment_number']}"
                if col in row and row[col].strip():
                    split_seconds = _parse_time(row[col])
                    pace = int(split_seconds / seg["distance_miles"]) if seg["distance_miles"] > 0 else 0
                    conn.execute(
                        """INSERT INTO historical_splits
                           (result_id, segment_id, split_time_seconds, pace_per_mile_seconds)
                           VALUES (?, ?, ?, ?)""",
                        (result_id, seg["id"], split_seconds, pace),
                    )

    return imported


def get_peer_cohort(conn, course_id, goal_time_seconds, window_seconds=3600):
    """Get finishers within +/- window of the goal time.

    Returns list of result dicts with their splits.
    """
    lo = goal_time_seconds - window_seconds
    hi = goal_time_seconds + window_seconds

    results = conn.execute(
        """SELECT * FROM historical_results
           WHERE course_id = ? AND dnf = 0
             AND finish_time_seconds BETWEEN ? AND ?
           ORDER BY finish_time_seconds""",
        (course_id, lo, hi),
    ).fetchall()

    cohort = []
    for r in results:
        splits = conn.execute(
            """SELECT hs.*, rs.segment_number, rs.distance_miles, rs.name as segment_name
               FROM historical_splits hs
               JOIN race_segments rs ON rs.id = hs.segment_id
               WHERE hs.result_id = ?
               ORDER BY rs.segment_number""",
            (r["id"],),
        ).fetchall()
        cohort.append({
            **dict(r),
            "splits": [dict(s) for s in splits],
        })

    return cohort


def analyze_cohort(conn, course_id, goal_time_seconds, window_seconds=3600):
    """Analyze peer cohort to produce per-segment statistics.

    Returns dict with cohort_size and per-segment median pace, variance,
    and danger zones (high variance segments).
    """
    cohort = get_peer_cohort(conn, course_id, goal_time_seconds, window_seconds)
    segments = get_segments(conn, course_id)

    if not cohort:
        return {
            "cohort_size": 0,
            "goal_time": _format_time(goal_time_seconds),
            "window_hours": window_seconds / 3600,
            "segments": [],
            "message": "No finishers found in the goal time window",
        }

    segment_stats = []
    for seg in segments:
        paces = []
        times = []
        for runner in cohort:
            for split in runner["splits"]:
                if split["segment_id"] == seg["id"]:
                    paces.append(split["pace_per_mile_seconds"])
                    times.append(split["split_time_seconds"])

        if paces:
            median_pace = statistics.median(paces)
            pace_stdev = statistics.stdev(paces) if len(paces) > 1 else 0
            median_time = statistics.median(times)
            stat = {
                "segment_number": seg["segment_number"],
                "segment_name": seg.get("name") or f"Seg {seg['segment_number']}",
                "distance_miles": seg["distance_miles"],
                "median_pace_seconds": int(median_pace),
                "median_pace_display": _format_pace(median_pace),
                "median_time_seconds": int(median_time),
                "pace_stdev_seconds": round(pace_stdev, 1),
                "sample_size": len(paces),
                "danger_zone": pace_stdev > 60,  # high variance = danger
            }
        else:
            stat = {
                "segment_number": seg["segment_number"],
                "segment_name": seg.get("name") or f"Seg {seg['segment_number']}",
                "distance_miles": seg["distance_miles"],
                "median_pace_seconds": None,
                "median_pace_display": "N/A",
                "median_time_seconds": None,
                "pace_stdev_seconds": None,
                "sample_size": 0,
                "danger_zone": False,
            }
        segment_stats.append(stat)

    # Identify slowdown curve: how much did the cohort slow in back half?
    first_half = [s for s in segment_stats
                  if s["median_pace_seconds"] and s["segment_number"] <= len(segments) // 2]
    second_half = [s for s in segment_stats
                   if s["median_pace_seconds"] and s["segment_number"] > len(segments) // 2]

    slowdown_pct = None
    if first_half and second_half:
        avg_first = statistics.mean([s["median_pace_seconds"] for s in first_half])
        avg_second = statistics.mean([s["median_pace_seconds"] for s in second_half])
        slowdown_pct = round(((avg_second - avg_first) / avg_first) * 100, 1)

    finish_times = [r["finish_time_seconds"] for r in cohort]
    return {
        "cohort_size": len(cohort),
        "goal_time": _format_time(goal_time_seconds),
        "window_hours": window_seconds / 3600,
        "median_finish_time": _format_time(int(statistics.median(finish_times))),
        "fastest_finish": _format_time(min(finish_times)),
        "slowest_finish": _format_time(max(finish_times)),
        "slowdown_pct": slowdown_pct,
        "danger_zones": [s["segment_name"] for s in segment_stats if s["danger_zone"]],
        "segments": segment_stats,
    }


# ---------------------------------------------------------------------------
# 3. Personalized Pace Plan Generator
# ---------------------------------------------------------------------------

def grade_adjusted_pace(base_pace_seconds, grade_pct):
    """Apply grade adjustment to base pace (seconds per mile).

    Uphill: adds time based on grade (steeper = slower).
    Downhill: recovers some time but with diminishing returns (quad cost).

    Formula based on Strava GAP / Minetti research:
      - Uphill: +12 sec/mile per 1% grade
      - Downhill: -6 sec/mile per 1% grade (capped at -8% grade benefit)
    """
    if grade_pct > 0:
        adjustment = grade_pct * 12  # slower on uphills
    else:
        effective_grade = max(grade_pct, -8)  # cap downhill benefit
        adjustment = effective_grade * 6  # partial recovery on downhill
    return base_pace_seconds + adjustment


def fatigue_multiplier(mile, total_miles, training_fade=None, cohort_slowdown_pct=None):
    """Calculate fatigue-based pace multiplier at a given point in the race.

    Blends training data (B2B fade) with peer cohort slowdown curve.
    Returns multiplier > 1.0 (e.g., 1.15 = 15% slower than fresh pace).
    """
    progress = mile / total_miles if total_miles > 0 else 0

    # Base fatigue model: exponential curve, minimal early, steep late
    # At mile 0: 1.0, at mile 60: ~1.10, at mile 80: ~1.20, at mile 100: ~1.35
    base_fatigue = 1.0 + 0.35 * (progress ** 2.5)

    # Blend with cohort slowdown if available
    if cohort_slowdown_pct is not None and cohort_slowdown_pct > 0:
        # Scale cohort slowdown into the fatigue curve
        cohort_factor = cohort_slowdown_pct / 100
        # Weight cohort data more as race progresses
        cohort_weight = progress * 0.6  # up to 60% cohort influence at end
        training_weight = 1.0 - cohort_weight
        base_fatigue = (base_fatigue * training_weight
                        + (1.0 + cohort_factor * progress) * cohort_weight)

    # Blend with training fade data if available
    if training_fade is not None and training_fade > 0:
        # training_fade = % slowdown observed in B2B long runs
        fade_factor = training_fade / 100
        # Apply training fade influence in middle miles
        if 0.3 < progress < 0.8:
            base_fatigue *= (1.0 + fade_factor * 0.3)

    return round(base_fatigue, 4)


def heat_adjustment(base_pace_seconds, temp_f):
    """Adjust pace for heat. Returns adjusted pace in seconds/mile."""
    if temp_f is None or temp_f <= HEAT_BASELINE_F:
        return base_pace_seconds
    degrees_above = temp_f - HEAT_BASELINE_F
    slowdown = base_pace_seconds * HEAT_SLOWDOWN_PER_DEGREE * degrees_above
    return base_pace_seconds + slowdown


def get_training_fade(conn, plan_id):
    """Calculate pace fade from back-to-back long runs in training.

    Returns fade percentage (how much slower day-2 was than day-1).
    """
    rows = conn.execute(
        """SELECT rf.actual_pace, dw.workout_type, dw.scheduled_date
           FROM run_feedback rf
           JOIN daily_workouts dw ON dw.id = rf.daily_workout_id
           WHERE rf.plan_id = ? AND dw.workout_type IN ('long_run', 'back_to_back')
             AND rf.actual_pace IS NOT NULL
           ORDER BY dw.scheduled_date""",
        (plan_id,),
    ).fetchall()

    if len(rows) < 2:
        return None

    # Look for consecutive-day pairs (Saturday long + Sunday B2B)
    fades = []
    for i in range(len(rows) - 1):
        date1 = datetime.strptime(rows[i]["scheduled_date"], "%Y-%m-%d")
        date2 = datetime.strptime(rows[i + 1]["scheduled_date"], "%Y-%m-%d")
        if (date2 - date1).days == 1:
            pace1 = rows[i]["actual_pace"]
            pace2 = rows[i + 1]["actual_pace"]
            if pace1 and pace2 and pace1 > 0:
                fade_pct = ((pace2 - pace1) / pace1) * 100
                if fade_pct > 0:
                    fades.append(fade_pct)

    return round(statistics.mean(fades), 1) if fades else None


def generate_race_plan(conn, course_id, plan_id, goal_time_seconds,
                       weather_temp_f=None, start_time="05:00"):
    """Generate A/B/C race execution plans.

    Returns dict with three scenario plans, each containing per-segment details.
    """
    segments = get_segments(conn, course_id)
    course = get_course(conn, course_id)
    targets = get_current_targets(conn, plan_id) if plan_id else None

    if not segments:
        return {"error": "No segments found for this course"}

    total_miles = course["total_distance_miles"]

    # Base pace from athlete targets or goal time
    if targets:
        base_pace_min = targets["long_run_pace"]  # min/mile
        base_pace_sec = base_pace_min * 60  # sec/mile
    else:
        base_pace_sec = goal_time_seconds / total_miles

    # Get cohort data if available
    cohort_data = analyze_cohort(conn, course_id, goal_time_seconds)
    cohort_slowdown = cohort_data.get("slowdown_pct")

    # Get training fade
    training_fade = get_training_fade(conn, plan_id) if plan_id else None

    # Generate three scenarios
    scenarios = {
        "A": {"label": "Goal Pace", "multiplier": 1.0},
        "B": {"label": "Conservative", "multiplier": 1.10},
        "C": {"label": "Survival", "multiplier": 1.25},
    }

    plans = {}
    for scenario_key, scenario in scenarios.items():
        plan_segments = []
        cumulative_seconds = 0

        for seg in segments:
            mid_mile = (seg["start_mile"] + seg["end_mile"]) / 2

            # Start with base pace adjusted for scenario
            seg_pace = base_pace_sec * scenario["multiplier"]

            # Apply grade adjustment
            seg_pace = grade_adjusted_pace(seg_pace, seg["avg_grade_pct"])

            # Apply fatigue curve
            fatigue = fatigue_multiplier(
                mid_mile, total_miles, training_fade, cohort_slowdown
            )
            seg_pace *= fatigue

            # Apply heat adjustment
            if weather_temp_f:
                seg_pace = heat_adjustment(seg_pace, weather_temp_f)

            # C plan: walk all climbs over 5% grade
            if scenario_key == "C" and seg["avg_grade_pct"] > 5:
                seg_pace = max(seg_pace, 20 * 60)  # 20 min/mile walk pace

            # Blend with cohort segment data if available
            if cohort_data.get("segments"):
                for cs in cohort_data["segments"]:
                    if (cs["segment_number"] == seg["segment_number"]
                            and cs["median_pace_seconds"]):
                        cohort_pace = cs["median_pace_seconds"] * scenario["multiplier"]
                        # 40% cohort, 60% model
                        seg_pace = seg_pace * 0.6 + cohort_pace * 0.4

            seg_pace = int(seg_pace)
            seg_time = int(seg_pace * seg["distance_miles"])
            cumulative_seconds += seg_time

            # Calculate ETA
            start_dt = datetime.strptime(start_time, "%H:%M")
            eta_dt = start_dt + timedelta(seconds=cumulative_seconds)
            eta_str = eta_dt.strftime("%H:%M")
            if cumulative_seconds >= 86400:
                days = cumulative_seconds // 86400
                eta_str = f"+{days}d {eta_str}"

            plan_segments.append({
                "segment_number": seg["segment_number"],
                "segment_name": seg.get("name") or f"Mile {seg['start_mile']}-{seg['end_mile']}",
                "distance_miles": seg["distance_miles"],
                "elevation_gain_ft": seg["elevation_gain_ft"],
                "avg_grade_pct": seg["avg_grade_pct"],
                "target_pace_seconds": seg_pace,
                "target_pace_display": _format_pace(seg_pace),
                "estimated_time_seconds": seg_time,
                "estimated_time_display": _format_time(seg_time),
                "cumulative_time_seconds": cumulative_seconds,
                "cumulative_time_display": _format_time(cumulative_seconds),
                "aid_station_eta": eta_str,
                "fatigue_multiplier": fatigue,
            })

        plans[scenario_key] = {
            "scenario": scenario_key,
            "label": scenario["label"],
            "total_time_seconds": cumulative_seconds,
            "total_time_display": _format_time(cumulative_seconds),
            "segments": plan_segments,
        }

    return {
        "course": course["name"],
        "course_id": course_id,
        "goal_time": _format_time(goal_time_seconds),
        "weather_temp_f": weather_temp_f,
        "start_time": start_time,
        "base_pace_display": _format_pace(base_pace_sec),
        "training_fade_pct": training_fade,
        "cohort_slowdown_pct": cohort_slowdown,
        "cohort_size": cohort_data.get("cohort_size", 0),
        "plans": plans,
    }


def save_race_plan(conn, course_id, plan_id, goal_time_seconds,
                   weather_temp_f, plans):
    """Persist generated race plans to the database."""
    saved_ids = {}
    for scenario_key, plan in plans.items():
        cursor = conn.execute(
            """INSERT INTO race_plans
               (course_id, plan_id, goal_time_seconds, weather_temp_f, scenario)
               VALUES (?, ?, ?, ?, ?)""",
            (course_id, plan_id, goal_time_seconds, weather_temp_f, scenario_key),
        )
        race_plan_id = cursor.lastrowid
        saved_ids[scenario_key] = race_plan_id

        segments = get_segments(conn, course_id)
        seg_by_num = {s["segment_number"]: s for s in segments}

        for ps in plan["segments"]:
            seg = seg_by_num.get(ps["segment_number"])
            if seg:
                conn.execute(
                    """INSERT INTO race_plan_segments
                       (race_plan_id, segment_id, target_pace_seconds,
                        estimated_time_seconds, cumulative_time_seconds,
                        aid_station_eta)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (race_plan_id, seg["id"], ps["target_pace_seconds"],
                     ps["estimated_time_seconds"], ps["cumulative_time_seconds"],
                     ps["aid_station_eta"]),
                )

    return saved_ids


# ---------------------------------------------------------------------------
# 4. Fueling Schedule Generator
# ---------------------------------------------------------------------------

def calorie_burn_per_mile(grade_pct, weight_lbs=DEFAULT_WEIGHT_LBS):
    """Estimate calorie burn per mile adjusted for grade and weight.

    Base: ~100 cal/mile for 150lb runner on flat.
    Grade: +5% per 1% uphill grade.
    Weight: scales linearly with body weight.
    """
    weight_factor = weight_lbs / 150
    grade_factor = 1.0 + max(0, grade_pct) * 0.05
    return round(BASE_CAL_PER_MILE * weight_factor * grade_factor, 1)


def generate_fueling_plan(conn, course_id, plan_segments, weight_lbs=DEFAULT_WEIGHT_LBS):
    """Generate per-segment fueling targets.

    plan_segments: list of segment dicts from generate_race_plan().
    Returns enriched segments with calorie/sodium/fluid targets.
    """
    segments = get_segments(conn, course_id)
    seg_by_num = {s["segment_number"]: s for s in segments}
    fueled = []
    cumulative_cal_burned = 0
    cumulative_cal_intake = 0
    bonk_risk_hours = 0

    for ps in plan_segments:
        seg = seg_by_num.get(ps["segment_number"], {})
        grade = seg.get("avg_grade_pct", 0)
        distance = ps["distance_miles"]
        time_hours = ps["estimated_time_seconds"] / 3600

        # Calorie burn for this segment
        cal_per_mile = calorie_burn_per_mile(grade, weight_lbs)
        cal_burned = round(cal_per_mile * distance)
        cumulative_cal_burned += cal_burned

        # Intake targets: scale with effort, not just clock time
        # Base: 250 cal/hr, up to 300 on climbs
        cal_per_hr = 250 if grade <= 3 else 275 if grade <= 6 else 200  # reduce on steep climbs
        cal_target = round(cal_per_hr * time_hours)
        cumulative_cal_intake += cal_target

        # Sodium: 500-1000 mg/hr, higher in heat/effort
        sodium_per_hr = 700  # mid-range default
        sodium_target = round(sodium_per_hr * time_hours)

        # Fluid: 20-24 oz/hr
        fluid_per_hr = 22  # oz
        fluid_target = round(fluid_per_hr * time_hours)

        # Fueling notes
        notes = []
        if grade > 5:
            notes.append("Steep climb — eat BEFORE this segment, hard to eat while climbing")
        if grade < -3:
            notes.append("Downhill — good opportunity to eat and drink")
        if ps["segment_number"] == 1:
            notes.append("Don't start fueling too early — first gel at 45-60 min")

        # Check bonk risk: cumulative intake < 80% of burn for 3+ segments
        deficit_ratio = cumulative_cal_intake / cumulative_cal_burned if cumulative_cal_burned > 0 else 1.0
        if deficit_ratio < 0.80:
            bonk_risk_hours += time_hours
            if bonk_risk_hours >= 3:
                notes.append("BONK RISK: Calorie deficit exceeding 20% for 3+ hours!")
        else:
            bonk_risk_hours = 0

        fueled.append({
            **ps,
            "cal_burned": cal_burned,
            "calories_target": cal_target,
            "sodium_mg_target": sodium_target,
            "fluid_oz_target": fluid_target,
            "cal_per_hr": cal_per_hr,
            "cumulative_cal_burned": cumulative_cal_burned,
            "cumulative_cal_intake": cumulative_cal_intake,
            "deficit_pct": round((1 - deficit_ratio) * 100, 1) if deficit_ratio < 1 else 0,
            "fueling_notes": "; ".join(notes) if notes else None,
        })

    return fueled


# ---------------------------------------------------------------------------
# 5. Crew Sheet Generator
# ---------------------------------------------------------------------------

def generate_crew_sheet(conn, course_id, plans, start_time="05:00",
                        weight_lbs=DEFAULT_WEIGHT_LBS):
    """Generate crew sheet with multi-scenario ETAs and action items.

    Returns crew-accessible aid station stops with A/B/C ETAs,
    gear/nutrition needs, and decision trees.
    """
    segments = get_segments(conn, course_id)
    course = get_course(conn, course_id)

    # Get fueling data for each plan
    plan_fueling = {}
    for key, plan in plans.items():
        plan_fueling[key] = generate_fueling_plan(
            conn, course_id, plan["segments"], weight_lbs
        )

    crew_stops = []
    start_dt = datetime.strptime(start_time, "%H:%M")

    for seg in segments:
        if not seg.get("crew_accessible"):
            continue

        stop = {
            "segment_number": seg["segment_number"],
            "station_name": seg.get("name") or f"Mile {seg['end_mile']}",
            "mile": seg["end_mile"],
            "drop_bag": bool(seg.get("drop_bag")),
            "etas": {},
            "fueling": {},
            "decision_tree": [],
        }

        for key, plan in plans.items():
            seg_data = None
            for ps in plan["segments"]:
                if ps["segment_number"] == seg["segment_number"]:
                    seg_data = ps
                    break

            if seg_data:
                eta_dt = start_dt + timedelta(seconds=seg_data["cumulative_time_seconds"])
                eta_display = eta_dt.strftime("%I:%M %p")
                elapsed = _format_time(seg_data["cumulative_time_seconds"])

                stop["etas"][key] = {
                    "clock_time": eta_display,
                    "elapsed": elapsed,
                    "cumulative_seconds": seg_data["cumulative_time_seconds"],
                }

        # Decision tree
        if "A" in stop["etas"] and "C" in stop["etas"]:
            a_time = stop["etas"]["A"]["clock_time"]
            c_time = stop["etas"]["C"]["clock_time"]
            stop["decision_tree"] = [
                f"If runner arrives by {a_time}: ON PLAN — quick turnaround, "
                f"hand pre-staged bottles and food",
                f"If runner arrives after {c_time}: SURVIVAL MODE — prioritize "
                f"calories, morale, and blister care. Slow the runner down",
                f"Between {a_time}-{c_time}: ADJUSTED — runner is off A plan "
                f"but can still finish strong. Encourage steady effort",
            ]

        # Night transition check
        for key in ("A", "B", "C"):
            if key in stop["etas"]:
                eta_dt = start_dt + timedelta(
                    seconds=stop["etas"][key]["cumulative_seconds"]
                )
                hour = eta_dt.hour
                if 18 <= hour <= 20:
                    stop["decision_tree"].append(
                        f"NIGHT TRANSITION ({key} plan): Have headlamp, extra layer, "
                        f"reflective vest ready"
                    )

        crew_stops.append(stop)

    return {
        "course": course["name"] if course else "Unknown",
        "total_miles": course["total_distance_miles"] if course else 0,
        "start_time": start_time,
        "crew_stops": crew_stops,
        "plans_summary": {
            key: {
                "label": plan["label"],
                "total_time": plan["total_time_display"],
            }
            for key, plan in plans.items()
        },
    }


def crew_sheet_to_markdown(crew_sheet):
    """Render crew sheet as markdown for Obsidian / printing."""
    lines = [
        f"# Crew Sheet — {crew_sheet['course']}",
        f"**Total Distance:** {crew_sheet['total_miles']} miles",
        f"**Start Time:** {crew_sheet['start_time']}",
        "",
        "## Plan Summary",
        "",
        "| Scenario | Label | Estimated Finish |",
        "|---|---|---|",
    ]

    for key in ("A", "B", "C"):
        if key in crew_sheet["plans_summary"]:
            p = crew_sheet["plans_summary"][key]
            lines.append(f"| {key} | {p['label']} | {p['total_time']} |")

    lines.extend(["", "---", ""])

    for stop in crew_sheet["crew_stops"]:
        lines.append(f"## {stop['station_name']} (Mile {stop['mile']})")
        if stop["drop_bag"]:
            lines.append("**Drop Bag Available**")
        lines.append("")

        # ETA table
        lines.append("| Plan | ETA | Elapsed |")
        lines.append("|---|---|---|")
        for key in ("A", "B", "C"):
            if key in stop["etas"]:
                e = stop["etas"][key]
                lines.append(f"| {key} | {e['clock_time']} | {e['elapsed']} |")

        lines.append("")

        # Decision tree
        if stop["decision_tree"]:
            lines.append("### Decision Tree")
            for d in stop["decision_tree"]:
                lines.append(f"- {d}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. Live Race Tracking
# ---------------------------------------------------------------------------

def race_checkin(conn, race_plan_id, segment_id, arrival_time, elapsed_seconds=None,
                 notes=None):
    """Log arrival at an aid station during the race."""
    conn.execute(
        """INSERT INTO race_checkins
           (race_plan_id, segment_id, actual_arrival_time, actual_elapsed_seconds, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (race_plan_id, segment_id, arrival_time, elapsed_seconds, notes),
    )


def get_race_status(conn, race_plan_id):
    """Get current race status: actual vs planned at each checkpoint."""
    checkins = conn.execute(
        """SELECT rc.*, rs.segment_number, rs.name as segment_name, rs.end_mile,
                  rps.cumulative_time_seconds as planned_seconds,
                  rps.aid_station_eta as planned_eta
           FROM race_checkins rc
           JOIN race_segments rs ON rs.id = rc.segment_id
           JOIN race_plan_segments rps ON rps.segment_id = rc.segment_id
                AND rps.race_plan_id = rc.race_plan_id
           WHERE rc.race_plan_id = ?
           ORDER BY rs.segment_number""",
        (race_plan_id,),
    ).fetchall()

    if not checkins:
        return {"race_plan_id": race_plan_id, "checkins": [], "status": "Not started"}

    status_entries = []
    for c in checkins:
        delta = None
        if c["actual_elapsed_seconds"] and c["planned_seconds"]:
            delta = c["actual_elapsed_seconds"] - c["planned_seconds"]

        status_entries.append({
            "segment": c["segment_number"],
            "station": c["segment_name"] or f"Mile {c['end_mile']}",
            "mile": c["end_mile"],
            "planned_elapsed": _format_time(c["planned_seconds"]),
            "actual_elapsed": _format_time(c["actual_elapsed_seconds"]) if c["actual_elapsed_seconds"] else "?",
            "delta_seconds": delta,
            "delta_display": f"+{_format_time(delta)}" if delta and delta > 0
                             else f"-{_format_time(abs(delta))}" if delta and delta < 0
                             else "On plan" if delta == 0 else "?",
            "notes": c["notes"],
        })

    last = checkins[-1]
    overall_delta = None
    if last["actual_elapsed_seconds"] and last["planned_seconds"]:
        overall_delta = last["actual_elapsed_seconds"] - last["planned_seconds"]

    if overall_delta is None:
        status_label = "In progress"
    elif overall_delta <= 0:
        status_label = "Ahead of plan"
    elif overall_delta <= 1800:
        status_label = "Slightly behind"
    else:
        status_label = "Behind plan"

    return {
        "race_plan_id": race_plan_id,
        "status": status_label,
        "last_checkin_mile": last["end_mile"],
        "overall_delta_seconds": overall_delta,
        "overall_delta_display": (
            f"+{_format_time(overall_delta)}" if overall_delta and overall_delta > 0
            else f"-{_format_time(abs(overall_delta))}" if overall_delta and overall_delta < 0
            else "On plan"
        ) if overall_delta is not None else "?",
        "checkins": status_entries,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(time_str):
    """Parse 'HH:MM:SS' or raw seconds into total seconds."""
    time_str = str(time_str).strip()
    if ":" in time_str:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    try:
        return int(float(time_str))
    except ValueError:
        return 0


def _format_time(seconds):
    """Format seconds as HH:MM:SS."""
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _format_pace(seconds_per_mile):
    """Format seconds/mile as MM:SS/mi."""
    if seconds_per_mile is None:
        return "N/A"
    seconds_per_mile = int(seconds_per_mile)
    m = seconds_per_mile // 60
    s = seconds_per_mile % 60
    return f"{m}:{s:02d}/mi"
