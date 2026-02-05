import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "workouts.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                category TEXT NOT NULL,
                primary_metric TEXT NOT NULL DEFAULT 'weight'
            );

            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL DEFAULT (date('now')),
                workout_type TEXT NOT NULL,
                duration_minutes INTEGER,
                notes TEXT,
                llm_generated INTEGER NOT NULL DEFAULT 0,
                prescribed_workout TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workout_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                exercise_id INTEGER NOT NULL REFERENCES exercises(id),
                sets INTEGER,
                reps INTEGER,
                weight_lbs REAL,
                time_seconds INTEGER,
                rounds_completed REAL,
                distance_meters REAL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS personal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_id INTEGER NOT NULL REFERENCES exercises(id),
                record_type TEXT NOT NULL,
                value REAL NOT NULL,
                date_achieved TEXT NOT NULL DEFAULT (date('now')),
                workout_id INTEGER REFERENCES workouts(id)
            );

            CREATE TABLE IF NOT EXISTS benchmark_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name TEXT NOT NULL,
                date TEXT NOT NULL DEFAULT (date('now')),
                result TEXT NOT NULL,
                notes TEXT,
                workout_id INTEGER REFERENCES workouts(id)
            );
        """)

        # Seed common exercises if table is empty
        count = conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0]
        if count == 0:
            exercises = [
                # Squat patterns
                ("back_squat", "Back Squat", "squat", "weight"),
                ("front_squat", "Front Squat", "squat", "weight"),
                ("goblet_squat", "Goblet Squat", "squat", "weight"),
                ("air_squat", "Air Squat", "squat", "reps"),
                ("overhead_squat", "Overhead Squat", "squat", "weight"),
                ("pistol_squat", "Pistol Squat", "squat", "reps"),
                ("bulgarian_split_squat", "Bulgarian Split Squat", "squat", "weight"),
                ("wall_sit", "Wall Sit", "squat", "time"),
                # Press patterns
                ("bench_press", "Bench Press", "press", "weight"),
                ("overhead_press", "Overhead Press", "press", "weight"),
                ("push_press", "Push Press", "press", "weight"),
                ("incline_bench_press", "Incline Bench Press", "press", "weight"),
                ("dumbbell_bench_press", "Dumbbell Bench Press", "press", "weight"),
                ("dumbbell_overhead_press", "Dumbbell Overhead Press", "press", "weight"),
                ("push_up", "Push-Up", "press", "reps"),
                ("dip", "Dip", "press", "reps"),
                ("handstand_push_up", "Handstand Push-Up", "press", "reps"),
                # Pull patterns
                ("deadlift", "Deadlift", "pull", "weight"),
                ("sumo_deadlift", "Sumo Deadlift", "pull", "weight"),
                ("romanian_deadlift", "Romanian Deadlift", "hinge", "weight"),
                ("pull_up", "Pull-Up", "pull", "reps"),
                ("chin_up", "Chin-Up", "pull", "reps"),
                ("barbell_row", "Barbell Row", "pull", "weight"),
                ("dumbbell_row", "Dumbbell Row", "pull", "weight"),
                ("pendlay_row", "Pendlay Row", "pull", "weight"),
                ("lat_pulldown", "Lat Pulldown", "pull", "weight"),
                ("cable_row", "Cable Row", "pull", "weight"),
                # Hinge patterns
                ("kettlebell_swing", "Kettlebell Swing", "hinge", "weight"),
                ("hip_thrust", "Hip Thrust", "hinge", "weight"),
                ("good_morning", "Good Morning", "hinge", "weight"),
                ("glute_bridge", "Glute Bridge", "hinge", "weight"),
                # Olympic lifts
                ("clean", "Clean", "olympic", "weight"),
                ("clean_and_jerk", "Clean & Jerk", "olympic", "weight"),
                ("snatch", "Snatch", "olympic", "weight"),
                ("power_clean", "Power Clean", "olympic", "weight"),
                ("hang_clean", "Hang Clean", "olympic", "weight"),
                ("thruster", "Thruster", "olympic", "weight"),
                # Lunge patterns
                ("front_rack_lunge", "Front Rack Lunge", "lunge", "weight"),
                ("walking_lunge", "Walking Lunge", "lunge", "weight"),
                ("reverse_lunge", "Reverse Lunge", "lunge", "weight"),
                ("lateral_lunge", "Lateral Lunge", "lunge", "weight"),
                ("step_up", "Step-Up", "lunge", "weight"),
                # Carry patterns
                ("farmers_carry", "Farmer's Carry", "carry", "weight"),
                ("overhead_carry", "Overhead Carry", "carry", "weight"),
                ("suitcase_carry", "Suitcase Carry", "carry", "weight"),
                # Cardio
                ("run", "Run", "cardio", "distance"),
                ("row_erg", "Row (Erg)", "cardio", "distance"),
                ("bike_erg", "Bike (Erg)", "cardio", "distance"),
                ("ski_erg", "Ski Erg", "cardio", "distance"),
                ("jump_rope", "Jump Rope", "cardio", "reps"),
                ("double_under", "Double Under", "cardio", "reps"),
                ("box_jump", "Box Jump", "cardio", "reps"),
                ("burpee", "Burpee", "cardio", "reps"),
                # Core
                ("sit_up", "Sit-Up", "core", "reps"),
                ("toes_to_bar", "Toes-to-Bar", "core", "reps"),
                ("ghd_sit_up", "GHD Sit-Up", "core", "reps"),
                ("plank", "Plank", "core", "time"),
                ("hanging_knee_raise", "Hanging Knee Raise", "core", "reps"),
                ("russian_twist", "Russian Twist", "core", "reps"),
                # Arms / Isolation
                ("bicep_curl", "Bicep Curl", "isolation", "weight"),
                ("tricep_extension", "Tricep Extension", "isolation", "weight"),
                ("lateral_raise", "Lateral Raise", "isolation", "weight"),
                ("face_pull", "Face Pull", "isolation", "weight"),
                ("leg_curl", "Leg Curl", "isolation", "weight"),
                ("leg_extension", "Leg Extension", "isolation", "weight"),
                ("calf_raise", "Calf Raise", "isolation", "weight"),
                # Gymnastic
                ("muscle_up", "Muscle-Up", "gymnastic", "reps"),
                ("ring_dip", "Ring Dip", "gymnastic", "reps"),
                ("rope_climb", "Rope Climb", "gymnastic", "reps"),
            ]
            conn.executemany(
                "INSERT INTO exercises (name, display_name, category, primary_metric) VALUES (?, ?, ?, ?)",
                exercises,
            )


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
