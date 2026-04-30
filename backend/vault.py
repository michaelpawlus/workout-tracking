"""Obsidian vault integration for run reports.

Three public functions:

- ``render_run_report(...)`` — produce the markdown body for a run report.
- ``write_run_report_to_vault(...)`` — persist that markdown into ``$OBSIDIAN_VAULT_PATH/workouts/``.
- ``append_product_log_entry(...)`` — append a session entry to ``workouts/PRODUCT_LOG.md``.

The writer prefers the ``oj`` CLI from the obsidian_journal project (it owns frontmatter
shape and atomic writes) and falls back to a direct file write if ``oj`` is missing or
errors out. Either way the note ends up at ``workouts/<filename>.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


OJ_BINARY = "/home/michaelpawlus/projects/obsidian_journal/.venv/bin/oj"
WORKOUTS_SUBDIR = "workouts"
PRODUCT_LOG_FILENAME = "PRODUCT_LOG.md"
JOURNAL_SUBDIR = "Journal"

PRODUCT_LOG_HEADER = """# Adaptive Coaching Product Log

Observations from dogfooding an AI running coach. Each entry captures what worked, what didn't, and what a productized version would need.

---

## Session Log

*After each run report, note product-relevant observations below.*
"""


class VaultError(Exception):
    """Raised when vault operations cannot proceed (e.g. missing OBSIDIAN_VAULT_PATH)."""


def _vault_root() -> Path:
    path = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not path:
        raise VaultError(
            "OBSIDIAN_VAULT_PATH is not set. Set it in ~/.bashrc to enable vault writes."
        )
    root = Path(path)
    if not root.exists():
        raise VaultError(f"OBSIDIAN_VAULT_PATH points to a missing directory: {root}")
    return root


def _workouts_dir() -> Path:
    out = _vault_root() / WORKOUTS_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def _slugify_title_segment(text: str, max_words: int = 6) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 \-]+", "", text or "").strip()
    if not cleaned:
        return ""
    words = cleaned.split()[:max_words]
    return " ".join(w.capitalize() for w in words)


def _classify_run_type(prescribed: dict | None, actual: dict | None) -> str:
    """Pick a short human label for the filename, e.g. 'Easy Run', 'Long Run'."""
    title = (prescribed or {}).get("title") or ""
    intensity = (prescribed or {}).get("intensity") or ""
    workout_type = (prescribed or {}).get("workout_type") or ""

    haystack = f"{title} {intensity} {workout_type}".lower()
    if "long" in haystack:
        return "Long Run"
    if "tempo" in haystack:
        return "Tempo Run"
    if "interval" in haystack or "speed" in haystack:
        return "Interval Run"
    if "recovery" in haystack:
        return "Recovery Run"
    if "race" in haystack or "time trial" in haystack or "tt" in haystack.split():
        return "Time Trial"
    if "easy" in haystack:
        return "Easy Run"

    distance = (actual or {}).get("distance_miles") or (prescribed or {}).get("target_distance_miles")
    if distance and distance >= 13:
        return "Long Run"
    return "Run"


def _build_filename(run_date: str, run_type: str, description: str | None) -> str:
    desc = _slugify_title_segment(description or "", max_words=6) or "Training Analysis"
    safe = re.sub(r"\s+", " ", f"{run_date} {run_type} {desc}").strip()
    safe = re.sub(r"[\\/:*?\"<>|]", "", safe)
    return f"{safe}.md"


def _fmt_pace(pace: float | None) -> str:
    if pace is None:
        return "N/A"
    minutes = int(pace)
    seconds = int(round((pace - minutes) * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}/mi"


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "N/A"
    return f"{value}{suffix}"


def render_run_report(
    *,
    run_date: str,
    prescribed: dict | None,
    actual: dict | None,
    feedback: dict | None,
    nutrition: dict | None = None,
    weekly_context: dict | None = None,
    notes: str | None = None,
) -> str:
    """Render a run report as markdown.

    Sections: Prescribed, Actual, Coaching Feedback, Nutrition, Notes. Sections with
    no data are omitted so the output stays readable. Always includes an actual section
    so the report is never empty.
    """
    prescribed = prescribed or {}
    actual = actual or {}
    feedback = feedback or {}

    lines: list[str] = []

    title = prescribed.get("title") or "Unscheduled run"
    lines.append(f"## {title}")
    lines.append("")
    lines.append(f"- Date: {run_date}")
    if prescribed.get("scheduled_date") and prescribed.get("scheduled_date") != run_date:
        lines.append(f"- Scheduled date: {prescribed['scheduled_date']}")
    if weekly_context:
        wk = weekly_context.get("week_number")
        wt = weekly_context.get("week_type")
        if wk:
            label = f"Week {wk}"
            if wt:
                label += f" ({wt})"
            lines.append(f"- {label}")
    lines.append("")

    # Prescribed
    if any(prescribed.get(k) is not None for k in ("target_distance_miles", "target_duration_minutes",
                                                    "target_pace_min_per_mile", "target_hr_zone",
                                                    "intensity", "description")):
        lines.append("## Prescribed")
        lines.append("")
        if prescribed.get("description"):
            lines.append(prescribed["description"])
            lines.append("")
        if prescribed.get("target_distance_miles") is not None:
            lines.append(f"- Distance: {_fmt(prescribed['target_distance_miles'], ' mi')}")
        if prescribed.get("target_duration_minutes") is not None:
            lines.append(f"- Duration: {_fmt(prescribed['target_duration_minutes'], ' min')}")
        if prescribed.get("target_pace_min_per_mile") is not None:
            lines.append(f"- Pace: {_fmt_pace(prescribed['target_pace_min_per_mile'])}")
        if prescribed.get("target_hr_zone"):
            lines.append(f"- HR Zone: {prescribed['target_hr_zone']}")
        if prescribed.get("intensity"):
            lines.append(f"- Intensity: {prescribed['intensity']}")
        lines.append("")

    # Actual
    lines.append("## Actual")
    lines.append("")
    lines.append(f"- Distance: {_fmt(actual.get('distance_miles'), ' mi')}")
    lines.append(f"- Duration: {_fmt(actual.get('duration_minutes'), ' min')}")
    lines.append(f"- Pace: {_fmt_pace(actual.get('avg_pace_min_per_mile'))}")
    if actual.get("avg_heart_rate") is not None:
        lines.append(f"- Avg HR: {_fmt(actual['avg_heart_rate'], ' bpm')}")
    if actual.get("max_heart_rate") is not None:
        lines.append(f"- Max HR: {_fmt(actual['max_heart_rate'], ' bpm')}")
    if actual.get("elevation_gain_ft") is not None:
        lines.append(f"- Elevation Gain: {_fmt(actual['elevation_gain_ft'], ' ft')}")
    if actual.get("effort_rating") is not None:
        lines.append(f"- Effort (RPE): {actual['effort_rating']}/10")
    lines.append("")

    # Coaching feedback
    has_feedback = any(feedback.get(k) for k in (
        "compliance_score", "overall_feedback", "pace_feedback", "hr_feedback",
        "distance_feedback", "race_readiness", "warnings",
    ))
    if has_feedback:
        lines.append("## Coaching Feedback")
        lines.append("")
        if feedback.get("compliance_score") is not None:
            lines.append(f"- Compliance Score: {feedback['compliance_score']}/100")
        if feedback.get("race_readiness"):
            lines.append(f"- Race Readiness: {feedback['race_readiness']}")
        if feedback.get("compliance_score") is not None or feedback.get("race_readiness"):
            lines.append("")
        if feedback.get("overall_feedback"):
            lines.append("### Overall")
            lines.append(feedback["overall_feedback"])
            lines.append("")
        if feedback.get("pace_feedback"):
            lines.append("### Pace")
            lines.append(feedback["pace_feedback"])
            lines.append("")
        if feedback.get("hr_feedback"):
            lines.append("### Heart Rate")
            lines.append(feedback["hr_feedback"])
            lines.append("")
        if feedback.get("distance_feedback"):
            lines.append("### Distance")
            lines.append(feedback["distance_feedback"])
            lines.append("")
        warnings = feedback.get("warnings") or []
        if warnings:
            lines.append("### Warnings")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

    # Nutrition
    if nutrition and any(nutrition.get(k) for k in (
        "pre_meal", "during_fuel", "during_hydration", "post_meal", "nutrition_notes",
    )):
        lines.append("## Nutrition")
        lines.append("")
        if nutrition.get("pre_meal"):
            lines.append(f"- Pre-run: {nutrition['pre_meal']}")
        if nutrition.get("during_fuel"):
            lines.append(f"- During fuel: {nutrition['during_fuel']}")
        if nutrition.get("during_hydration"):
            lines.append(f"- During hydration: {nutrition['during_hydration']}")
        if nutrition.get("post_meal"):
            lines.append(f"- Post-run: {nutrition['post_meal']}")
        if nutrition.get("nutrition_notes"):
            lines.append("")
            lines.append(f"_Notes:_ {nutrition['nutrition_notes']}")
        lines.append("")

    if notes:
        lines.append("## Notes")
        lines.append("")
        lines.append(notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _frontmatter(run_date: str, run_type: str) -> str:
    tag = run_type.lower().replace(" ", "-")
    return (
        "---\n"
        f"date: '{run_date}'\n"
        "tags:\n"
        "- workout\n"
        f"- workout/{tag}\n"
        "type: workout-report\n"
        "---\n\n"
    )


def _try_oj_capture(body: str) -> tuple[bool, str | None]:
    """Try to capture via ``oj``. Returns (ok, journal_path_or_None)."""
    if not Path(OJ_BINARY).exists() and not shutil.which("oj"):
        return False, None
    binary = OJ_BINARY if Path(OJ_BINARY).exists() else "oj"

    try:
        proc = subprocess.run(
            [binary, "--json", "journal", "-t", "free-form", "-q", body],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, None

    if proc.returncode != 0:
        return False, None

    out = (proc.stdout or "").strip()
    if not out:
        return True, None
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return True, None
    path = payload.get("path") or payload.get("note_path") or payload.get("file")
    return True, path


def write_run_report_to_vault(
    *,
    run_date: str,
    prescribed: dict | None,
    actual: dict | None,
    feedback: dict | None,
    nutrition: dict | None = None,
    weekly_context: dict | None = None,
    notes: str | None = None,
    description: str | None = None,
    use_oj: bool = True,
) -> dict:
    """Render a run report and write it to ``workouts/`` in the Obsidian vault.

    Tries the ``oj`` CLI first (so frontmatter conventions stay consistent across
    projects) then moves the resulting note from ``Journal/`` to ``workouts/`` with
    the canonical filename. Falls back to a direct file write when ``oj`` is unavailable
    or fails.

    Returns ``{"path": <absolute md path>, "method": "oj"|"direct", "filename": ...}``.
    Raises ``VaultError`` if the vault path itself is unusable.
    """
    workouts_dir = _workouts_dir()  # raises VaultError if env unset/missing
    body = render_run_report(
        run_date=run_date, prescribed=prescribed, actual=actual,
        feedback=feedback, nutrition=nutrition,
        weekly_context=weekly_context, notes=notes,
    )

    run_type = _classify_run_type(prescribed, actual)
    desc_source = description
    if not desc_source:
        title = (prescribed or {}).get("title") or ""
        # Strip a leading run-type word if it duplicates run_type (e.g. "Easy Run 4mi")
        desc_source = re.sub(r"^(Easy|Long|Tempo|Recovery|Interval)\s+Run\s*", "", title, flags=re.I).strip()
    if not desc_source and weekly_context and weekly_context.get("week_number"):
        desc_source = f"Week {weekly_context['week_number']} Training Analysis"
    filename = _build_filename(run_date, run_type, desc_source or "Training Analysis")
    target = workouts_dir / filename

    if use_oj:
        ok, journal_path = _try_oj_capture(body)
        if ok:
            # oj writes to Journal/ by default; move it into workouts/.
            src: Path | None = None
            if journal_path:
                jp = Path(journal_path)
                if jp.exists():
                    src = jp
            if src is None:
                # Fall back: scan Journal/ for the most recently written free-form note.
                journal_dir = _vault_root() / JOURNAL_SUBDIR
                if journal_dir.exists():
                    candidates = sorted(
                        journal_dir.glob("*.md"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if candidates:
                        src = candidates[0]
            if src and src.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(target))
                return {"path": str(target), "method": "oj", "filename": filename}
            # oj said ok but we can't find the note — fall through to direct write.

    # Direct fallback
    target.write_text(_frontmatter(run_date, run_type) + body, encoding="utf-8")
    return {"path": str(target), "method": "direct", "filename": filename}


def append_product_log_entry(
    *,
    run_date: str,
    summary: str,
    insight: str,
    title: str | None = None,
) -> dict:
    """Append a session entry to ``workouts/PRODUCT_LOG.md``.

    Creates the file with the standard header if it does not exist. Each entry has the
    shape used elsewhere in the log: ``### YYYY-MM-DD — Title`` with ``**What happened:**``
    and ``**Product insight:**`` paragraphs.
    """
    workouts_dir = _workouts_dir()
    log_path = workouts_dir / PRODUCT_LOG_FILENAME

    if not log_path.exists():
        log_path.write_text(PRODUCT_LOG_HEADER, encoding="utf-8")

    heading = f"### {run_date}"
    if title:
        heading = f"{heading} — {title}"

    entry = (
        f"\n{heading}\n\n"
        f"**What happened:** {summary.strip()}\n\n"
        f"**Product insight:** {insight.strip()}\n"
    )

    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)

    return {"path": str(log_path), "appended": True}
