"""Race-report aggregator — research-brief builder for BR100 course/strategy intel.

This module embodies the project's agent-driven pattern (see CLAUDE.md): the CLI does
**not** call an LLM to synthesize the guide. Instead it emits a structured *research
brief* — a repeatable "research order" grounded in the loaded course — that a Claude Code
session executes with the deep-research harness (fan-out search → fetch → cross-verify →
synthesize with citations). The synthesized guide is then persisted to the Obsidian vault
via ``vault.write_race_intel_to_vault``.

Public functions:

- ``build_research_brief(course, ...)`` — structured brief (sources, queries, output
  sections, synthesis method) grounded in the course metadata.
- ``render_guide_skeleton(brief)`` — a markdown skeleton of the output sections, usable
  as a fallback artifact or a scaffold for the agent to fill.
"""

from __future__ import annotations

from typing import Any


# BR100 lives in Cuyahoga Valley National Park / Summit & Cuyahoga counties, NE Ohio,
# run in late July. These defaults ground weather/region queries when not derivable
# from the course row.
DEFAULT_REGION = "Cuyahoga Valley National Park, Summit County, Northeast Ohio"
DEFAULT_RACE_DATE = "2026-07-26"
DEFAULT_TARGET_FINISH = "26:00:00"


def _source_categories(name: str, year: int) -> list[dict[str, Any]]:
    """Concrete, grounded search queries per source category from issue #15."""
    prior = year - 1
    q = name  # already quoted by callers where needed
    return [
        {
            "category": "Official race site & athlete guide",
            "why": "Authoritative aid-station list, mile markers, crew-access rules, "
                   "drop-bag locations, cutoffs, and course map. Highest-trust source.",
            "where": ["burningriver.run", "westernreserveracingco.com", "ultrasignup.com"],
            "queries": [
                f'"{q}" {year} athlete guide',
                f'"{q}" aid station list crew access drop bags',
                f'"{q}" course map elevation profile cutoffs',
                f'"{q}" {year} runner manual',
            ],
        },
        {
            "category": "Race coverage & results media",
            "why": "Course narrative, terrain character, and how the race actually plays "
                   "out near the front and middle of the pack.",
            "where": ["irunfar.com", "ultrarunning.com", "trailrunnermag.com"],
            "queries": [
                f'"{q}" race report',
                f'"{q}" {prior} results recap',
                f'"{q}" 100 mile course preview',
            ],
        },
        {
            "category": "UltraSignup reviews & ratings",
            "why": "Crowd-sourced difficulty signal and recurring complaints "
                   "(heat, towpath monotony, night sections, specific climbs).",
            "where": ["ultrasignup.com"],
            "queries": [
                f'"{q}" UltraSignup reviews',
                f'"{q}" course difficulty review',
            ],
        },
        {
            "category": "Reddit r/ultrarunning trip reports",
            "why": "Candid, recent first-person failure modes and pacing/fueling lessons.",
            "where": ["reddit.com/r/ultrarunning", "reddit.com/r/trailrunning"],
            "queries": [
                f'"{q}" site:reddit.com race report',
                f"{q} reddit trip report DNF",
                f"{q} reddit heat humidity night",
            ],
        },
        {
            "category": "Personal blogs & race reports",
            "why": "Segment-by-segment narrative, drop-bag contents, crew logistics, and "
                   "the insider notes runners wish they'd had.",
            "where": ["blogspot.com", "wordpress.com", "medium.com", "strava.com"],
            "queries": [
                f'"{q}" 100 race report blog',
                f'"{q}" pacing strategy crew plan',
                f'"{q}" first 100 miler report',
            ],
        },
        {
            "category": "YouTube race recaps & course previews",
            "why": "Visual terrain reconnaissance for the major climbs, towpath/road "
                   "stretches, and night sections.",
            "where": ["youtube.com"],
            "queries": [
                f'"{q}" race recap',
                f'"{q}" course preview',
                f'"{q}" 100 miler vlog',
            ],
        },
        {
            "category": "Late-July NE Ohio weather history",
            "why": "Heat/humidity and overnight-low patterns drive fueling, sodium, "
                   "and night-kit decisions — and BR's signature mid-pack failure mode.",
            "where": ["weather.gov", "wunderground.com", "weatherspark.com"],
            "queries": [
                f"{DEFAULT_REGION} late July temperature humidity dew point history",
                "Akron Ohio July 26 average high low temperature dew point",
                "Cuyahoga Valley July heat index overnight low historical",
            ],
        },
    ]


