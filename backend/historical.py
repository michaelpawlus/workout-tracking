"""Historical analysis — ingest & learn from the athlete's OWN prior races.

Unlike ``race_engine``'s ``historical_results`` (peer finishers on a single
course, keyed to course geometry), this module models the athlete's own prior
efforts at a given distance — across different courses — as benchmark efforts.
It extracts the signals that calibrate the BR100 plan: late-race fade, the
positive split, HR drift, and stoppage (elapsed-vs-moving) time.

These lessons feed three places:
  - coaching     (run-report feedback via ``analyze_run_feedback``)
  - programming  (training implications surfaced in the analysis)
  - race reports (historical fade blended into the Race Day Engine pace plan)

Storage lives in the ``athlete_races`` table (see ``database.py``). Data is
entered manually / seeded from known efforts, and optionally enriched from
Strava when connected.
"""

import statistics

from .race_engine import _parse_time, _format_time, _format_pace


# ---------------------------------------------------------------------------
# Known prior 100s (from issue #13). Seedable so the analysis works offline,
# before any Strava pull. Times are stored as seconds.
# ---------------------------------------------------------------------------

KNOWN_RACES = [
    {
        "name": "Tunnel Hill 100",
        "race_date": "2021-11-13",
        "distance_miles": 101.1,
        "elevation_gain_ft": 1900,
        "finish_time_seconds": _parse_time("25:23:00"),
        "moving_time_seconds": _parse_time("23:34:00"),
        "first_half_seconds": _parse_time("13:03:00"),
        "second_half_seconds": _parse_time("14:56:00"),
        "avg_hr": None,
        "terrain": "flat (crushed limestone rail-trail)",
        "dnf": 0,
        "strava_activity_id": 6257195830,
        "notes": "PR. Smallest fade of any 100 — the pacing template.",
    },
    {
        "name": "Wolverine State 100",
        "race_date": "2025-10-11",
        "distance_miles": 102.8,
        "elevation_gain_ft": 5049,
        "finish_time_seconds": _parse_time("27:17:00"),
        "moving_time_seconds": None,
        "first_half_seconds": _parse_time("14:06:00"),
        "second_half_seconds": _parse_time("17:42:00"),
        "avg_hr": 122,
        "terrain": "hilly loop course",
        "dnf": 0,
        "strava_activity_id": 16121062071,
        "notes": "Heavy late walking; avg HR only 122 — aerobically capable, "
                 "limited by late-race discipline, not fitness.",
    },
    {
        "name": "Canal Corridor 100",
        "race_date": "2023-10-07",
        "distance_miles": 76.8,
        "elevation_gain_ft": 900,
        "finish_time_seconds": None,
        "moving_time_seconds": None,
        "first_half_seconds": _parse_time("12:08:00"),
        "second_half_seconds": _parse_time("15:48:00"),
        "avg_hr": None,
        "terrain": "flat canal towpath",
        "dnf": 1,
        "strava_activity_id": 10003645304,
        "notes": "Weather-derailed walk-in. Went out hot, overnight storms. "
                 "Worst fade — mental, not just physical.",
    },
    {
        "name": "OBU 90",
        "race_date": "2025-03-22",
        "distance_miles": 90.2,
        "elevation_gain_ft": None,
        "finish_time_seconds": _parse_time("22:04:00"),
        "moving_time_seconds": None,
        "first_half_seconds": None,
        "second_half_seconds": None,
        "avg_hr": None,
        "terrain": None,
        "dnf": 0,
        "strava_activity_id": 13967459991,
        "notes": "Strong finish; no half-split on file.",
    },
]

