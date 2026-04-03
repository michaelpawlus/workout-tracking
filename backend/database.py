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


def _needs_check_migration(conn, table, column, test_value):
    """Test if a CHECK constraint rejects a new valid value by inspecting the DDL."""
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not ddl:
        return False
    # If the DDL doesn't contain the test_value in the CHECK, it needs migration
    return test_value not in ddl[0]


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
                source TEXT NOT NULL DEFAULT 'manual',
                plan_id INTEGER REFERENCES training_plans(id),
                plan_week INTEGER,
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

            CREATE TABLE IF NOT EXISTS training_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                goal TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                total_weeks INTEGER NOT NULL,
                mesocycle_weeks INTEGER NOT NULL DEFAULT 4,
                status TEXT NOT NULL DEFAULT 'active',
                plan_json TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS training_plan_weeks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
                week_number INTEGER NOT NULL,
                week_type TEXT NOT NULL CHECK(week_type IN ('build', 'deload', 'base', 'peak', 'taper', 'race', 'recovery')),
                focus TEXT,
                notes TEXT,
                UNIQUE(plan_id, week_number)
            );

            CREATE TABLE IF NOT EXISTS plan_benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
                week_id INTEGER NOT NULL REFERENCES training_plan_weeks(id) ON DELETE CASCADE,
                benchmark_name TEXT NOT NULL,
                benchmark_type TEXT NOT NULL CHECK(benchmark_type IN ('time_trial', 'max_lift', 'timed_wod', 'amrap', 'maf_test', 'endurance_test', 'race')),
                target_value REAL,
                scheduled_date TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                result_value REAL,
                result_notes TEXT,
                workout_id INTEGER REFERENCES workouts(id)
            );

            CREATE TABLE IF NOT EXISTS daily_workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
                week_id INTEGER NOT NULL REFERENCES training_plan_weeks(id) ON DELETE CASCADE,
                scheduled_date TEXT NOT NULL,
                workout_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                target_distance_miles REAL,
                target_duration_minutes INTEGER,
                target_pace_min_per_mile REAL,
                target_hr_zone TEXT,
                intensity TEXT,
                notes TEXT,
                is_benchmark INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                actual_workout_id INTEGER REFERENCES workouts(id)
            );

            CREATE TABLE IF NOT EXISTS run_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL REFERENCES workouts(id),
                daily_workout_id INTEGER REFERENCES daily_workouts(id),
                plan_id INTEGER REFERENCES training_plans(id),
                prescribed_distance_miles REAL,
                actual_distance_miles REAL,
                prescribed_pace REAL,
                actual_pace REAL,
                avg_heart_rate INTEGER,
                max_heart_rate INTEGER,
                elevation_gain_ft REAL,
                effort_rating INTEGER,
                compliance_score REAL,
                pace_feedback TEXT,
                hr_feedback TEXT,
                overall_feedback TEXT,
                warnings TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS weekly_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES training_plans(id),
                week_number INTEGER NOT NULL,
                target_miles REAL,
                actual_miles REAL DEFAULT 0,
                runs_planned INTEGER,
                runs_completed INTEGER DEFAULT 0,
                long_run_miles REAL,
                avg_easy_pace REAL,
                avg_heart_rate REAL,
                notes TEXT,
                UNIQUE(plan_id, week_number)
            );

            CREATE TABLE IF NOT EXISTS strava_tokens (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS athlete_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
                effective_date TEXT NOT NULL,
                easy_pace REAL NOT NULL,
                long_run_pace REAL NOT NULL,
                tempo_pace REAL NOT NULL,
                threshold_pace REAL,
                maf_hr INTEGER NOT NULL DEFAULT 137,
                zone2_ceiling INTEGER NOT NULL DEFAULT 137,
                zone3_ceiling INTEGER NOT NULL DEFAULT 155,
                zone4_ceiling INTEGER NOT NULL DEFAULT 170,
                source TEXT NOT NULL,
                trigger_benchmark_id INTEGER REFERENCES plan_benchmarks(id),
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Race Day Engine tables
            CREATE TABLE IF NOT EXISTS race_courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                year INTEGER,
                total_distance_miles REAL,
                total_elevation_gain_ft REAL,
                gpx_file_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS race_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES race_courses(id) ON DELETE CASCADE,
                segment_number INTEGER NOT NULL,
                name TEXT,
                start_mile REAL NOT NULL,
                end_mile REAL NOT NULL,
                distance_miles REAL NOT NULL,
                elevation_gain_ft REAL NOT NULL DEFAULT 0,
                elevation_loss_ft REAL NOT NULL DEFAULT 0,
                avg_grade_pct REAL NOT NULL DEFAULT 0,
                max_grade_pct REAL NOT NULL DEFAULT 0,
                terrain_notes TEXT,
                crew_accessible INTEGER NOT NULL DEFAULT 0,
                drop_bag INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS historical_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES race_courses(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                runner_name TEXT,
                finish_time_seconds INTEGER,
                dnf INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS historical_splits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER NOT NULL REFERENCES historical_results(id) ON DELETE CASCADE,
                segment_id INTEGER NOT NULL REFERENCES race_segments(id) ON DELETE CASCADE,
                split_time_seconds INTEGER NOT NULL,
                pace_per_mile_seconds INTEGER
            );

            CREATE TABLE IF NOT EXISTS race_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES race_courses(id) ON DELETE CASCADE,
                plan_id INTEGER REFERENCES training_plans(id),
                goal_time_seconds INTEGER NOT NULL,
                weather_temp_f REAL,
                scenario TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS race_plan_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_plan_id INTEGER NOT NULL REFERENCES race_plans(id) ON DELETE CASCADE,
                segment_id INTEGER NOT NULL REFERENCES race_segments(id) ON DELETE CASCADE,
                target_pace_seconds INTEGER NOT NULL,
                estimated_time_seconds INTEGER NOT NULL,
                cumulative_time_seconds INTEGER NOT NULL,
                aid_station_eta TEXT,
                calories_target INTEGER,
                sodium_mg_target INTEGER,
                fluid_oz_target INTEGER,
                fueling_notes TEXT,
                crew_notes TEXT
            );

            CREATE TABLE IF NOT EXISTS race_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_plan_id INTEGER NOT NULL REFERENCES race_plans(id) ON DELETE CASCADE,
                segment_id INTEGER NOT NULL REFERENCES race_segments(id) ON DELETE CASCADE,
                actual_arrival_time TEXT,
                actual_elapsed_seconds INTEGER,
                notes TEXT
            );
        """)

        # Migrate CHECK constraints if DB predates ultra plan support
        if _needs_check_migration(conn, "training_plan_weeks", "week_type", "base"):
            conn.execute("ALTER TABLE training_plan_weeks RENAME TO _old_tpw")
            conn.execute("""
                CREATE TABLE training_plan_weeks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
                    week_number INTEGER NOT NULL,
                    week_type TEXT NOT NULL CHECK(week_type IN ('build', 'deload', 'base', 'peak', 'taper', 'race', 'recovery')),
                    focus TEXT,
                    notes TEXT,
                    UNIQUE(plan_id, week_number)
                )
            """)
            conn.execute("INSERT INTO training_plan_weeks SELECT * FROM _old_tpw")
            conn.execute("DROP TABLE _old_tpw")

        if _needs_check_migration(conn, "plan_benchmarks", "benchmark_type", "maf_test"):
            conn.execute("ALTER TABLE plan_benchmarks RENAME TO _old_pb")
            conn.execute("""
                CREATE TABLE plan_benchmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
                    week_id INTEGER NOT NULL REFERENCES training_plan_weeks(id) ON DELETE CASCADE,
                    benchmark_name TEXT NOT NULL,
                    benchmark_type TEXT NOT NULL CHECK(benchmark_type IN ('time_trial', 'max_lift', 'timed_wod', 'amrap', 'maf_test', 'endurance_test', 'race')),
                    target_value REAL,
                    scheduled_date TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    result_value REAL,
                    result_notes TEXT,
                    workout_id INTEGER REFERENCES workouts(id)
                )
            """)
            conn.execute("INSERT INTO plan_benchmarks SELECT * FROM _old_pb")
            conn.execute("DROP TABLE _old_pb")

        # Add new columns to workouts if they don't exist (migration)
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(workouts)").fetchall()}
        if "source" not in existing_cols:
            conn.execute("ALTER TABLE workouts ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
        if "plan_id" not in existing_cols:
            conn.execute("ALTER TABLE workouts ADD COLUMN plan_id INTEGER REFERENCES training_plans(id)")
        if "plan_week" not in existing_cols:
            conn.execute("ALTER TABLE workouts ADD COLUMN plan_week INTEGER")
        if "strava_activity_id" not in existing_cols:
            conn.execute("ALTER TABLE workouts ADD COLUMN strava_activity_id INTEGER")

        # Add nutrition columns to run_feedback if they don't exist (migration)
        rf_cols = {row[1] for row in conn.execute("PRAGMA table_info(run_feedback)").fetchall()}
        for col in ("pre_meal", "during_fuel", "during_hydration", "post_meal", "nutrition_notes"):
            if col not in rf_cols:
                conn.execute(f"ALTER TABLE run_feedback ADD COLUMN {col} TEXT")

        # Seed athlete_targets for existing plans that lack them
        plans_without_targets = conn.execute(
            """SELECT id, start_date FROM training_plans
               WHERE id NOT IN (SELECT DISTINCT plan_id FROM athlete_targets)"""
        ).fetchall()
        for p in plans_without_targets:
            conn.execute(
                """INSERT INTO athlete_targets
                   (plan_id, effective_date, easy_pace, long_run_pace, tempo_pace,
                    maf_hr, zone2_ceiling, zone3_ceiling, zone4_ceiling, source, notes)
                   VALUES (?, ?, 10.0, 10.5, 8.0, 137, 137, 155, 170, 'initial', 'Plan defaults')""",
                (p["id"], p["start_date"]),
            )

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
