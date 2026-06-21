"""Tests for aid-station import — populating named, flagged course segments.

Run with: ``python3 -m unittest backend.tests.test_aid_stations -v`` from repo root.

Uses a throwaway SQLite file and a synthetic GPX (the real BR100 GPX is not in
the repo), so the real ``workouts.db`` and course data are never touched.
"""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from unittest.mock import patch

from backend import database


def _write_synthetic_gpx(path, n=200, lat0=40.0, lon0=-81.5):
    """Write a GPX track that climbs then descends.

    Steps of 0.0009 deg latitude are ~100 m apart, so ``n`` steps make a course
    of roughly ``n * 100 m``. Returns nothing; just writes the file.
    """
    pts = []
    for i in range(n + 1):
        lat = lat0 + i * 0.0009
        ele = 300 + 200 * math.sin(math.pi * i / n)  # hill: up then back down
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lon0:.6f}"><ele>{ele:.1f}</ele></trkpt>')
    gpx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test"><trk><trkseg>\n'
        + "\n".join(pts)
        + "\n</trkseg></trk></gpx>\n"
    )
    with open(path, "w") as f:
        f.write(gpx)


class AidStationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

        self._gpx = tempfile.NamedTemporaryFile(suffix=".gpx", delete=False)
        self._gpx.close()
        _write_synthetic_gpx(self._gpx.name)

        from backend import race_engine
        self.race_engine = race_engine

        # Load the synthetic course so an aid-station rebuild has something to act on.
        with database.get_db() as conn:
            cid, segs, total, gain = race_engine.load_course(
                conn, self._gpx.name, "Test Course", 2026,
            )
        self.course_id = cid
        self.total = total

        # Aid stations at quarter points + finish; mix of crew/drop flags.
        self.stations = [
            {"mile": round(total * 0.25, 2), "name": "Quarter", "crew": True, "drop_bag": False, "notes": "close noon"},
            {"mile": round(total * 0.50, 2), "name": "Half (turn)", "crew": True, "drop_bag": True, "notes": None},
            {"mile": round(total * 0.75, 2), "name": "Three-Quarter", "crew": False, "drop_bag": True, "notes": None},
            {"mile": round(total, 2), "name": "Finish", "crew": True, "drop_bag": True, "notes": None},
        ]

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)
        os.unlink(self._gpx.name)


class CsvParsingTests(AidStationTestCase):
    def test_reads_and_sorts_skipping_comments(self):
        csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w")
        csv.write(
            "# a comment line\n"
            "mile,name,crew,drop_bag,notes\n"
            "8.9,North Hawkins,1,0,close 9:21 AM\n"
            "4.7,Schumacher,0,0,water only\n"
            "# trailing comment\n"
        )
        csv.close()
        try:
            stations = self.race_engine.read_aid_stations_csv(csv.name)
        finally:
            os.unlink(csv.name)
        self.assertEqual([s["name"] for s in stations], ["Schumacher", "North Hawkins"])
        self.assertEqual(stations[0]["mile"], 4.7)
        self.assertFalse(stations[0]["crew"])
        self.assertTrue(stations[1]["crew"])

    def test_crew_code_values_are_truthy(self):
        # The real guide uses CREW codes like "50/100" / "100", not 1/0.
        self.assertTrue(self.race_engine._truthy("50/100"))
        self.assertTrue(self.race_engine._truthy("100"))
        self.assertTrue(self.race_engine._truthy("x"))
        self.assertFalse(self.race_engine._truthy(""))
        self.assertFalse(self.race_engine._truthy("-"))
        self.assertFalse(self.race_engine._truthy("0"))


class ImportTests(AidStationTestCase):
    def test_rebuild_names_and_flags_segments(self):
        with database.get_db() as conn:
            result = self.race_engine.import_aid_stations(conn, self.stations, course_id=self.course_id)
            segs = self.race_engine.get_segments(conn, self.course_id)

        self.assertTrue(result["applied"])
        self.assertEqual(len(segs), 4)
        self.assertEqual([s["name"] for s in segs],
                         ["Quarter", "Half (turn)", "Three-Quarter", "Finish"])
        # Crew + drop flags land on the right segments.
        self.assertEqual([s["crew_accessible"] for s in segs], [1, 1, 0, 1])
        self.assertEqual([s["drop_bag"] for s in segs], [0, 1, 1, 1])
        # Notes carried into terrain_notes.
        self.assertEqual(segs[0]["terrain_notes"], "close noon")
        # Final segment reaches the course end; elevation was recomputed (hill).
        self.assertAlmostEqual(segs[-1]["end_mile"], round(self.total, 2), places=1)
        self.assertGreater(sum(s["elevation_gain_ft"] for s in segs), 0)

    def test_crew_and_drop_summaries(self):
        with database.get_db() as conn:
            result = self.race_engine.import_aid_stations(conn, self.stations, course_id=self.course_id)
        self.assertEqual(result["crew_stations"], ["Quarter", "Half (turn)", "Finish"])
        self.assertEqual(result["drop_bag_stations"], ["Half (turn)", "Three-Quarter", "Finish"])

    def test_dry_run_does_not_write(self):
        with database.get_db() as conn:
            before = self.race_engine.get_segments(conn, self.course_id)
            result = self.race_engine.import_aid_stations(
                conn, self.stations, course_id=self.course_id, dry_run=True)
            after = self.race_engine.get_segments(conn, self.course_id)
        self.assertFalse(result["applied"])
        # Segment rows are unchanged (still the original 5-mile buckets, unnamed).
        self.assertEqual(len(after), len(before))
        self.assertTrue(all(s["name"] is None for s in after))
        # But the preview shows the rebuilt, named segments.
        self.assertEqual(len(result["segments"]), 4)

    def test_rebuild_is_idempotent_in_place(self):
        with database.get_db() as conn:
            self.race_engine.import_aid_stations(conn, self.stations, course_id=self.course_id)
            self.race_engine.import_aid_stations(conn, self.stations, course_id=self.course_id)
            courses = conn.execute("SELECT COUNT(*) FROM race_courses").fetchone()[0]
            segs = self.race_engine.get_segments(conn, self.course_id)
        # No duplicate course rows, and segments replaced (not appended).
        self.assertEqual(courses, 1)
        self.assertEqual(len(segs), 4)

    def test_finish_short_of_course_end_snaps_to_end(self):
        # Guide miles rarely match the GPX exactly (issue #18). Put the finish a
        # mile short of the course end and confirm no extra unnamed tail segment
        # appears and the finish segment is extended to the true end.
        stations = [dict(s) for s in self.stations]
        stations[-1]["mile"] = round(self.total - 1.0, 2)
        with database.get_db() as conn:
            result = self.race_engine.import_aid_stations(conn, stations, course_id=self.course_id)
            segs = self.race_engine.get_segments(conn, self.course_id)
        self.assertEqual(len(segs), 4)
        self.assertEqual(segs[-1]["name"], "Finish")
        self.assertAlmostEqual(segs[-1]["end_mile"], round(self.total, 2), places=1)
        self.assertEqual(sum(1 for s in segs if s["name"] == "Finish"), 1)

    def test_missing_gpx_raises(self):
        with database.get_db() as conn:
            conn.execute("UPDATE race_courses SET gpx_file_path = ? WHERE id = ?",
                         ("/no/such/file.gpx", self.course_id))
            with self.assertRaises(FileNotFoundError):
                self.race_engine.import_aid_stations(conn, self.stations, course_id=self.course_id)


if __name__ == "__main__":
    unittest.main()
