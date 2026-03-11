"""Intervals.icu integration for pushing structured workouts to Coros.

Converts BR100 training plan workouts into Intervals.icu's text-based
workout description format, then creates them as planned events via the API.
Intervals.icu auto-syncs the next week of planned workouts to Coros.
"""

import os
import re
import requests
from datetime import datetime


API_BASE = "https://intervals.icu/api/v1"


def _auth():
    """Return (username, password) tuple for Basic Auth."""
    api_key = os.environ.get("INTERVALS_ICU_API_KEY")
    if not api_key:
        raise ValueError(
            "INTERVALS_ICU_API_KEY not set. "
            "Get one from intervals.icu Settings > Developer Settings"
        )
    return ("API_KEY", api_key)


def _athlete_id():
    """Return athlete ID, defaulting to '0' (authenticated user)."""
    return os.environ.get("INTERVALS_ICU_ATHLETE_ID", "0")


def workout_to_icu_description(workout_dict, targets=None):
    """Convert a BR100 workout dict into Intervals.icu description syntax.

    Returns a string using Intervals.icu's structured text format:
      - Step label duration target

    If targets dict is provided (from athlete_targets), uses adaptive HR zones
    in descriptions instead of hardcoded zone labels.
    """
    desc = workout_dict.get("description", "")
    wtype = workout_dict.get("workout_type", "easy_run")

    if not desc:
        return _default_description(workout_dict)

    lower = desc.lower()
    title_lower = workout_dict.get("title", "").lower()

    if "maf" in title_lower or ("137" in lower and "hr" in lower):
        return _maf_description(desc, workout_dict)

    if "5k" in lower and ("time trial" in lower or "all-out" in lower):
        return _5k_tt_description(desc)

    if "tempo" in lower:
        return _tempo_description(desc)

    if "hill repeat" in lower or "hill workout" in lower:
        return _hills_description(desc)

    if "strides" in lower:
        return _strides_description(desc, workout_dict)

    if wtype in ("long_run", "back_to_back"):
        return _long_run_description(workout_dict)

    return _default_description(workout_dict)


def _default_description(w):
    """Fallback: single easy run step."""
    dist = w.get("target_distance_miles")
    if dist:
        return f"- Run {dist}mi Z2 HR"
    dur = w.get("target_duration_minutes")
    if dur:
        return f"- Run {dur}m Z2 HR"
    return "- Run 30m Z2 HR"


def _maf_description(desc, w):
    """MAF test: warmup + steady MAF effort."""
    warmup_match = re.search(r"warm\s*up\s+(\d+)\s*min", desc, re.IGNORECASE)
    warmup_min = int(warmup_match.group(1)) if warmup_match else 10
    maf_min = w.get("target_duration_minutes") or 30
    lines = [
        f"- Warmup {warmup_min}m Z1 HR",
        f"- MAF Test {maf_min}m 135-139bpm HR",
    ]
    return "\n".join(lines)


def _5k_tt_description(desc):
    """5K Time Trial: warmup + 5K all-out + cooldown."""
    wu_match = re.search(r"(\d+)\s*mi\s*warmup", desc, re.IGNORECASE)
    wu_mi = float(wu_match.group(1)) if wu_match else 1.0
    cd_match = re.search(r"(\d+)\s*mi\s*cooldown", desc, re.IGNORECASE)
    cd_mi = float(cd_match.group(1)) if cd_match else 1.0
    lines = [
        f"- Warmup {wu_mi}mi Z1-Z2 HR",
        "- 5K Time Trial 5km",
        f"- Cooldown {cd_mi}mi Z1-Z2 HR",
    ]
    return "\n".join(lines)


def _tempo_description(desc):
    """Tempo run: warmup + tempo segments + cooldown."""
    lower = desc.lower()
    lines = []

    wu_match = re.search(r"(\d+)\s*mi\s*warmup", desc, re.IGNORECASE)
    wu_mi = float(wu_match.group(1)) if wu_match else 2.0
    lines.append(f"- Warmup {wu_mi}mi Z1-Z2 HR")

    # Interval tempo: NxMmi
    interval_match = re.search(r"(\d+)\s*x\s*(\d+)\s*mi", lower)
    if interval_match:
        reps = int(interval_match.group(1))
        rep_dist = float(interval_match.group(2))
        rec_match = re.search(r"(\d+)\s*min\s*(jog|recovery)", lower)
        rec_min = int(rec_match.group(1)) if rec_match else 2

        lines.append("")
        lines.append(f"{reps}x")
        lines.append(f"- Tempo {rep_dist}mi Z3 HR")
        lines.append(f"- Recovery jog {rec_min}m Z1-Z2 HR")
        lines.append("")
    else:
        # Continuous tempo
        tempo_match = re.search(r"(\d+)\s*mi\s*(at\s*)?tempo", lower)
        tempo_mi = float(tempo_match.group(1)) if tempo_match else 4.0
        lines.append(f"- Tempo {tempo_mi}mi Z3 HR")

    cd_match = re.search(r"(\d+)\s*mi\s*cooldown", desc, re.IGNORECASE)
    cd_mi = float(cd_match.group(1)) if cd_match else 1.0
    lines.append(f"- Cooldown {cd_mi}mi Z1-Z2 HR")

    return "\n".join(lines)


