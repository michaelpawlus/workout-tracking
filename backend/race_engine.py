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
# 1b. Aid-Station Import (segment breaks + names + crew/drop-bag flags)
# ---------------------------------------------------------------------------

def _truthy(value):
    """Interpret a CSV cell as a boolean flag.

    Accepts 1/0, yes/no, true/false, x, and crew-style codes like ``50/100``
    or ``100`` (any non-empty, non-falsey value counts as set).
    """
    if value is None:
        return False
    s = str(value).strip().lower()
    if s in ("", "0", "no", "false", "n", "-"):
        return False
    return True


def read_aid_stations_csv(csv_file_path):
    """Read an aid-station file into normalized station dicts.

    Expected columns: ``mile``, ``name`` (required); ``crew``, ``drop_bag``,
    ``notes`` (optional). Lines beginning with ``#`` and blank lines are skipped
    so the file can carry provenance/comments. Returns a list sorted by mile.
    """
    stations = []
    with open(csv_file_path, "r") as f:
        rows = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
        reader = csv.DictReader(rows)
        for row in reader:
            mile_raw = (row.get("mile") or "").strip()
            name = (row.get("name") or "").strip()
            if not mile_raw or not name:
                continue
            stations.append({
                "mile": round(float(mile_raw), 2),
                "name": name,
                "crew": _truthy(row.get("crew")),
                "drop_bag": _truthy(row.get("drop_bag")),
                "notes": (row.get("notes") or "").strip() or None,
            })
    return sorted(stations, key=lambda s: s["mile"])


def build_aid_station_segments(points, stations):
    """Re-derive segments from GPX trackpoints using aid stations as breaks.

    Each station's mile becomes a segment-end break; the resulting segment is
    named after the station at its end and inherits its crew/drop-bag flags and
    notes. Returns ``(segments, total_distance_miles, total_gain_ft)``.
    """
    stations = sorted(stations, key=lambda s: s["mile"])
    breaks = [s["mile"] for s in stations]

    # The guide's aid miles and the GPX distance rarely match exactly (issue #18
    # flags ~100.7 guide mi vs ~101.8 GPX mi). The last station is the finish, so
    # snap its break to the true course end — otherwise build_segments_from_gpx
    # appends an unnamed tail segment that would mislabel as a duplicate finish.
    if points and breaks:
        total_miles = points[-1]["cumulative_distance_m"] * METERS_TO_MILES
        breaks[-1] = max(breaks[-1], round(total_miles, 2))

    segments, total_dist, total_gain = build_segments_from_gpx(points, breaks)

    for seg in segments:
        # Match each rebuilt segment to the station nearest its end mile. Index
        # matching is fragile if a sparse segment gets skipped, so match by mile.
        station = min(stations, key=lambda s: abs(s["mile"] - seg["end_mile"]))
        seg["name"] = station["name"]
        seg["crew_accessible"] = 1 if station["crew"] else 0
        seg["drop_bag"] = 1 if station["drop_bag"] else 0
        seg["terrain_notes"] = station["notes"]

    return segments, total_dist, total_gain


def _course_dependents(conn, course_id):
    """Count saved rows that reference this course's segments and would be lost
    on a rebuild (FK ON DELETE CASCADE clears them when segments are replaced)."""
    return {
        "historical_splits": conn.execute(
            """SELECT COUNT(*) FROM historical_splits
               WHERE segment_id IN (SELECT id FROM race_segments WHERE course_id = ?)""",
            (course_id,),
        ).fetchone()[0],
        "race_plan_segments": conn.execute(
            """SELECT COUNT(*) FROM race_plan_segments
               WHERE segment_id IN (SELECT id FROM race_segments WHERE course_id = ?)""",
            (course_id,),
        ).fetchone()[0],
        "race_checkins": conn.execute(
            """SELECT COUNT(*) FROM race_checkins
               WHERE segment_id IN (SELECT id FROM race_segments WHERE course_id = ?)""",
            (course_id,),
        ).fetchone()[0],
    }


