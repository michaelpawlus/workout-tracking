"""Tests for the race-day mental rehearsal plan (issue #9, piece 3).

Run with: ``python3 -m unittest backend.tests.test_race_mental -v`` from repo root.

Uses a throwaway SQLite file + a synthetic GPX course, so real data is never touched.
Mirrors the crew-manual test harness (they share the pacing spine).
"""

from __future__ import annotations

import math
import os
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from backend import database


def _write_synthetic_gpx(path, n=200, lat0=40.0, lon0=-81.5):
    pts = []
    for i in range(n + 1):
        lat = lat0 + i * 0.0009
        ele = 300 + 200 * math.sin(math.pi * i / n)
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lon0:.6f}"><ele>{ele:.1f}</ele></trkpt>')
    gpx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test"><trk><trkseg>\n'
        + "\n".join(pts)
        + "\n</trkseg></trk></gpx>\n"
    )
    with open(path, "w") as f:
        f.write(gpx)


MENTAL_YAML = textwrap.dedent("""
    meta:
      race: Test Course
      start_time: "04:00"
      governor_goal_time: "26:00:00"
      sunset: "20:50"
      sunrise: "06:15"
    mantras:
      - "Calm is strong."
    reframes:
      low_mood: "The low is chemical, not the truth."
      heat: "Respect the heat."
    anchors:
      - "In 3, out 2."
    visualization:
      - "See the calm start."
    zones:
      launch:
        max_fraction: 0.20
        label: "Launch"
        likely_feel: "Fresh and eager."
        do: "Let the field go."
        deploy: "Bank composure, not time."
      settle:
        max_fraction: 0.55
        label: "Settle"
        likely_feel: "Into a groove."
        do: "Lock the fueling clock."
        deploy: "Patient, present."
      dark_patch:
        max_fraction: 0.72
        label: "Dark patch"
        likely_feel: "The famous low."
        do: "Next aid station only."
        deploy: "This will pass."
        reframe: low_mood
      closer:
        max_fraction: 1.01
        label: "Closer"
        likely_feel: "Finish pulling you in."
        do: "Empty the tank."
        deploy: "Calm is strong."
    overlays:
      night:
        label: "Night"
        likely_feel: "World shrinks to the headlamp."
        do: "Chase the light."
        deploy: "Keep eating."
      cohort_danger:
        note: "Peers scatter here."
""")


class RaceMentalTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

        self._gpx = tempfile.NamedTemporaryFile(suffix=".gpx", delete=False)
        self._gpx.close()
        _write_synthetic_gpx(self._gpx.name)

        from backend import race_engine, race_mental
        self.race_engine = race_engine
        self.race_mental = race_mental

        with database.get_db() as conn:
            cid, segs, total, gain = race_engine.load_course(
                conn, self._gpx.name, "Test Course", 2026,
            )
            stations = [
                {"mile": round(total * 0.25, 2), "name": "Quarter",
                 "crew": True, "drop_bag": False, "notes": "close 9:00 AM"},
                {"mile": round(total * 0.50, 2), "name": "Half",
                 "crew": True, "drop_bag": True, "notes": "close noon"},
                {"mile": round(total * 0.65, 2), "name": "TwoThirds",
                 "crew": True, "drop_bag": False, "notes": "close 5:00 PM"},
                {"mile": round(total * 0.90, 2), "name": "Nine",
                 "crew": True, "drop_bag": False, "notes": "close 3:00 AM"},
                {"mile": round(total, 2), "name": "Finish",
                 "crew": True, "drop_bag": True, "notes": "close 10:00 AM"},
            ]
            race_engine.import_aid_stations(conn, stations, course_id=cid)
        self.course_id = cid
        self.total = total

        self._profile_file = tempfile.NamedTemporaryFile(
            suffix=".yaml", delete=False, mode="w")
        self._profile_file.write(MENTAL_YAML)
        self._profile_file.close()

        self._splits_file = tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w")
        self._splits_file.write(
            "mile,name,elapsed\n"
            "12.1,A,2:29:13\n"
            "50.3,Half,11:05:24\n"
            "100.5,Finish,26:39:43\n"
        )
        self._splits_file.close()

    def tearDown(self):
        self._patcher.stop()
        for f in (self._tmp.name, self._gpx.name,
                  self._profile_file.name, self._splits_file.name):
            os.unlink(f)

    def _profile(self):
        return self.race_mental.load_mental_profile(self._profile_file.name)

    def _build(self, **kw):
        with database.get_db() as conn:
            return self.race_mental.build_mental_script(
                conn, self.course_id, self._profile(), **kw)

    # -- profile loader ------------------------------------------------------

    def test_load_profile_ok(self):
        p = self._profile()
        self.assertEqual(p["meta"]["start_time"], "04:00")
        self.assertIn("dark_patch", p["zones"])

    def test_load_profile_missing_zones_raises(self):
        bad = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
        bad.write("meta:\n  start_time: '04:00'\n")  # no zones
        bad.close()
        try:
            with self.assertRaises(ValueError) as ctx:
                self.race_mental.load_mental_profile(bad.name)
            self.assertIn("zones", str(ctx.exception))
        finally:
            os.unlink(bad.name)

    def test_load_profile_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.race_mental.load_mental_profile("/no/such/mental.yaml")

    # -- zone classification -------------------------------------------------

    def test_zone_for_fraction_boundaries(self):
        zones = self._profile()["zones"]
        self.assertEqual(self.race_mental._zone_for_fraction(zones, 0.05)[0], "launch")
        self.assertEqual(self.race_mental._zone_for_fraction(zones, 0.20)[0], "launch")
        self.assertEqual(self.race_mental._zone_for_fraction(zones, 0.30)[0], "settle")
        self.assertEqual(self.race_mental._zone_for_fraction(zones, 0.66)[0], "dark_patch")
        self.assertEqual(self.race_mental._zone_for_fraction(zones, 0.99)[0], "closer")

    # -- build_mental_script -------------------------------------------------

    def test_every_segment_gets_a_zone_and_cue(self):
        script = self._build()
        self.assertTrue(script["segments"])
        for e in script["segments"]:
            self.assertIn(e["zone"], self._profile()["zones"])
            self.assertTrue(e["deploy"])

    def test_dark_patch_zone_present_and_carries_reframe(self):
        script = self._build()
        dark = [e for e in script["segments"] if e["zone"] == "dark_patch"]
        self.assertTrue(dark, "expected a dark-patch band on a 100mi course")
        self.assertTrue(all(e["reframe"] for e in dark))
        lo, hi = script["dark_patch_range"]
        self.assertLess(lo, hi)

    def test_skeleton_pins_finish_to_governor(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        script = self._build(skeleton=sk)
        # last segment cumulative == governor goal (26h) exactly
        self.assertEqual(script["segments"][-1]["eta_elapsed"], "26:00:00")
        self.assertEqual(script["eta_source"], "peer-split skeleton")

    def test_night_overlay_flags_late_segments(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        script = self._build(skeleton=sk)
        self.assertIsNotNone(script["night_onset_mile"])
        # early segments are daylight; some late segment is night
        self.assertFalse(script["segments"][0]["night"])
        self.assertTrue(any(e["night"] for e in script["segments"]))

    def test_cohort_danger_overlay(self):
        fake_cohort = {
            "cohort_size": 5,
            "segments": [
                {"segment_number": 1, "danger_zone": True},
                {"segment_number": 2, "danger_zone": False},
            ],
        }
        script = self._build(cohort=fake_cohort)
        seg1 = next(e for e in script["segments"] if e["segment_number"] == 1)
        seg2 = next(e for e in script["segments"] if e["segment_number"] == 2)
        self.assertTrue(seg1["cohort_danger"])
        self.assertFalse(seg2["cohort_danger"])

    # -- markdown ------------------------------------------------------------

    def test_markdown_has_core_sections_and_cues(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        md = self.race_mental.mental_script_to_markdown(self._build(skeleton=sk))
        self.assertIn("# Test Course — Mental Race Plan", md)
        self.assertIn("## Mantras", md)
        self.assertIn("## Pre-race visualization", md)
        self.assertIn("### Dark patch", md)
        self.assertIn("This will pass.", md)
        self.assertIn("🌙 night", md)


class CapstoneMentalSignalTestCase(unittest.TestCase):
    """The capstone dossier should fold in the bundled mental signal (issue #9 piece 3)."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

        self._gpx = tempfile.NamedTemporaryFile(suffix=".gpx", delete=False)
        self._gpx.close()
        _write_synthetic_gpx(self._gpx.name)

        from backend import race_engine, race_capstone
        self.race_engine = race_engine
        self.race_capstone = race_capstone

        with database.get_db() as conn:
            cid, segs, total, gain = race_engine.load_course(
                conn, self._gpx.name, "Test Course", 2026,
            )
        self.course_id = cid

    def tearDown(self):
        self._patcher.stop()
        for f in (self._tmp.name, self._gpx.name):
            os.unlink(f)

    def test_dossier_includes_mental_signal(self):
        with database.get_db() as conn:
            course = dict(self.race_engine.get_course(conn))
            dossier = self.race_capstone.build_capstone_dossier(
                conn, course, plan_id=None,
                goal_time_seconds=26 * 3600, start_time="04:00",
            )
        mental = dossier["signals"]["mental"]
        self.assertIsNotNone(mental, "bundled mental profile should be found")
        self.assertTrue(mental["zones"])
        self.assertIn("mental_plan", dossier["references"])
        headings = [s["heading"] for s in dossier["output_sections"]]
        self.assertTrue(any("Mental Race Plan" in h for h in headings))


if __name__ == "__main__":
    unittest.main()
