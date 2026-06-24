"""Capstone — the BR100 meta-synthesis race-strategy report (issue #16).

This is the deliverable everything else feeds into. Following the project's agent-driven
pattern (CLAUDE.md), the CLI does **not** call an LLM to write the report. Instead it
*gathers every internal signal the engine has already computed* — adaptive pace/HR
targets, the athlete's own-history fade analysis, the peer-cohort split curve, the
A/B/C race plan, the per-segment fueling schedule, the crew/drop-bag station flags, and
a digest of the training block — into one structured **dossier**, and pairs it with a
*synthesis order* (objective + method + output sections) that a Claude Code session
executes to write or **update** the comprehensive strategy report.

The report is persisted to the Obsidian vault via ``vault.write_race_intel_to_vault``
under a STABLE filename, so re-running the command with fresh data (e.g. one more long
run) updates the same **living document** in place rather than spawning a new file.

Public surface:

- ``build_capstone_dossier(conn, course, ...)`` — the gathered signal dossier + order.
- ``render_capstone_skeleton(dossier)`` — a markdown scaffold of the output sections.
"""

from __future__ import annotations

from typing import Any

from . import race_engine
from . import historical
from . import vault
from .adapt import get_current_targets


DEFAULT_TARGET_FINISH = "26:00:00"
DEFAULT_TITLE = "Burning River 100 Race Strategy"
# 26h is the *governor* the whole plan paces to (sub-24 is a stretch only). Pull the
# peer cohort within a 1-hour window of the governor so the back-half curve is grounded
# in finishers who actually ran near the target.
DEFAULT_WINDOW_SECONDS = 3600


def _training_block_digest(conn, plan_id: int | None) -> dict[str, Any]:
    """Summarize the logged training block from ``run_feedback``.

    The rich narrative for each run lives in the vault (``workouts/``); here we surface
    the structured facts the report's pacing/fueling math leans on — recent sessions,
    the long-run capstones, and HR/elevation context.
    """
    if plan_id is None:
        return {"count": 0, "sessions": [], "long_runs": [], "note": "No active plan."}

    rows = conn.execute(
        """SELECT rf.actual_distance_miles, rf.actual_pace, rf.avg_heart_rate,
                  rf.max_heart_rate, rf.elevation_gain_ft, rf.effort_rating,
                  rf.overall_feedback, w.date AS run_date, dw.title AS title
           FROM run_feedback rf
           JOIN workouts w ON w.id = rf.workout_id
           LEFT JOIN daily_workouts dw ON dw.id = rf.daily_workout_id
           WHERE rf.plan_id = ?
           ORDER BY w.date DESC
           LIMIT 20""",
        (plan_id,),
    ).fetchall()

    sessions = []
    for r in rows:
        d = dict(r)
        sessions.append({
            "date": d.get("run_date"),
            "title": d.get("title"),
            "distance_miles": d.get("actual_distance_miles"),
            "pace_display": race_engine._format_pace(d["actual_pace"] * 60)
                if d.get("actual_pace") else None,
            "avg_hr": d.get("avg_heart_rate"),
            "max_hr": d.get("max_heart_rate"),
            "elevation_gain_ft": d.get("elevation_gain_ft"),
            "effort_rating": d.get("effort_rating"),
        })

    long_runs = [s for s in sessions
                 if (s["distance_miles"] or 0) >= 18.0]

    return {
        "count": len(sessions),
        "latest_date": sessions[0]["date"] if sessions else None,
        "sessions": sessions,
        "long_runs": long_runs,
        "note": ("Structured facts only — read the per-run narrative in the vault "
                 "`workouts/` notes and `PRODUCT_LOG.md` for the qualitative picture."),
    }


def _vault_references(course_name: str, title: str) -> dict[str, Any]:
    """Vault docs the synthesizing agent should read for narrative depth."""
    refs: dict[str, Any] = {
        "report_path": None,
        "report_exists": False,
        "course_guide": f"race-prep/{course_name} Course & Strategy Guide.md",
        "peer_learnings": f"race-prep/{course_name} Peer Split Learnings.md",
        "crew_manual": f"race/{course_name} Crew Manual.md",
        "workouts_dir": "workouts/  (per-run narrative + Mohican benchmark report)",
        "product_log": "workouts/PRODUCT_LOG.md",
        "memory": "Workout App memory: Mohican benchmark, fueling protocol, course guide notes.",
    }
    try:
        path = vault.race_intel_target_path(title)
        refs["report_path"] = str(path)
        refs["report_exists"] = path.exists()
    except vault.VaultError:
        pass
    return refs


