import json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from database import init_db, get_db
from llm import (
    generate_workout, parse_workout_log, parse_workout_image,
    analyze_progress, generate_training_plan, analyze_plan_progress,
)
import strava


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="Workout Tracker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic models ---

class WorkoutRequest(BaseModel):
    prompt: str


class LogRequest(BaseModel):
    user_input: str
    prescribed_workout: dict | None = None


class SaveWorkoutLog(BaseModel):
    workout_type: str
    duration_minutes: int | None = None
    notes: str | None = None
    prescribed_workout: dict | None = None
    exercises: list[dict]
    source: str = "manual"
    plan_id: int | None = None
    plan_week: int | None = None


class PRConfirm(BaseModel):
    exercise_name: str
    record_type: str
    value: float
    workout_id: int | None = None


class ProgressQuery(BaseModel):
    question: str


class PlanRequest(BaseModel):
    goal: str
    total_weeks: int
    modalities: list[str]
    start_date: str
    mesocycle_weeks: int = 4
    notes: str = ""


class PlanUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


class BenchmarkResult(BaseModel):
    result_value: float
    result_notes: str = ""
    workout_id: int | None = None


# --- Exercise endpoints ---

@app.get("/api/exercises")
def list_exercises():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM exercises ORDER BY category, display_name"
        ).fetchall()
    return [dict(r) for r in rows]


# --- Workout generation ---

@app.post("/api/workouts/generate")
def generate(req: WorkoutRequest):
    try:
        workout = generate_workout(req.prompt)
        return workout
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Workout logging ---

