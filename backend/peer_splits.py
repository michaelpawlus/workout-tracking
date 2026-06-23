"""Peer split comparison — acquisition + analysis layer for BR100 finisher cohorts (#14).

The race engine already stores peer results (``historical_results`` / ``historical_splits``)
and analyzes a cohort near a goal time (``race_engine.analyze_cohort`` / ``race cohort``).
What was missing for #14 is the **acquisition** layer: how the cohort data gets in.

Following the project's agent-driven pattern (see CLAUDE.md, mirrors #15's
``race aggregate-reports``), the CLI does **not** scrape timing sites itself. Instead it
emits a structured *research order* — exactly which results to pull (last year's BR100
finishers near the target), which timing mats to read, and the precise CSV schema to fill —
that a Claude Code session executes (official results are agentic; Strava detail is hybrid:
the user drops a link/export and the agent parses it). The filled CSV is ingested by
``import_peer_splits_long`` and analyzed by the existing ``cohort`` command.

Key wrinkle handled here: official timing mats are **sparse** (BR100 has ~8 mats, not one
per aid station) and report **cumulative** elapsed time. ``import_peer_splits_long`` maps
each mat to the nearest course segment and distributes each mat-to-mat leg's pace across the
segments it covers, producing a per-segment pace curve ``analyze_cohort`` can median.

Public functions:

- ``build_research_order(course, segments, ...)`` — structured order (window, sources,
  queries, timing mats, output CSV schema, hybrid Strava intake).
- ``skeleton_csv(course, segments, ...)`` — a fillable long-format CSV scaffold.
- ``import_peer_splits_long(conn, course_id, csv_path, ...)`` — ingest sparse cumulative
  mat splits into ``historical_results`` / ``historical_splits``.
- ``peer_learnings_markdown(analysis, cohort, ...)`` — render cohort lessons for the vault.
"""

from __future__ import annotations

import csv
from typing import Any

from . import race_engine
from .race_engine import _format_pace, _format_time, _parse_time


DEFAULT_TARGET_FINISH = "26:00:00"
# ±30 min around the governor → the issue's ~25:30–26:30 finisher band.
DEFAULT_WINDOW_SECONDS = 30 * 60

# BR100's official timing mats (per the 2025 participant guide: Chestnut, Valley Picnic,
# Kendall Lake, Silver Springs, Front Street — read out & back). Miles are 2025-guide
# values; ``map_mat_to_segment`` snaps them onto whatever course is loaded. The agent should
# confirm/extend these against the actual results page when filling the order.
KNOWN_TIMING_MATS: list[dict[str, Any]] = [
    {"mile": 12.1, "name": "Chestnut Shelter (out)"},
    {"mile": 26.5, "name": "Valley Picnic (out)"},
    {"mile": 40.3, "name": "Kendall Lake (out)"},
    {"mile": 50.3, "name": "Silver Springs (turnaround)"},
    {"mile": 60.5, "name": "Kendall Lake (return)"},
    {"mile": 74.2, "name": "Valley Picnic (return)"},
    {"mile": 88.6, "name": "Chestnut Shelter (return)"},
    {"mile": 96.3, "name": "Schumacher (return)"},
    {"mile": 100.5, "name": "Front Street (finish)"},
]

LONG_CSV_COLUMNS = [
    "runner_name", "finish_time", "dnf", "mat_mile", "mat_name", "elapsed",
    "year", "source",
]


def _safe_seconds(value: str) -> int | None:
    """Parse an HH:MM:SS / seconds string to a positive int, else None.

    ``_parse_time`` returns 0 for word placeholders ("N/A") and *raises* on
    colon-shaped ones ("--:--"), so a single junk cell in a hand-filled CSV could
    silently zero a mat or abort the whole import. This normalizes both to None.
    """
    try:
        secs = _parse_time(value)
    except (ValueError, TypeError):
        return None
    return secs if secs > 0 else None


