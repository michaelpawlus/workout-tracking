#!/usr/bin/env python3
"""CLI for Burning River 100 training plan and feedback system.

All commands support --json for agent-friendly output.
Exit codes: 0=success, 1=error, 2=not found.
"""

from dotenv import load_dotenv
load_dotenv()

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from .database import init_db, get_db
from .ultra_plan import create_br100_plan
from .llm import analyze_run_feedback, analyze_strava_screenshot
from .nutrition import get_nutrition_tier, get_guidelines_for_workout
from .adapt import (
    get_current_targets, get_targets_history, seed_initial_targets,
    adapt_from_maf, adapt_from_5k_tt, adapt_from_trends,
    apply_targets_to_future_workouts, format_adaptation_report,
    find_unprocessed_benchmarks, set_manual_targets,
)
from . import strava
from . import race_engine


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


def _get_plan(conn):
    plan = conn.execute(
        "SELECT * FROM training_plans WHERE name = 'Burning River 100' AND status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return plan


def cmd_init(args):
    init_db()
    with get_db() as conn:
        existing = _get_plan(conn)
        if existing and not args.force:
            result = {"plan_id": existing["id"], "message": "Plan already exists", "already_existed": True}
            _print(result, args.json)
            return

        if existing and args.force:
            plan_id = existing["id"]
            # Cascade delete all plan data
            # Delete feedback for workouts in this plan
            conn.execute("""DELETE FROM run_feedback WHERE workout_id IN
                            (SELECT id FROM workouts WHERE plan_id = ?)""", (plan_id,))
            conn.execute("DELETE FROM run_feedback WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM athlete_targets WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM daily_workouts WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM workouts WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM weekly_summaries WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM plan_benchmarks WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM training_plan_weeks WHERE plan_id = ?", (plan_id,))
            conn.execute("DELETE FROM training_plans WHERE id = ?", (plan_id,))
            if not args.json:
                print(f"Deleted existing plan (id={plan_id})", file=sys.stderr)

        plan_id = create_br100_plan(conn)

    result = {"plan_id": plan_id, "message": "Burning River 100 plan created"}
    if not args.json:
        print(f"Created Burning River 100 plan (id={plan_id})")
        print("20 weeks: Mar 9 - Jul 26, 2026 (Mon-Sun weeks)")
        print("Goal: Sub-24 hours")
    else:
        _print(result, True)


def cmd_today(args):
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        workout = conn.execute(
            "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
            (plan["id"], today),
        ).fetchone()

        if not workout:
            _err(f"No workout scheduled for {today}", args.json, 2)

        result = dict(workout)

    # Add nutrition guidance for medium/long tier workouts
    dist = result.get("target_distance_miles") or 0
    dur = result.get("target_duration_minutes")
    tier = get_nutrition_tier(dist, dur) if dist else "short"
    if tier in ("medium", "long"):
        guidelines = get_guidelines_for_workout(
            result.get("workout_type", "easy_run"), dist, dur
        )
        result["nutrition"] = guidelines

    if args.json:
        _print(result, True)
    else:
        w = result
        status = "DONE" if w["completed"] else "TODO"
        print(f"[{status}] {w['title']}")
        print(f"Date: {w['scheduled_date']}")
        print(f"Type: {w['workout_type']} | Intensity: {w.get('intensity', 'N/A')}")
        if w.get("target_distance_miles"):
            print(f"Distance: {w['target_distance_miles']} miles")
        if w.get("target_duration_minutes"):
            print(f"Duration: {w['target_duration_minutes']} min")
        if w.get("target_pace_min_per_mile"):
            pace = w["target_pace_min_per_mile"]
            print(f"Target pace: {int(pace)}:{int((pace % 1) * 60):02d}/mi")
        if w.get("target_hr_zone"):
            print(f"HR Zone: {w['target_hr_zone']}")
        if w.get("description"):
            print(f"\n{w['description']}")
        if w.get("nutrition"):
            n = w["nutrition"]
            print(f"\nNutrition ({n['tier_label']}):")
            print(f"  Pre-run:  {n['pre_run']['carbs_g']}g carbs, {n['pre_run']['timing']}")
            print(f"  During:   {n['during_run']['carbs_g_per_hr']} cal/hr, {n['during_run']['water_oz_per_hr']} oz water/hr")
            print(f"  Post-run: {n['post_run']['protein_g']}g protein + {n['post_run']['carbs_g']}g carbs ({n['post_run']['recovery_window']})")


def cmd_week(args):
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        if args.week_num:
            week_row = conn.execute(
                "SELECT * FROM training_plan_weeks WHERE plan_id = ? AND week_number = ?",
                (plan["id"], args.week_num),
            ).fetchone()
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            week_row = conn.execute(
                """SELECT tpw.* FROM training_plan_weeks tpw
                   JOIN daily_workouts dw ON dw.week_id = tpw.id
                   WHERE dw.plan_id = ? AND dw.scheduled_date <= ?
                   ORDER BY dw.scheduled_date DESC LIMIT 1""",
                (plan["id"], today),
            ).fetchone()
            if not week_row:
                week_row = conn.execute(
                    "SELECT * FROM training_plan_weeks WHERE plan_id = ? ORDER BY week_number LIMIT 1",
                    (plan["id"],),
                ).fetchone()

        if not week_row:
            _err("Week not found", args.json, 2)

        week = dict(week_row)
        workouts = conn.execute(
            "SELECT * FROM daily_workouts WHERE week_id = ? ORDER BY scheduled_date",
            (week_row["id"],),
        ).fetchall()
        week["workouts"] = [dict(w) for w in workouts]

        summary = conn.execute(
            "SELECT * FROM weekly_summaries WHERE plan_id = ? AND week_number = ?",
            (plan["id"], week["week_number"]),
        ).fetchone()
        if summary:
            week["summary"] = dict(summary)

    if args.json:
        _print(week, True)
    else:
        print(f"=== Week {week['week_number']} ({week['week_type'].upper()}) ===")
        print(f"Focus: {week.get('focus', 'N/A')}")
        if week.get("notes"):
            print(f"Notes: {week['notes']}")
        if week.get("summary"):
            s = week["summary"]
            print(f"Target: {s.get('target_miles', '?')} mi | Completed: {s.get('actual_miles', 0)} mi | Runs: {s.get('runs_completed', 0)}/{s.get('runs_planned', '?')}")
        print()
        for w in week["workouts"]:
            status = "[x]" if w["completed"] else "[ ]"
            dist = f" ({w['target_distance_miles']}mi)" if w.get("target_distance_miles") else ""
            print(f"  {status} {w['scheduled_date']}  {w['title']}{dist}  [{w['intensity'] or ''}]")


def _submit_run(distance, duration=None, hr=None, max_hr=None, elevation=None,
                effort=None, pace=None, notes="", source="cli", run_date=None,
                skip_feedback=False, strava_activity_id=None, as_json=False,
                pre_meal=None, during_fuel=None, during_hydration=None,
                post_meal=None, nutrition_notes=None, scheduled_date=None):
    """Core run submission logic. Returns result dict.

    scheduled_date: if the run was prescribed for a different day, look up
    that day's workout instead of run_date.
    """
    if run_date is None:
        run_date = datetime.now().strftime("%Y-%m-%d")

    if pace is None and distance and duration:
        pace = duration / distance

    lookup_date = scheduled_date or run_date

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", as_json, 2)

        daily = conn.execute(
            "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
            (plan["id"], lookup_date),
        ).fetchone()

        cursor = conn.execute(
            """INSERT INTO workouts (date, workout_type, duration_minutes, notes, source, plan_id, strava_activity_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_date, "cardio", duration, notes, source, plan["id"], strava_activity_id),
        )
        workout_id = cursor.lastrowid

        daily_workout_id = None
        if daily:
            daily_workout_id = daily["id"]
            conn.execute(
                "UPDATE daily_workouts SET completed = 1, actual_workout_id = ? WHERE id = ?",
                (workout_id, daily["id"]),
            )

        prescribed = dict(daily) if daily else {"title": "Unscheduled run", "scheduled_date": run_date}
        actual = {
            "distance_miles": distance,
            "duration_minutes": duration,
            "avg_pace_min_per_mile": pace,
            "avg_heart_rate": hr,
            "max_heart_rate": max_hr,
            "elevation_gain_ft": elevation,
            "effort_rating": effort,
        }

        week_row = conn.execute(
            """SELECT tpw.*, ws.target_miles, ws.actual_miles, ws.runs_planned, ws.runs_completed
               FROM training_plan_weeks tpw
               LEFT JOIN weekly_summaries ws ON ws.plan_id = tpw.plan_id AND ws.week_number = tpw.week_number
               JOIN daily_workouts dw ON dw.week_id = tpw.id
               WHERE dw.plan_id = ? AND dw.scheduled_date = ?
               LIMIT 1""",
            (plan["id"], run_date),
        ).fetchone()
        weekly_context = dict(week_row) if week_row else None

        if week_row:
            conn.execute(
                """UPDATE weekly_summaries SET actual_miles = actual_miles + ?, runs_completed = runs_completed + 1
                   WHERE plan_id = ? AND week_number = ?""",
                (distance, plan["id"], week_row["week_number"]),
            )

        if skip_feedback:
            return {"workout_id": workout_id, "feedback": None}

        trends = conn.execute(
            """SELECT rf.actual_distance_miles, rf.actual_pace, rf.avg_heart_rate,
                      rf.compliance_score, rf.created_at
               FROM run_feedback rf WHERE rf.plan_id = ?
               ORDER BY rf.created_at DESC LIMIT 10""",
            (plan["id"],),
        ).fetchall()
        trend_data = [dict(t) for t in trends] if trends else None

        bms = conn.execute(
            """SELECT pb.benchmark_name, pb.benchmark_type, pb.scheduled_date,
                      pb.completed, pb.result_value, pb.result_notes
               FROM plan_benchmarks pb WHERE pb.plan_id = ?
               ORDER BY pb.scheduled_date""",
            (plan["id"],),
        ).fetchall()
        benchmark_data = [dict(b) for b in bms]

        race_info = {
            "race": "Burning River 100",
            "date": "2026-07-25",
            "goal": "Sub-24 hours",
            "weeks_remaining": max(0, (datetime(2026, 7, 25) - datetime.now()).days // 7),
        }

        current_targets = get_current_targets(conn, plan["id"])
        targets_dict = dict(current_targets) if current_targets else None

    nutrition_data = None
    if any(v is not None for v in (pre_meal, during_fuel, during_hydration, post_meal, nutrition_notes)):
        nutrition_data = {
            "pre_meal": pre_meal,
            "during_fuel": during_fuel,
            "during_hydration": during_hydration,
            "post_meal": post_meal,
            "nutrition_notes": nutrition_notes,
        }

    try:
        if not as_json:
            print("Analyzing run...", file=sys.stderr)
        feedback = analyze_run_feedback(prescribed, actual, weekly_context, trend_data, benchmark_data, race_info, athlete_targets=targets_dict, nutrition_data=nutrition_data)
    except Exception as e:
        feedback = {
            "compliance_score": None,
            "pace_feedback": f"Unable to generate AI feedback: {e}",
            "hr_feedback": "", "distance_feedback": "", "overall_feedback": "",
            "warnings": [], "race_readiness": "Unknown",
        }

    with get_db() as conn:
        conn.execute(
            """INSERT INTO run_feedback
               (workout_id, daily_workout_id, plan_id, prescribed_distance_miles,
                actual_distance_miles, prescribed_pace, actual_pace, avg_heart_rate,
                max_heart_rate, elevation_gain_ft, effort_rating, compliance_score,
                pace_feedback, hr_feedback, overall_feedback, warnings,
                pre_meal, during_fuel, during_hydration, post_meal, nutrition_notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workout_id, daily_workout_id, plan["id"],
             prescribed.get("target_distance_miles"), distance,
             prescribed.get("target_pace_min_per_mile"), pace,
             hr, max_hr, elevation, effort,
             feedback.get("compliance_score"),
             feedback.get("pace_feedback", ""),
             feedback.get("hr_feedback", ""),
             feedback.get("overall_feedback", ""),
             json.dumps(feedback.get("warnings", [])),
             pre_meal, during_fuel, during_hydration, post_meal, nutrition_notes),
        )

    return {"workout_id": workout_id, "feedback": feedback}


def cmd_submit(args):
    if args.image:
        _submit_image(args)
        return

    if not args.distance:
        _err("--distance is required (or use --image)", args.json)

    run_date = args.date or datetime.now().strftime("%Y-%m-%d")
    result = _submit_run(
        distance=args.distance, duration=args.duration, hr=args.hr,
        max_hr=args.max_hr, elevation=args.elevation, effort=args.effort,
        pace=args.pace, notes=args.notes or "", source="cli",
        run_date=run_date, as_json=args.json,
        pre_meal=args.pre_meal, during_fuel=args.during_fuel,
        during_hydration=args.during_hydration, post_meal=args.post_meal,
        nutrition_notes=args.nutrition_notes,
        scheduled_date=getattr(args, 'scheduled_date', None),
    )

    feedback = result.get("feedback", {}) or {}

    if args.json:
        _print(result, True)
    else:
        print(f"\nRun logged (workout #{result['workout_id']})")
        print(f"Distance: {args.distance}mi | Duration: {args.duration or '?'}min | Pace: {_fmt_pace(args.pace)}")
        if args.hr:
            print(f"Avg HR: {args.hr} bpm")
        score = feedback.get("compliance_score")
        if score is not None:
            print(f"\nCompliance Score: {score}/100")
        if feedback.get("overall_feedback"):
            print(f"\n{feedback['overall_feedback']}")
        if feedback.get("warnings"):
            print("\nWarnings:")
            for w in feedback["warnings"]:
                print(f"  - {w}")
        if feedback.get("race_readiness"):
            print(f"\nRace Readiness: {feedback['race_readiness']}")


def _submit_image(args):
    import os
    if not os.path.exists(args.image):
        _err(f"File not found: {args.image}", args.json)

    ext = os.path.splitext(args.image)[1].lower()
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp"}
    media_type = media_types.get(ext)
    if not media_type:
        _err(f"Unsupported image type: {ext}", args.json)

    with open(args.image, "rb") as f:
        image_bytes = f.read()

    with get_db() as conn:
        plan = _get_plan(conn)
        today = datetime.now().strftime("%Y-%m-%d")
        daily = None
        if plan:
            daily = conn.execute(
                "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
                (plan["id"], today),
            ).fetchone()

    prescribed = dict(daily) if daily else None

    if not args.json:
        print("Extracting data from screenshot...", file=sys.stderr)

    extracted = analyze_strava_screenshot(image_bytes, media_type, prescribed)

    result = {"extracted": extracted, "prescribed": prescribed}
    if args.json:
        _print(result, True)
    else:
        print("\nExtracted from screenshot:")
        _print_dict(extracted)
        if prescribed:
            print(f"\nPrescribed: {prescribed.get('title', 'N/A')}")


def cmd_feedback(args):
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan", args.json, 2)

        rows = conn.execute(
            """SELECT rf.*, dw.title as workout_title
               FROM run_feedback rf
               LEFT JOIN daily_workouts dw ON dw.id = rf.daily_workout_id
               WHERE rf.plan_id = ?
               ORDER BY rf.created_at DESC LIMIT 5""",
            (plan["id"],),
        ).fetchall()

    if not rows:
        _err("No feedback recorded yet", args.json, 2)

    results = []
    for r in rows:
        d = dict(r)
        if d.get("warnings"):
            try:
                d["warnings"] = json.loads(d["warnings"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)

    if args.json:
        _print(results, True)
    else:
        for fb in results:
            print(f"--- {fb.get('workout_title', 'Run')} ({fb['created_at']}) ---")
            print(f"Distance: {fb.get('actual_distance_miles', '?')}mi (prescribed: {fb.get('prescribed_distance_miles', '?')}mi)")
            if fb.get("compliance_score") is not None:
                print(f"Score: {fb['compliance_score']}/100")
            if fb.get("overall_feedback"):
                print(fb["overall_feedback"])
            print()


def cmd_progress(args):
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan", args.json, 2)

        total = conn.execute(
            "SELECT COUNT(*) as total FROM daily_workouts WHERE plan_id = ? AND workout_type != 'rest'",
            (plan["id"],),
        ).fetchone()["total"]
        completed = conn.execute(
            "SELECT COUNT(*) as done FROM daily_workouts WHERE plan_id = ? AND completed = 1",
            (plan["id"],),
        ).fetchone()["done"]

        summaries = conn.execute(
            "SELECT * FROM weekly_summaries WHERE plan_id = ? ORDER BY week_number",
            (plan["id"],),
        ).fetchall()

        benchmarks = conn.execute(
            """SELECT pb.*, tpw.week_number FROM plan_benchmarks pb
               JOIN training_plan_weeks tpw ON tpw.id = pb.week_id
               WHERE pb.plan_id = ? ORDER BY pb.scheduled_date""",
            (plan["id"],),
        ).fetchall()

    today = datetime.now()
    race_date = datetime(2026, 7, 25)
    weeks_remaining = max(0, (race_date - today).days // 7)

    result = {
        "plan_id": plan["id"],
        "race": "Burning River 100",
        "race_date": "2026-07-25",
        "goal": "Sub-24 hours",
        "weeks_remaining": weeks_remaining,
        "workouts_total": total,
        "workouts_completed": completed,
        "completion_pct": round(completed / total * 100, 1) if total else 0,
        "weekly_summaries": [dict(s) for s in summaries],
        "benchmarks": [dict(b) for b in benchmarks],
    }

    if args.json:
        _print(result, True)
    else:
        print(f"=== Burning River 100 Progress ===")
        print(f"Race: July 25, 2026 | Goal: Sub-24hr | Weeks left: {weeks_remaining}")
        print(f"Workouts: {completed}/{total} ({result['completion_pct']}%)")
        print()
        bm_done = sum(1 for b in result["benchmarks"] if b["completed"])
        bm_total = len(result["benchmarks"])
        print(f"Benchmarks: {bm_done}/{bm_total} completed")
        for b in result["benchmarks"]:
            status = "DONE" if b["completed"] else "    "
            val = f" = {b['result_value']}" if b.get("result_value") else ""
            print(f"  [{status}] Wk{b['week_number']}: {b['benchmark_name']} ({b['scheduled_date']}){val}")


def cmd_benchmarks(args):
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan", args.json, 2)

        rows = conn.execute(
            """SELECT pb.*, tpw.week_number, tpw.week_type
               FROM plan_benchmarks pb
               JOIN training_plan_weeks tpw ON tpw.id = pb.week_id
               WHERE pb.plan_id = ? ORDER BY pb.scheduled_date""",
            (plan["id"],),
        ).fetchall()

    results = [dict(r) for r in rows]

    if args.json:
        _print(results, True)
    else:
        print("=== Benchmark Schedule ===")
        for b in results:
            status = "DONE" if b["completed"] else "TODO"
            val = f" -> {b['result_value']}" if b.get("result_value") else ""
            notes = f"  ({b['result_notes']})" if b.get("result_notes") else ""
            print(f"  [{status}] Wk{b['week_number']} ({b['week_type']}): {b['benchmark_name']}")
            print(f"         Date: {b['scheduled_date']}{val}{notes}")


def cmd_upcoming(args):
    today = datetime.now()
    end = today + timedelta(days=args.days)
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan", args.json, 2)

        rows = conn.execute(
            """SELECT * FROM daily_workouts
               WHERE plan_id = ? AND scheduled_date >= ? AND scheduled_date <= ?
               ORDER BY scheduled_date""",
            (plan["id"], today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        ).fetchall()

    results = [dict(r) for r in rows]

    if args.json:
        _print(results, True)
    else:
        print(f"=== Next {args.days} Days ===")
        for w in results:
            status = "[x]" if w["completed"] else "[ ]"
            dist = f" ({w['target_distance_miles']}mi)" if w.get("target_distance_miles") else ""
            print(f"  {status} {w['scheduled_date']}  {w['title']}{dist}  [{w.get('intensity', '')}]")


def cmd_strava_connect(args):
    init_db()
    if not args.access_token or not args.refresh_token:
        _err("--access-token and --refresh-token are required", args.json)

    if strava.is_connected() and not args.force:
        _err("Strava already connected. Use --force to overwrite.", args.json)

    expires_at = args.expires_at or int(time.time()) + 21600  # default 6h from now
    strava._save_tokens(args.access_token, args.refresh_token, expires_at)

    result = {"status": "connected", "expires_at": expires_at}
    if args.json:
        _print(result, True)
    else:
        print("Strava tokens saved successfully.")
        print(f"Expires at: {datetime.fromtimestamp(expires_at).isoformat()}")


def cmd_strava_status(args):
    init_db()
    connected = strava.is_connected()
    tokens = strava._get_tokens()

    if args.json:
        result = {"connected": connected}
        if tokens:
            result["expires_at"] = tokens["expires_at"]
            result["expired"] = tokens["expires_at"] < int(time.time())
        _print(result, True)
    else:
        if connected:
            exp = datetime.fromtimestamp(tokens["expires_at"])
            expired = tokens["expires_at"] < int(time.time())
            status = "EXPIRED" if expired else "valid"
            print(f"Strava: Connected (token {status}, expires {exp.isoformat()})")
        else:
            print("Strava: Not connected")


def cmd_strava_import(args):
    init_db()
    if not strava.is_connected():
        _err("Strava not connected. Run: ultra strava-connect", args.json, 2)

    try:
        activities = strava.get_activities(per_page=args.count)
    except Exception as e:
        _err(f"Failed to fetch Strava activities: {e}", args.json)

    if not args.all_types:
        activities = [a for a in activities if a.get("sport_type", a.get("type", "")).lower() in ("run", "trail run", "virtualrun")]

    if args.list:
        results = []
        for a in activities:
            dist_m = a.get("distance", 0)
            dist_mi = round(dist_m / 1609.34, 2)
            results.append({
                "id": a["id"],
                "name": a.get("name", ""),
                "type": a.get("sport_type", a.get("type", "")),
                "date": a.get("start_date_local", "")[:10],
                "distance_miles": dist_mi,
                "moving_time_min": round(a.get("moving_time", 0) / 60, 1),
            })
        if args.json:
            _print(results, True)
        else:
            print(f"=== {len(results)} Strava Activities ===")
            for r in results:
                print(f"  [{r['id']}] {r['date']}  {r['name']}  {r['distance_miles']}mi  ({r['type']})")
        return

    # Import mode
    imported = 0
    skipped = 0
    errors = []

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

    for a in activities:
        activity_id = a["id"]

        # Check for duplicate
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM workouts WHERE strava_activity_id = ?", (activity_id,)
            ).fetchone()
        if existing:
            skipped += 1
            continue

        dist_m = a.get("distance", 0)
        dist_mi = round(dist_m / 1609.34, 2)
        duration_min = round(a.get("moving_time", 0) / 60, 1)
        elevation_ft = round(a.get("total_elevation_gain", 0) * 3.28084, 1) if a.get("total_elevation_gain") else None
        avg_hr = a.get("average_heartrate")
        max_hr = a.get("max_heartrate")
        run_date = a.get("start_date_local", "")[:10]

        pace = None
        if dist_mi and duration_min:
            pace = duration_min / dist_mi

        try:
            result = _submit_run(
                distance=dist_mi, duration=duration_min, hr=avg_hr,
                max_hr=max_hr, elevation=elevation_ft, pace=pace,
                notes=a.get("name", ""), source="strava", run_date=run_date,
                skip_feedback=args.no_feedback, strava_activity_id=activity_id,
                as_json=args.json,
            )
            imported += 1
            if not args.json:
                print(f"  Imported: {run_date} - {a.get('name', '')} ({dist_mi}mi)")
        except SystemExit:
            errors.append({"id": activity_id, "error": "submission failed"})
        except Exception as e:
            errors.append({"id": activity_id, "error": str(e)})

    summary = {"imported": imported, "skipped": skipped, "errors": len(errors)}
    if errors:
        summary["error_details"] = errors

    if args.json:
        _print(summary, True)
    else:
        print(f"\nImport complete: {imported} imported, {skipped} skipped (duplicates)")
        if errors:
            print(f"  {len(errors)} errors")


def cmd_export_fit(args):
    import os
    from .fit_export import export_workout_fit, export_week_fits

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        # Fetch adaptive HR zones
        targets = get_current_targets(conn, plan["id"])
        hr_zones = _targets_to_hr_zones(targets)

        output_dir = os.path.join(os.path.dirname(__file__), "fit_exports")

        if args.all:
            workouts = conn.execute(
                "SELECT * FROM daily_workouts WHERE plan_id = ? ORDER BY scheduled_date",
                (plan["id"],),
            ).fetchall()
            results = export_week_fits([dict(w) for w in workouts], output_dir, hr_zones=hr_zones)

        elif args.week:
            week_row = conn.execute(
                "SELECT * FROM training_plan_weeks WHERE plan_id = ? AND week_number = ?",
                (plan["id"], args.week),
            ).fetchone()
            if not week_row:
                _err(f"Week {args.week} not found", args.json, 2)
            workouts = conn.execute(
                "SELECT * FROM daily_workouts WHERE week_id = ? ORDER BY scheduled_date",
                (week_row["id"],),
            ).fetchall()
            results = export_week_fits([dict(w) for w in workouts], output_dir, hr_zones=hr_zones)

        else:
            target_date = args.date or datetime.now().strftime("%Y-%m-%d")
            workout = conn.execute(
                "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
                (plan["id"], target_date),
            ).fetchone()
            if not workout:
                _err(f"No workout found for {target_date}", args.json, 2)
            w = dict(workout)
            if w["workout_type"] in ("rest", "cross_train"):
                _err(f"Rest day on {target_date} — no FIT file to export", args.json, 2)
            result = export_workout_fit(w, output_dir, hr_zones=hr_zones)
            results = [result]

    if args.json:
        _print({"exported": results, "count": len(results)}, True)
    else:
        print(f"Exported {len(results)} FIT file(s) to {output_dir}/")
        for r in results:
            if "error" in r:
                print(f"  ERROR {r['date']}: {r['title']} — {r['error']}")
            else:
                print(f"  {r['date']}: {r['title']} ({r['steps']} steps, {r['size_bytes']}B)")


def cmd_icu_push(args):
    from .intervals_icu import create_event, create_events_bulk, workout_to_icu_description

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        if args.upcoming:
            today = datetime.now().strftime("%Y-%m-%d")
            end = (datetime.now() + timedelta(days=args.upcoming)).strftime("%Y-%m-%d")
            workouts = conn.execute(
                """SELECT * FROM daily_workouts WHERE plan_id = ?
                   AND scheduled_date >= ? AND scheduled_date <= ?
                   ORDER BY scheduled_date""",
                (plan["id"], today, end),
            ).fetchall()
            workout_list = [dict(w) for w in workouts]
        elif args.all:
            workouts = conn.execute(
                "SELECT * FROM daily_workouts WHERE plan_id = ? ORDER BY scheduled_date",
                (plan["id"],),
            ).fetchall()
            workout_list = [dict(w) for w in workouts]
        elif args.week:
            week_row = conn.execute(
                "SELECT * FROM training_plan_weeks WHERE plan_id = ? AND week_number = ?",
                (plan["id"], args.week),
            ).fetchone()
            if not week_row:
                _err(f"Week {args.week} not found", args.json, 2)
            workouts = conn.execute(
                "SELECT * FROM daily_workouts WHERE week_id = ? ORDER BY scheduled_date",
                (week_row["id"],),
            ).fetchall()
            workout_list = [dict(w) for w in workouts]
        else:
            target_date = args.date or datetime.now().strftime("%Y-%m-%d")
            workout = conn.execute(
                "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
                (plan["id"], target_date),
            ).fetchone()
            if not workout:
                _err(f"No workout found for {target_date}", args.json, 2)
            w = dict(workout)
            if w["workout_type"] in ("rest", "cross_train"):
                _err(f"Rest day on {target_date} — nothing to push", args.json, 2)
            workout_list = [w]

    # Fetch adaptive targets for ICU descriptions
    with get_db() as conn:
        plan = _get_plan(conn)
        icu_targets = get_current_targets(conn, plan["id"]) if plan else None

    if args.dry_run:
        skip_types = {"rest", "cross_train"}
        results = []
        for w in workout_list:
            if w.get("workout_type") in skip_types:
                continue
            icu_desc = workout_to_icu_description(w, targets=icu_targets)
            results.append({
                "date": w["scheduled_date"],
                "title": w["title"],
                "icu_description": icu_desc,
            })
        if args.json:
            _print({"dry_run": True, "workouts": results, "count": len(results)}, True)
        else:
            for r in results:
                print(f"--- {r['date']}: {r['title']} ---")
                print(r["icu_description"])
                print()
            print(f"{len(results)} workout(s) would be pushed")
        return

    results = create_events_bulk(workout_list)
    created = sum(1 for r in results if r["status"] == "created")
    errors = sum(1 for r in results if r["status"] == "error")

    if args.json:
        _print({"pushed": results, "created": created, "errors": errors}, True)
    else:
        print(f"Pushed {created} workout(s) to Intervals.icu")
        if errors:
            print(f"  {errors} error(s)")
        for r in results:
            status = "OK" if r["status"] == "created" else "ERR"
            print(f"  [{status}] {r['date']}: {r['title']}")
            if r.get("error"):
                print(f"         {r['error']}")


def _targets_to_hr_zones(targets):
    """Convert athlete_targets row to HR zones dict for fit_export."""
    if not targets:
        return None
    return {
        1: (100, 120),
        2: (120, targets["zone2_ceiling"]),
        3: (targets["zone2_ceiling"], targets["zone3_ceiling"]),
        4: (targets["zone3_ceiling"], targets["zone4_ceiling"]),
        5: (targets["zone4_ceiling"], 195),
    }


def cmd_adapt(args):
    from database import get_connection

    conn = get_connection()
    try:
        plan = conn.execute(
            "SELECT * FROM training_plans WHERE name = 'Burning River 100' AND status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        plan_id = plan["id"]
        old_targets = get_current_targets(conn, plan_id)
        adaptations = []

        # Process unprocessed benchmarks
        unprocessed = find_unprocessed_benchmarks(conn, plan_id)
        for bm in unprocessed:
            if bm["benchmark_type"] == "maf_test" and bm.get("result_value"):
                result = adapt_from_maf(conn, plan_id, bm["id"], bm["result_value"])
                if result:
                    adaptations.append({
                        "source": "maf_test",
                        "benchmark": bm["benchmark_name"],
                        "maf_pace": result.get("maf_pace"),
                        "targets": result["targets"],
                    })
            elif bm["benchmark_type"] == "time_trial" and bm.get("result_value"):
                result = adapt_from_5k_tt(conn, plan_id, bm["id"], bm["result_value"])
                if result:
                    adaptations.append({
                        "source": "5k_tt",
                        "benchmark": bm["benchmark_name"],
                        "five_k_pace": result.get("five_k_pace"),
                        "targets": result["targets"],
                    })

        # Check trends
        trend_result = adapt_from_trends(conn, plan_id)

        # Get final targets
        new_targets = get_current_targets(conn, plan_id)
        report = format_adaptation_report(old_targets, new_targets, "adapt_command")

        # Apply to future workouts (unless dry-run)
        workouts_updated = 0
        if not args.dry_run and report["changed"]:
            workouts_updated = apply_targets_to_future_workouts(conn, plan_id, new_targets)

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except SystemExit:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    output = {
        "adaptations": adaptations,
        "trend_check": trend_result,
        "current_targets": dict(new_targets) if new_targets else None,
        "report": report,
        "workouts_updated": workouts_updated,
        "dry_run": args.dry_run,
    }

    if args.json:
        _print(output, True)
    else:
        if not adaptations and (not trend_result or not trend_result.get("change")):
            print("No adaptations needed.")
            if trend_result and trend_result.get("reason"):
                print(f"  Trends: {trend_result['reason']}")
        else:
            for a in adaptations:
                print(f"Adapted from {a['source']}: {a['benchmark']}")
            if trend_result and trend_result.get("change"):
                print(f"Trend adjustment: {trend_result['change']}")
            if report["changes"]:
                print("\nTarget changes:")
                for c in report["changes"]:
                    old_str = _fmt_pace(c["old"]) if "pace" in c["field"] else str(c["old"])
                    new_str = _fmt_pace(c["new"]) if "pace" in c["field"] else str(c["new"])
                    print(f"  {c['label']}: {old_str} → {new_str}")
            if args.dry_run:
                print("\n(dry run — no changes applied)")
            else:
                print(f"\nUpdated {workouts_updated} future workout(s)")


def cmd_targets(args):
    from database import get_connection

    if getattr(args, 'set', False):
        # Manual target override mode
        conn = get_connection()
        try:
            plan = conn.execute(
                "SELECT * FROM training_plans WHERE name = 'Burning River 100' AND status = 'active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not plan:
                _err("No active BR100 plan", args.json, 2)

            result = set_manual_targets(
                conn, plan["id"],
                easy=args.easy, long_run=args.long_run, tempo=args.tempo,
                notes=args.notes or "Manual CLI override",
            )
            if not result:
                _err("Failed to set targets", args.json)

            updated = apply_targets_to_future_workouts(conn, plan["id"], result["targets"])
            conn.commit()
        except SystemExit:
            conn.rollback()
            raise
        finally:
            conn.close()

        output = {"targets": result["targets"], "workouts_updated": updated}
        if args.json:
            _print(output, True)
        else:
            t = result["targets"]
            print("Targets updated:")
            print(f"  Easy: {_fmt_pace(t['easy_pace'])} | Long: {_fmt_pace(t['long_run_pace'])} | Tempo: {_fmt_pace(t['tempo_pace'])}")
            print(f"  Updated {updated} future workout(s)")
        return

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        if args.history:
            history = get_targets_history(conn, plan["id"])
            if args.json:
                _print(history, True)
            else:
                print("=== Target History ===")
                for t in history:
                    print(f"\n[{t['effective_date']}] source={t['source']}")
                    print(f"  Easy: {_fmt_pace(t['easy_pace'])} | Long: {_fmt_pace(t['long_run_pace'])} | Tempo: {_fmt_pace(t['tempo_pace'])}")
                    thr = _fmt_pace(t['threshold_pace']) if t.get('threshold_pace') else "N/A"
                    print(f"  Threshold: {thr} | MAF HR: {t['maf_hr']}")
                    if t.get("notes"):
                        print(f"  Notes: {t['notes']}")
        else:
            targets = get_current_targets(conn, plan["id"])
            if not targets:
                _err("No targets found. Run: python cli.py ultra init --force", args.json, 2)
            if args.json:
                _print(dict(targets), True)
            else:
                print("=== Current Targets ===")
                print(f"Effective: {targets['effective_date']} (source: {targets['source']})")
                print(f"Easy Pace:     {_fmt_pace(targets['easy_pace'])}")
                print(f"Long Run Pace: {_fmt_pace(targets['long_run_pace'])}")
                print(f"Tempo Pace:    {_fmt_pace(targets['tempo_pace'])}")
                thr = _fmt_pace(targets['threshold_pace']) if targets.get('threshold_pace') else "N/A"
                print(f"Threshold:     {thr}")
                print(f"MAF HR:        {targets['maf_hr']} bpm")
                print(f"Zone 2 Ceil:   {targets['zone2_ceiling']} bpm")
                print(f"Zone 3 Ceil:   {targets['zone3_ceiling']} bpm")
                print(f"Zone 4 Ceil:   {targets['zone4_ceiling']} bpm")


def cmd_nutrition(args):
    dist = args.distance
    dur = args.duration

    if not dist and not dur:
        # Default: show today's workout nutrition
        today = datetime.now().strftime("%Y-%m-%d")
        with get_db() as conn:
            plan = _get_plan(conn)
            if plan:
                workout = conn.execute(
                    "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
                    (plan["id"], today),
                ).fetchone()
                if workout:
                    dist = workout["target_distance_miles"] or 0
                    dur = workout["target_duration_minutes"]

    if not dist:
        dist = 5.0  # fallback

    guidelines = get_guidelines_for_workout(
        args.workout_type or "easy_run", dist, dur
    )

    if args.json:
        _print(guidelines, True)
    else:
        g = guidelines
        print(f"=== Nutrition Guidelines: {g['tier_label']} ===")
        print(f"Workout: {g['workout_type']} | {g['distance_miles']}mi")
        print()
        print("Pre-Run:")
        print(f"  Carbs: {g['pre_run']['carbs_g']}g | Timing: {g['pre_run']['timing']}")
        print(f"  Examples: {', '.join(g['pre_run']['examples'])}")
        print()
        print("During Run:")
        print(f"  Water: {g['during_run']['water_oz_per_hr']} oz/hr")
        print(f"  Fuel: {g['during_run']['carbs_g_per_hr']}")
        print(f"  Sodium: {g['during_run']['sodium_mg_per_hr']} mg/hr")
        print(f"  Notes: {g['during_run']['notes']}")
        print()
        print("Post-Run:")
        print(f"  Protein: {g['post_run']['protein_g']}g | Carbs: {g['post_run']['carbs_g']}g")
        print(f"  Window: {g['post_run']['recovery_window']}")
        print(f"  Examples: {', '.join(g['post_run']['examples'])}")


# ---------------------------------------------------------------------------
# Race Day Engine commands
# ---------------------------------------------------------------------------

def cmd_race_load_course(args):
    with get_db() as conn:
        breaks = None
        if args.segment_breaks:
            breaks = [float(x.strip()) for x in args.segment_breaks.split(",")]

        course_id, segments, total_dist, total_gain = race_engine.load_course(
            conn, args.gpx_file, args.name, args.year, breaks,
        )

        result = {
            "course_id": course_id,
            "name": args.name,
            "year": args.year,
            "total_distance_miles": total_dist,
            "total_elevation_gain_ft": total_gain,
            "segments": len(segments),
            "message": f"Loaded {len(segments)} segments from GPX",
        }

    if args.json:
        _print(result, True)
    else:
        print(f"Course: {args.name} ({args.year})")
        print(f"Distance: {total_dist} mi | Gain: {total_gain} ft")
        print(f"Segments: {len(segments)}")
        for s in segments:
            print(f"  {s['segment_number']:2d}. "
                  f"Mile {s['start_mile']:5.1f}-{s['end_mile']:5.1f} "
                  f"({s['distance_miles']:.1f}mi) "
                  f"+{s['elevation_gain_ft']:.0f}/-{s['elevation_loss_ft']:.0f}ft "
                  f"avg {s['avg_grade_pct']:.1f}%")


def cmd_race_import_results(args):
    with get_db() as conn:
        course = race_engine.get_course(conn, name=args.course_name)
        if not course:
            _err(f"No course found for '{args.course_name}'. Load a course first.",
                 args.json, 2)

        imported = race_engine.import_historical_results(
            conn, course["id"], args.csv_file, args.year,
        )

        result = {
            "course_id": course["id"],
            "year": args.year,
            "imported": imported,
            "message": f"Imported {imported} results",
        }

    _print(result, args.json)


def cmd_race_cohort(args):
    goal_seconds = race_engine._parse_time(args.goal_time)
    with get_db() as conn:
        course = race_engine.get_course(conn, name=args.course_name)
        if not course:
            _err(f"No course found. Load a course first.", args.json, 2)

        analysis = race_engine.analyze_cohort(conn, course["id"], goal_seconds)

    if args.json:
        _print(analysis, True)
    else:
        print(f"=== Peer Cohort Analysis ===")
        print(f"Goal: {analysis['goal_time']} | Window: ±{analysis['window_hours']}hr")
        print(f"Cohort size: {analysis['cohort_size']}")
        if analysis["cohort_size"] > 0:
            print(f"Median finish: {analysis['median_finish_time']}")
            print(f"Range: {analysis['fastest_finish']} - {analysis['slowest_finish']}")
            if analysis["slowdown_pct"] is not None:
                print(f"Back-half slowdown: {analysis['slowdown_pct']}%")
            if analysis["danger_zones"]:
                print(f"Danger zones: {', '.join(analysis['danger_zones'])}")
            print()
            print(f"{'Seg':>3}  {'Name':<25} {'Dist':>5} {'Pace':>9} {'StdDev':>6} {'Danger'}")
            print("-" * 65)
            for s in analysis["segments"]:
                danger = " ⚠" if s["danger_zone"] else ""
                print(f"{s['segment_number']:3d}  {s['segment_name']:<25} "
                      f"{s['distance_miles']:5.1f} {s['median_pace_display']:>9} "
                      f"{s['pace_stdev_seconds'] or 0:6.0f}s{danger}")


def cmd_race_plan(args):
    goal_seconds = race_engine._parse_time(args.goal_time)
    with get_db() as conn:
        course = race_engine.get_course(conn)
        if not course:
            _err("No course loaded. Run: ultra race load-course", args.json, 2)

        plan = _get_plan(conn)
        plan_id = plan["id"] if plan else None

        result = race_engine.generate_race_plan(
            conn, course["id"], plan_id, goal_seconds,
            weather_temp_f=args.weather_temp,
            start_time=args.start_time or "05:00",
        )

        if args.save:
            saved = race_engine.save_race_plan(
                conn, course["id"], plan_id, goal_seconds,
                args.weather_temp, result["plans"],
            )
            result["saved_plan_ids"] = saved

    if args.json:
        _print(result, True)
    else:
        print(f"=== Race Plan: {result['course']} ===")
        print(f"Goal: {result['goal_time']} | Base pace: {result['base_pace_display']}")
        if result["weather_temp_f"]:
            print(f"Weather: {result['weather_temp_f']}°F")
        if result["training_fade_pct"]:
            print(f"Training fade: {result['training_fade_pct']}%")
        if result["cohort_size"] > 0:
            print(f"Cohort: {result['cohort_size']} runners "
                  f"(slowdown: {result['cohort_slowdown_pct']}%)")
        print()

        for key in ("A", "B", "C"):
            p = result["plans"][key]
            print(f"--- {key}: {p['label']} (finish: {p['total_time_display']}) ---")
            print(f"{'Seg':>3} {'Name':<20} {'Dist':>5} {'Gain':>6} "
                  f"{'Pace':>9} {'SegTime':>8} {'Elapsed':>8} {'ETA':>7}")
            print("-" * 80)
            for s in p["segments"]:
                print(f"{s['segment_number']:3d} {s['segment_name']:<20} "
                      f"{s['distance_miles']:5.1f} {s['elevation_gain_ft']:+6.0f} "
                      f"{s['target_pace_display']:>9} {s['estimated_time_display']:>8} "
                      f"{s['cumulative_time_display']:>8} {s['aid_station_eta']:>7}")
            print()


def cmd_race_nutrition(args):
    goal_seconds = race_engine._parse_time(args.goal_time)
    with get_db() as conn:
        course = race_engine.get_course(conn)
        if not course:
            _err("No course loaded.", args.json, 2)

        plan = _get_plan(conn)
        plan_id = plan["id"] if plan else None

        race_plan = race_engine.generate_race_plan(
            conn, course["id"], plan_id, goal_seconds,
        )

        # Use A plan for fueling
        a_segments = race_plan["plans"]["A"]["segments"]
        fueled = race_engine.generate_fueling_plan(
            conn, course["id"], a_segments,
            weight_lbs=args.weight or race_engine.DEFAULT_WEIGHT_LBS,
        )

    if args.json:
        _print(fueled, True)
    else:
        print(f"=== Race Fueling Plan (A scenario) ===")
        print(f"{'Seg':>3} {'Name':<20} {'Cal/hr':>6} {'CalTgt':>6} "
              f"{'Na mg':>5} {'FlOz':>4} {'Deficit':>7} Notes")
        print("-" * 90)
        for s in fueled:
            notes = s.get("fueling_notes") or ""
            deficit = f"{s['deficit_pct']:.0f}%" if s["deficit_pct"] > 0 else "OK"
            print(f"{s['segment_number']:3d} "
                  f"{s['segment_name']:<20} "
                  f"{s['cal_per_hr']:6d} {s['calories_target']:6d} "
                  f"{s['sodium_mg_target']:5d} {s['fluid_oz_target']:4d} "
                  f"{deficit:>7} {notes}")


def cmd_race_crew_sheet(args):
    with get_db() as conn:
        course = race_engine.get_course(conn)
        if not course:
            _err("No course loaded.", args.json, 2)

        plan = _get_plan(conn)
        plan_id = plan["id"] if plan else None

        goal_seconds = race_engine._parse_time(args.goal_time) if args.goal_time else 24 * 3600

        race_plan = race_engine.generate_race_plan(
            conn, course["id"], plan_id, goal_seconds,
            start_time=args.start_time or "05:00",
        )

        crew_sheet = race_engine.generate_crew_sheet(
            conn, course["id"], race_plan["plans"],
            start_time=args.start_time or "05:00",
        )

    if args.json:
        _print(crew_sheet, True)
    else:
        md = race_engine.crew_sheet_to_markdown(crew_sheet)
        print(md)

        # Save to file if requested
        if args.output:
            with open(args.output, "w") as f:
                f.write(md)
            print(f"\nSaved to {args.output}", file=sys.stderr)


def cmd_race_checkin(args):
    with get_db() as conn:
        # Find the A-plan race_plan_id for the latest course
        row = conn.execute(
            """SELECT rp.id FROM race_plans rp
               JOIN race_courses rc ON rc.id = rp.course_id
               WHERE rp.scenario = 'A'
               ORDER BY rp.id DESC LIMIT 1"""
        ).fetchone()
        if not row:
            _err("No race plan found. Generate one first: ultra race plan --save",
                 args.json, 2)
        race_plan_id = row["id"]

        # Find segment by station name or number
        seg = None
        if args.station.isdigit():
            seg = conn.execute(
                """SELECT rs.id FROM race_segments rs
                   JOIN race_plans rp ON rp.course_id = rs.course_id
                   WHERE rp.id = ? AND rs.segment_number = ?""",
                (race_plan_id, int(args.station)),
            ).fetchone()
        else:
            seg = conn.execute(
                """SELECT rs.id FROM race_segments rs
                   JOIN race_plans rp ON rp.course_id = rs.course_id
                   WHERE rp.id = ? AND rs.name LIKE ?""",
                (race_plan_id, f"%{args.station}%"),
            ).fetchone()

        if not seg:
            _err(f"Segment not found: {args.station}", args.json, 2)

        elapsed = race_engine._parse_time(args.time) if args.time else None

        race_engine.race_checkin(
            conn, race_plan_id, seg["id"],
            args.clock_time, elapsed, args.notes,
        )

        result = {
            "race_plan_id": race_plan_id,
            "segment_id": seg["id"],
            "station": args.station,
            "message": "Check-in recorded",
        }

    _print(result, args.json)


def cmd_race_status(args):
    with get_db() as conn:
        row = conn.execute(
            """SELECT rp.id FROM race_plans rp
               WHERE rp.scenario = 'A'
               ORDER BY rp.id DESC LIMIT 1"""
        ).fetchone()
        if not row:
            _err("No race plan found.", args.json, 2)

        status = race_engine.get_race_status(conn, row["id"])

    if args.json:
        _print(status, True)
    else:
        print(f"=== Race Status: {status['status']} ===")
        if status.get("overall_delta_display"):
            print(f"Overall: {status['overall_delta_display']}")
        print()
        for c in status["checkins"]:
            print(f"  Mile {c['mile']:5.1f} ({c['station']}): "
                  f"planned {c['planned_elapsed']} | actual {c['actual_elapsed']} "
                  f"| {c['delta_display']}")


def cmd_race_segments(args):
    with get_db() as conn:
        course = race_engine.get_course(conn)
        if not course:
            _err("No course loaded.", args.json, 2)

        segments = race_engine.get_segments(conn, course["id"])

        if args.set_name and args.segment:
            seg = next((s for s in segments if s["segment_number"] == args.segment), None)
            if not seg:
                _err(f"Segment {args.segment} not found", args.json, 2)
            race_engine.update_segment_metadata(
                conn, seg["id"],
                name=args.set_name,
                terrain_notes=args.terrain,
                crew_accessible=args.crew if args.crew is not None else None,
                drop_bag=args.drop_bag if args.drop_bag is not None else None,
            )
            result = {"segment": args.segment, "message": f"Updated segment {args.segment}"}
            _print(result, args.json)
            return

    if args.json:
        _print(segments, True)
    else:
        print(f"=== {course['name']} — Segments ===")
        print(f"{'#':>3} {'Name':<25} {'Miles':>8} {'Gain':>7} {'Loss':>7} "
              f"{'Grade':>6} {'Crew':>4} {'Drop':>4}")
        print("-" * 75)
        for s in segments:
            name = s.get("name") or f"Mile {s['start_mile']}-{s['end_mile']}"
            crew = "✓" if s["crew_accessible"] else ""
            drop = "✓" if s["drop_bag"] else ""
            print(f"{s['segment_number']:3d} {name:<25} "
                  f"{s['start_mile']:3.1f}-{s['end_mile']:3.1f} "
                  f"{s['elevation_gain_ft']:+7.0f} {s['elevation_loss_ft']:+7.0f} "
                  f"{s['avg_grade_pct']:5.1f}% {crew:>4} {drop:>4}")


def cmd_export_md(args):
    import os
    from ultra_plan import generate_training_plan_markdown

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        md = generate_training_plan_markdown(conn, plan["id"])

    if not md:
        _err("Failed to generate markdown", args.json)

    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "TRAINING_PLAN.md")
    with open(output_path, "w") as f:
        f.write(md)

    result = {"path": output_path, "message": "TRAINING_PLAN.md regenerated"}
    if args.json:
        _print(result, True)
    else:
        print(f"Wrote {output_path}")


def _fmt_pace(pace):
    if pace is None:
        return "N/A"
    return f"{int(pace)}:{int((pace % 1) * 60):02d}/mi"


def main():
    parser = argparse.ArgumentParser(description="Workout tracker CLI")
    subparsers = parser.add_subparsers(dest="command")

    # ultra subcommand
    ultra_parser = subparsers.add_parser("ultra", help="Burning River 100 training")
    ultra_sub = ultra_parser.add_subparsers(dest="ultra_command")

    # ultra init
    init_p = ultra_sub.add_parser("init", help="Create the 20-week BR100 plan")
    init_p.add_argument("--force", action="store_true", help="Delete and recreate existing plan")
    init_p.add_argument("--json", action="store_true")

    # ultra today
    today_p = ultra_sub.add_parser("today", help="Today's prescribed workout")
    today_p.add_argument("--json", action="store_true")

    # ultra week
    week_p = ultra_sub.add_parser("week", help="Full week schedule")
    week_p.add_argument("week_num", nargs="?", type=int, default=None)
    week_p.add_argument("--json", action="store_true")

    # ultra submit
    submit_p = ultra_sub.add_parser("submit", help="Submit run data + get feedback")
    submit_p.add_argument("--distance", type=float)
    submit_p.add_argument("--duration", type=float)
    submit_p.add_argument("--hr", type=int, help="Average heart rate")
    submit_p.add_argument("--max-hr", type=int)
    submit_p.add_argument("--elevation", type=float, help="Elevation gain in feet")
    submit_p.add_argument("--effort", type=int, choices=range(1, 11), help="RPE 1-10")
    submit_p.add_argument("--pace", type=float, help="Avg pace min/mi")
    submit_p.add_argument("--notes", type=str, default="")
    submit_p.add_argument("--image", type=str, help="Strava screenshot path")
    submit_p.add_argument("--date", type=str, help="Run date (YYYY-MM-DD), default today")
    submit_p.add_argument("--pre-meal", type=str, help="Pre-run meal description")
    submit_p.add_argument("--during-fuel", type=str, help="During-run fueling (gels, food, etc)")
    submit_p.add_argument("--during-hydration", type=str, help="During-run hydration")
    submit_p.add_argument("--post-meal", type=str, help="Post-run meal description")
    submit_p.add_argument("--nutrition-notes", type=str, help="Nutrition observations (bonking, GI issues, etc)")
    submit_p.add_argument("--scheduled-date", type=str, help="Date of the prescribed workout if different from --date")
    submit_p.add_argument("--json", action="store_true")

    # ultra feedback
    fb_p = ultra_sub.add_parser("feedback", help="Recent run feedback")
    fb_p.add_argument("--json", action="store_true")

    # ultra progress
    prog_p = ultra_sub.add_parser("progress", help="Overall progress dashboard")
    prog_p.add_argument("--json", action="store_true")

    # ultra benchmarks
    bm_p = ultra_sub.add_parser("benchmarks", help="Benchmark schedule + results")
    bm_p.add_argument("--json", action="store_true")

    # ultra upcoming
    up_p = ultra_sub.add_parser("upcoming", help="Next N days of workouts")
    up_p.add_argument("--days", type=int, default=7)
    up_p.add_argument("--json", action="store_true")

    # ultra icu-push
    icu_p = ultra_sub.add_parser("icu-push", help="Push workouts to Intervals.icu (syncs to Coros)")
    icu_p.add_argument("--week", type=int, help="Push all workouts for a specific week")
    icu_p.add_argument("--date", type=str, help="Push workout for specific date (YYYY-MM-DD)")
    icu_p.add_argument("--upcoming", type=int, metavar="DAYS", help="Push next N days of workouts")
    icu_p.add_argument("--all", action="store_true", help="Push entire plan")
    icu_p.add_argument("--dry-run", action="store_true", help="Show what would be pushed without calling API")
    icu_p.add_argument("--json", action="store_true")

    # ultra export-fit
    ef_p = ultra_sub.add_parser("export-fit", help="Export FIT workout files for Coros")
    ef_p.add_argument("--week", type=int, help="Export all workouts for a specific week")
    ef_p.add_argument("--date", type=str, help="Export workout for specific date (YYYY-MM-DD)")
    ef_p.add_argument("--all", action="store_true", help="Export entire plan")
    ef_p.add_argument("--json", action="store_true")

    # ultra strava-connect
    sc_p = ultra_sub.add_parser("strava-connect", help="Seed Strava tokens")
    sc_p.add_argument("--access-token", type=str, required=True)
    sc_p.add_argument("--refresh-token", type=str, required=True)
    sc_p.add_argument("--expires-at", type=int, default=None, help="Token expiry unix timestamp")
    sc_p.add_argument("--force", action="store_true", help="Overwrite existing tokens")
    sc_p.add_argument("--json", action="store_true")

    # ultra strava-status
    ss_p = ultra_sub.add_parser("strava-status", help="Check Strava connection")
    ss_p.add_argument("--json", action="store_true")

    # ultra strava-import
    si_p = ultra_sub.add_parser("strava-import", help="Import runs from Strava")
    si_p.add_argument("--count", type=int, default=10, help="Number of activities to fetch")
    si_p.add_argument("--list", action="store_true", help="List activities without importing")
    si_p.add_argument("--all-types", action="store_true", help="Include non-run activities")
    si_p.add_argument("--no-feedback", action="store_true", help="Skip LLM feedback")
    si_p.add_argument("--json", action="store_true")

    # ultra adapt
    adapt_p = ultra_sub.add_parser("adapt", help="Run adaptive target adjustments")
    adapt_p.add_argument("--dry-run", action="store_true", help="Show proposed changes without applying")
    adapt_p.add_argument("--json", action="store_true")

    # ultra targets
    tgt_p = ultra_sub.add_parser("targets", help="Show or set pace/HR targets")
    tgt_p.add_argument("--history", action="store_true", help="Show full target timeline")
    tgt_p.add_argument("--set", action="store_true", help="Set targets manually")
    tgt_p.add_argument("--easy", type=float, help="Easy pace (min/mi, e.g. 10.25)")
    tgt_p.add_argument("--long-run", type=float, help="Long run pace (min/mi)")
    tgt_p.add_argument("--tempo", type=float, help="Tempo pace (min/mi)")
    tgt_p.add_argument("--notes", type=str, help="Note for this target change")
    tgt_p.add_argument("--json", action="store_true")

    # ultra plan
    plan_p = ultra_sub.add_parser("plan", help="Export/manage the training plan")
    plan_p.add_argument("--export-md", action="store_true", help="Regenerate TRAINING_PLAN.md from DB")
    plan_p.add_argument("--json", action="store_true")

    # ultra nutrition
    nut_p = ultra_sub.add_parser("nutrition", help="Nutrition guidelines for a workout")
    nut_p.add_argument("--distance", type=float, help="Distance in miles")
    nut_p.add_argument("--duration", type=float, help="Duration in minutes")
    nut_p.add_argument("--workout-type", type=str, default=None, help="Workout type (easy_run, long_run, etc)")
    nut_p.add_argument("--json", action="store_true")

    # -----------------------------------------------------------------------
    # ultra race — Race Day Engine subcommands
    # -----------------------------------------------------------------------
    race_p = ultra_sub.add_parser("race", help="Race day planning & execution")
    race_sub = race_p.add_subparsers(dest="race_command")

    # ultra race load-course
    rlc_p = race_sub.add_parser("load-course", help="Load a race course from GPX file")
    rlc_p.add_argument("gpx_file", type=str, help="Path to GPX file")
    rlc_p.add_argument("--name", type=str, required=True, help="Course name")
    rlc_p.add_argument("--year", type=int, required=True, help="Race year")
    rlc_p.add_argument("--segment-breaks", type=str,
                       help="Comma-separated mile markers for segments (e.g. '5.2,12.8,20.1')")
    rlc_p.add_argument("--json", action="store_true")

    # ultra race import-results
    rir_p = race_sub.add_parser("import-results", help="Import historical race results from CSV")
    rir_p.add_argument("csv_file", type=str, help="Path to CSV file")
    rir_p.add_argument("--year", type=int, required=True, help="Results year")
    rir_p.add_argument("--course-name", type=str, default="Burning River 100",
                       help="Course name to associate results with")
    rir_p.add_argument("--json", action="store_true")

    # ultra race cohort
    rco_p = race_sub.add_parser("cohort", help="Analyze peer cohort from historical results")
    rco_p.add_argument("--goal-time", type=str, required=True,
                       help="Goal finish time (HH:MM:SS)")
    rco_p.add_argument("--course-name", type=str, default="Burning River 100")
    rco_p.add_argument("--json", action="store_true")

    # ultra race plan
    rpl_p = race_sub.add_parser("plan", help="Generate A/B/C race execution plans")
    rpl_p.add_argument("--goal-time", type=str, required=True,
                       help="Goal finish time (HH:MM:SS)")
    rpl_p.add_argument("--weather-temp", type=float, help="Forecast temperature (°F)")
    rpl_p.add_argument("--start-time", type=str, default="05:00",
                       help="Race start time (HH:MM, default 05:00)")
    rpl_p.add_argument("--save", action="store_true", help="Save plan to database")
    rpl_p.add_argument("--json", action="store_true")

    # ultra race nutrition
    rnu_p = race_sub.add_parser("nutrition", help="Generate per-segment fueling plan")
    rnu_p.add_argument("--goal-time", type=str, required=True,
                       help="Goal finish time (HH:MM:SS)")
    rnu_p.add_argument("--weight", type=int, help="Runner weight in lbs (default 170)")
    rnu_p.add_argument("--json", action="store_true")

    # ultra race crew-sheet
    rcs_p = race_sub.add_parser("crew-sheet", help="Generate crew sheet with multi-scenario ETAs")
    rcs_p.add_argument("--goal-time", type=str, help="Goal finish time (HH:MM:SS)")
    rcs_p.add_argument("--start-time", type=str, default="05:00",
                       help="Race start time (HH:MM)")
    rcs_p.add_argument("--output", type=str, help="Save markdown to file path")
    rcs_p.add_argument("--json", action="store_true")

    # ultra race checkin
    rci_p = race_sub.add_parser("checkin", help="Log arrival at an aid station during race")
    rci_p.add_argument("--station", type=str, required=True,
                       help="Station name or segment number")
    rci_p.add_argument("--time", type=str, help="Elapsed time (HH:MM:SS)")
    rci_p.add_argument("--clock-time", type=str, help="Actual clock time of arrival")
    rci_p.add_argument("--notes", type=str)
    rci_p.add_argument("--json", action="store_true")

    # ultra race status
    rst_p = race_sub.add_parser("status", help="Show current race status vs plan")
    rst_p.add_argument("--json", action="store_true")

    # ultra race segments
    rsg_p = race_sub.add_parser("segments", help="View/edit course segments")
    rsg_p.add_argument("--segment", type=int, help="Segment number to edit")
    rsg_p.add_argument("--set-name", type=str, help="Set segment/aid station name")
    rsg_p.add_argument("--terrain", type=str, help="Set terrain notes")
    rsg_p.add_argument("--crew", type=int, choices=[0, 1], help="Set crew accessible (0/1)")
    rsg_p.add_argument("--drop-bag", type=int, choices=[0, 1], help="Set drop bag available (0/1)")
    rsg_p.add_argument("--json", action="store_true")

    # -----------------------------------------------------------------------
    # gym subcommand — strength & gym workout tracking
    # -----------------------------------------------------------------------
    gym_parser = subparsers.add_parser("gym", help="Strength & gym workout tracking")
    gym_sub = gym_parser.add_subparsers(dest="gym_command")

    # gym log
    gym_log_p = gym_sub.add_parser("log", help="Log a strength workout")
    gym_log_p.add_argument("--exercise", action="append", metavar="NAME:SETS:REPS:WEIGHT",
                           help="Exercise in name:sets:reps:weight format (repeatable)")
    gym_log_p.add_argument("--text", type=str, help="Free-text workout description (uses AI to parse)")
    gym_log_p.add_argument("--date", type=str, help="Workout date (YYYY-MM-DD), default today")
    gym_log_p.add_argument("--duration", type=int, help="Total session duration in minutes")
    gym_log_p.add_argument("--notes", type=str, default="")
    gym_log_p.add_argument("--json", action="store_true")

    # gym pr
    gym_pr_p = gym_sub.add_parser("pr", help="View/manage personal records")
    gym_pr_p.add_argument("--exercise", type=str, help="Filter by exercise name (snake_case)")
    gym_pr_p.add_argument("--set", action="store_true", help="Set a PR manually")
    gym_pr_p.add_argument("--type", type=str, choices=["1RM", "5RM", "max_reps", "best_time"],
                          help="PR type (required with --set)")
    gym_pr_p.add_argument("--value", type=float, help="PR value (required with --set)")
    gym_pr_p.add_argument("--json", action="store_true")

    # gym suggest
    gym_sug_p = gym_sub.add_parser("suggest", help="Generate a workout with weight suggestions")
    gym_sug_p.add_argument("prompt", nargs="?", type=str, help="Workout request (e.g., 'upper body 30 minutes')")
    gym_sug_p.add_argument("--type", type=str, choices=["upper-body", "lower-body", "full-body", "push", "pull", "legs"],
                           help="Shorthand workout type preset")
    gym_sug_p.add_argument("--json", action="store_true")

    # gym history
    gym_hist_p = gym_sub.add_parser("history", help="View past gym sessions")
    gym_hist_p.add_argument("--last", type=int, default=10, help="Number of workouts to show")
    gym_hist_p.add_argument("--exercise", type=str, help="Filter by exercise name")
    gym_hist_p.add_argument("--json", action="store_true")

    # gym exercises
    gym_ex_p = gym_sub.add_parser("exercises", help="List available exercises")
    gym_ex_p.add_argument("--category", type=str, help="Filter by category")
    gym_ex_p.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    init_db()

    if args.command == "ultra":
        if not getattr(args, "ultra_command", None):
            ultra_parser.print_help()
            sys.exit(1)

        if args.ultra_command == "race":
            if not getattr(args, "race_command", None):
                race_p.print_help()
                sys.exit(1)

            race_commands = {
                "load-course": cmd_race_load_course,
                "import-results": cmd_race_import_results,
                "cohort": cmd_race_cohort,
                "plan": cmd_race_plan,
                "nutrition": cmd_race_nutrition,
                "crew-sheet": cmd_race_crew_sheet,
                "checkin": cmd_race_checkin,
                "status": cmd_race_status,
                "segments": cmd_race_segments,
            }

            cmd = race_commands.get(args.race_command)
            if cmd:
                cmd(args)
            else:
                race_p.print_help()
                sys.exit(1)
            return

        ultra_commands = {
            "init": cmd_init,
            "today": cmd_today,
            "week": cmd_week,
            "submit": cmd_submit,
            "feedback": cmd_feedback,
            "progress": cmd_progress,
            "benchmarks": cmd_benchmarks,
            "upcoming": cmd_upcoming,
            "icu-push": cmd_icu_push,
            "export-fit": cmd_export_fit,
            "strava-connect": cmd_strava_connect,
            "strava-status": cmd_strava_status,
            "strava-import": cmd_strava_import,
            "adapt": cmd_adapt,
            "targets": cmd_targets,
            "plan": cmd_export_md,
            "nutrition": cmd_nutrition,
        }

        cmd = ultra_commands.get(args.ultra_command)
        if cmd:
            cmd(args)
        else:
            ultra_parser.print_help()
            sys.exit(1)

    elif args.command == "gym":
        from .gym import cmd_gym_log, cmd_gym_pr, cmd_gym_suggest, cmd_gym_history, cmd_gym_exercises

        if not getattr(args, "gym_command", None):
            gym_parser.print_help()
            sys.exit(1)

        gym_commands = {
            "log": cmd_gym_log,
            "pr": cmd_gym_pr,
            "suggest": cmd_gym_suggest,
            "history": cmd_gym_history,
            "exercises": cmd_gym_exercises,
        }

        cmd = gym_commands.get(args.gym_command)
        if cmd:
            cmd(args)
        else:
            gym_parser.print_help()
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
