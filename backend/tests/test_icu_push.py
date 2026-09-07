"""Tests for the Intervals.icu push, which is what actually reaches the watch.

The push is delete-then-recreate. The delete sweep used to be built only from
dates carrying a runnable workout, which meant a day that *became* a rest day
kept the event from when it was a run: nothing ever deleted it, and it synced
to Coros alongside the replacement plan. Reshaping a block (5 days/week down to
3) is exactly when that bites, so it is guarded here.
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from unittest.mock import patch

from backend import cli, database
from backend.marathon_plan import create_columbus_plan


def _args(**kw):
    """Build an icu-push argparse namespace with the parser's own defaults."""
    ns = argparse.Namespace(
        week=None, date=None, upcoming=None, all=False,
        as_date=None, no_replace=False, dry_run=False, json=True,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class IcuPushReplaceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()
        with database.get_db() as conn:
            create_columbus_plan(conn)
        cli._set_active_plan_name(cli.COLUMBUS_PLAN_NAME)

    def tearDown(self):
        self._patcher.stop()
        cli._set_active_plan_name(None)

    def _push(self, existing, **kw):
        """Run a push against a fake calendar; return the deleted event ids."""
        deleted = []
        with patch("backend.intervals_icu.list_events", return_value=existing), \
             patch("backend.intervals_icu.delete_event", side_effect=lambda i: deleted.append(i)), \
             patch("backend.intervals_icu.create_events_bulk", return_value=[]), \
             patch.object(cli, "_print"):
            cli.cmd_icu_push(_args(**kw))
        return deleted

    def test_rest_day_events_are_swept(self):
        """A stale event on a rest day is deleted, not orphaned.

        Week 5's Monday (2026-09-07) is a cross-train day and Friday
        (2026-09-11) is a rest day: neither pushes a workout, so before the fix
        neither was ever cleared.
        """
        existing = [
            {"id": 1, "start_date_local": "2026-09-07T00:00:00"},  # cross_train
            {"id": 2, "start_date_local": "2026-09-09T00:00:00"},  # tempo
            {"id": 3, "start_date_local": "2026-09-11T00:00:00"},  # rest
        ]
        deleted = self._push(existing, week=5)
        self.assertEqual(sorted(deleted), [1, 2, 3])

    def test_sweep_range_covers_the_whole_week(self):
        """The listed range spans the selection, not just its runnable days."""
        captured = {}

        def _list(oldest, newest):
            captured["range"] = (oldest, newest)
            return []

        with patch("backend.intervals_icu.list_events", side_effect=_list), \
             patch("backend.intervals_icu.delete_event"), \
             patch("backend.intervals_icu.create_events_bulk", return_value=[]), \
             patch.object(cli, "_print"):
            cli.cmd_icu_push(_args(week=5))

        # Week 5 is Mon 2026-09-07 (cross-train) through Sun 2026-09-13.
        self.assertEqual(captured["range"], ("2026-09-07", "2026-09-13"))

    def test_events_outside_the_selection_survive(self):
        """The sweep is bounded by the pushed dates; neighbours are untouched."""
        existing = [
            {"id": 9, "start_date_local": "2026-09-06T00:00:00"},   # week 4, not pushed
            {"id": 2, "start_date_local": "2026-09-09T00:00:00"},   # week 5
            {"id": 8, "start_date_local": "2026-09-14T00:00:00"},   # week 6, not pushed
        ]
        deleted = self._push(existing, week=5)
        self.assertEqual(deleted, [2])

    def test_no_replace_deletes_nothing(self):
        existing = [{"id": 1, "start_date_local": "2026-09-09T00:00:00"}]
        self.assertEqual(self._push(existing, week=5, no_replace=True), [])


if __name__ == "__main__":
    unittest.main()