def _hills_description(desc):
    """Hill repeats: warmup + repeats + cooldown."""
    lower = desc.lower()
    lines = []

    wu_match = re.search(r"(\d+)\s*mi\s*warmup", desc, re.IGNORECASE)
    wu_mi = float(wu_match.group(1)) if wu_match else 2.0
    lines.append(f"- Warmup {wu_mi}mi Z1-Z2 HR")

    rep_match = re.search(r"(\d+)\s*x\s*(\d+)\s*(sec|min)", lower)
    if rep_match:
        reps = int(rep_match.group(1))
        dur = int(rep_match.group(2))
        unit = rep_match.group(3)
        dur_str = f"{dur}s" if unit == "sec" else f"{dur}m"
        rec_str = dur_str  # recovery ~same duration

        lines.append("")
        lines.append(f"{reps}x")
        lines.append(f"- Hill {dur_str} Z4 HR")
        lines.append(f"- Jog down {rec_str} Z1-Z2 HR")
        lines.append("")

    cd_match = re.search(r"(\d+)\s*mi\s*cooldown", desc, re.IGNORECASE)
    cd_mi = float(cd_match.group(1)) if cd_match else 1.0
    lines.append(f"- Cooldown {cd_mi}mi Z1-Z2 HR")

    return "\n".join(lines)


def _strides_description(desc, w):
    """Easy run + strides."""
    lower = desc.lower()
    main_match = re.search(r"(\d+)\s*mi\s*easy", lower)
    main_mi = float(main_match.group(1)) if main_match else (w.get("target_distance_miles") or 5)

    stride_match = re.search(r"(\d+)\s*x\s*100m\s*strides", lower)
    num_strides = int(stride_match.group(1)) if stride_match else 6

    lines = [
        f"- Easy run {main_mi}mi Z2 HR",
        "",
        f"{num_strides}x",
        "- Stride 100mtr Z4-Z5 HR",
        "- Jog back 100mtr Z1 HR",
    ]
    return "\n".join(lines)


def _long_run_description(w):
    """Long run: single steady effort."""
    dist = w.get("target_distance_miles") or 10
    return f"- Long run {dist}mi Z2 HR"


def create_event(workout_dict, dry_run=False):
    """Create a planned workout event in Intervals.icu.

    Returns the created event dict from the API.
    """
    icu_desc = workout_to_icu_description(workout_dict)
    title = workout_dict.get("title", "Workout")
    date = workout_dict.get("scheduled_date", datetime.now().strftime("%Y-%m-%d"))

    payload = {
        "start_date_local": f"{date}T00:00:00",
        "type": "Run",
        "category": "WORKOUT",
        "name": title,
        "description": icu_desc,
        "indoor": False,
    }

    dist = workout_dict.get("target_distance_miles")
    if dist:
        payload["distance"] = dist * 1609.34  # convert to meters

    dur = workout_dict.get("target_duration_minutes")
    if dur:
        payload["moving_time"] = dur * 60  # convert to seconds

    if dry_run:
        return {"payload": payload, "dry_run": True}

    athlete = _athlete_id()
    resp = requests.post(
        f"{API_BASE}/athlete/{athlete}/events",
        json=payload,
        auth=_auth(),
    )
    resp.raise_for_status()
    return resp.json()


def create_events_bulk(workouts, dry_run=False):
    """Create multiple planned workout events.

    Skips rest days and cross-training. Returns list of results.
    """
    skip_types = {"rest", "cross_train"}
    results = []

    for w in workouts:
        if w.get("workout_type") in skip_types:
            continue
        try:
            result = create_event(w, dry_run=dry_run)
            results.append({
                "date": w.get("scheduled_date"),
                "title": w.get("title"),
                "status": "created" if not dry_run else "dry_run",
                "event": result,
            })
        except Exception as e:
            results.append({
                "date": w.get("scheduled_date"),
                "title": w.get("title"),
                "status": "error",
                "error": str(e),
            })

    return results


def list_events(oldest, newest):
    """List planned events in a date range."""
    athlete = _athlete_id()
    resp = requests.get(
        f"{API_BASE}/athlete/{athlete}/events",
        params={"oldest": oldest, "newest": newest},
        auth=_auth(),
    )
    resp.raise_for_status()
    return resp.json()


def delete_event(event_id):
    """Delete a planned event."""
    athlete = _athlete_id()
    resp = requests.delete(
        f"{API_BASE}/athlete/{athlete}/events/{event_id}",
        auth=_auth(),
    )
    resp.raise_for_status()
    return True
