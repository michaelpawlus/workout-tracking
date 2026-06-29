"""Tests for the static BR100 training plan (issue #9, piece 2: weekly mental focus)."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from backend import database
from backend.ultra_plan import (
    MENTAL_FOCUS,
    WEEKS,
    create_br100_plan,
    generate_training_plan_markdown,
)


class WeeklyMentalFocusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

    def tearDown(self):
        self._patcher.stop()

    def test_every_week_has_a_mental_prescription(self):
        # The dict should cover all 20 plan weeks with non-empty text.
        self.assertEqual(set(MENTAL_FOCUS), {w[0] for w in WEEKS})
        self.assertTrue(all(MENTAL_FOCUS[w[0]].strip() for w in WEEKS))

    def test_plan_creation_persists_mental_focus(self):
        with database.get_db() as conn:
            plan_id = create_br100_plan(conn)
            rows = conn.execute(
                "SELECT week_number, mental_focus FROM training_plan_weeks "
                "WHERE plan_id = ? ORDER BY week_number",
                (plan_id,),
            ).fetchall()
        self.assertEqual(len(rows), 20)
        for r in rows:
            self.assertEqual(r["mental_focus"], MENTAL_FOCUS[r["week_number"]])

    def test_markdown_export_includes_mental_line(self):
        with database.get_db() as conn:
            plan_id = create_br100_plan(conn)
            md = generate_training_plan_markdown(conn, plan_id)
        # One Mental line per week.
        self.assertEqual(md.count("**Mental:**"), 20)
        self.assertIn(MENTAL_FOCUS[13], md)

    def test_backfill_populates_legacy_plan_weeks(self):
        # Simulate a plan that predates piece 2: NULL out mental_focus, then
        # re-run init_db() and confirm the migration backfills every week.
        with database.get_db() as conn:
            create_br100_plan(conn)
            conn.execute("UPDATE training_plan_weeks SET mental_focus = NULL")
            conn.commit()
        database.init_db()
        with database.get_db() as conn:
            missing = conn.execute(
                "SELECT COUNT(*) AS n FROM training_plan_weeks WHERE mental_focus IS NULL"
            ).fetchone()["n"]
        self.assertEqual(missing, 0)


if __name__ == "__main__":
    unittest.main()
