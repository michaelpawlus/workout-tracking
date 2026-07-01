"""Race-day mental rehearsal — the mental race plan (issue #9, piece 3).

Mental energy management is a peer dimension to fitness and economy (issue #9), so
race day gets its own rehearsal artifact rather than a soft footnote. This module
maps each course segment of the Race Day Engine to a mental **zone** (by mile
fraction of the course), overlays **night** (segments whose ETA lands after sunset)
and the peer cohort's high-divergence **danger** segments, and renders a
per-segment "what you'll likely feel here → what to deploy" plan.

Design mirrors the crew manual (``race_engine.generate_crew_manual``): all the
athlete-specific content — mantras, reframes, anchors, the pre-race visualization,
and the zone scripts — lives in a hand-editable YAML profile
(``data/br100_mental_race_plan.yaml``); nothing race-specific is hardcoded here, so
a second race is a second profile. Pacing (and therefore the clock/night tags) uses
the SAME spine as the crew manual (``race_engine.segment_cumulative_seconds``), so
both surfaces put the athlete in the same place at the same time.

Public surface:

- ``load_mental_profile(path)`` — parse + validate the YAML profile.
- ``build_mental_script(conn, course_id, profile, ...)`` — the structured plan.
- ``mental_script_to_markdown(script)`` — printable / vault markdown.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import race_engine


# Sections/keys a usable profile must carry (fail loudly, like load_crew_protocol).
_REQUIRED_TOP = ["zones"]


def load_mental_profile(path: str) -> dict:
    """Load and validate a race-day mental rehearsal profile (YAML).

    Returns the parsed dict. Raises ``FileNotFoundError`` if the file is missing and
    ``ValueError`` if it is malformed or missing the ``zones`` map, so a broken
    profile fails with a clear message rather than rendering an empty plan.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dep declared in requirements
        raise ValueError(
            "PyYAML is required to read mental race plan profiles "
            "(`pip install pyyaml`)."
        ) from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Mental race plan profile not found: {path}")

    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Mental race plan profile is not a mapping: {path}")

    for key in _REQUIRED_TOP:
        if not isinstance(data.get(key), dict) or not data[key]:
            raise ValueError(
                f"Mental race plan profile {path} is missing required section: {key}"
            )
    return data


def _zone_for_fraction(zones: dict, frac: float) -> tuple[str, dict]:
    """Pick the zone whose ``max_fraction`` first covers ``frac``.

    Zones are considered in ascending ``max_fraction`` order so the profile can list
    them in any order. Falls back to the last (highest) zone for the finish.
    """
    ordered = sorted(zones.items(), key=lambda kv: kv[1].get("max_fraction", 1.0))
    for key, zone in ordered:
        if frac <= zone.get("max_fraction", 1.0):
            return key, zone
    key, zone = ordered[-1]
    return key, zone