def _output_sections() -> list[dict[str, str]]:
    """The sections the synthesized guide must contain (issue #15 Output)."""
    return [
        {"heading": "Race Overview",
         "what": "Distance, total climb, terrain mix (towpath/road vs singletrack), "
                 "start time, time limit, and key cutoffs."},
        {"heading": "Aid Station Table",
         "what": "Every aid station: name, mile, distance from previous, climb between "
                 "stations, crew access (Y/N), drop bag (Y/N), pacer rules."},
        {"heading": "Crew & Drop-Bag Logistics",
         "what": "Which stations crew can reach, parking/access notes, drive times "
                 "between crew points, and recommended drop-bag placement."},
        {"heading": "Course Sections & Terrain",
         "what": "Section-by-section character — the major climbs, the towpath/road "
                 "stretches, technical singletrack, and where the runnable miles are."},
        {"heading": "Common Failure Points",
         "what": "Where runners blow up: major climbs, late-July heat/humidity, the "
                 "night sections, low patches, and the towpath/road monotony."},
        {"heading": "Weather Patterns (late July)",
         "what": "Typical highs/lows, dew point/humidity, overnight conditions, and "
                 "storm risk — with the gear and fueling implications."},
        {"heading": "Gear & Drop-Bag Implications",
         "what": "Night kit (lights, layers), heat management, shoe/sock strategy for "
                 "towpath vs trail, and what to stage in each drop bag."},
        {"heading": "Course-Knowledge Notes",
         "what": "The insider tips runners say they wish they'd had — landmark cues, "
                 "where to bank time, where to hold back."},
        {"heading": "Sources & Confidence",
         "what": "Cited source URLs, plus explicit flags for single-source or "
                 "contradictory claims and any gaps the research could not close."},
    ]


def _method() -> list[str]:
    """Deep-research harness steps the executing agent should follow."""
    return [
        "Fan out web searches across every source category below (use the queries verbatim, then iterate).",
        "Fetch the highest-signal pages first: the official athlete guide, recent race reports, UltraSignup reviews.",
        "Cross-verify every aid-station name / mile / crew flag against >=2 independent sources; flag single-source claims.",
        "Synthesize into the output sections; cite each non-obvious claim with its source URL inline.",
        "Call out contradictions, stale (pre-reroute) course info, and gaps explicitly — do not paper over uncertainty.",
        "Save the finished guide to the vault: `ultra race aggregate-reports --save-guide -` (markdown on stdin).",
    ]


def build_research_brief(
    course: dict,
    *,
    race_date: str | None = None,
    target_finish: str = DEFAULT_TARGET_FINISH,
    segments_named: bool | None = None,
) -> dict[str, Any]:
    """Build a structured research brief grounded in the loaded course.

    ``course`` is a row dict from ``race_engine.get_course`` (keys: name, year,
    total_distance_miles, total_elevation_gain_ft). ``segments_named`` lets the caller
    flag that the loaded segments still lack aid-station names so the brief can note the
    feed-forward into ``race segments`` (and the crew manual, issue #12).
    """
    name = course["name"]
    year = course["year"]

    brief: dict[str, Any] = {
        "race": {
            "name": name,
            "year": year,
            "race_date": race_date or DEFAULT_RACE_DATE,
            "region": DEFAULT_REGION,
            "distance_miles": course.get("total_distance_miles"),
            "elevation_gain_ft": course.get("total_elevation_gain_ft"),
            "target_finish": target_finish,
        },
        "objective": (
            f"Gather all available public {name} intel and synthesize one comprehensive "
            f"course/strategy guide (target finish {target_finish}). Output feeds the "
            "capstone race-strategy report (issue #16) and can populate aid-station names, "
            "crew flags, and drop-bag data for `race segments` / the crew manual (issue #12)."
        ),
        "method": _method(),
        "sources": _source_categories(name, year),
        "output_sections": _output_sections(),
    }

    if segments_named is False:
        brief["feed_forward"] = (
            "The loaded course segments are unnamed and have no crew/drop-bag flags. "
            "Capture the aid-station name + mile + crew/drop-bag for each station so they "
            "can be written back via `ultra race segments --segment N --set-name ... "
            "--crew 1 --drop-bag 1`."
        )

    return brief


def render_guide_skeleton(brief: dict[str, Any]) -> str:
    """Render a markdown skeleton of the guide's output sections.

    Useful as a fallback artifact or a scaffold the agent fills in. The real content is
    produced by the deep-research pass, not by this function.
    """
    race = brief["race"]
    lines: list[str] = []
    lines.append(f"# {race['name']} — Course & Strategy Guide")
    lines.append("")
    dist = race.get("distance_miles")
    gain = race.get("elevation_gain_ft")
    meta_bits = [f"{race['year']}", race.get("region", "")]
    if dist:
        meta_bits.append(f"{dist:g} mi")
    if gain:
        meta_bits.append(f"{gain:,.0f} ft climb")
    meta_bits.append(f"target {race.get('target_finish')}")
    lines.append("*" + " · ".join(b for b in meta_bits if b) + "*")
    lines.append("")
    lines.append("> Skeleton only — fill each section from the research brief "
                 "(`ultra race aggregate-reports --json`).")
    lines.append("")
    for section in brief["output_sections"]:
        lines.append(f"## {section['heading']}")
        lines.append("")
        lines.append(f"_{section['what']}_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
