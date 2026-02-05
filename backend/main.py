import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import init_db, get_db
from llm import generate_workout, parse_workout_log, analyze_progress


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


class PRConfirm(BaseModel):
    exercise_name: str
    record_type: str
    value: float
    workout_id: int | None = None


class ProgressQuery(BaseModel):
    question: str


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


@app.post("/api/workouts/save")
def save_workout(req: SaveWorkoutLog):
    with get_db() as conn:
        prescribed_json = json.dumps(req.prescribed_workout) if req.prescribed_workout else None
        cursor = conn.execute(
            """INSERT INTO workouts (workout_type, duration_minutes, notes, llm_generated, prescribed_workout)
               VALUES (?, ?, ?, ?, ?)""",
            (req.workout_type, req.duration_minutes, req.notes,
             1 if req.prescribed_workout else 0, prescribed_json),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
