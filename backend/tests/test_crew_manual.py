"""Tests for the crew manual generator (issue #12).

Run with: ``python3 -m unittest backend.tests.test_crew_manual -v`` from repo root.

Uses a throwaway SQLite file + a synthetic GPX course (the real BR100 GPX is not
in the repo), so real data is never touched.
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


PROTOCOL_YAML = textwrap.dedent("""
    meta:
      race: Test Course
      start_time: "04:00"
      governor_goal_time: "26:00:00"
      sunset: "20:50"
    fueling:
      carb_g_per_hr: 60
      gel_carb_g: 30
      sodium_mg_per_hr: [500, 700]
      sodium_mg_per_hr_hot: 800
      fluid_oz_per_hr: [20, 24]
      electrolyte: Flash IV
      primary_carb: "NeverSecond C30 gels (30 g carb each)"
    cooling:
      hot_threshold_f: 75
      methods: ["Ice bandana", "Cold water always"]
    chafing:
      prevention: ["Lube every stop"]
    per_stop_workflow:
      on_arrival: ["Swap bottles"]
    night_kit:
      contents: [headlamp]
""")


class CrewManualTestCase(unittest.TestCase):
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

        with database.get_db() as conn:
            cid, segs, total, gain = race_engine.load_course(
                conn, self._gpx.name, "Test Course", 2026,
            )
            # Crew-accessible stations across the course, with cutoff/aid notes.
            stations = [
                {"mile": round(total * 0.25, 2), "name": "Quarter",
                 "crew": True, "drop_bag": False, "notes": "close 9:00 AM; full aid; PB&J"},
                {"mile": round(total * 0.50, 2), "name": "Half",
                 "crew": True, "drop_bag": True, "notes": "close noon; turnaround"},
                {"mile": round(total * 0.75, 2), "name": "ThreeQ",
                 "crew": True, "drop_bag": False, "notes": "close 3:00 PM"},
                {"mile": round(total, 2), "name": "Finish",
                 "crew": True, "drop_bag": True, "notes": "close 10:00 AM"},
            ]
            race_engine.import_aid_stations(conn, stations, course_id=cid)
        self.course_id = cid
        self.total = total

        self._protocol_file = tempfile.NamedTemporaryFile(
            suffix=".yaml", delete=False, mode="w")
        self._protocol_file.write(PROTOCOL_YAML)
        self._protocol_file.close()

        self._splits_file = tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w")
        self._splits_file.write(
            "mile,name,elapsed\n"
            "12.1,A,2:29:13\n"
            "50.3,Half,11:05:24\n"   # >50% of time at 50% distance => positive split
            "100.5,Finish,26:39:43\n"
        )
        self._splits_file.close()

    def tearDown(self):
        self._patcher.stop()
        for f in (self._tmp.name, self._gpx.name,
                  self._protocol_file.name, self._splits_file.name):
            os.unlink(f)

    # -- protocol loader -----------------------------------------------------

    def test_load_protocol_ok(self):
        p = self.race_engine.load_crew_protocol(self._protocol_file.name)
        self.assertEqual(p["meta"]["start_time"], "04:00")
        self.assertEqual(p["fueling"]["gel_carb_g"], 30)

    def test_load_protocol_missing_keys_raises(self):
        bad = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
        bad.write("meta:\n  start_time: '04:00'\n")  # no governor, no fueling
        bad.close()
        try:
            with self.assertRaises(ValueError) as ctx:
                self.race_engine.load_crew_protocol(bad.name)
            self.assertIn("governor_goal_time", str(ctx.exception))
            self.assertIn("fueling", str(ctx.exception))
        finally:
            os.unlink(bad.name)

    def test_load_protocol_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.race_engine.load_crew_protocol("/no/such/profile.yaml")

    # -- split skeleton ------------------------------------------------------

    def test_skeleton_loads_and_anchors_zero(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        self.assertEqual(sk["points"][0], (0.0, 0))
        self.assertEqual(sk["total_miles"], 100.5)
        self.assertEqual(sk["total_seconds"], 26 * 3600 + 39 * 60 + 43)

    def test_skeleton_scales_to_goal_and_keeps_fade(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        goal = 26 * 3600
        # Finish lands exactly on goal.
        end = self.race_engine.eta_seconds_from_skeleton(sk, 100.0, 100.0, goal)
        self.assertAlmostEqual(end, goal, delta=1)
        # Positive split (late fade): halfway through the DISTANCE is reached in
        # LESS than half the total time, because the back half is slower.
        mid = self.race_engine.eta_seconds_from_skeleton(sk, 50.0, 100.0, goal)
        self.assertLess(mid, goal * 0.5)

    # -- end-to-end manual ---------------------------------------------------

    def _gen(self, **kw):
        protocol = self.race_engine.load_crew_protocol(self._protocol_file.name)
        with database.get_db() as conn:
            return self.race_engine.generate_crew_manual(
                conn, self.course_id, protocol, **kw)

    def test_manual_uses_skeleton_when_provided(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        m = self._gen(skeleton=sk)
        self.assertIn("skeleton", m["eta_source"])
        self.assertEqual(len(m["crew_stops"]), 4)
        # ETAs strictly increase by mile (compare numerically, not as strings).
        secs = [self.race_engine._parse_time(s["eta_elapsed"]) for s in m["crew_stops"]]
        self.assertEqual(secs, sorted(secs))
        self.assertEqual(len(set(secs)), len(secs))
        # Per-leg fuel: 30g gels at 60g/hr => ~2 gels/hr, +1 spare.
        first_leg = m["crew_stops"][0]["next_leg"]
        self.assertGreater(first_leg["gels"], 0)
        self.assertEqual(first_leg["gels_with_spare"], first_leg["gels"] + 1)

    def test_manual_engine_path_without_skeleton(self):
        m = self._gen()
        self.assertIn("engine", m["eta_source"])
        self.assertEqual(len(m["crew_stops"]), 4)

    def test_hot_weather_escalates_sodium(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        hot = self._gen(skeleton=sk, weather_temp_f=85)
        cool = self._gen(skeleton=sk, weather_temp_f=60)
        self.assertTrue(hot["hot"])
        self.assertFalse(cool["hot"])
        self.assertEqual(hot["fueling_summary"]["sodium_mg_per_hr_working"], 800)
        self.assertEqual(cool["fueling_summary"]["sodium_mg_per_hr_working"], 700)

    def test_engine_path_honors_governor_not_training_pace(self):
        # An active plan with a deliberately FAST long_run_pace must NOT pull the
        # engine-path ETAs off the governor: pacing should derive from the goal.
        with database.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO training_plans(name,goal,start_date,total_weeks,status) "
                "VALUES('P','sub-26','2026-03-09',20,'active')")
            pid = cur.lastrowid
            conn.execute(
                "INSERT INTO athlete_targets(plan_id,effective_date,easy_pace,"
                "long_run_pace,tempo_pace,source) VALUES(?,?,?,?,?,?)",
                (pid, "2026-03-09", 8.0, 8.0, 7.0, "test"))
        goal = 20 * 3600
        m = self._gen(goal_time_seconds=goal)  # no skeleton -> engine path
        self.assertIn("engine", m["eta_source"])
        finish = self.race_engine._parse_time(m["crew_stops"][-1]["eta_elapsed"])
        # Goal-based pace over this short course => a many-hours finish; an 8:00/mi
        # training pace would finish in well under an hour. Assert it tracks the goal.
        self.assertGreater(finish, goal * 0.7)

    def test_cutoff_parsed_when_not_first_note(self):
        # Mirrors real BR100 Silver Springs data: cutoff buried mid-notes.
        cutoff, rest = self.race_engine._split_aid_notes(
            "50M turnaround; pacers allowed from here; close 8:30 PM; mashed potatoes")
        self.assertEqual(cutoff, "8:30 PM")
        self.assertEqual(rest, "50M turnaround; pacers allowed from here; mashed potatoes")

    def test_planned_gels_meet_carb_target(self):
        # Each leg's planned gels (before the +1 spare) must cover the carb target.
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        m = self._gen(skeleton=sk)
        carb_hr = m["fueling_summary"]["carb_g_per_hr"]
        gel_g = m["fueling_summary"]["gel_carb_g"]
        for stop in m["crew_stops"]:
            nl = stop["next_leg"]
            if not nl:
                continue
            leg_hours = self.race_engine._parse_time(nl["time_display"]) / 3600
            self.assertGreaterEqual(nl["gels"] * gel_g, carb_hr * leg_hours - 1e-9)

    def test_markdown_renders_key_sections(self):
        sk = self.race_engine.load_split_skeleton(self._splits_file.name)
        md = self.race_engine.crew_manual_to_markdown(self._gen(skeleton=sk))
        self.assertIn("Crew Manual", md)
        self.assertIn("Fueling target", md)
        self.assertIn("Cooling playbook", md)
        self.assertIn("On pace if in before", md)
        self.assertIn("hand", md.lower())


if __name__ == "__main__":
    unittest.main()