def import_aid_stations(conn, stations, course_id=None, dry_run=False):
    """Rebuild a loaded course's segments from an aid-station list.

    Re-parses the course's stored GPX, breaks it at the aid-station miles, names
    each segment after the station at its end, and sets crew/drop-bag flags —
    replacing the course's segments in place (no duplicate course row). Pass
    ``dry_run=True`` to preview without writing.

    Returns a result dict with the rebuilt segments, totals, and a ``dependents``
    count of saved splits/plans/check-ins that a write would cascade-delete.
    """
    course = get_course(conn, course_id=course_id)
    if not course:
        raise ValueError("No course loaded. Run `ultra race load-course` first.")

    gpx_path = course.get("gpx_file_path")
    if not gpx_path or not Path(gpx_path).exists():
        raise FileNotFoundError(
            f"Course GPX not found at {gpx_path!r}. The aid-station import "
            "recomputes per-segment elevation from the original GPX, so the "
            "file referenced by the loaded course must be present."
        )

    points = parse_gpx(gpx_path)
    segments, total_dist, total_gain = build_aid_station_segments(points, stations)
    dependents = _course_dependents(conn, course["id"])

    result = {
        "course_id": course["id"],
        "course": course["name"],
        "total_distance_miles": total_dist,
        "total_elevation_gain_ft": total_gain,
        "segment_count": len(segments),
        "crew_stations": [s["name"] for s in segments if s["crew_accessible"]],
        "drop_bag_stations": [s["name"] for s in segments if s["drop_bag"]],
        "dependents": dependents,
        "segments": segments,
        "applied": False,
    }

    if dry_run:
        return result

    conn.execute("DELETE FROM race_segments WHERE course_id = ?", (course["id"],))
    for seg in segments:
        conn.execute(
            """INSERT INTO race_segments
               (course_id, segment_number, name, start_mile, end_mile, distance_miles,
                elevation_gain_ft, elevation_loss_ft, avg_grade_pct, max_grade_pct,
                terrain_notes, crew_accessible, drop_bag)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course["id"], seg["segment_number"], seg["name"], seg["start_mile"],
             seg["end_mile"], seg["distance_miles"], seg["elevation_gain_ft"],
             seg["elevation_loss_ft"], seg["avg_grade_pct"], seg["max_grade_pct"],
             seg["terrain_notes"], seg["crew_accessible"], seg["drop_bag"]),
        )

    # Keep the course totals consistent with the new segmentation.
    conn.execute(
        """UPDATE race_courses SET total_distance_miles = ?,
           total_elevation_gain_ft = ? WHERE id = ?""",
        (total_dist, total_gain, course["id"]),
    )

    result["applied"] = True
    return result


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


def fatigue_multiplier(mile, total_miles, training_fade=None, cohort_slowdown_pct=None,
                       historical_fade_pct=None):
    """Calculate fatigue-based pace multiplier at a given point in the race.

    Blends training data (B2B fade), peer cohort slowdown curve, and the
    athlete's own prior-race late fade. Returns multiplier > 1.0 (e.g., 1.15 =
    15% slower than fresh pace).
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

    # Bias the late race toward the athlete's documented prior-race fade.
    # Bounded and back-loaded so a ~20% historical positive split adds only a
    # few percent at the very end, where the athlete has historically collapsed.
    if historical_fade_pct is not None and historical_fade_pct > 0 and progress > 0.5:
        hist_factor = historical_fade_pct / 100
        base_fatigue *= (1.0 + hist_factor * 0.15 * progress)

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

    # Get the athlete's own prior-race late fade (same-distance efforts).
    from . import historical  # lazy: historical imports race_engine helpers
    try:
        historical_fade = historical.get_historical_fade(conn, target_distance=total_miles)
    except Exception:
        historical_fade = None

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
                mid_mile, total_miles, training_fade, cohort_slowdown,
                historical_fade,
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
        "historical_fade_pct": historical_fade,
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
# 5b. Crew Manual Generator (issue #12)
#
# A governor-based crew manual: pace to a realistic target (26h for BR100, not
# the 24h stretch goal) and render, per crew-accessible aid station, the ETA,
# fuel to hand for the next leg, and the athlete's cooling / chafing / kit
# protocol. Everything race- or athlete-specific lives in a checked-in YAML
# profile (backend/data/br100_crew_protocol.yaml) so a second race is a second
# profile, not a code change.
#
# ETAs come from one of two sources, in priority order:
#   1. a peer-split skeleton (a real finisher's cumulative splits, scaled to the
#      governor goal) — captures the late-race fade shape (issue #14), OR
#   2. the engine's grade+fade race plan (the goal-pace "A" scenario).
# ---------------------------------------------------------------------------

