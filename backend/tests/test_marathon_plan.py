"""Tests for the 10-week Columbus Marathon plan and the marathon-pace target.

Also guards the BR100 path against the plan-lookup generalization: once _get_plan()
stopped hardcoding a race name, "the active plan" became ambiguous, and the thing
most worth proving is that the two plans cannot see each other.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from unittest.mock import patch

from backend import database
from backend.adapt import (
    adapt_from_5k_tt,
    apply_targets_to_future_workouts,
    get_current_targets,
    riegel_marathon_pace,
)
from backend.marathon_plan import (
    LONG_RUNS,
    MENTAL_FOCUS,
    MP_IN_LONG_RUN,
    PROVISIONAL_MARATHON_PACE,
    RACE_DATE,
    TOTAL_WEEKS,
    WEEKS,
    create_columbus_plan,
    generate_marathon_plan_markdown,
)


class MarathonPlanTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

    def tearDown(self):
        self._patcher.stop()


class PlanStructureTest(MarathonPlanTestBase):
    def test_plan_spans_ten_weeks_ending_on_race_day(self):
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            plan = conn.execute(
                "SELECT * FROM training_plans WHERE id = ?", (plan_id,)
            ).fetchone()
        self.assertEqual(plan["total_weeks"], TOTAL_WEEKS)
        self.assertEqual(plan["start_date"], "2026-08-10")
        # The plan must end exactly on race day, not merely near it.
        self.assertEqual(plan["end_date"], RACE_DATE)

    def test_every_week_has_a_mental_prescription(self):
        self.assertEqual(set(MENTAL_FOCUS), {w[0] for w in WEEKS})
        self.assertTrue(all(MENTAL_FOCUS[w[0]].strip() for w in WEEKS))

        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            rows = conn.execute(
                "SELECT week_number, mental_focus FROM training_plan_weeks "
                "WHERE plan_id = ? ORDER BY week_number",
                (plan_id,),
            ).fetchall()
        self.assertEqual(len(rows), TOTAL_WEEKS)
        for r in rows:
            self.assertEqual(r["mental_focus"], MENTAL_FOCUS[r["week_number"]])

    def test_every_long_run_lands_on_sunday(self):
        # Columbus is a Sunday race, so long runs rehearse race-day timing. This is
        # the one structural difference from the ultra plan most likely to regress.
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            rows = conn.execute(
                """SELECT scheduled_date, title FROM daily_workouts
                   WHERE plan_id = ? AND workout_type IN ('long_run', 'marathon_pace', 'race')
                     AND target_distance_miles >= 8
                   ORDER BY scheduled_date""",
                (plan_id,),
            ).fetchall()
        self.assertEqual(len(rows), TOTAL_WEEKS)
        for r in rows:
            weekday = dt.date.fromisoformat(r["scheduled_date"]).weekday()
            self.assertEqual(weekday, 6, f"{r['title']} on {r['scheduled_date']} is not a Sunday")

    def test_race_day_is_the_final_workout(self):
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            last = conn.execute(
                """SELECT scheduled_date, title, workout_type, target_distance_miles
                   FROM daily_workouts WHERE plan_id = ?
                   ORDER BY scheduled_date DESC LIMIT 1""",
                (plan_id,),
            ).fetchone()
        self.assertEqual(last["scheduled_date"], RACE_DATE)
        self.assertEqual(last["workout_type"], "race")
        self.assertAlmostEqual(last["target_distance_miles"], 26.2, places=1)

    def test_every_benchmark_has_a_matching_flagged_workout(self):
        # A benchmark scheduled on a day the plan fills with an ordinary shakeout is
        # a silent conflict — the athlete gets contradictory instructions.
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            benchmarks = conn.execute(
                "SELECT benchmark_name, scheduled_date FROM plan_benchmarks WHERE plan_id = ?",
                (plan_id,),
            ).fetchall()
            self.assertEqual(len(benchmarks), 4)
            for b in benchmarks:
                workout = conn.execute(
                    """SELECT title, is_benchmark FROM daily_workouts
                       WHERE plan_id = ? AND scheduled_date = ?""",
                    (plan_id, b["scheduled_date"]),
                ).fetchone()
                self.assertIsNotNone(
                    workout, f"{b['benchmark_name']} has no workout on {b['scheduled_date']}")
                self.assertTrue(
                    workout["is_benchmark"],
                    f"{b['benchmark_name']} falls on unflagged workout '{workout['title']}'")

    def test_weekly_mileage_tracks_its_declared_band(self):
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            for week_num, _wtype, low, high, _focus in WEEKS:
                week_id = conn.execute(
                    "SELECT id FROM training_plan_weeks WHERE plan_id = ? AND week_number = ?",
                    (plan_id, week_num),
                ).fetchone()["id"]
                total = conn.execute(
                    """SELECT COALESCE(SUM(target_distance_miles), 0) AS s
                       FROM daily_workouts WHERE week_id = ?""",
                    (week_id,),
                ).fetchone()["s"]
                # Tolerance absorbs duration-based sessions (MAF test) that carry no
                # distance; the point is to catch a band that has drifted from reality.
                self.assertGreaterEqual(total, low - 2, f"week {week_num} under its band")
                self.assertLessEqual(total, high + 2, f"week {week_num} over its band")

    def test_marathon_pace_miles_progress_then_taper(self):
        weeks = sorted(MP_IN_LONG_RUN)
        volumes = [MP_IN_LONG_RUN[w] for w in weeks]
        self.assertEqual(volumes, sorted(volumes[:-1]) + [volumes[-1]])
        self.assertEqual(max(MP_IN_LONG_RUN.values()), 8)
        # The peak MP session must not be the last one — taper follows it.
        peak_week = max(MP_IN_LONG_RUN, key=MP_IN_LONG_RUN.get)
        self.assertLess(peak_week, max(weeks))
        self.assertLessEqual(max(LONG_RUNS[w] for w in LONG_RUNS if w < TOTAL_WEEKS), 20)


class MarathonPaceTargetTest(MarathonPlanTestBase):
    def test_riegel_projection_matches_known_equivalents(self):
        # A 28:00 5K is the athlete's Mar 12 baseline and should project to ~4:28,
        # which is what makes sub-4:30 a fair goal rather than a stretch.
        pace = riegel_marathon_pace(28 * 60)
        self.assertAlmostEqual(pace, 10.24, places=1)
        finish_minutes = pace * 26.2188
        self.assertAlmostEqual(finish_minutes / 60, 4.47, places=1)

    def test_riegel_rejects_nonsense_input(self):
        self.assertIsNone(riegel_marathon_pace(0))
        self.assertIsNone(riegel_marathon_pace(-1))
        self.assertIsNone(riegel_marathon_pace(1800, tt_distance_miles=0))

    def test_plan_seeds_a_provisional_marathon_pace(self):
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            targets = get_current_targets(conn, plan_id, as_of_date="2026-08-10")
        self.assertEqual(targets["marathon_pace"], PROVISIONAL_MARATHON_PACE)

    def test_mp_workouts_start_at_the_provisional_pace(self):
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            rows = conn.execute(
                """SELECT target_pace_min_per_mile p FROM daily_workouts
                   WHERE plan_id = ? AND workout_type = 'marathon_pace'""",
                (plan_id,),
            ).fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["p"], PROVISIONAL_MARATHON_PACE)

    def test_time_trial_retargets_every_marathon_pace_session(self):
        """The week-3 gate: a TT result must flow through to the MP workouts."""
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            benchmark = conn.execute(
                """SELECT id, scheduled_date FROM plan_benchmarks
                   WHERE plan_id = ? AND benchmark_type = 'time_trial'""",
                (plan_id,),
            ).fetchone()

            result = adapt_from_5k_tt(conn, plan_id, benchmark["id"], 27 * 60 + 40)
            self.assertIsNotNone(result["marathon_pace"])
            # 27:40 is faster than the 28:00 baseline, so goal pace must get faster.
            self.assertLess(result["marathon_pace"], PROVISIONAL_MARATHON_PACE)

            apply_targets_to_future_workouts(
                conn, plan_id, result["targets"], from_date=benchmark["scheduled_date"])

            rows = conn.execute(
                """SELECT target_pace_min_per_mile p FROM daily_workouts
                   WHERE plan_id = ? AND workout_type = 'marathon_pace'
                     AND scheduled_date >= ?""",
                (plan_id, benchmark["scheduled_date"]),
            ).fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertAlmostEqual(r["p"], result["marathon_pace"], places=2)

    def test_ultra_plan_leaves_marathon_pace_unset(self):
        # NULL means "not applicable to this race", not "not yet measured".
        from backend.ultra_plan import create_br100_plan

        with database.get_db() as conn:
            plan_id = create_br100_plan(conn)
            targets = get_current_targets(conn, plan_id, as_of_date="2026-03-09")
        self.assertIsNone(targets["marathon_pace"])


class MarkdownExportTest(MarathonPlanTestBase):
    def test_markdown_includes_targets_and_mental_lines(self):
        with database.get_db() as conn:
            plan_id = create_columbus_plan(conn)
            md = generate_marathon_plan_markdown(conn, plan_id)

        self.assertIn("Columbus Marathon", md)
        self.assertIn("| Marathon Pace |", md)
        self.assertEqual(md.count("**Mental:**"), TOTAL_WEEKS)
        self.assertEqual(md.count("## Week "), TOTAL_WEEKS)
        self.assertIn("5K Time Trial #3", md)

    def test_markdown_returns_none_for_unknown_plan(self):
        with database.get_db() as conn:
            self.assertIsNone(generate_marathon_plan_markdown(conn, 99999))


class PlanIsolationTest(MarathonPlanTestBase):
    """The generalized lookup must never let one race's plan answer for another."""

    def test_pinned_lookup_does_not_leak_across_races(self):
        from backend import cli
        from backend.ultra_plan import create_br100_plan

        try:
            with database.get_db() as conn:
                br100_id = create_br100_plan(conn)
                columbus_id = create_columbus_plan(conn)

                cli._set_active_plan_name(cli.BR100_PLAN_NAME)
                self.assertEqual(cli._get_plan(conn)["id"], br100_id)
                self.assertIn("Burning River 100", cli._no_plan_msg())

                cli._set_active_plan_name(cli.COLUMBUS_PLAN_NAME)
                self.assertEqual(cli._get_plan(conn)["id"], columbus_id)
                self.assertIn("marathon init", cli._no_plan_msg())

                # An explicit override beats the pinned default.
                self.assertEqual(
                    cli._get_plan(conn, cli.BR100_PLAN_NAME)["id"], br100_id)
        finally:
            cli._set_active_plan_name(None)

    def test_missing_plan_resolves_to_none_not_the_other_race(self):
        from backend import cli

        try:
            with database.get_db() as conn:
                create_columbus_plan(conn)
                cli._set_active_plan_name(cli.BR100_PLAN_NAME)
                self.assertIsNone(cli._get_plan(conn))
        finally:
            cli._set_active_plan_name(None)

    def test_plan_spec_selects_the_right_builder_and_filename(self):
        from backend import cli

        try:
            cli._set_active_plan_name(cli.COLUMBUS_PLAN_NAME)
            self.assertEqual(cli._plan_spec()["filename"], "MARATHON_PLAN.md")
            cli._set_active_plan_name(cli.BR100_PLAN_NAME)
            self.assertEqual(cli._plan_spec()["filename"], "TRAINING_PLAN.md")
        finally:
            cli._set_active_plan_name(None)


if __name__ == "__main__":
    unittest.main()