def map_mat_to_segment(segments: list[dict], mile: float, tol: float = 1.5) -> dict | None:
    """Snap a timing-mat mile to the course segment whose END mile is closest.

    Segments end at aid stations (after ``load-aid-stations``), so a mat sits at or just
    before a segment boundary. Returns the segment dict, or ``None`` if nothing is within
    ``tol`` miles (a mat that doesn't correspond to a loaded segment).
    """
    best = None
    best_d = tol
    for seg in segments:
        d = abs(seg["end_mile"] - mile)
        if d <= best_d:
            best_d = d
            best = seg
    return best


def build_research_order(
    course: dict,
    segments: list[dict],
    goal_seconds: int,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    target_n: int = 12,
) -> dict[str, Any]:
    """Build the structured peer-split research order grounded in the loaded course."""
    name = course["name"]
    year = course.get("year") or 0
    prior = year - 1 if year else None
    lo = goal_seconds - window_seconds
    hi = goal_seconds + window_seconds

    mats = []
    for mat in KNOWN_TIMING_MATS:
        seg = map_mat_to_segment(segments, mat["mile"])
        mats.append({
            "mile": mat["mile"],
            "name": mat["name"],
            "maps_to_segment": seg["segment_number"] if seg else None,
            "segment_name": (seg.get("name") if seg else None),
        })

    q = f'"{name}"'
    return {
        "race": {
            "name": name,
            "year": year,
            "prior_year": prior,
            "total_miles": course.get("total_distance_miles"),
        },
        "objective": (
            "Find last year's finishers near the target time and learn from their "
            "per-aid-station splits — where the field holds pace vs. blows up — to "
            "pressure-test the governor pace plan."
        ),
        "target_window": {
            "goal_time": _format_time(goal_seconds),
            "window_minutes": window_seconds // 60,
            "finish_low": _format_time(lo),
            "finish_high": _format_time(hi),
            "target_finishers": target_n,
        },
        "sources": [
            {
                "category": "Official results + timing splits (AGENTIC — primary)",
                "why": "Per-mat cumulative splits for every finisher; no Strava needed. "
                       "Highest-value, lowest-friction path. Filter to the finish window.",
                "where": [
                    "ultrasignup.com (search the race, open the results year)",
                    "RTRT.me / track.rtrt.me (live + archived timing splits)",
                    "westernreserveracingco.com / burningriver.run results link",
                ],
                "queries": [
                    f"{q} {prior} results ultrasignup" if prior else f"{q} results ultrasignup",
                    f"{q} {prior} timing splits RTRT" if prior else f"{q} timing splits",
                    f"{q} {prior} 100 mile finishers splits" if prior else f"{q} finishers splits",
                ],
            },
            {
                "category": "Strava / narrative detail (HYBRID — long tail)",
                "why": "Arbitrary athletes' Strava data is NOT cleanly agentic (API exposes "
                       "only own/friends; scraping is brittle/ToS-risky). For a specific "
                       "near-target finisher, the USER drops a Strava activity link or export "
                       "and the agent parses mile/elapsed into one runner's rows.",
                "where": ["user-provided Strava link or .gpx/.fit/.csv export"],
                "queries": [],
            },
        ],
        "timing_mats": mats,
        "output": {
            "format": "long CSV (one row per (runner, timing mat)); lines starting with # ignored",
            "columns": LONG_CSV_COLUMNS,
            "notes": [
                "elapsed = CUMULATIVE elapsed time at that mat (HH:MM:SS), not leg time.",
                "finish_time + dnf may repeat on each of a runner's rows (first non-empty wins).",
                "Omit the finish mat row if you like — the importer closes the curve to the "
                "course finish using finish_time automatically.",
                "year defaults to the --year flag; source is a free-text provenance tag "
                "(e.g. 'ultrasignup', 'rtrt', 'strava:<athlete>').",
                "Only the finish window matters — skip finishers far outside it.",
            ],
            "then": [
                "ultra race peer-splits --import <filled.csv> --year <prior>",
                f"ultra race cohort --goal-time {_format_time(goal_seconds)} --json",
                "ultra race peer-splits --learnings --vault",
            ],
        },
        "analysis_sections": [
            "Cohort overview: N finishers, finish range, median",
            "Where the field fades: back-half slowdown %, highest-variance (danger-zone) segments",
            "Pacing skeleton: median per-segment pace vs. the governor plan — where to bank vs. hold",
            "Failure-point corroboration: do peer slow segments line up with the heat/foot/night "
            "danger zones from the course guide?",
        ],
    }