_FIELDS = (
    "name", "race_date", "distance_miles", "elevation_gain_ft",
    "finish_time_seconds", "moving_time_seconds", "first_half_seconds",
    "second_half_seconds", "avg_hr", "first_half_hr", "second_half_hr",
    "terrain", "dnf", "strava_activity_id", "notes",
)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def add_race(conn, **fields):
    """Insert (or replace, by name+date) one athlete race. Returns row id."""
    row = {f: fields.get(f) for f in _FIELDS}
    if not row.get("name") or not row.get("race_date"):
        raise ValueError("name and race_date are required")
    row["dnf"] = 1 if row.get("dnf") else 0

    existing = conn.execute(
        "SELECT id FROM athlete_races WHERE name = ? AND race_date = ?",
        (row["name"], row["race_date"]),
    ).fetchone()

    cols = list(_FIELDS)
    vals = [row[c] for c in cols]
    if existing:
        assignments = ", ".join(f"{c} = ?" for c in cols)
        conn.execute(
            f"UPDATE athlete_races SET {assignments} WHERE id = ?",
            (*vals, existing["id"]),
        )
        return existing["id"]
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO athlete_races ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    return cur.lastrowid


def seed_known_races(conn):
    """Seed the known prior 100s. Idempotent (keyed on name+date). Returns count."""
    for race in KNOWN_RACES:
        add_race(conn, **race)
    return len(KNOWN_RACES)


def get_races(conn, target_distance=None, tolerance_pct=15.0, include_dnf=True):
    """Return athlete races, optionally filtered to a target distance.

    ``target_distance`` keeps only races within ``tolerance_pct`` percent of the
    given distance — i.e. "any prior races of the same distance".
    """
    rows = conn.execute(
        "SELECT * FROM athlete_races ORDER BY race_date"
    ).fetchall()
    races = [dict(r) for r in rows]

    if not include_dnf:
        races = [r for r in races if not r["dnf"]]

    if target_distance is not None:
        lo = target_distance * (1 - tolerance_pct / 100)
        hi = target_distance * (1 + tolerance_pct / 100)
        races = [r for r in races
                 if r["distance_miles"] and lo <= r["distance_miles"] <= hi]

    return races


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def race_metrics(race):
    """Compute derived metrics for one race: fade, split, HR drift, stoppage."""
    m = {
        "fade_pct": None,
        "positive_split": None,
        "hr_drift_bpm": None,
        "stoppage_seconds": None,
        "stoppage_pct": None,
        "avg_pace_seconds": None,
        "avg_pace_display": None,
    }

    fh, sh = race.get("first_half_seconds"), race.get("second_half_seconds")
    if fh and sh and fh > 0:
        m["fade_pct"] = round(((sh - fh) / fh) * 100, 1)
        m["positive_split"] = sh > fh

    fhr, shr = race.get("first_half_hr"), race.get("second_half_hr")
    if fhr and shr:
        m["hr_drift_bpm"] = shr - fhr

    finish = race.get("finish_time_seconds")
    moving = race.get("moving_time_seconds")
    if finish and moving and finish >= moving:
        m["stoppage_seconds"] = finish - moving
        m["stoppage_pct"] = round((finish - moving) / finish * 100, 1)

    dist = race.get("distance_miles")
    base = finish or moving
    if base and dist:
        pace = base / dist
        m["avg_pace_seconds"] = int(pace)
        m["avg_pace_display"] = _format_pace(pace)

    return m


def get_historical_fade(conn, target_distance=None, tolerance_pct=15.0):
    """Mean late-race fade % across the athlete's non-DNF same-distance races.

    Used by the Race Day Engine to bias the late-race fatigue curve toward the
    athlete's documented failure mode. Returns None when no fade data exists.
    """
    fades = []
    for race in get_races(conn, target_distance, tolerance_pct, include_dnf=False):
        f = race_metrics(race)["fade_pct"]
        if f is not None and f > 0:
            fades.append(f)
    return round(statistics.mean(fades), 1) if fades else None


# ---------------------------------------------------------------------------
# Analysis — the object consumed by coaching / programming / race reports
# ---------------------------------------------------------------------------

