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

from database import init_db, get_db
from ultra_plan import create_br100_plan
from llm import analyze_run_feedback, analyze_strava_screenshot
from adapt import (
    get_current_targets, get_targets_history, seed_initial_targets,
    adapt_from_maf, adapt_from_5k_tt, adapt_from_trends,
    apply_targets_to_future_workouts, format_adaptation_report,
    find_unprocessed_benchmarks,
)
import strava


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
        print("20 weeks: Mar 6 - Jul 25, 2026")
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
                skip_feedback=False, strava_activity_id=None, as_json=False):
    """Core run submission logic. Returns result dict."""
    if run_date is None:
        run_date = datetime.now().strftime("%Y-%m-%d")

    if pace is None and distance and duration:
        pace = duration / distance

    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", as_json, 2)

        daily = conn.execute(
            "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
            (plan["id"], run_date),
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

    try:
        if not as_json:
            print("Analyzing run...", file=sys.stderr)
        feedback = analyze_run_feedback(prescribed, actual, weekly_context, trend_data, benchmark_data, race_info, athlete_targets=targets_dict)
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
                pace_feedback, hr_feedback, overall_feedback, warnings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workout_id, daily_workout_id, plan["id"],
             prescribed.get("target_distance_miles"), distance,
             prescribed.get("target_pace_min_per_mile"), pace,
             hr, max_hr, elevation, effort,
             feedback.get("compliance_score"),
             feedback.get("pace_feedback", ""),
             feedback.get("hr_feedback", ""),
             feedback.get("overall_feedback", ""),
             json.dumps(feedback.get("warnings", []))),
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
    from fit_export import export_workout_fit, export_week_fits

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
    from intervals_icu import create_event, create_events_bulk, workout_to_icu_description

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
    tgt_p = ultra_sub.add_parser("targets", help="Show current pace/HR targets")
    tgt_p.add_argument("--history", action="store_true", help="Show full target timeline")
    tgt_p.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command != "ultra" or not args.ultra_command:
        parser.print_help()
        sys.exit(1)

    init_db()

    commands = {
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
    }

    cmd = commands.get(args.ultra_command)
    if cmd:
        cmd(args)
    else:
        ultra_parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