# Required keys validated on load so a half-filled profile fails loudly.
_PROTOCOL_REQUIRED = {
    "meta": ("start_time", "governor_goal_time"),
    "fueling": ("carb_g_per_hr", "gel_carb_g", "sodium_mg_per_hr"),
}


def load_crew_protocol(path):
    """Load and validate a crew/race-execution protocol profile (YAML).

    Returns the parsed dict. Raises ``FileNotFoundError`` if the file is missing
    and ``ValueError`` if required keys are absent, so a malformed profile fails
    with a clear message rather than rendering a broken manual.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dep declared in requirements
        raise ValueError(
            "PyYAML is required to read crew protocol profiles "
            "(`pip install pyyaml`)."
        ) from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Crew protocol profile not found: {path}")

    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Crew protocol profile is not a mapping: {path}")

    missing = []
    for section, keys in _PROTOCOL_REQUIRED.items():
        if section not in data or not isinstance(data[section], dict):
            missing.append(section)
            continue
        for key in keys:
            if data[section].get(key) is None:
                missing.append(f"{section}.{key}")
    if missing:
        raise ValueError(
            f"Crew protocol profile {path} is missing required keys: "
            + ", ".join(missing)
        )

    return data


def load_split_skeleton(path):
    """Load a peer finisher's cumulative splits to use as a pacing skeleton.

    CSV columns: ``mile`` and ``elapsed`` (HH:MM:SS) are required; ``name`` is
    optional. Lines starting with ``#`` are ignored. Returns a dict with the
    total distance/time and an ascending list of ``(mile, seconds)`` points
    anchored at ``(0, 0)``. The shape (not the absolute times) is what matters —
    callers scale it to the governor goal via ``eta_seconds_from_skeleton``.
    """
    points = []
    names = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
        for row in reader:
            if not row.get("mile") or not row.get("elapsed"):
                continue
            mile = float(row["mile"])
            secs = _parse_time(row["elapsed"])
            points.append((mile, secs))
            if row.get("name"):
                names[mile] = row["name"].strip()

    points.sort(key=lambda p: p[0])
    if not points:
        raise ValueError(f"No usable split rows in skeleton CSV: {path}")
    if points[0][0] > 0:
        points.insert(0, (0.0, 0))

    return {
        "total_miles": points[-1][0],
        "total_seconds": points[-1][1],
        "points": points,
        "names": names,
    }


def eta_seconds_from_skeleton(skeleton, mile, course_total_miles, goal_seconds):
    """Scaled, fade-shaped elapsed seconds at ``mile`` for the governor goal.

    Maps the target mile to the same *fraction* of the skeleton's course (so a
    100.5 mi analog maps cleanly onto a 101.8 mi course), interpolates the
    finisher's cumulative time at that fraction, then scales the whole curve so
    the finish lands exactly on ``goal_seconds``. Preserves the positive-split
    shape instead of assuming even pacing.
    """
    total_m = skeleton["total_miles"]
    total_s = skeleton["total_seconds"]
    if total_m <= 0 or total_s <= 0:
        return 0.0
    frac = mile / course_total_miles if course_total_miles else 0
    target_mile = min(max(frac * total_m, 0), total_m)

    pts = skeleton["points"]
    raw = pts[-1][1]
    for (m0, s0), (m1, s1) in zip(pts, pts[1:]):
        if target_mile <= m1:
            span = (m1 - m0) or 1
            raw = s0 + (s1 - s0) * (target_mile - m0) / span
            break

    return raw * (goal_seconds / total_s)


def _sodium_per_hr(protocol, hot):
    """Resolve the working sodium rate (mg/hr) from the profile, hot-aware."""
    fueling = protocol.get("fueling", {})
    if hot and fueling.get("sodium_mg_per_hr_hot"):
        return fueling["sodium_mg_per_hr_hot"]
    rate = fueling.get("sodium_mg_per_hr")
    if isinstance(rate, (list, tuple)) and rate:
        return rate[-1]  # top of the range as the working target
    return rate


def _avg_rate(value):
    """Mean of a [lo, hi] range, or the scalar itself."""
    if isinstance(value, (list, tuple)) and value:
        return sum(value) / len(value)
    return value


def generate_crew_manual(conn, course_id, protocol, goal_time_seconds=None,
                         start_time=None, weather_temp_f=None, skeleton=None,
                         weight_lbs=DEFAULT_WEIGHT_LBS):
    """Build a structured crew manual paced to the governor target.

    ETAs come from ``skeleton`` (a peer-split pacing skeleton) when provided,
    otherwise from the engine's goal-pace race plan. Per-leg fuel is computed
    from the athlete's protocol rates (carb g/hr, sodium mg/hr) so the numbers
    match the documented plan. Returns a dict ready for ``crew_manual_to_markdown``.
    """
    meta = protocol.get("meta", {})
    fueling = protocol.get("fueling", {})

    if goal_time_seconds is None:
        goal_time_seconds = _parse_time(meta.get("governor_goal_time", "26:00:00"))
    if start_time is None:
        start_time = meta.get("start_time", "05:00")

    course = get_course(conn, course_id)
    segments = get_segments(conn, course_id)
    total_miles = course["total_distance_miles"] if course else 0

    # Cumulative elapsed seconds at each segment's end mile, from either source.
    if skeleton:
        cum_by_num = {
            s["segment_number"]: eta_seconds_from_skeleton(
                skeleton, s["end_mile"], total_miles, goal_time_seconds)
            for s in segments
        }
        eta_source = "peer-split skeleton"
        gov_finish_display = _format_time(goal_time_seconds)
    else:
        # Engine fallback: force GOAL-based pacing so the manual's ETAs honor the
        # governor. Passing the active plan id would make generate_race_plan base
        # pace on the athlete's *current training* targets (long_run_pace) instead,
        # letting ETAs/fuel/night-kit timing drift off the advertised governor.
        # (The skeleton path above already scales the curve to the goal.)
        race_plan = generate_race_plan(
            conn, course_id, None, goal_time_seconds,
            weather_temp_f=weather_temp_f, start_time=start_time,
        )
        gov = race_plan["plans"]["A"]["segments"]
        cum_by_num = {s["segment_number"]: s["cumulative_time_seconds"] for s in gov}
        eta_source = "engine model (grade + fade)"
        gov_finish_display = race_plan["plans"]["A"]["total_time_display"]

    hot_threshold = (protocol.get("cooling") or {}).get("hot_threshold_f")
    hot = (weather_temp_f is not None and hot_threshold is not None
           and weather_temp_f >= hot_threshold)

    carb_per_hr = fueling.get("carb_g_per_hr", 60)
    gel_carb = fueling.get("gel_carb_g") or 30
    sodium_hr = _sodium_per_hr(protocol, hot)
    fluid_hr = _avg_rate(fueling.get("fluid_oz_per_hr", 22))

    start_dt = datetime.strptime(start_time, "%H:%M")
    sunset_dt = _clock_on_day(meta.get("sunset"), start_dt)

    crew_segs = [s for s in segments if s.get("crew_accessible")]

    crew_stops = []
    for idx, seg in enumerate(crew_segs):
        cumulative = cum_by_num.get(seg["segment_number"], 0)
        eta_dt = start_dt + timedelta(seconds=cumulative)
        cutoff, aid_notes = _split_aid_notes(seg.get("terrain_notes"))

        next_leg = None
        night_handoff = False
        if idx + 1 < len(crew_segs):
            nxt = crew_segs[idx + 1]
            nxt_cum = cum_by_num.get(nxt["segment_number"], cumulative)
            leg_secs = max(0, nxt_cum - cumulative)
            leg_hours = leg_secs / 3600 if leg_secs else 0
            leg_miles = round(nxt["end_mile"] - seg["end_mile"], 1)
            # Ceil so the planned handoff actually covers the leg's carb target;
            # the +1 below is then a genuine spare, not making up a shortfall.
            gels = math.ceil(carb_per_hr * leg_hours / gel_carb) if leg_hours else 0
            next_leg = {
                "to": nxt.get("name") or f"Mile {nxt['end_mile']}",
                "miles": leg_miles,
                "time_display": _format_time(leg_secs),
                "gels": gels,
                "gels_with_spare": gels + 1,
                "sodium_mg": round(sodium_hr * leg_hours) if sodium_hr else None,
                "fluid_oz": round(fluid_hr * leg_hours) if fluid_hr else None,
            }
            # Night-kit handoff: last daylight crew stop whose next leg crosses sunset.
            if sunset_dt:
                nxt_eta = start_dt + timedelta(seconds=nxt_cum)
                if eta_dt < sunset_dt <= nxt_eta:
                    night_handoff = True

        crew_stops.append({
            "segment_number": seg["segment_number"],
            "station_name": seg.get("name") or f"Mile {seg['end_mile']}",
            "mile": seg["end_mile"],
            "drop_bag": bool(seg.get("drop_bag")),
            "eta_clock": _eta_clock(eta_dt, start_dt),
            "eta_elapsed": _format_time(cumulative),
            "cutoff": cutoff,
            "aid_notes": aid_notes,
            "next_leg": next_leg,
            "night_kit_handoff": night_handoff,
        })

    return {
        "course": course["name"] if course else "Unknown",
        "total_miles": total_miles,
        "start_time": start_time,
        "eta_source": eta_source,
        "governor_goal_display": _format_time(goal_time_seconds),
        "governor_finish_display": gov_finish_display,
        "weather_temp_f": weather_temp_f,
        "hot": hot,
        "fueling_summary": {
            "carb_g_per_hr": carb_per_hr,
            "gel": fueling.get("primary_carb", f"gels ({gel_carb} g each)"),
            "gel_carb_g": gel_carb,
            "sodium_mg_per_hr": fueling.get("sodium_mg_per_hr"),
            "sodium_mg_per_hr_working": sodium_hr,
            "fluid_oz_per_hr": fueling.get("fluid_oz_per_hr"),
            "electrolyte": fueling.get("electrolyte"),
            "savory_switch_hour": fueling.get("savory_switch_hour"),
        },
        "priorities": protocol.get("priorities") or [],
        "cooling": protocol.get("cooling") or {},
        "chafing": protocol.get("chafing") or {},
        "drop_bags": protocol.get("drop_bags") or {},
        "night_kit": protocol.get("night_kit") or {},
        "per_stop_workflow": protocol.get("per_stop_workflow") or {},
        "crew": protocol.get("crew") or {},
        "research": protocol.get("research") or {},
        "meta": meta,
        "crew_stops": crew_stops,
    }


def _clock_on_day(clock_str, start_dt):
    """Parse 'HH:MM' to a datetime on the same notional day as start_dt."""
    if not clock_str:
        return None
    try:
        t = datetime.strptime(str(clock_str), "%H:%M")
    except ValueError:
        return None
    return start_dt.replace(hour=t.hour, minute=t.minute)


def _eta_clock(eta_dt, start_dt):
    """Format an ETA as a 12-hour clock, tagging the next day."""
    label = eta_dt.strftime("%I:%M %p").lstrip("0")
    if (eta_dt - start_dt).days >= 1:
        label += " (+1d)"
    return label


def _split_aid_notes(terrain_notes):
    """Split aid notes into (cutoff, rest).

    Pulls the first ``close ...`` token from *anywhere* in the semicolon-delimited
    notes (e.g. Silver Springs is ``50M turnaround; pacers allowed; close 8:30 PM;
    ...``), not just the leading part, so the cutoff field is never left buried in
    the aid text.
    """
    if not terrain_notes:
        return None, None
    parts = [p.strip() for p in str(terrain_notes).split(";")]
    cutoff = None
    rest = []
    for part in parts:
        if cutoff is None and part.lower().startswith("close"):
            cutoff = part[len("close"):].strip()
        elif part:
            rest.append(part)
    return cutoff, "; ".join(rest) or None


def crew_manual_to_markdown(manual):
    """Render a crew manual dict as printable / vault markdown."""
    m = manual
    L = []

    L.append(f"# {m['course']} — Crew Manual "
             f"({m['governor_goal_display']} governor)")
    L.append("")
    L.append(f"**Start:** {m['start_time']} · **Target finish:** "
             f"~{m['governor_finish_display']} elapsed · "
             f"**Total:** {m['total_miles']} miles")
    if m["weather_temp_f"] is not None:
        heat = " — HOT, escalate all cooling" if m["hot"] else ""
        L.append(f"**Forecast:** {m['weather_temp_f']}°F{heat}")
    L.append(f"*ETAs from: {m['eta_source']}.*")
    L.append("")
    L.append("> Pace to these times to bank **confidence**, not chase the stretch "
             "goal. Arriving *before* each ETA means everything is working. "
             "Don't push past the plan; don't panic if a stop runs long.")
    L.append("")

    if m["priorities"]:
        L.append("## Why this manual exists (his limiters)")
        L.append("")
        for p in m["priorities"]:
            L.append(f"1. {p}")
        L.append("")

    fs = m["fueling_summary"]
    L.append("## Fueling target")
    L.append("")
    L.append(f"- **~{fs['carb_g_per_hr']} g carb/hr** via {fs['gel']}")
    na = fs["sodium_mg_per_hr"]
    na_disp = f"{na[0]}–{na[1]}" if isinstance(na, list) else na
    L.append(f"- **~{na_disp} mg sodium/hr** "
             f"(working target {fs['sodium_mg_per_hr_working']} mg/hr)"
             + (f" via {fs['electrolyte']}" if fs.get("electrolyte") else ""))
    fl = fs["fluid_oz_per_hr"]
    if fl:
        fl_disp = f"{fl[0]}–{fl[1]}" if isinstance(fl, list) else fl
        L.append(f"- **~{fl_disp} oz fluid/hr**")
    if fs.get("savory_switch_hour"):
        L.append(f"- After ~{fs['savory_switch_hour']} h / dark, shift to **savory** "
                 f"(broth, ramen, potatoes).")
    L.append("- Hand the exact gel count per leg **+1 spare**. Keep the carb mix cold.")
    L.append("")

    wf = m["per_stop_workflow"]
    if wf:
        L.append("## What to do at EVERY crew stop")
        L.append("")
        for phase, title in (("before_arrival", "Before he arrives"),
                             ("on_arrival", "On arrival"),
                             ("out_the_door", "Out the door"),
                             ("log", "Log")):
            items = wf.get(phase)
            if items:
                L.append(f"**{title}**")
                for it in items:
                    L.append(f"- {it}")
                L.append("")

    cooling = m["cooling"]
    if cooling.get("methods"):
        L.append("## Cooling playbook"
                 + (" — ESCALATE (hot forecast)" if m["hot"] else ""))
        L.append("")
        for meth in cooling["methods"]:
            L.append(f"- {meth}")
        if cooling.get("ice_available_aid"):
            L.append(f"- **Top up ice between crew stops at:** "
                     f"{', '.join(cooling['ice_available_aid'])}")
        if cooling.get("interaction_warning"):
            L.append("")
            L.append(f"> ⚠️ {cooling['interaction_warning']}")
        L.append("")

    chafing = m["chafing"]
    if chafing.get("prevention"):
        L.append("## Chafing playbook")
        L.append("")
        for it in chafing["prevention"]:
            L.append(f"- {it}")
        if chafing.get("blister_kit"):
            L.append(f"- Blister kit: {', '.join(chafing['blister_kit'])}")
        L.append("")

    db = m["drop_bags"]
    if db.get("contents"):
        L.append("## Drop bags")
        L.append("")
        L.append("Standard: " + ", ".join(db["contents"]) + ".")
        if db.get("night_bag_extras"):
            L.append("")
            L.append("Night bags add: " + ", ".join(db["night_bag_extras"]) + ".")
        L.append("")
    nk = m["night_kit"]
    if nk.get("contents"):
        L.append("## Night kit")
        L.append("")
        L.append("Contents: " + ", ".join(nk["contents"]) + ".")
        if nk.get("pacer_pickup"):
            L.append(f"Pacer pickup: **{nk['pacer_pickup']}**.")
        L.append("")

    if m["crew"].get("cooler_plan"):
        L.append("## Cooler & ice plan")
        L.append("")
        for it in m["crew"]["cooler_plan"]:
            L.append(f"- {it}")
        L.append("")

    if m["research"]:
        L.append("## Race research notes")
        L.append("")
        for k, v in m["research"].items():
            L.append(f"- **{k}:** {v}")
        L.append("")

    L.append("---")
    L.append("")
    L.append("## Crew-accessible stops")
    L.append("")
    for i, stop in enumerate(m["crew_stops"], 1):
        tags = []
        if stop["drop_bag"]:
            tags.append("DROP BAG")
        if stop["night_kit_handoff"]:
            tags.append("HAND OFF NIGHT KIT")
        tag_str = f"  · **{' · '.join(tags)}**" if tags else ""
        L.append(f"### {i} · {stop['station_name']} — Mile {stop['mile']}{tag_str}")
        line = (f"- **On pace if in before {stop['eta_clock']}** "
                f"(elapsed {stop['eta_elapsed']})")
        if stop["cutoff"]:
            line += f" · cutoff {stop['cutoff']}"
        L.append(line)
        if stop["aid_notes"]:
            L.append(f"- Aid: {stop['aid_notes']}")
        nl = stop["next_leg"]
        if nl:
            extra = []
            if nl["sodium_mg"]:
                extra.append(f"~{nl['sodium_mg']} mg Na")
            if nl["fluid_oz"]:
                extra.append(f"~{nl['fluid_oz']} oz")
            extra_str = (" + " + ", ".join(extra)) if extra else ""
            L.append(f"- **Next → {nl['to']}, {nl['miles']} mi ({nl['time_display']}):** "
                     f"hand **{nl['gels']} gels +1**{extra_str}.")
        else:
            L.append("- **FINISH** — dry clothes, warm layer, fluids + sodium, real food.")
        if stop["night_kit_handoff"]:
            L.append("- 🔦 **Hand the night kit here** — he hits the dark before the next crew stop.")
        L.append("")

    return "\n".join(L).rstrip() + "\n"


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