@app.post("/api/workouts/parse-log")
def parse_log(req: LogRequest):
    try:
        parsed = parse_workout_log(req.user_input, req.prescribed_workout)
        return parsed
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/workouts/parse-image")
async def parse_image(
    file: UploadFile = File(...),
    prescribed_workout: str = Form(default=""),
):
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {file.content_type}")
    try:
        image_bytes = await file.read()
        prescribed = json.loads(prescribed_workout) if prescribed_workout else None
        parsed = parse_workout_image(image_bytes, file.content_type, prescribed)
        return parsed
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid prescribed_workout JSON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/workouts/save")
def save_workout(req: SaveWorkoutLog):
    with get_db() as conn:
        prescribed_json = json.dumps(req.prescribed_workout) if req.prescribed_workout else None
        cursor = conn.execute(
            """INSERT INTO workouts (workout_type, duration_minutes, notes, llm_generated, prescribed_workout, source, plan_id, plan_week)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.workout_type, req.duration_minutes, req.notes,
             1 if req.prescribed_workout else 0, prescribed_json,
             req.source, req.plan_id, req.plan_week),
        )
        workout_id = cursor.lastrowid

        for ex in req.exercises:
            # Look up exercise by name
            row = conn.execute(
                "SELECT id FROM exercises WHERE name = ?", (ex["exercise_name"],)
            ).fetchone()
            if not row:
                # Try to create it on the fly
                cursor2 = conn.execute(
                    "INSERT INTO exercises (name, display_name, category, primary_metric) VALUES (?, ?, ?, ?)",
                    (ex["exercise_name"], ex.get("display_name", ex["exercise_name"]),
                     ex.get("category", "other"), ex.get("primary_metric", "weight")),
                )
                exercise_id = cursor2.lastrowid
            else:
                exercise_id = row["id"]

            conn.execute(
                """INSERT INTO workout_exercises
                   (workout_id, exercise_id, sets, reps, weight_lbs, time_seconds, rounds_completed, distance_meters, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (workout_id, exercise_id,
                 ex.get("sets"), ex.get("reps"), ex.get("weight_lbs"),
                 ex.get("time_seconds"), ex.get("rounds_completed"),
                 ex.get("distance_meters"), ex.get("notes")),
            )

    return {"workout_id": workout_id, "message": "Workout saved"}


# --- Workout history ---

@app.get("/api/workouts")
def list_workouts(limit: int = 20):
    with get_db() as conn:
        workouts = conn.execute(
            "SELECT * FROM workouts ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for w in workouts:
            wd = dict(w)
            exercises = conn.execute("""
                SELECT we.*, e.display_name, e.name as exercise_name, e.category
                FROM workout_exercises we
                JOIN exercises e ON e.id = we.exercise_id
                WHERE we.workout_id = ?
            """, (w["id"],)).fetchall()
            wd["exercises"] = [dict(e) for e in exercises]
            result.append(wd)
    return result


@app.get("/api/workouts/{workout_id}")
def get_workout(workout_id: int):
    with get_db() as conn:
        w = conn.execute("SELECT * FROM workouts WHERE id = ?", (workout_id,)).fetchone()
        if not w:
            raise HTTPException(status_code=404, detail="Workout not found")
        wd = dict(w)
        exercises = conn.execute("""
            SELECT we.*, e.display_name, e.name as exercise_name, e.category
            FROM workout_exercises we
            JOIN exercises e ON e.id = we.exercise_id
            WHERE we.workout_id = ?
        """, (workout_id,)).fetchall()
        wd["exercises"] = [dict(e) for e in exercises]
    return wd


# --- Personal records ---

@app.get("/api/prs")
def list_prs():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT pr.*, e.display_name, e.name as exercise_name
            FROM personal_records pr
            JOIN exercises e ON e.id = pr.exercise_id
            ORDER BY pr.date_achieved DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/prs/confirm")
def confirm_pr(req: PRConfirm):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM exercises WHERE name = ?", (req.exercise_name,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Exercise not found")
        exercise_id = row["id"]

        # Check if this actually beats the current record
        current = conn.execute("""
            SELECT value FROM personal_records
            WHERE exercise_id = ? AND record_type = ?
            ORDER BY value DESC LIMIT 1
        """, (exercise_id, req.record_type)).fetchone()

        is_new_pr = current is None or req.value > current["value"]

        if is_new_pr:
            conn.execute(
                """INSERT INTO personal_records (exercise_id, record_type, value, workout_id)
                   VALUES (?, ?, ?, ?)""",
                (exercise_id, req.record_type, req.value, req.workout_id),
            )
            return {"confirmed": True, "message": f"New PR recorded: {req.value}!"}
        else:
            return {"confirmed": False, "message": f"Current record is {current['value']}, not a new PR."}


# --- Progress analysis ---

@app.post("/api/progress/analyze")
def analyze(req: ProgressQuery):
    try:
        analysis = analyze_progress(req.question)
        return {"analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Benchmark tests ---

@app.get("/api/benchmarks")
def list_benchmarks():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_tests ORDER BY date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# --- Training Plans ---

@app.post("/api/plans/generate")
def create_plan(req: PlanRequest):
    try:
        plan_data = generate_training_plan(
            goal=req.goal,
            total_weeks=req.total_weeks,
            modalities=req.modalities,
            start_date=req.start_date,
            mesocycle_weeks=req.mesocycle_weeks,
            notes=req.notes,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    start = datetime.strptime(req.start_date, "%Y-%m-%d")
    end = start + timedelta(weeks=req.total_weeks)

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO training_plans (name, goal, start_date, end_date, total_weeks, mesocycle_weeks, plan_json, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (plan_data.get("plan_name", "Training Plan"), req.goal,
             req.start_date, end.strftime("%Y-%m-%d"), req.total_weeks,
             req.mesocycle_weeks, json.dumps(plan_data),
             plan_data.get("notes", "")),
        )
        plan_id = cursor.lastrowid

        for week in plan_data.get("weeks", []):
            week_cursor = conn.execute(
                """INSERT INTO training_plan_weeks (plan_id, week_number, week_type, focus, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (plan_id, week["week_number"], week["week_type"],
                 week.get("focus", ""), week.get("notes", "")),
            )
            week_id = week_cursor.lastrowid

            week_start = start + timedelta(weeks=week["week_number"] - 1)
            week_end = week_start + timedelta(days=6)

            for bm in week.get("benchmarks", []):
                conn.execute(
                    """INSERT INTO plan_benchmarks (plan_id, week_id, benchmark_name, benchmark_type, target_value, scheduled_date)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (plan_id, week_id, bm["benchmark_name"], bm["benchmark_type"],
                     bm.get("target_value"), week_end.strftime("%Y-%m-%d")),
                )

    return {"plan_id": plan_id, "plan": plan_data}


@app.get("/api/plans")
def list_plans():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, goal, start_date, end_date, total_weeks, mesocycle_weeks, status, notes, created_at FROM training_plans ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int):
    with get_db() as conn:
        plan = conn.execute("SELECT * FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        pd = dict(plan)
        if pd.get("plan_json"):
            pd["plan_json"] = json.loads(pd["plan_json"])

        weeks = conn.execute(
            "SELECT * FROM training_plan_weeks WHERE plan_id = ? ORDER BY week_number", (plan_id,)
        ).fetchall()
        pd["weeks"] = []
        for w in weeks:
            wd = dict(w)
            benchmarks = conn.execute(
                "SELECT * FROM plan_benchmarks WHERE week_id = ?", (w["id"],)
            ).fetchall()
            wd["benchmarks"] = [dict(b) for b in benchmarks]
            pd["weeks"].append(wd)

    return pd


@app.put("/api/plans/{plan_id}")
def update_plan(plan_id: int, req: PlanUpdate):
    with get_db() as conn:
        plan = conn.execute("SELECT id FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        if req.status:
            conn.execute("UPDATE training_plans SET status = ? WHERE id = ?", (req.status, plan_id))
        if req.notes is not None:
            conn.execute("UPDATE training_plans SET notes = ? WHERE id = ?", (req.notes, plan_id))
    return {"message": "Plan updated"}


@app.get("/api/plans/{plan_id}/benchmarks")
def list_plan_benchmarks(plan_id: int):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT pb.*, tpw.week_number, tpw.week_type
            FROM plan_benchmarks pb
            JOIN training_plan_weeks tpw ON tpw.id = pb.week_id
            WHERE pb.plan_id = ?
            ORDER BY tpw.week_number, pb.id
        """, (plan_id,)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/plans/{plan_id}/benchmarks/{benchmark_id}/result")
def record_benchmark_result(plan_id: int, benchmark_id: int, req: BenchmarkResult):
    with get_db() as conn:
        bm = conn.execute(
            "SELECT * FROM plan_benchmarks WHERE id = ? AND plan_id = ?", (benchmark_id, plan_id)
        ).fetchone()
        if not bm:
            raise HTTPException(status_code=404, detail="Benchmark not found")
        conn.execute(
            """UPDATE plan_benchmarks SET completed = 1, result_value = ?, result_notes = ?, workout_id = ?
               WHERE id = ?""",
            (req.result_value, req.result_notes, req.workout_id, benchmark_id),
        )
    return {"message": "Benchmark result recorded"}


@app.get("/api/plans/{plan_id}/progress")
def plan_progress(plan_id: int):
    with get_db() as conn:
        plan = conn.execute("SELECT * FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        plan_data = dict(plan)
        if plan_data.get("plan_json"):
            plan_data["plan_json"] = json.loads(plan_data["plan_json"])

        benchmarks = conn.execute("""
            SELECT pb.*, tpw.week_number, tpw.week_type
            FROM plan_benchmarks pb
            JOIN training_plan_weeks tpw ON tpw.id = pb.week_id
            WHERE pb.plan_id = ?
            ORDER BY tpw.week_number, pb.id
        """, (plan_id,)).fetchall()
        benchmarks = [dict(b) for b in benchmarks]

    try:
        analysis = analyze_plan_progress(plan_data, benchmarks)
        return {"analysis": analysis, "benchmarks": benchmarks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Strava Integration ---

@app.get("/api/strava/auth-url")
def strava_auth_url():
    if not strava.STRAVA_CLIENT_ID:
        raise HTTPException(status_code=400, detail="STRAVA_CLIENT_ID not configured")
    return {"url": strava.get_auth_url()}


@app.get("/api/strava/callback")
def strava_callback(code: str):
    try:
        strava.exchange_code(code)
        return RedirectResponse(url="http://localhost:5173?strava=connected")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strava/status")
def strava_status():
    return {"connected": strava.is_connected()}


@app.get("/api/strava/activities")
def strava_activities(per_page: int = 30):
    try:
        activities = strava.get_activities(per_page)
        return activities
    except RuntimeError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/strava/import/{activity_id}")
def strava_import(activity_id: int):
    try:
        activity = strava.get_activity_detail(activity_id)
        workout_text = strava.strava_to_workout_text(activity)
        parsed = parse_workout_log(workout_text)
        parsed["source"] = "strava"
        parsed["strava_activity"] = {
            "id": activity["id"],
            "name": activity.get("name", ""),
            "type": activity.get("sport_type", activity.get("type", "")),
        }
        return parsed
    except RuntimeError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/strava/disconnect")
def strava_disconnect():
    strava.disconnect()
    return {"message": "Strava disconnected"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
