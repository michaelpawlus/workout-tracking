"""Tests for peer-split acquisition + analysis (#14).

Run with: ``python3 -m unittest backend.tests.test_peer_splits -v`` from repo root.

Uses a throwaway SQLite file and a synthetic GPX, so the real ``workouts.db`` is
never touched. The crux being tested is the sparse-mat importer: official timing
mats are far fewer than course segments and report cumulative elapsed time, so
``import_peer_splits_long`` must map mats to segments and distribute each leg's
pace across the segments it covers.
"""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from unittest.mock import patch

from backend import database, peer_splits, race_engine


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


class PeerSplitsTestCase(unittest.TestCase):
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
                conn, self._gpx.name, "Test Course", 2026,
            )
            self.course_id = cid
            self.total = total
            # Four named segments at quarter points so mats can map to boundaries.
            stations = [
                {"mile": round(total * 0.25, 2), "name": "Q1", "crew": True, "drop_bag": False, "notes": None},
                {"mile": round(total * 0.50, 2), "name": "Turn", "crew": True, "drop_bag": True, "notes": None},
                {"mile": round(total * 0.75, 2), "name": "Q3", "crew": False, "drop_bag": False, "notes": None},
                {"mile": round(total, 2), "name": "Finish", "crew": True, "drop_bag": True, "notes": None},
            ]
            race_engine.import_aid_stations(conn, stations, course_id=cid)
            self.segments = race_engine.get_segments(conn, cid)

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)
        os.unlink(self._gpx.name)

    def _write_csv(self, text):
        f = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w")
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    # --- mat→segment mapping ------------------------------------------------
    def test_map_mat_snaps_to_nearest_segment_end(self):
        half = self.total * 0.5
        seg = peer_splits.map_mat_to_segment(self.segments, half)
        self.assertEqual(seg["name"], "Turn")
        # A mat far from any boundary returns None.
        self.assertIsNone(peer_splits.map_mat_to_segment(self.segments, self.total + 50))

    # --- import: sparse cumulative mats → per-segment splits -----------------
    def test_import_distributes_leg_pace_and_closes_to_finish(self):
        half = round(self.total * 0.5, 2)
        # One mat at the turnaround (1:30:00) + finish via finish_time (4:00:00).
        csv = self._write_csv(
            "runner_name,finish_time,dnf,mat_mile,mat_name,elapsed,year,source\n"
            f"A,4:00:00,0,{half},Turn,1:30:00,2025,ultrasignup\n"
        )
        with database.get_db() as conn:
            res = peer_splits.import_peer_splits_long(conn, self.course_id, csv, default_year=2025)
            cohort = race_engine.get_peer_cohort(conn, self.course_id, 4 * 3600, 3600)

        self.assertEqual(res["imported"], 1)
        self.assertFalse(res["warnings"])
        self.assertEqual(len(cohort), 1)
        splits = {s["segment_name"]: s for s in cohort[0]["splits"]}
        # Every segment got a split, including the back half closed off the finish.
        self.assertEqual(set(splits), {"Q1", "Turn", "Q3", "Finish"})
        # First leg pace = 5400s / half; second leg pace = 9000s / half → slower.
        first_pace = 5400 / half
        second_pace = 9000 / half
        self.assertAlmostEqual(splits["Q1"]["pace_per_mile_seconds"], round(first_pace), delta=2)
        self.assertAlmostEqual(splits["Finish"]["pace_per_mile_seconds"], round(second_pace), delta=2)
        self.assertGreater(splits["Finish"]["pace_per_mile_seconds"],
                           splits["Q1"]["pace_per_mile_seconds"])

    def test_dnf_recorded_without_splits(self):
        csv = self._write_csv(
            "runner_name,finish_time,dnf,mat_mile,mat_name,elapsed,year,source\n"
            f"B,,dnf,{round(self.total * 0.25, 2)},Q1,1:00:00,2025,ultrasignup\n"
        )
        with database.get_db() as conn:
            res = peer_splits.import_peer_splits_long(conn, self.course_id, csv, default_year=2025)
            row = conn.execute(
                "SELECT dnf FROM historical_results WHERE runner_name='B'").fetchone()
            nsplits = conn.execute("SELECT COUNT(*) FROM historical_splits").fetchone()[0]
        self.assertEqual(res["imported"], 1)
        self.assertEqual(row["dnf"], 1)
        self.assertEqual(nsplits, 0)

    def test_cohort_excludes_dnf_and_analyzes_splits(self):
        half = round(self.total * 0.5, 2)
        csv = self._write_csv(
            "runner_name,finish_time,dnf,mat_mile,mat_name,elapsed,year,source\n"
            f"A,4:00:00,0,{half},Turn,1:30:00,2025,ultrasignup\n"
            f"C,,dnf,{half},Turn,2:00:00,2025,ultrasignup\n"
        )
        with database.get_db() as conn:
            peer_splits.import_peer_splits_long(conn, self.course_id, csv, default_year=2025)
            analysis = race_engine.analyze_cohort(conn, self.course_id, 4 * 3600, 3600)
        self.assertEqual(analysis["cohort_size"], 1)  # DNF excluded
        self.assertGreater(len(analysis["segments"]), 0)

    def test_placeholder_elapsed_is_skipped_not_zeroed(self):
        half = round(self.total * 0.5, 2)
        q1 = round(self.total * 0.25, 2)
        # The Q1 mat uses an "N/A" placeholder — it must be skipped (with a warning),
        # not recorded as elapsed 0 (which would put it at race start).
        csv = self._write_csv(
            "runner_name,finish_time,dnf,mat_mile,mat_name,elapsed,year,source\n"
            f"A,4:00:00,0,{q1},Q1,N/A,2025,ultrasignup\n"
            f"A,4:00:00,0,{half},Turn,1:30:00,2025,ultrasignup\n"
        )
        with database.get_db() as conn:
            res = peer_splits.import_peer_splits_long(conn, self.course_id, csv, default_year=2025)
            cohort = race_engine.get_peer_cohort(conn, self.course_id, 4 * 3600, 3600)
        self.assertTrue(any("N/A" in w for w in res["warnings"]))
        # First leg pace comes from 0→Turn (5400s), NOT a bogus 0-elapsed Q1 mat.
        splits = {s["segment_name"]: s for s in cohort[0]["splits"]}
        self.assertAlmostEqual(splits["Q1"]["pace_per_mile_seconds"],
                               round(5400 / half), delta=2)

    def test_colon_placeholder_skips_mat_without_aborting(self):
        half = round(self.total * 0.5, 2)
        q1 = round(self.total * 0.25, 2)
        # "--:--" makes _parse_time raise; the import must skip that mat, not abort.
        csv = self._write_csv(
            "runner_name,finish_time,dnf,mat_mile,mat_name,elapsed,year,source\n"
            f"A,4:00:00,0,{q1},Q1,--:--,2025,ultrasignup\n"
            f"A,4:00:00,0,{half},Turn,1:30:00,2025,ultrasignup\n"
        )
        with database.get_db() as conn:
            res = peer_splits.import_peer_splits_long(conn, self.course_id, csv, default_year=2025)
        self.assertEqual(res["imported"], 1)
        self.assertTrue(any("--:--" in w for w in res["warnings"]))

    def test_runner_without_finish_evidence_is_skipped(self):
        # A non-DNF runner with only an early mat and no finish_time / finish mat must
        # NOT be recorded as a finisher.
        q1 = round(self.total * 0.25, 2)
        csv = self._write_csv(
            "runner_name,finish_time,dnf,mat_mile,mat_name,elapsed,year,source\n"
            f"Ghost,,0,{q1},Q1,1:00:00,2025,ultrasignup\n"
        )
        with database.get_db() as conn:
            res = peer_splits.import_peer_splits_long(conn, self.course_id, csv, default_year=2025)
            n = conn.execute("SELECT COUNT(*) FROM historical_results").fetchone()[0]
        self.assertEqual(res["imported"], 0)
        self.assertEqual(n, 0)
        self.assertTrue(any("incomplete" in w for w in res["warnings"]))

    # --- research order -----------------------------------------------------
    def test_research_order_maps_mats_and_lists_schema(self):
        course = {"name": "Test Course", "year": 2026, "total_distance_miles": self.total}
        order = peer_splits.build_research_order(course, self.segments, 26 * 3600)
        self.assertEqual(order["race"]["prior_year"], 2025)
        self.assertEqual(order["target_window"]["finish_low"], "25:30:00")
        self.assertEqual(order["target_window"]["finish_high"], "26:30:00")
        self.assertIn("runner_name", order["output"]["columns"])
        self.assertTrue(any(s["category"].startswith("Strava") for s in order["sources"]))


if __name__ == "__main__":
    unittest.main()
