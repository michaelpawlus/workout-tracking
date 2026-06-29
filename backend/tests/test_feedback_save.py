"""Retroactive `ultra feedback --save` must not lose the AI mental coaching.

Regression test for the persistence gap: `mental_feedback` is produced by the
LLM during submit but was never stored on `run_feedback`, so rebuilding the
vault note from the row dropped the `## Mental` coaching.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from unittest.mock import patch

from backend import cli, database
from backend.ultra_plan import create_br100_plan


class FeedbackSaveMentalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()
        with database.get_db() as conn:
            self.plan_id = create_br100_plan(conn)
            wid = conn.execute(
                "INSERT INTO workouts (date, workout_type, plan_id) "
                "VALUES ('2026-04-22', 'easy_run', ?)",
                (self.plan_id,),
            ).lastrowid
            conn.execute(
                """INSERT INTO run_feedback
                   (workout_id, plan_id, actual_distance_miles, avg_heart_rate,
                    overall_feedback, mental_feedback, mental_state, mental_notes)
                   VALUES (?, ?, 6.0, 138, 'Solid easy run.',
                           'Your HR dropped 4bpm whenever you settled the breath — keep it up.',
                           'calm', 'felt centered')""",
                (wid, self.plan_id),
            )
            conn.commit()

    def tearDown(self):
        self._patcher.stop()

    def test_save_restores_persisted_mental_feedback(self):
        captured = {}

        def fake_write(**kwargs):
            captured.update(kwargs)
            return {"path": "/fake/note.md"}

        args = types.SimpleNamespace(id=None, json=True)
        with patch.object(cli, "_write_run_to_vault", side_effect=fake_write), \
                patch.object(cli, "_print"):
            cli._save_feedback_to_vault(args)

        # The reconstructed feedback dict must carry the AI mental coaching...
        self.assertEqual(
            captured["feedback"]["mental_feedback"],
            "Your HR dropped 4bpm whenever you settled the breath — keep it up.",
        )
        # ...and the raw mental fields too.
        self.assertEqual(captured["mental"]["mental_state"], "calm")

    def test_migration_adds_mental_feedback_column(self):
        with database.get_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(run_feedback)")}
        self.assertIn("mental_feedback", cols)


if __name__ == "__main__":
    unittest.main()