def build_mental_script(
    conn,
    course_id: int,
    profile: dict,
    *,
    goal_time_seconds: int | None = None,
    start_time: str | None = None,
    weather_temp_f: float | None = None,
    skeleton: dict | None = None,
    cohort: dict | None = None,
) -> dict[str, Any]:
    """Build a per-segment mental race plan paced to the governor.

    Walks the course segments, classifies each into a mental zone by mile fraction,
    overlays night (ETA past the profile's ``sunset``) and cohort danger segments,
    and attaches the zone's script (likely feel / what to do / cue to deploy) plus
    any referenced reframe. ``cohort`` is an optional ``analyze_cohort`` result used
    only to flag high-divergence segments — pace does not depend on it.

    Returns a JSON-serializable dict ready for ``mental_script_to_markdown``.
    """
    meta = profile.get("meta", {})
    zones = profile.get("zones", {})
    reframes = profile.get("reframes", {})
    overlays = profile.get("overlays", {})

    if goal_time_seconds is None:
        goal_time_seconds = race_engine._parse_time(
            meta.get("governor_goal_time", "26:00:00"))
    if start_time is None:
        start_time = meta.get("start_time", "05:00")

    course = race_engine.get_course(conn, course_id)
    segments = race_engine.get_segments(conn, course_id)
    total_miles = course["total_distance_miles"] if course else 0

    cum_by_num, eta_source = race_engine.segment_cumulative_seconds(
        conn, course_id, segments, total_miles, goal_time_seconds,
        start_time=start_time, weather_temp_f=weather_temp_f, skeleton=skeleton,
    )

    start_dt = datetime.strptime(start_time, "%H:%M")
    sunset_dt = race_engine._clock_on_day(meta.get("sunset"), start_dt)
    sunrise_dt = race_engine._clock_on_day(meta.get("sunrise"), start_dt)
    # Second sunrise lands on race day + 1 for an overnight finish.
    sunrise_next = sunrise_dt + timedelta(days=1) if sunrise_dt else None

    # Peer cohort high-divergence ("danger") segments, if a cohort was supplied.
    danger_nums = set()
    if cohort:
        for cs in cohort.get("segments", []):
            if cs.get("danger_zone"):
                danger_nums.add(cs["segment_number"])

    plan_segments = []
    night_onset_mile = None
    dark_patch_miles: list[float] = []
    for seg in segments:
        mid_mile = (seg["start_mile"] + seg["end_mile"]) / 2
        frac = mid_mile / total_miles if total_miles else 0
        zone_key, zone = _zone_for_fraction(zones, frac)

        cumulative = cum_by_num.get(seg["segment_number"], 0)
        eta_dt = start_dt + timedelta(seconds=cumulative)

        # Night = arrival between sunset and the next sunrise.
        is_night = False
        if sunset_dt and eta_dt >= sunset_dt:
            is_night = sunrise_next is None or eta_dt < sunrise_next
        if is_night and night_onset_mile is None:
            night_onset_mile = round(seg["end_mile"], 1)

        is_danger = seg["segment_number"] in danger_nums
        if zone_key == "dark_patch":
            dark_patch_miles.append(round(seg["end_mile"], 1))

        reframe_key = zone.get("reframe")
        entry = {
            "segment_number": seg["segment_number"],
            "station_name": seg.get("name") or f"Mile {seg['end_mile']:.1f}",
            "start_mile": round(seg["start_mile"], 1),
            "end_mile": round(seg["end_mile"], 1),
            "eta_clock": race_engine._eta_clock(eta_dt, start_dt),
            "eta_elapsed": race_engine._format_time(cumulative),
            "zone": zone_key,
            "zone_label": zone.get("label", zone_key),
            "likely_feel": zone.get("likely_feel"),
            "do": zone.get("do"),
            "deploy": zone.get("deploy"),
            "reframe": reframes.get(reframe_key) if reframe_key else None,
            "night": is_night,
            "cohort_danger": is_danger,
        }
        plan_segments.append(entry)

    dark_patch_range = None
    if dark_patch_miles:
        lo = min(dark_patch_miles)
        # low end of the first dark-patch segment for a truthful "miles X–Y"
        lo_seg = next((e for e in plan_segments if e["zone"] == "dark_patch"), None)
        if lo_seg:
            lo = lo_seg["start_mile"]
        dark_patch_range = (lo, max(dark_patch_miles))

    return {
        "course": course["name"] if course else "Unknown",
        "total_miles": total_miles,
        "start_time": start_time,
        "governor_goal_display": race_engine._format_time(goal_time_seconds),
        "weather_temp_f": weather_temp_f,
        "eta_source": eta_source,
        "sunset": meta.get("sunset"),
        "sunrise": meta.get("sunrise"),
        "night_onset_mile": night_onset_mile,
        "dark_patch_range": dark_patch_range,
        "cohort_size": cohort.get("cohort_size") if cohort else 0,
        "mantras": profile.get("mantras") or [],
        "reframes": reframes,
        "anchors": profile.get("anchors") or [],
        "visualization": profile.get("visualization") or [],
        "overlays": overlays,
        "meta": meta,
        "segments": plan_segments,
    }


