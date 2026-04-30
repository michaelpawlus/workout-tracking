"""Tests for backend.vault module.

Run with: ``python3 -m unittest backend.tests.test_vault -v`` from the repo root.

The tests use a temporary directory as ``OBSIDIAN_VAULT_PATH`` so they never touch
the real vault. The ``oj`` happy-path test simulates ``oj`` by patching
``vault._try_oj_capture``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import vault


# ---------------------------------------------------------------------------
# render_run_report
# ---------------------------------------------------------------------------


class RenderRunReportTests(unittest.TestCase):
    def test_includes_all_sections_when_data_present(self):
        md = vault.render_run_report(
            run_date="2026-04-29",
            prescribed={
                "title": "Easy Run",
                "description": "Aerobic base run.",
                "target_distance_miles": 5.0,
                "target_duration_minutes": 50,
                "target_pace_min_per_mile": 10.0,
                "target_hr_zone": "Z2",
                "intensity": "easy",
            },
            actual={
                "distance_miles": 5.1,
                "duration_minutes": 51,
                "avg_pace_min_per_mile": 10.0,
                "avg_heart_rate": 140,
                "max_heart_rate": 152,
                "elevation_gain_ft": 120,
                "effort_rating": 4,
            },
            feedback={
                "compliance_score": 92,
                "overall_feedback": "Solid execution.",
                "pace_feedback": "Right on target.",
                "hr_feedback": "Z2 throughout.",
                "warnings": ["Watch hydration tomorrow."],
                "race_readiness": "On track",
            },
            nutrition={"pre_meal": "oatmeal", "during_fuel": "1 gel"},
            weekly_context={"week_number": 8, "week_type": "build"},
            notes="Felt smooth.",
        )

        for header in ("## Easy Run", "## Prescribed", "## Actual", "## Coaching Feedback",
                       "## Nutrition", "## Notes"):
            self.assertIn(header, md)
        self.assertIn("Week 8 (build)", md)
        self.assertIn("Compliance Score: 92/100", md)
        self.assertIn("Race Readiness: On track", md)
        self.assertIn("Watch hydration tomorrow.", md)
        self.assertIn("Pre-run: oatmeal", md)
        self.assertTrue(md.endswith("\n"))

    def test_omits_sections_with_no_data(self):
        md = vault.render_run_report(
            run_date="2026-04-29",
            prescribed=None,
            actual={"distance_miles": 3.0, "duration_minutes": 30,
                    "avg_pace_min_per_mile": 10.0},
            feedback=None,
        )
        self.assertNotIn("## Prescribed", md)
        self.assertNotIn("## Coaching Feedback", md)
        self.assertNotIn("## Nutrition", md)
        self.assertNotIn("## Notes", md)
        self.assertIn("## Actual", md)

    def test_pace_formatting_handles_decimal_minutes(self):
        md = vault.render_run_report(
            run_date="2026-04-29",
            prescribed=None,
            actual={"distance_miles": 1.0, "duration_minutes": 10,
                    "avg_pace_min_per_mile": 10.5},
            feedback=None,
        )
        self.assertIn("10:30/mi", md)


# ---------------------------------------------------------------------------
# write_run_report_to_vault
# ---------------------------------------------------------------------------


class WriteRunReportTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.vault_root = Path(self._tempdir.name)
        self._prev_env = os.environ.get("OBSIDIAN_VAULT_PATH")
        os.environ["OBSIDIAN_VAULT_PATH"] = str(self.vault_root)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("OBSIDIAN_VAULT_PATH", None)
        else:
            os.environ["OBSIDIAN_VAULT_PATH"] = self._prev_env
        self._tempdir.cleanup()

    def _payload(self, **overrides):
        base = {
            "run_date": "2026-04-29",
            "prescribed": {"title": "Easy Run Week 8 Aerobic Base",
                           "intensity": "easy", "workout_type": "easy_run"},
            "actual": {"distance_miles": 5.0, "duration_minutes": 50,
                       "avg_pace_min_per_mile": 10.0, "avg_heart_rate": 140},
            "feedback": {"compliance_score": 90, "overall_feedback": "Good run.",
                         "warnings": []},
        }
        base.update(overrides)
        return base

    def test_direct_write_when_oj_disabled(self):
        result = vault.write_run_report_to_vault(use_oj=False, **self._payload())
        path = Path(result["path"])
        self.assertTrue(path.exists())
        self.assertEqual(result["method"], "direct")
        self.assertTrue(path.name.startswith("2026-04-29 Easy Run"))
        text = path.read_text()
        self.assertIn("---", text)  # frontmatter present in fallback
        self.assertIn("## Easy Run", text)
        self.assertIn("Compliance Score: 90/100", text)

    def test_creates_workouts_dir_if_missing(self):
        # vault root exists but workouts/ does not
        self.assertFalse((self.vault_root / "workouts").exists())
        vault.write_run_report_to_vault(use_oj=False, **self._payload())
        self.assertTrue((self.vault_root / "workouts").exists())

    def test_falls_back_when_oj_fails(self):
        with patch("backend.vault._try_oj_capture", return_value=(False, None)):
            result = vault.write_run_report_to_vault(use_oj=True, **self._payload())
        self.assertEqual(result["method"], "direct")
        self.assertTrue(Path(result["path"]).exists())

    def test_uses_oj_then_moves_into_workouts(self):
        # Simulate oj writing a note into Journal/, then assert vault.py moves it.
        journal_dir = self.vault_root / "Journal"
        journal_dir.mkdir()
        sim_note = journal_dir / "free-form-note.md"
        sim_note.write_text("---\ntype: free-form\n---\n\n## body\n")

        with patch("backend.vault._try_oj_capture",
                   return_value=(True, str(sim_note))):
            result = vault.write_run_report_to_vault(use_oj=True, **self._payload())

        self.assertEqual(result["method"], "oj")
        target = Path(result["path"])
        self.assertTrue(target.exists())
        self.assertEqual(target.parent.name, "workouts")
        self.assertFalse(sim_note.exists())  # original moved

    def test_classifies_long_run_by_distance(self):
        result = vault.write_run_report_to_vault(
            use_oj=False,
            run_date="2026-04-29",
            prescribed={"title": "Saturday capstone"},
            actual={"distance_miles": 18.0, "duration_minutes": 200,
                    "avg_pace_min_per_mile": 11.1},
            feedback=None,
        )
        self.assertIn("Long Run", Path(result["path"]).name)

    def test_unset_vault_path_raises_vault_error(self):
        os.environ.pop("OBSIDIAN_VAULT_PATH")
        with self.assertRaises(vault.VaultError):
            vault.write_run_report_to_vault(use_oj=False, **self._payload())

    def test_filename_strips_invalid_chars(self):
        result = vault.write_run_report_to_vault(
            use_oj=False,
            run_date="2026-04-29",
            prescribed={"title": "Tempo: 3x1mi @ goal/race", "intensity": "tempo"},
            actual={"distance_miles": 6.0, "duration_minutes": 50,
                    "avg_pace_min_per_mile": 8.3},
            feedback=None,
        )
        name = Path(result["path"]).name
        for c in (":", "/", "\\", "?", "*", '"', "<", ">", "|"):
            self.assertNotIn(c, name)


# ---------------------------------------------------------------------------
# append_product_log_entry
# ---------------------------------------------------------------------------


class ProductLogTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.vault_root = Path(self._tempdir.name)
        self._prev_env = os.environ.get("OBSIDIAN_VAULT_PATH")
        os.environ["OBSIDIAN_VAULT_PATH"] = str(self.vault_root)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("OBSIDIAN_VAULT_PATH", None)
        else:
            os.environ["OBSIDIAN_VAULT_PATH"] = self._prev_env
        self._tempdir.cleanup()

    def test_creates_file_with_header_when_missing(self):
        log_path = self.vault_root / "workouts" / "PRODUCT_LOG.md"
        self.assertFalse(log_path.exists())
        vault.append_product_log_entry(
            run_date="2026-04-29",
            summary="Test session.",
            insight="Insight here.",
            title="Easy Run",
        )
        self.assertTrue(log_path.exists())
        text = log_path.read_text()
        self.assertIn("# Adaptive Coaching Product Log", text)
        self.assertIn("## Session Log", text)
        self.assertIn("### 2026-04-29 — Easy Run", text)
        self.assertIn("**What happened:** Test session.", text)
        self.assertIn("**Product insight:** Insight here.", text)

    def test_appends_to_existing_file_without_duplicating_header(self):
        log_path = self.vault_root / "workouts" / "PRODUCT_LOG.md"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("# Adaptive Coaching Product Log\n\nExisting body.\n")

        vault.append_product_log_entry(
            run_date="2026-04-29", summary="Two.", insight="Insightful.",
        )
        text = log_path.read_text()
        # Header appears exactly once.
        self.assertEqual(text.count("# Adaptive Coaching Product Log"), 1)
        self.assertIn("Existing body.", text)
        self.assertIn("**What happened:** Two.", text)


if __name__ == "__main__":
    unittest.main()