def analyze_history(conn, target_distance=None, tolerance_pct=15.0):
    """Aggregate the athlete's prior races into actionable lessons.

    Returns a dict with per-race metrics, aggregate stats, the dominant failure
    mode, coaching lessons, and training implications.
    """
    races = get_races(conn, target_distance, tolerance_pct, include_dnf=True)

    enriched = []
    for r in races:
        metrics = race_metrics(r)
        enriched.append({
            "name": r["name"],
            "race_date": r["race_date"],
            "distance_miles": r["distance_miles"],
            "elevation_gain_ft": r["elevation_gain_ft"],
            "terrain": r["terrain"],
            "dnf": bool(r["dnf"]),
            "finish_time": _format_time(r["finish_time_seconds"]) if r["finish_time_seconds"] else None,
            "first_half": _format_time(r["first_half_seconds"]) if r["first_half_seconds"] else None,
            "second_half": _format_time(r["second_half_seconds"]) if r["second_half_seconds"] else None,
            "avg_hr": r["avg_hr"],
            "notes": r["notes"],
            **metrics,
        })

    if not enriched:
        return {
            "count": 0,
            "target_distance": target_distance,
            "races": [],
            "message": "No prior races on file. Seed with: ultra race history --seed",
        }

    faded = [e for e in enriched if e["fade_pct"] is not None]
    fades = [e["fade_pct"] for e in faded]
    avg_fade = round(statistics.mean(fades), 1) if fades else None
    worst = max(faded, key=lambda e: e["fade_pct"]) if faded else None
    best = min(faded, key=lambda e: e["fade_pct"]) if faded else None
    dnfs = [e for e in enriched if e["dnf"]]
    positive_splits = [e for e in enriched if e["positive_split"]]

    lessons = []
    implications = []
    failure_mode = None

    if fades:
        if avg_fade > 8:
            failure_mode = "late-race fade (positive split)"
            lessons.append(
                f"Late fade is the #1 failure mode: average second-half slowdown "
                f"of {avg_fade}% across {len(fades)} efforts "
                f"({len(positive_splits)}/{len(enriched)} were positive splits). "
                f"The BR100 26h target lives or dies on the second half."
            )
            implications.append(
                "Bias long runs toward negative-split execution; rehearse "
                "back-half-fast finishes and B2B fatigue resistance."
            )
        if worst:
            lessons.append(
                f"Worst fade: {worst['name']} ({worst['fade_pct']}%)"
                + (f" — {worst['notes']}" if worst.get("notes") else "")
            )
        if best and best["fade_pct"] <= 15:
            lessons.append(
                f"Best pacing template: {best['name']} ({best['fade_pct']}% fade)"
                + (f" — {best['notes']}" if best.get("notes") else "")
            )

    # Aerobic-capability signal: a low avg HR alongside a big fade means the
    # limiter was discipline/durability, not engine.
    for e in enriched:
        if e["avg_hr"] and e["avg_hr"] < 130 and e["fade_pct"] and e["fade_pct"] > 20:
            lessons.append(
                f"{e['name']}: avg HR {e['avg_hr']} with {e['fade_pct']}% fade — "
                f"aerobically under-stressed, so the late collapse was durability/"
                f"discipline, not fitness."
            )

    if dnfs:
        lessons.append(
            f"{len(dnfs)} DNF/short on file ("
            + ", ".join(e["name"] for e in dnfs)
            + ") — weather/mental low-patches ended races that fitness could finish. "
            "Build explicit contingencies for night, heat, and dark patches."
        )
        implications.append(
            "Treat mental/weather contingency planning as a first-class race-prep item."
        )

    # Elevation reality check vs BR100 (~10,568 ft).
    finishers = [e for e in enriched if not e["dnf"] and e["elevation_gain_ft"]]
    if finishers:
        hilliest = max(finishers, key=lambda e: e["elevation_gain_ft"])
        if hilliest["elevation_gain_ft"] < 10568:
            implications.append(
                f"BR100 (~10,568 ft) is hillier than every prior finish "
                f"(max {int(hilliest['elevation_gain_ft'])} ft at {hilliest['name']}); "
                f"26h needs better second-half discipline than any prior 100."
            )

    return {
        "count": len(enriched),
        "target_distance": target_distance,
        "tolerance_pct": tolerance_pct,
        "avg_fade_pct": avg_fade,
        "historical_fade_pct": get_historical_fade(conn, target_distance, tolerance_pct),
        "positive_split_count": len(positive_splits),
        "dnf_count": len(dnfs),
        "failure_mode": failure_mode,
        "lessons": lessons,
        "training_implications": implications,
        "races": enriched,
    }


# ---------------------------------------------------------------------------
# Strava enrichment (optional — requires a connected token)
# ---------------------------------------------------------------------------