def mental_script_to_markdown(script: dict) -> str:
    """Render a mental race plan dict as printable / vault markdown."""
    s = script
    L: list[str] = []

    L.append(f"# {s['course']} — Mental Race Plan "
             f"({s['governor_goal_display']} governor)")
    L.append("")
    L.append(f"**Start:** {s['start_time']} · **Total:** {s['total_miles']} miles")
    if s.get("weather_temp_f") is not None:
        L.append(f"**Forecast:** {s['weather_temp_f']}°F")
    tags = []
    if s.get("night_onset_mile") is not None:
        tags.append(f"night from ~mile {s['night_onset_mile']}")
    if s.get("dark_patch_range"):
        lo, hi = s["dark_patch_range"]
        tags.append(f"dark patch ~miles {lo:g}–{hi:g}")
    if tags:
        L.append("*" + " · ".join(tags) + f" · ETAs from {s['eta_source']}.*")
    else:
        L.append(f"*ETAs from {s['eta_source']}.*")
    L.append("")
    L.append("> Mental energy is the third lever alongside fitness and economy. "
             "You **pre-loaded** these tools across the block — race day is "
             "**deploy, not rehearse**. Calm is strong.")
    L.append("")

    # --- The trained toolkit -------------------------------------------------
    if s["mantras"]:
        L.append("## Mantras")
        L.append("")
        for m in s["mantras"]:
            L.append(f"- {m}")
        L.append("")

    if s["reframes"]:
        L.append("## Reframes")
        L.append("")
        for _, text in s["reframes"].items():
            L.append(f"- {text}")
        L.append("")

    if s["anchors"]:
        L.append("## Anchors (where attention goes when the mind wanders)")
        L.append("")
        for a in s["anchors"]:
            L.append(f"- {a}")
        L.append("")

    if s["visualization"]:
        L.append("## Pre-race visualization")
        L.append("")
        L.append("*Rehearse the night before and at the start line — see yourself "
                 "calm exactly where it usually gets hard.*")
        L.append("")
        for v in s["visualization"]:
            L.append(f"1. {v}")
        L.append("")

    # --- Segment-by-segment rehearsal ---------------------------------------
    # Zone guidance is stated once per zone; the aid stations under it carry their
    # own ETA + overlay flags. This keeps the sheet skimmable at the aid station.
    L.append("## Segment-by-segment")
    L.append("")
    L.append("*You move through the zones below in order. Each zone's cue applies to "
             "every aid station listed under it — read it as you come in.*")
    L.append("")

    last_zone = None
    for e in s["segments"]:
        if e["zone"] != last_zone:
            if L and L[-1] != "":
                L.append("")  # blank line before a new zone heading
            L.append(f"### {e['zone_label']}")
            L.append("")
            if e["likely_feel"]:
                L.append(f"*Likely feel:* {e['likely_feel']}")
                L.append("")
            if e["do"]:
                L.append(f"- **Do:** {e['do']}")
            L.append(f"- **Deploy:** {e['deploy']}")
            if e["reframe"]:
                L.append(f"- **Reframe:** *{e['reframe']}*")
            L.append("")
            L.append("Aid stations:")
            last_zone = e["zone"]

        flags = []
        if e["night"]:
            flags.append("🌙 night")
        if e["cohort_danger"]:
            flags.append("⚠ peers scatter here")
        flag_str = f" — {', '.join(flags)}" if flags else ""

        L.append(f"- Mile {e['end_mile']:g} · {e['station_name']} "
                 f"(~{e['eta_clock']}, {e['eta_elapsed']} elapsed){flag_str}")
    L.append("")

    # --- Overlays as standing reminders -------------------------------------
    overlays = s.get("overlays") or {}
    night_ov = overlays.get("night")
    if night_ov and s.get("night_onset_mile") is not None:
        L.append(f"## When it gets dark (~mile {s['night_onset_mile']:g})")
        L.append("")
        if night_ov.get("likely_feel"):
            L.append(f"*Likely feel:* {night_ov['likely_feel']}")
            L.append("")
        if night_ov.get("do"):
            L.append(f"- Do: {night_ov['do']}")
        if night_ov.get("deploy"):
            L.append(f"- Deploy: **{night_ov['deploy']}**")
        L.append("")

    return "\n".join(L).rstrip() + "\n"