def skeleton_csv(course: dict, segments: list[dict], goal_seconds: int) -> str:
    """A fillable long-format CSV scaffold with the timing-mat rows pre-listed."""
    lines = [
        f"# {course['name']} peer-split cohort — fill one block per finisher near "
        f"{_format_time(goal_seconds)}.",
        "# Long format: one row per (runner, timing mat). elapsed = cumulative HH:MM:SS.",
        "# Delete mats with no split for a given runner; the finish row is optional.",
        ",".join(LONG_CSV_COLUMNS),
    ]
    # One worked example block (placeholders) so the schema is unambiguous.
    for mat in KNOWN_TIMING_MATS:
        lines.append(
            f"Example Runner,26:00:00,0,{mat['mile']},{mat['name']},,,ultrasignup"
        )
    return "\n".join(lines) + "\n"


def import_peer_splits_long(
    conn,
    course_id: int,
    csv_path: str,
    default_year: int,
) -> dict[str, Any]:
    """Ingest sparse, cumulative mat splits (long CSV) into the historical tables.

    Groups rows by runner, snaps each mat to a segment, then distributes each mat-to-mat
    leg's average pace across the segments that leg covers (so ``analyze_cohort`` sees a
    full per-segment curve from sparse mats). Returns counts + any warnings.
    """
    segments = sorted(race_engine.get_segments(conn, course_id), key=lambda s: s["segment_number"])
    max_seg_num = segments[-1]["segment_number"] if segments else 0

    runners: dict[str, dict] = {}
    warnings: list[str] = []

    with open(csv_path, "r") as f:
        reader = csv.DictReader(r for r in f if not r.lstrip().startswith("#"))
        for row in reader:
            nm = (row.get("runner_name") or "").strip()
            if not nm:
                continue
            r = runners.setdefault(nm, {
                "finish_time": None, "dnf": 0, "year": None, "source": None, "mats": [],
            })
            ft = (row.get("finish_time") or "").strip()
            if ft and r["finish_time"] is None:
                r["finish_time"] = _safe_seconds(ft)  # None for "N/A"/"--:--"
            dnf = (row.get("dnf") or "").strip().lower()
            if dnf in ("1", "true", "yes", "dnf"):
                r["dnf"] = 1
            yr = (row.get("year") or "").strip()
            if yr and r["year"] is None:
                try:
                    r["year"] = int(yr)
                except ValueError:
                    pass
            src = (row.get("source") or "").strip()
            if src and not r["source"]:
                r["source"] = src
            mile = (row.get("mat_mile") or "").strip()
            elapsed = (row.get("elapsed") or "").strip()
            if mile and elapsed:
                try:
                    mile_f = float(mile)
                except ValueError:
                    warnings.append(f"{nm}: unparseable mat mile={mile!r}, row skipped")
                    continue
                # Placeholders ("N/A", "--:--") must skip just this mat, not zero it or
                # abort the import — see _safe_seconds.
                elapsed_s = _safe_seconds(elapsed)
                if elapsed_s is None:
                    warnings.append(f"{nm}: non-positive/unparseable elapsed={elapsed!r} "
                                    f"at mile {mile}, mat skipped")
                    continue
                r["mats"].append((mile_f, elapsed_s))

    imported = 0
    splits_written = 0
    for nm, r in runners.items():
        if r["dnf"]:
            # Store the DNF so cohort context / counts are honest, but skip splits.
            conn.execute(
                """INSERT INTO historical_results
                   (course_id, year, runner_name, finish_time_seconds, dnf)
                   VALUES (?, ?, ?, ?, 1)""",
                (course_id, r["year"] or default_year, nm, r["finish_time"] or 0),
            )
            imported += 1
            continue

        finish = r["finish_time"]
        # Snap each mat to its nearest segment BOUNDARY, then key legs off segment numbers —
        # not raw mat miles. Official mats can sit well before their segment's end mile
        # (e.g. mat 60.5 → Kendall Lake ending 61.2), so a raw-mile cutoff would mis-charge
        # that segment to the next leg and corrupt late-race paces.
        snapped = []  # (segment_number, cumulative_elapsed)
        for mat_mile, mat_elapsed in sorted(r["mats"], key=lambda m: m[0]):
            seg = map_mat_to_segment(segments, mat_mile)
            if seg is None:
                warnings.append(f"{nm}: mat at mile {mat_mile} maps to no segment, skipped")
                continue
            snapped.append((seg["segment_number"], mat_elapsed))
        # Keep the last elapsed per segment and ensure strictly increasing segment numbers.
        by_seg: dict[int, int] = {}
        for seg_num, elapsed in snapped:
            by_seg[seg_num] = elapsed
        # Cumulative times must increase with distance; a hand-filled CSV typo that goes
        # backwards (e.g. 74.2M earlier than 60.5M) would make a leg non-positive — writing
        # no splits for those segments yet still advancing past the bad mat, leaving holes
        # and distorting the next leg. Drop any mat that doesn't advance the clock.
        snapped_sorted, mono, last_e = sorted(by_seg.items()), [], 0
        for seg_num, elapsed in snapped_sorted:
            if elapsed <= last_e:
                warnings.append(f"{nm}: non-increasing cumulative time at seg {seg_num} "
                                f"({_format_time(elapsed)} ≤ previous), mat skipped")
                continue
            mono.append((seg_num, elapsed))
            last_e = elapsed
        snapped = mono
        if not snapped:
            warnings.append(f"{nm}: no usable mat splits, skipped")
            continue

        # Require real finish evidence before counting someone as a finisher: either a
        # parsed finish_time, or a mat that snaps to the final segment. Otherwise the last
        # captured mat (e.g. a 96.3M split) would be stored as the finish, sneaking an
        # incomplete runner into the cohort and skewing analyze_cohort's late-race learning.
        has_finish_mat = snapped[-1][0] == max_seg_num
        if not finish and not has_finish_mat:
            warnings.append(f"{nm}: no finish time and no finish-line mat — "
                            f"incomplete record, skipped")
            continue
        # Close the curve to the finish so segments after the last mat get a pace.
        if finish and not has_finish_mat:
            if finish <= snapped[-1][1]:
                warnings.append(f"{nm}: finish time {_format_time(finish)} is not after the "
                                f"last mat, incomplete record, skipped")
                continue
            snapped.append((max_seg_num, finish))

        cursor = conn.execute(
            """INSERT INTO historical_results
               (course_id, year, runner_name, finish_time_seconds, dnf)
               VALUES (?, ?, ?, ?, 0)""",
            (course_id, r["year"] or default_year, nm, finish or snapped[-1][1]),
        )
        result_id = cursor.lastrowid
        imported += 1

        prev_seg_num, prev_elapsed = 0, 0
        for seg_num, mat_elapsed in snapped:
            covered = [s for s in segments if prev_seg_num < s["segment_number"] <= seg_num]
            leg_dist = sum(s["distance_miles"] for s in covered)
            leg_time = mat_elapsed - prev_elapsed
            if leg_dist > 0 and leg_time > 0:
                leg_pace = leg_time / leg_dist
                for seg in covered:
                    sp = int(round(leg_pace * seg["distance_miles"]))
                    conn.execute(
                        """INSERT INTO historical_splits
                           (result_id, segment_id, split_time_seconds, pace_per_mile_seconds)
                           VALUES (?, ?, ?, ?)""",
                        (result_id, seg["id"], sp, int(round(leg_pace))),
                    )
                    splits_written += 1
            prev_seg_num, prev_elapsed = seg_num, mat_elapsed

    return {
        "imported": imported,
        "runners": list(runners.keys()),
        "splits_written": splits_written,
        "warnings": warnings,
    }


