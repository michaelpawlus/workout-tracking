"""Gym/strength workout CLI command handlers.

Provides: log, pr, suggest, history, exercises commands.
All commands support --json for agent-friendly output.
"""

import json
import sys
from datetime import date

from .database import get_db, init_db


# ---------------------------------------------------------------------------
# Output helpers (mirror cli.py patterns)
# ---------------------------------------------------------------------------

def _print(data, as_json=False, file=sys.stdout):
    if as_json:
        print(json.dumps(data, indent=2, default=str), file=file)
    else:
        if isinstance(data, str):
            print(data, file=file)
        elif isinstance(data, dict):
            _print_dict(data)
        elif isinstance(data, list):
            for item in data:
                _print_dict(item)
                print()


def _print_dict(d, indent=0):
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            print(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list):
            print(f"{prefix}{k}:")
            for item in v:
                if isinstance(item, dict):
                    _print_dict(item, indent + 1)
                    print()
                else:
                    print(f"{prefix}  - {item}")
        else:
            print(f"{prefix}{k}: {v}")


def _err(msg, as_json=False, code=1):
    if as_json:
        print(json.dumps({"error": msg, "code": code}), file=sys.stdout)
    else:
        print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_exercise(conn, name):
    """Look up an exercise by snake_case name. Returns dict or None."""
    row = conn.execute(
        "SELECT id, name, display_name, category, primary_metric FROM exercises WHERE name = ?",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def _parse_exercise_arg(conn, arg_str):
    """Parse 'name:sets:reps:weight' into a validated dict.

    Fields after name are optional — trailing colons can be omitted.
    Returns dict with keys: exercise_name, exercise_id, display_name, sets, reps, weight_lbs.
    """
    parts = arg_str.split(":")
    name = parts[0].strip()
    ex = _lookup_exercise(conn, name)
    if not ex:
        return None, f"Unknown exercise '{name}'. Run 'gym exercises' to see available names."

    def _int_or_none(idx):
        try:
            return int(parts[idx]) if len(parts) > idx and parts[idx].strip() else None
        except ValueError:
            return None

    def _float_or_none(idx):
        try:
            return float(parts[idx]) if len(parts) > idx and parts[idx].strip() else None
        except ValueError:
            return None

    return {
        "exercise_name": ex["name"],
        "exercise_id": ex["id"],
        "display_name": ex["display_name"],
        "sets": _int_or_none(1),
        "reps": _int_or_none(2),
        "weight_lbs": _float_or_none(3),
    }, None


def _detect_and_save_prs(conn, workout_id, exercises, workout_date):
    """Compare logged exercises against personal_records. Insert new PRs. Return list of new PRs."""
    new_prs = []
    for ex in exercises:
        eid = ex["exercise_id"]
        weight = ex.get("weight_lbs")
        reps = ex.get("reps")
        sets = ex.get("sets")

        if not weight or not reps:
            continue

        # Determine which record types to check
        # Only estimate 1RM from sets of 10 or fewer reps (Epley breaks down above that)
        checks = []
        if reps == 1:
            checks.append(("1RM", weight))
        elif reps <= 5:
            checks.append(("5RM", weight))
            est_1rm = round(weight * (1 + reps / 30), 1)
            checks.append(("1RM", est_1rm))
        elif reps <= 10:
            est_1rm = round(weight * (1 + reps / 30), 1)
            checks.append(("1RM", est_1rm))

        for record_type, value in checks:
            current = conn.execute(
                "SELECT id, value FROM personal_records WHERE exercise_id = ? AND record_type = ? ORDER BY value DESC LIMIT 1",
                (eid, record_type),
            ).fetchone()

            previous = dict(current)["value"] if current else None

            if previous is None or value > previous:
                conn.execute(
                    "INSERT INTO personal_records (exercise_id, record_type, value, date_achieved, workout_id) VALUES (?, ?, ?, ?, ?)",
                    (eid, record_type, value, workout_date, workout_id),
                )
                pr_entry = {
                    "exercise": ex["display_name"],
                    "record_type": record_type,
                    "value": value,
                }
                if record_type == "1RM" and reps > 1:
                    pr_entry["note"] = f"Estimated from {reps}@{weight}lbs (Epley)"
                if previous is not None:
                    pr_entry["previous"] = previous
                new_prs.append(pr_entry)

    return new_prs


def _save_gym_workout(conn, exercises, workout_date, duration, notes, source="cli"):
    """Insert workout + exercises into DB. Returns result dict."""
    cursor = conn.execute(
        "INSERT INTO workouts (date, workout_type, duration_minutes, notes, source) VALUES (?, 'strength', ?, ?, ?)",
        (workout_date, duration, notes, source),
    )
    workout_id = cursor.lastrowid

    for ex in exercises:
        conn.execute(
            """INSERT INTO workout_exercises (workout_id, exercise_id, sets, reps, weight_lbs, time_seconds, rounds_completed, distance_meters, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workout_id,
                ex["exercise_id"],
                ex.get("sets"),
                ex.get("reps"),
                ex.get("weight_lbs"),
                ex.get("time_seconds"),
                ex.get("rounds_completed"),
                ex.get("distance_meters"),
                ex.get("notes"),
            ),
        )

    new_prs = _detect_and_save_prs(conn, workout_id, exercises, workout_date)

    exercises_logged = []
    for ex in exercises:
        entry = {"exercise": ex["display_name"]}
        if ex.get("sets"):
            entry["sets"] = ex["sets"]
        if ex.get("reps"):
            entry["reps"] = ex["reps"]
        if ex.get("weight_lbs"):
            entry["weight_lbs"] = ex["weight_lbs"]
        if ex.get("time_seconds"):
            entry["time_seconds"] = ex["time_seconds"]
        exercises_logged.append(entry)

    return {
        "workout_id": workout_id,
        "date": workout_date,
        "duration_minutes": duration,
        "exercises_logged": exercises_logged,
        "new_prs": new_prs,
    }


def _format_exercise_line(ex):
    """Format a single exercise for human-readable output."""
    parts = [ex.get("exercise") or ex.get("display_name", "?")]
    s, r, w = ex.get("sets"), ex.get("reps"), ex.get("weight_lbs")
    if s and r:
        parts.append(f"{s}x{r}")
    elif r:
        parts.append(f"{r} reps")
    if w:
        parts.append(f"@ {w} lbs")
    t = ex.get("time_seconds")
    if t:
        parts.append(f"{t}s")
    return "  " + " ".join(parts)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_gym_log(args):
    """Log a strength/gym workout."""
    as_json = getattr(args, "json", False)
    workout_date = getattr(args, "date", None) or date.today().isoformat()
    duration = getattr(args, "duration", None)
    notes = getattr(args, "notes", "") or ""
    exercise_args = getattr(args, "exercise", None)
    text_input = getattr(args, "text", None)

    if not exercise_args and not text_input:
        _err("Provide --exercise or --text to log a workout.", as_json)
    if exercise_args and text_input:
        _err("Use --exercise or --text, not both.", as_json)

    with get_db() as conn:
        if exercise_args:
            # Structured mode
            exercises = []
            for arg in exercise_args:
                parsed, error = _parse_exercise_arg(conn, arg)
                if error:
                    _err(error, as_json)
                exercises.append(parsed)

            result = _save_gym_workout(conn, exercises, workout_date, duration, notes, source="cli")

        else:
            # Free-text mode — use LLM
            try:
                from llm import parse_workout_log
            except Exception as e:
                _err(f"LLM parsing unavailable: {e}. Use --exercise flags instead.", as_json)

            try:
                parsed = parse_workout_log(text_input)
            except Exception as e:
                _err(f"Failed to parse workout text: {e}", as_json)

            if parsed.get("clarifications_needed"):
                if as_json:
                    _print({"clarifications_needed": parsed["clarifications_needed"]}, as_json=True)
                else:
                    print("Clarification needed:")
                    for c in parsed["clarifications_needed"]:
                        print(f"  - {c}")
                sys.exit(2)

            # Resolve exercise names to IDs
            exercises = []
            for ex in parsed.get("exercises", []):
                name = ex.get("exercise_name", "")
                db_ex = _lookup_exercise(conn, name)
                if not db_ex:
                    print(f"Warning: skipping unknown exercise '{name}'", file=sys.stderr)
                    continue
                exercises.append({
                    "exercise_name": db_ex["name"],
                    "exercise_id": db_ex["id"],
                    "display_name": db_ex["display_name"],
                    "sets": ex.get("sets"),
                    "reps": ex.get("reps"),
                    "weight_lbs": ex.get("weight_lbs"),
                    "time_seconds": ex.get("time_seconds"),
                    "rounds_completed": ex.get("rounds_completed"),
                    "distance_meters": ex.get("distance_meters"),
                    "notes": ex.get("notes"),
                })

            if not exercises:
                _err("No valid exercises found in the text.", as_json)

            llm_duration = parsed.get("duration_minutes")
            result = _save_gym_workout(
                conn, exercises, workout_date,
                duration or llm_duration, notes or parsed.get("notes", ""),
                source="llm",
            )

    if as_json:
        _print(result, as_json=True)
    else:
        print(f"\nGym workout logged (#{result['workout_id']}) — {result['date']}")
        if result.get("duration_minutes"):
            print(f"Duration: {result['duration_minutes']} min")
        print()
        for ex in result["exercises_logged"]:
            print(_format_exercise_line(ex))
        if result["new_prs"]:
            print()
            for pr in result["new_prs"]:
                note = f" ({pr['note']})" if pr.get("note") else ""
                prev = f" (prev: {pr['previous']} lbs)" if pr.get("previous") is not None else " (first record!)"
                print(f"  NEW PR! {pr['exercise']} {pr['record_type']}: {pr['value']} lbs{note}{prev}")


def cmd_gym_pr(args):
    """View or set personal records."""
    as_json = getattr(args, "json", False)
    exercise_filter = getattr(args, "exercise", None)
    set_mode = getattr(args, "set", False)

    with get_db() as conn:
        if set_mode:
            # Set a PR manually
            pr_type = getattr(args, "type", None)
            value = getattr(args, "value", None)
            if not exercise_filter or not pr_type or value is None:
                _err("--set requires --exercise, --type, and --value.", as_json)

            ex = _lookup_exercise(conn, exercise_filter)
            if not ex:
                _err(f"Unknown exercise '{exercise_filter}'.", as_json)

            current = conn.execute(
                "SELECT value FROM personal_records WHERE exercise_id = ? AND record_type = ? ORDER BY value DESC LIMIT 1",
                (ex["id"], pr_type),
            ).fetchone()
            previous = dict(current)["value"] if current else None

            conn.execute(
                "INSERT INTO personal_records (exercise_id, record_type, value, date_achieved) VALUES (?, ?, ?, ?)",
                (ex["id"], pr_type, value, date.today().isoformat()),
            )

            result = {
                "exercise": ex["display_name"],
                "record_type": pr_type,
                "value": value,
                "previous": previous,
            }
            if as_json:
                _print(result, as_json=True)
            else:
                prev_str = f" (prev: {previous})" if previous is not None else " (first record!)"
                print(f"PR set: {ex['display_name']} {pr_type} = {value}{prev_str}")
            return

        # List PRs
        query = """
            SELECT e.name, e.display_name, e.category, pr.record_type, pr.value, pr.date_achieved
            FROM personal_records pr
            JOIN exercises e ON e.id = pr.exercise_id
        """
        params = []
        if exercise_filter:
            query += " WHERE e.name = ?"
            params.append(exercise_filter)
        query += " ORDER BY e.display_name, pr.record_type, pr.value DESC"

        rows = conn.execute(query, params).fetchall()

    if not rows:
        if as_json:
            _print([], as_json=True)
        else:
            msg = f"No PRs recorded for '{exercise_filter}'." if exercise_filter else "No PRs recorded yet."
            print(msg)
        return

    records = [dict(r) for r in rows]

    if as_json:
        _print(records, as_json=True)
        return

    # Group by exercise for human output
    print("\n=== Personal Records ===\n")
    current_exercise = None
    for r in records:
        if r["display_name"] != current_exercise:
            current_exercise = r["display_name"]
            print(f"{current_exercise}:")
        print(f"  {r['record_type']}: {r['value']} ({r['date_achieved']})")


def cmd_gym_suggest(args):
    """Generate a workout with weight suggestions."""
    as_json = getattr(args, "json", False)
    prompt = getattr(args, "prompt", None)
    preset = getattr(args, "type", None)

    presets = {
        "upper-body": "Upper body workout, 30-45 minutes",
        "lower-body": "Lower body workout, 30-45 minutes",
        "full-body": "Full body workout, 45-60 minutes",
        "push": "Push day (chest, shoulders, triceps), 30-45 minutes",
        "pull": "Pull day (back, biceps), 30-45 minutes",
        "legs": "Leg day (squats, lunges, deadlifts), 30-45 minutes",
    }

    if not prompt and not preset:
        _err("Provide a prompt or --type preset.", as_json)

    request = prompt or presets[preset]

    try:
        from llm import generate_workout
    except Exception as e:
        _err(f"LLM unavailable: {e}", as_json)

    try:
        workout = generate_workout(request)
    except Exception as e:
        _err(f"Failed to generate workout: {e}", as_json)

    if as_json:
        _print(workout, as_json=True)
        return

    # Human-readable output
    name = workout.get("workout_name", "Workout")
    est = workout.get("estimated_duration_minutes", "?")
    print(f"\n=== {name} (est. {est} min) ===\n")

    if workout.get("description"):
        print(f"{workout['description']}\n")

    for section in workout.get("sections", []):
        fmt = f" — {section['format']}" if section.get("format") else ""
        print(f"{section['name']}{fmt}:")
        for ex in section.get("exercises", []):
            parts = [f"  {ex.get('display_name', ex.get('exercise_name', '?'))}:"]
            s, r = ex.get("sets"), ex.get("reps")
            if s and r:
                parts.append(f"{s}x{r}")
            elif r:
                parts.append(f"{r} reps")
            w = ex.get("weight_suggestion_lbs")
            if w:
                parts.append(f"@ {w} lbs")
            t = ex.get("time_seconds")
            if t:
                parts.append(f"({t}s)")
            if ex.get("notes"):
                parts.append(f"— {ex['notes']}")
            print(" ".join(parts))
        print()

    if workout.get("coaching_notes"):
        print(f"Coach's Notes: {workout['coaching_notes']}\n")

    # Template log command
    exercise_strs = []
    for section in workout.get("sections", []):
        for ex in section.get("exercises", []):
            name_str = ex.get("exercise_name", "")
            s = ex.get("sets") or ""
            r = ex.get("reps") or ""
            w = ex.get("weight_suggestion_lbs") or ""
            if name_str:
                exercise_strs.append(f'--exercise "{name_str}:{s}:{r}:{w}"')
    if exercise_strs:
        print("# To log after completing:")
        print(f"python3 cli.py gym log {' '.join(exercise_strs)}")


def cmd_gym_history(args):
    """View past gym sessions."""
    as_json = getattr(args, "json", False)
    limit = getattr(args, "last", 10) or 10
    exercise_filter = getattr(args, "exercise", None)

    with get_db() as conn:
        if exercise_filter:
            # Get workout IDs that contain this exercise
            rows = conn.execute("""
                SELECT DISTINCT w.id, w.date, w.workout_type, w.duration_minutes, w.notes
                FROM workouts w
                JOIN workout_exercises we ON we.workout_id = w.id
                JOIN exercises e ON e.id = we.exercise_id
                WHERE w.workout_type IN ('strength', 'hiit', 'metcon')
                  AND e.name = ?
                ORDER BY w.date DESC, w.id DESC
                LIMIT ?
            """, (exercise_filter, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, date, workout_type, duration_minutes, notes
                FROM workouts
                WHERE workout_type IN ('strength', 'hiit', 'metcon')
                ORDER BY date DESC, id DESC
                LIMIT ?
            """, (limit,)).fetchall()

        workouts = []
        for w in rows:
            w_dict = dict(w)
            ex_rows = conn.execute("""
                SELECT e.display_name, e.name, we.sets, we.reps, we.weight_lbs,
                       we.time_seconds, we.rounds_completed, we.distance_meters, we.notes
                FROM workout_exercises we
                JOIN exercises e ON e.id = we.exercise_id
                WHERE we.workout_id = ?
            """, (w_dict["id"],)).fetchall()
            w_dict["exercises"] = [dict(r) for r in ex_rows]
            workouts.append(w_dict)

    if not workouts:
        if as_json:
            _print([], as_json=True)
        else:
            print("No gym workouts logged yet.")
        return

    if as_json:
        _print(workouts, as_json=True)
        return

    print(f"\n=== Gym History (last {len(workouts)}) ===\n")
    for w in workouts:
        dur = f" ({w['duration_minutes']} min)" if w.get("duration_minutes") else ""
        print(f"{w['date']} — {w['workout_type']}{dur}")
        for ex in w["exercises"]:
            parts = [f"  {ex['display_name']}:"]
            if ex.get("sets") and ex.get("reps"):
                parts.append(f"{ex['sets']}x{ex['reps']}")
            elif ex.get("reps"):
                parts.append(f"{ex['reps']} reps")
            if ex.get("weight_lbs"):
                parts.append(f"@ {ex['weight_lbs']} lbs")
            if ex.get("time_seconds"):
                parts.append(f"({ex['time_seconds']}s)")
            print(" ".join(parts))
        print()


def cmd_gym_exercises(args):
    """List available exercises."""
    as_json = getattr(args, "json", False)
    category_filter = getattr(args, "category", None)

    with get_db() as conn:
        if category_filter:
            rows = conn.execute(
                "SELECT name, display_name, category, primary_metric FROM exercises WHERE category = ? ORDER BY display_name",
                (category_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, display_name, category, primary_metric FROM exercises ORDER BY category, display_name"
            ).fetchall()

    exercises = [dict(r) for r in rows]

    if not exercises:
        if as_json:
            _print([], as_json=True)
        else:
            msg = f"No exercises in category '{category_filter}'." if category_filter else "No exercises found."
            print(msg)
        return

    if as_json:
        _print(exercises, as_json=True)
        return

    print("\n=== Available Exercises ===\n")
    current_cat = None
    cat_count = 0
    for ex in exercises:
        if ex["category"] != current_cat:
            if current_cat is not None:
                print()
            current_cat = ex["category"]
            cat_count = sum(1 for e in exercises if e["category"] == current_cat)
            print(f"{current_cat} ({cat_count}):")
        print(f"  {ex['name']} — {ex['display_name']} ({ex['primary_metric']})")
