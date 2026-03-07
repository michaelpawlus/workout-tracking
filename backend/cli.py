#!/usr/bin/env python3
"""CLI for Burning River 100 training plan and feedback system.

All commands support --json for agent-friendly output.
Exit codes: 0=success, 1=error, 2=not found.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta

from database import init_db, get_db
from ultra_plan import create_br100_plan
from llm import analyze_run_feedback, analyze_strava_screenshot


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
        if existing:
            result = {"plan_id": existing["id"], "message": "Plan already exists", "already_existed": True}
            _print(result, args.json)
            return

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


def cmd_submit(args):
    if args.image:
        _submit_image(args)
        return

    if not args.distance:
        _err("--distance is required (or use --image)", args.json)

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        plan = _get_plan(conn)
        if not plan:
            _err("No active BR100 plan. Run: python cli.py ultra init", args.json, 2)

        daily = conn.execute(
            "SELECT * FROM daily_workouts WHERE plan_id = ? AND scheduled_date = ?",
            (plan["id"], today),
        ).fetchone()

        pace = args.pace
        if pace is None and args.distance and args.duration:
            pace = args.duration / args.distance

        cursor = conn.execute(
            """INSERT INTO workouts (date, workout_type, duration_minutes, notes, source, plan_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (today, "cardio", args.duration, args.notes or "", "cli", plan["id"]),
        )
        workout_id = cursor.lastrowid

        daily_workout_id = None
        if daily:
            daily_workout_id = daily["id"]
            conn.execute(
                "UPDATE daily_workouts SET completed = 1, actual_workout_id = ? WHERE id = ?",
                (workout_id, daily["id"]),
            )

        prescribed = dict(daily) if daily else {"title": "Unscheduled run", "scheduled_date": today}
        actual = {
            "distance_miles": args.distance,
            "duration_minutes": args.duration,
            "avg_pace_min_per_mile": pace,
            "avg_heart_rate": args.hr,
            "max_heart_rate": args.max_hr,
            "elevation_gain_ft": args.elevation,
            "effort_rating": args.effort,
        }

        # Weekly context
        week_row = conn.execute(
            """SELECT tpw.*, ws.target_miles, ws.actual_miles, ws.runs_planned, ws.runs_completed
               FROM training_plan_weeks tpw
               LEFT JOIN weekly_summaries ws ON ws.plan_id = tpw.plan_id AND ws.week_number = tpw.week_number
               JOIN daily_workouts dw ON dw.week_id = tpw.id
               WHERE dw.plan_id = ? AND dw.scheduled_date = ?
               LIMIT 1""",
            (plan["id"], today),
        ).fetchone()
        weekly_context = dict(week_row) if week_row else None

        if week_row:
            conn.execute(
                """UPDATE weekly_summaries SET actual_miles = actual_miles + ?, runs_completed = runs_completed + 1
                   WHERE plan_id = ? AND week_number = ?""",
                (args.distance, plan["id"], week_row["week_number"]),
            )

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

    # LLM feedback
    try:
        if not args.json:
            print("Analyzing run...", file=sys.stderr)
        feedback = analyze_run_feedback(prescribed, actual, weekly_context, trend_data, benchmark_data, race_info)
    except Exception as e:
        feedback = {
            "compliance_score": None,
            "pace_feedback": f"Unable to generate AI feedback: {e}",
            "hr_feedback": "", "distance_feedback": "", "overall_feedback": "",
            "warnings": [], "race_readiness": "Unknown",
        }

    # Save feedback
    with get_db() as conn:
        conn.execute(
            """INSERT INTO run_feedback
               (workout_id, daily_workout_id, plan_id, prescribed_distance_miles,
                actual_distance_miles, prescribed_pace, actual_pace, avg_heart_rate,
                max_heart_rate, elevation_gain_ft, effort_rating, compliance_score,
                pace_feedback, hr_feedback, overall_feedback, warnings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workout_id, daily_workout_id, plan["id"],
             prescribed.get("target_distance_miles"), args.distance,
             prescribed.get("target_pace_min_per_mile"), pace,
             args.hr, args.max_hr, args.elevation, args.effort,
             feedback.get("compliance_score"),
             feedback.get("pace_feedback", ""),
             feedback.get("hr_feedback", ""),
             feedback.get("overall_feedback", ""),
             json.dumps(feedback.get("warnings", []))),
        )

    result = {"workout_id": workout_id, "feedback": feedback}

    if args.json:
        _print(result, True)
    else:
        print(f"\nRun logged (workout #{workout_id})")
        print(f"Distance: {args.distance}mi | Duration: {args.duration or '?'}min | Pace: {_fmt_pace(pace)}")
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
    }

    cmd = commands.get(args.ultra_command)
    if cmd:
        cmd(args)
    else:
        ultra_parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