def enrich_from_strava(conn, race_id, activity_id):
    """Pull detail for one activity and fill in missing fields on a race row.

    Fills distance, elevation, finish (elapsed) + moving time, and avg HR. Half
    splits are computed from ``splits_standard`` (mile splits) when present.
    Raises RuntimeError if Strava isn't connected.
    """
    from . import strava  # lazy: avoids requiring `requests` for offline use

    act = strava.get_activity_detail(activity_id)

    dist_mi = round(act["distance"] / 1609.34, 2) if act.get("distance") else None
    elev_ft = round(act["total_elevation_gain"] * 3.28084, 0) if act.get("total_elevation_gain") else None
    elapsed = act.get("elapsed_time")
    moving = act.get("moving_time")
    avg_hr = round(act["average_heartrate"]) if act.get("average_heartrate") else None

    first_half = second_half = first_hr = second_hr = None
    splits = act.get("splits_standard") or []
    if splits:
        n = len(splits)
        half = n // 2
        first = splits[:half]
        second = splits[half:]
        if first and second:
            first_half = sum(s.get("moving_time", s.get("elapsed_time", 0)) for s in first)
            second_half = sum(s.get("moving_time", s.get("elapsed_time", 0)) for s in second)
            fh_hrs = [s["average_heartrate"] for s in first if s.get("average_heartrate")]
            sh_hrs = [s["average_heartrate"] for s in second if s.get("average_heartrate")]
            if fh_hrs:
                first_hr = round(statistics.mean(fh_hrs))
            if sh_hrs:
                second_hr = round(statistics.mean(sh_hrs))

    # Only overwrite fields we actually pulled (COALESCE-style: keep existing).
    updates = {
        "distance_miles": dist_mi,
        "elevation_gain_ft": elev_ft,
        "finish_time_seconds": elapsed,
        "moving_time_seconds": moving,
        "avg_hr": avg_hr,
        "first_half_seconds": first_half,
        "second_half_seconds": second_half,
        "first_half_hr": first_hr,
        "second_half_hr": second_hr,
        "strava_activity_id": activity_id,
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if updates:
        assignments = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE athlete_races SET {assignments} WHERE id = ?",
            (*updates.values(), race_id),
        )
    return updates


# ---------------------------------------------------------------------------
# Markdown rendering (race-report / vault output)
# ---------------------------------------------------------------------------

def history_to_markdown(analysis):
    """Render an analysis dict as a markdown report."""
    if not analysis.get("count"):
        return "# Historical Race Analysis\n\nNo prior races on file.\n"

    lines = ["# Historical Race Analysis — Prior Efforts", ""]
    if analysis.get("target_distance"):
        lines.append(
            f"_Races within ±{analysis['tolerance_pct']:.0f}% of "
            f"{analysis['target_distance']} mi._\n"
        )
    if analysis.get("failure_mode"):
        lines.append(f"**Dominant failure mode:** {analysis['failure_mode']}  ")
    if analysis.get("avg_fade_pct") is not None:
        lines.append(f"**Average late-race fade:** {analysis['avg_fade_pct']}%  ")
    lines.append("")

    lines.append("| Race | Date | Dist | Finish | Fade | Avg Pace | Stoppage |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in analysis["races"]:
        fade = f"+{r['fade_pct']}%" if r.get("fade_pct") is not None else "—"
        finish = r.get("finish_time") or ("DNF" if r["dnf"] else "—")
        pace = r.get("avg_pace_display") or "—"
        stop = (f"{r['stoppage_pct']}%" if r.get("stoppage_pct") is not None else "—")
        lines.append(
            f"| {r['name']} | {r['race_date']} | {r['distance_miles']} | "
            f"{finish} | {fade} | {pace} | {stop} |"
        )
    lines.append("")

    if analysis.get("lessons"):
        lines.append("## Lessons")
        for l in analysis["lessons"]:
            lines.append(f"- {l}")
        lines.append("")

    if analysis.get("training_implications"):
        lines.append("## Training Implications")
        for i in analysis["training_implications"]:
            lines.append(f"- {i}")
        lines.append("")

    return "\n".join(lines)
