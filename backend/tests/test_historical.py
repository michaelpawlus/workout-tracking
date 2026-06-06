"""Tests for backend.historical — the athlete's own prior-race analysis.

Run with: ``python3 -m unittest backend.tests.test_historical -v`` from repo root.

Each test runs against a throwaway SQLite file so the real ``workouts.db`` is
never touched. ``database.DB_PATH`` is patched before ``init_db`` runs.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from backend import database


class HistoricalTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()
        # Import after patching so the module's get_db uses the temp path.
        from backend import historical
        self.historical = historical

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)


class SeedAndMetricsTests(HistoricalTestCase):
    def test_seed_is_idempotent(self):
        with database.get_db() as conn:
            self.historical.seed_known_races(conn)
            self.historical.seed_known_races(conn)  # second seed must not dup
            rows = conn.execute("SELECT COUNT(*) FROM athlete_races").fetchone()[0]
        self.assertEqual(rows, len(self.historical.KNOWN_RACES))

    def test_fade_and_stoppage_metrics(self):
        # Tunnel Hill: 13:03 -> 14:56 halves, 25:23 elapsed / 23:34 moving.
        race = {
            "first_half_seconds": 13 * 3600 + 3 * 60,
            "second_half_seconds": 14 * 3600 + 56 * 60,
            "finish_time_seconds": 25 * 3600 + 23 * 60,
            "moving_time_seconds": 23 * 3600 + 34 * 60,
            "distance_miles": 101.1,
            "first_half_hr": None,
            "second_half_hr": None,
        }
        m = self.historical.race_metrics(race)
        self.assertAlmostEqual(m["fade_pct"], 14.4, places=1)
        self.assertTrue(m["positive_split"])
        self.assertEqual(m["stoppage_seconds"], 6540)
        self.assertAlmostEqual(m["stoppage_pct"], 7.2, places=1)

    def test_hr_drift(self):
        m = self.historical.race_metrics(
            {"first_half_hr": 140, "second_half_hr": 132}
        )
        self.assertEqual(m["hr_drift_bpm"], -8)


class AnalysisTests(HistoricalTestCase):
    def test_failure_mode_and_implications(self):
        with database.get_db() as conn:
            self.historical.seed_known_races(conn)
            analysis = self.historical.analyze_history(conn)
        self.assertEqual(analysis["count"], len(self.historical.KNOWN_RACES))
        self.assertEqual(analysis["failure_mode"], "late-race fade (positive split)")
        self.assertGreater(analysis["avg_fade_pct"], 8)
        self.assertTrue(analysis["lessons"])
        # BR100 elevation reality-check implication should fire.
        self.assertTrue(any("hillier" in i for i in analysis["training_implications"]))

    def test_distance_filter_excludes_off_distance(self):
        with database.get_db() as conn:
            self.historical.seed_known_races(conn)
            self.historical.add_race(
                conn, name="Local 50K", race_date="2024-09-01",
                distance_miles=31.0, finish_time_seconds=18000,
            )
            full = self.historical.analyze_history(conn)
            near100 = self.historical.analyze_history(conn, target_distance=100)
        names = {r["name"] for r in near100["races"]}
        self.assertIn("Local 50K", {r["name"] for r in full["races"]})
        self.assertNotIn("Local 50K", names)
        self.assertNotIn("Canal Corridor 100", names)  # 76.8 mi, outside ±15%

    def test_historical_fade_positive(self):
        with database.get_db() as conn:
            self.historical.seed_known_races(conn)
            fade = self.historical.get_historical_fade(conn, target_distance=100)
        self.assertIsNotNone(fade)
        self.assertGreater(fade, 0)

    def test_empty_history_message(self):
        with database.get_db() as conn:
            analysis = self.historical.analyze_history(conn)
        self.assertEqual(analysis["count"], 0)
        self.assertIn("Seed", analysis["message"])


if __name__ == "__main__":
    unittest.main()