def peer_learnings_markdown(analysis: dict, cohort: list[dict], goal_display: str) -> str:
    """Render the cohort analysis as a vault-ready learnings report."""
    n = analysis.get("cohort_size", 0)
    lines = [
        f"# Peer Split Learnings — {analysis.get('goal_time', goal_display)} cohort",
        "",
        f"**Cohort:** {n} finishers within ±{analysis.get('window_hours', 0.5)} h of "
        f"{analysis.get('goal_time', goal_display)}.",
    ]
    if n == 0:
        lines.append("")
        lines.append("> No finishers in the window yet. Run the research order, fill the CSV, "
                     "and `peer-splits --import` first.")
        return "\n".join(lines) + "\n"

    # Flag divergence RELATIVELY (coefficient of variation = stdev / median pace), not by
    # the engine's absolute 60s threshold — at 15+ min/mi ultra pace every segment trips an
    # absolute cutoff. A segment is a "divergence point" if its CV is in the cohort's top
    # quartile: that's where finishers actually separate.
    scored = [s for s in analysis["segments"] if s.get("median_pace_seconds")]
    for s in scored:
        sd_s = s.get("pace_stdev_seconds") or 0
        s["_cv"] = sd_s / s["median_pace_seconds"] if s["median_pace_seconds"] else 0
    cvs = sorted((s["_cv"] for s in scored), reverse=True)
    cv_cut = cvs[max(0, len(cvs) // 4 - 1)] if cvs else 0
    diverge = [s for s in scored if s["_cv"] >= cv_cut and s["_cv"] > 0]

    lines += [
        f"**Finish range:** {analysis['fastest_finish']} – {analysis['slowest_finish']} "
        f"(median {analysis['median_finish_time']}).",
        "",
        "## Where the field fades",
        "",
    ]
    sd = analysis.get("slowdown_pct")
    if sd is not None:
        lines.append(f"- **Back-half slowdown:** {sd}% — the cohort's second-half pace is "
                     f"{sd}% slower than the first half. Plan for it; don't fight it.")
    if diverge:
        names = ", ".join(f"{s['segment_name']} (seg {s['segment_number']})"
                          for s in sorted(diverge, key=lambda x: -x["_cv"])[:6])
        lines.append(f"- **Highest-divergence segments (where finishes separate most):** {names}. "
                     "Execute these deliberately — they decide the day more than the average mile.")
    lines += ["", "## Pacing skeleton (median per-segment)", "",
              "| Seg | Station | Dist | Median pace | StdDev | Diverge |",
              "|----:|---------|-----:|------------:|-------:|:-------:|"]
    for s in scored:
        flag = "⚠️" if s in diverge else ""
        lines.append(
            f"| {s['segment_number']} | {s['segment_name']} | {s['distance_miles']:.1f} | "
            f"{s['median_pace_display']} | {s['pace_stdev_seconds'] or 0:.0f}s | {flag} |"
        )
    lines += [
        "",
        "## Cohort finishers",
        "",
    ]
    for r in cohort:
        lines.append(f"- {r['runner_name']} — {_format_time(r['finish_time_seconds'])}")
    lines += [
        "",
        "> Compare this skeleton against the governor crew manual: bank time only where the "
        "cohort holds pace; protect the high-divergence segments and the back-half fade.",
    ]
    return "\n".join(lines) + "\n"
