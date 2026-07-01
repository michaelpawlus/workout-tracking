"""Tests for the capstone synthesis dossier (#16).

Run with: ``python3 -m unittest backend.tests.test_capstone -v`` from repo root.

Uses a throwaway SQLite file + synthetic GPX so the real ``workouts.db`` is never
touched. The capstone CLI does no LLM work — it gathers every internal signal into a
JSON-serializable dossier + synthesis order — so the tests assert the dossier shape,
that signals are wired through, and that the living-document path detection flips the
synthesis method between "write fresh" and "update in place".
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database, historical, race_capstone, race_engine, vault


def _write_synthetic_gpx(path, n=200, lat0=40.0, lon0=-81.5):
    pts = []
    for i in range(n + 1):
        lat = lat0 + i * 0.0009
        ele = 300 + 200 * math.sin(math.pi * i / n)
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lon0:.6f}"><ele>{ele:.1f}</ele></trkpt>')
    gpx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test"><trk><trkseg>\n'
        + "\n".join(pts) + "\n</trkseg></trk></gpx>\n"
    )
    with open(path, "w") as f:
        f.write(gpx)


class CapstoneTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

        self._gpx = tempfile.NamedTemporaryFile(suffix=".gpx", delete=False)
        self._gpx.close()
        _write_synthetic_gpx(self._gpx.name)

        with database.get_db() as conn:
            cid, segs, total, gain = race_engine.load_course(
                conn, self._gpx.name, "Test Course", 2026)
            self.course_id = cid
            self.total = total
            stations = [
                {"mile": round(total * 0.25, 2), "name": "Q1", "crew": True, "drop_bag": False, "notes": None},
                {"mile": round(total * 0.50, 2), "name": "Turn", "crew": True, "drop_bag": True, "notes": None},
                {"mile": round(total * 0.75, 2), "name": "Q3", "crew": False, "drop_bag": False, "notes": None},
                {"mile": round(total, 2), "name": "Finish", "crew": True, "drop_bag": True, "notes": None},
            ]
            race_engine.import_aid_stations(conn, stations, course_id=cid)
            # A prior race so the own-history fade signal is non-empty.
            historical.add_race(
                conn, name="Prior 100", race_date="2024-08-01",
                distance_miles=100.0, elevation_gain_ft=8000.0,
                finish_time_seconds=24 * 3600,
                first_half_seconds=11 * 3600, second_half_seconds=13 * 3600,
                terrain="trail", notes="positive split")

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)
        os.unlink(self._gpx.name)

    def _course(self, conn):
        return dict(race_engine.get_course(conn, self.course_id))

    def test_dossier_shape_and_signals(self):
        with database.get_db() as conn:
            dossier = race_capstone.build_capstone_dossier(
                conn, self._course(conn), plan_id=None,
                goal_time_seconds=26 * 3600, weather_temp_f=80.0)

        # Fully JSON-serializable (the CLI emits it via _print).
        json.dumps(dossier)

        self.assertEqual(set(dossier), {
            "race", "objective", "signals", "references", "method", "output_sections"})
        sig = dossier["signals"]
        self.assertEqual(set(sig), {
            "targets", "history", "peer_cohort", "race_plan",
            "fueling", "crew_stations", "mental", "training_block"})

        # History wired through.
        self.assertEqual(sig["history"]["count"], 1)
        self.assertIsNotNone(sig["history"]["avg_fade_pct"])

        # A/B/C plan with per-segment rows; fueling costs every segment.
        for band in ("A", "B", "C"):
            self.assertIn(band, sig["race_plan"]["plans"])
        with database.get_db() as conn:
            n_segments = len(race_engine.get_segments(conn, self.course_id))
        self.assertEqual(len(sig["fueling"]), n_segments)
        self.assertEqual(len(sig["race_plan"]["plans"]["A"]["segments"]), n_segments)

        # Only crew/drop-bag stations are surfaced (Q3 is neither).
        names = {s["name"] for s in sig["crew_stations"]}
        self.assertIn("Turn", names)
        self.assertNotIn("Q3", names)
        for s in sig["crew_stations"]:
            self.assertTrue(s["crew_accessible"] or s["drop_bag"])

        # No plan → targets/training block degrade gracefully.
        self.assertIsNone(sig["targets"])
        self.assertEqual(sig["training_block"]["count"], 0)

        # Weather + governor framing carried in the header.
        self.assertEqual(dossier["race"]["weather_temp_f"], 80.0)
        self.assertEqual(dossier["race"]["target_finish"], "26:00:00")

    def test_output_sections_cover_issue_16(self):
        with database.get_db() as conn:
            dossier = race_capstone.build_capstone_dossier(
                conn, self._course(conn), plan_id=None, goal_time_seconds=26 * 3600)
        headings = " ".join(s["heading"].lower() for s in dossier["output_sections"])
        for needle in ("pacing", "fueling", "walk", "crew", "conting", "revision"):
            self.assertIn(needle, headings)

    def test_skeleton_renders(self):
        with database.get_db() as conn:
            dossier = race_capstone.build_capstone_dossier(
                conn, self._course(conn), plan_id=None, goal_time_seconds=26 * 3600)
        md = race_capstone.render_capstone_skeleton(dossier)
        self.assertIn("# Test Course — Race Strategy", md)
        self.assertIn("## Revision Log", md)

    def test_living_document_method_flips_on_existing_report(self):
        with tempfile.TemporaryDirectory() as vault_root:
            with patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": vault_root}):
                title = "Test Course Race Strategy"
                with database.get_db() as conn:
                    course = self._course(conn)
                    fresh = race_capstone.build_capstone_dossier(
                        conn, course, plan_id=None, goal_time_seconds=26 * 3600,
                        title=title)
                self.assertFalse(fresh["references"]["report_exists"])
                self.assertIn("Write the report fresh", fresh["method"][0])

                # Simulate a prior save, then re-gather: method must switch to update.
                vault.write_race_intel_to_vault(
                    title=title, body="# prior\n", doc_type="strategy-report")
                self.assertTrue(vault.race_intel_target_path(title).exists())

                with database.get_db() as conn:
                    again = race_capstone.build_capstone_dossier(
                        conn, self._course(conn), plan_id=None,
                        goal_time_seconds=26 * 3600, title=title)
                self.assertTrue(again["references"]["report_exists"])
                self.assertIn("UPDATE the existing report", again["method"][0])


if __name__ == "__main__":
    unittest.main()