def _output_sections() -> list[dict[str, str]]:
    """The sections the synthesized capstone report must contain (issue #16 Output)."""
    return [
        {"heading": "Executive Summary & Goal",
         "what": "Finish-primary framing: 26h governor (sub-24 a stretch only). The one "
                 "paragraph the athlete re-reads at mile 70 — the race in a nutshell."},
        {"heading": "Segment-by-Segment Pacing Plan (26h)",
         "what": "Every segment/aid station: target pace, segment time, cumulative "
                 "elapsed, and clock ETA — biased to a negative split given the own-history "
                 "fade and the peer back-half slowdown. Flag the danger-zone segments."},
        {"heading": "Fueling & Sodium Schedule",
         "what": "Per-segment: gels every 30–40 min between aid, ~60–70 g carb/hr, "
                 "~500–700 mg sodium/hr. Bake in the Mohican lesson (drop HEED-as-primary; "
                 "use the athlete's Flash IV + NeverSecond protocol). Heat escalation."},
        {"heading": "Walk/Run & Power-Hike Strategy",
         "what": "Where to run, where to power-hike the climbs, and the run/walk cadence "
                 "for the towpath and the night sections. Concrete rules, not vibes."},
        {"heading": "Crew Plan & Drop-Bag Contents",
         "what": "Per crew-accessible station: ETA window, what crew does, and exact "
                 "drop-bag contents (night kit, shoes/socks, fuel restock, cooling)."},
        {"heading": "Contingencies — Heat, Night & Low Patches",
         "what": "Explicit if/then plans. Anchor on the Canal Corridor weather-DNF lesson: "
                 "do NOT let bad overnight weather end the race mentally. Heat, GI, dark-"
                 "patch, and behind-pace playbooks."},
        {"heading": "Lessons Integrated",
         "what": "Trace each pacing/fueling decision back to its signal — own-history fade, "
                 "peer cohort, Mohican benchmark, training block — so the plan is auditable."},
        {"heading": "Revision Log",
         "what": "Living-document changelog: dated entries noting what new data (which run) "
                 "moved which numbers. Append on every regeneration; never overwrite."},
    ]


def _method(existing: bool) -> list[str]:
    """Synthesis steps the executing agent should follow."""
    steps = []
    if existing:
        steps.append(
            "UPDATE the existing report in place (it is linked under `references.report_path`): "
            "read it first, preserve its structure and narrative voice, revise the numbers from "
            "this dossier, and add a dated entry to the Revision Log describing what changed.")
    else:
        steps.append(
            "Write the report fresh from the output sections below; seed an empty Revision Log "
            "with today's dated 'initial draft' entry.")
    steps += [
        "Read the linked vault docs for narrative depth before writing: the course guide, the "
        "peer-split learnings, the Mohican benchmark note, and the recent `workouts/` reports.",
        "Build the segment pacing table from `signals.race_plan` — pick the scenario aligned to "
        "the 26h governor as primary; keep A/B/C as the aggressive/governor/survival bands.",
        "Bias the pace curve to a negative split: weight the back-half toward the own-history "
        "fade (`signals.history.avg_fade_pct`) and the peer cohort slowdown "
        "(`signals.peer_cohort.slowdown_pct`); call out the danger-zone segments explicitly.",
        "Cross-check the fueling schedule against the athlete's logged protocol and the Mohican "
        "HEED lesson — do not invent products; reconcile g/hr and mg/hr against `signals.fueling`.",
        "Keep every claim traceable to a signal in this dossier or a cited vault doc; flag any "
        "gap the data cannot close rather than papering over it.",
        "Save with `ultra race capstone --save-guide -` (markdown on stdin) — stable filename, so "
        "the living document is updated in place.",
    ]
    return steps


def build_capstone_dossier(
    conn,
    course: dict,
    *,
    plan_id: int | None = None,
    goal_time_seconds: int,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    weather_temp_f: float | None = None,
    start_time: str = "05:00",
    weight_lbs: float | None = None,
    title: str = DEFAULT_TITLE,
) -> dict[str, Any]:
    """Gather every internal signal into a dossier + synthesis order for issue #16.

    ``course`` is a row dict from ``race_engine.get_course``. The returned dict is fully
    JSON-serializable: a ``race`` header, a ``signals`` block (targets, history, peer
    cohort, race plan, fueling, crew stations, training block), ``references`` to the
    vault docs to read, and the ``objective`` / ``method`` / ``output_sections`` that
    tell the agent how to write or update the report.
    """
    course_id = course["id"]
    goal_display = race_engine._format_time(goal_time_seconds)

    # --- adaptive targets -------------------------------------------------
    targets = get_current_targets(conn, plan_id) if plan_id else None

    # --- own-history fade analysis ----------------------------------------
    history = historical.analyze_history(conn)

    # --- peer cohort split curve ------------------------------------------
    cohort = race_engine.analyze_cohort(conn, course_id, goal_time_seconds, window_seconds)

    # --- A/B/C race plan ---------------------------------------------------
    race_plan = race_engine.generate_race_plan(
        conn, course_id, plan_id, goal_time_seconds,
        weather_temp_f=weather_temp_f, start_time=start_time)

    # --- per-segment fueling (off the A scenario) -------------------------
    a_segments = race_plan["plans"]["A"]["segments"]
    fueling = race_engine.generate_fueling_plan(
        conn, course_id, a_segments,
        weight_lbs=weight_lbs or race_engine.DEFAULT_WEIGHT_LBS)

    # --- crew / drop-bag stations -----------------------------------------
    segments = race_engine.get_segments(conn, course_id)
    crew_stations = [
        {
            "segment_number": s["segment_number"],
            "name": s.get("name") or f"Mile {s['start_mile']:.1f}",
            "mile": round(s["end_mile"], 1),
            "crew_accessible": bool(s.get("crew_accessible")),
            "drop_bag": bool(s.get("drop_bag")),
            "terrain_notes": s.get("terrain_notes"),
        }
        for s in segments
        if s.get("crew_accessible") or s.get("drop_bag")
    ]

    # --- training block digest --------------------------------------------
    training_block = _training_block_digest(conn, plan_id)

    references = _vault_references(course["name"], title)

    dossier: dict[str, Any] = {
        "race": {
            "name": course["name"],
            "year": course.get("year"),
            "distance_miles": course.get("total_distance_miles"),
            "elevation_gain_ft": course.get("total_elevation_gain_ft"),
            "target_finish": goal_display,
            "governor": "26h finish-primary; sub-24 is a stretch only",
            "weather_temp_f": weather_temp_f,
            "start_time": start_time,
        },
        "objective": (
            f"Synthesize every signal below into ONE comprehensive {course['name']} "
            f"race-strategy report paced to a {goal_display} governor. This is the capstone "
            "(issue #16): segment pacing, fueling/sodium, walk/run + power-hiking, crew & "
            "drop-bag plan, and heat/night/low-patch contingencies — all traceable to the "
            "data. It is a LIVING document: re-running this command with new data updates "
            "the same vault file in place."
        ),
        "signals": {
            "targets": dict(targets) if targets else None,
            "history": {
                "count": history.get("count"),
                "failure_mode": history.get("failure_mode"),
                "avg_fade_pct": history.get("avg_fade_pct"),
                "positive_split_count": history.get("positive_split_count"),
                "dnf_count": history.get("dnf_count"),
                "lessons": history.get("lessons"),
                "training_implications": history.get("training_implications"),
                "races": history.get("races"),
            },
            "peer_cohort": cohort,
            "race_plan": race_plan,
            "fueling": fueling,
            "crew_stations": crew_stations,
            "training_block": training_block,
        },
        "references": references,
        "method": _method(references.get("report_exists", False)),
        "output_sections": _output_sections(),
    }
    return dossier


def render_capstone_skeleton(dossier: dict[str, Any]) -> str:
    """Render a markdown scaffold of the report's output sections.

    A fallback artifact / fill-in scaffold; the real content is the agent's synthesis of
    the dossier signals, not this function.
    """
    race = dossier["race"]
    lines: list[str] = []
    lines.append(f"# {race['name']} — Race Strategy")
    lines.append("")
    meta_bits = [str(race.get("year") or "")]
    if race.get("distance_miles"):
        meta_bits.append(f"{race['distance_miles']:g} mi")
    if race.get("elevation_gain_ft"):
        meta_bits.append(f"{race['elevation_gain_ft']:,.0f} ft climb")
    meta_bits.append(f"governor {race.get('target_finish')}")
    if race.get("weather_temp_f"):
        meta_bits.append(f"{race['weather_temp_f']:g}°F")
    lines.append("*" + " · ".join(b for b in meta_bits if b) + "*")
    lines.append("")
    lines.append("> Skeleton only — synthesize each section from the dossier "
                 "(`ultra race capstone --json`).")
    lines.append("")
    for section in dossier["output_sections"]:
        lines.append(f"## {section['heading']}")
        lines.append("")
        lines.append(f"_{section['what']}_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
