"""Generate FIT workout files for Coros Apex 2 Pro.

Parses workout descriptions from the BR100 training plan and creates
structured FIT files with HR/pace targets and step-by-step guidance.
"""

import os
import re
from datetime import datetime

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.workout_message import WorkoutMessage
from fit_tool.profile.messages.workout_step_message import WorkoutStepMessage
from fit_tool.profile.profile_type import (
    FileType, Sport, SubSport, Intensity, WorkoutStepDuration, WorkoutStepTarget, Manufacturer,
)

# HR zone boundaries (bpm) — offset by +100 for FIT custom HR targets
HR_ZONES = {
    1: (100, 120),   # Recovery
    2: (120, 137),   # MAF / Aerobic
    3: (137, 155),   # Tempo
    4: (155, 170),   # Threshold
    5: (170, 195),   # VO2max
}

# Pace zones (min/mi → m/s for FIT speed targets)
def _pace_to_speed(pace_min_per_mile):
    """Convert min/mi pace to m/s * 1000 (FIT speed unit)."""
    if pace_min_per_mile <= 0:
        return 0
    meters_per_mile = 1609.34
    seconds = pace_min_per_mile * 60
    return int((meters_per_mile / seconds) * 1000)


def _miles_to_cm(miles):
    """Convert miles to centimeters (FIT distance unit for duration)."""
    return int(miles * 1609.34 * 100)


def _minutes_to_ms(minutes):
    """Convert minutes to milliseconds (FIT time unit for duration)."""
    return int(minutes * 60 * 1000)


def _resolve_hr_zones(hr_zones=None):
    """Return HR zone dict, using overrides if provided."""
    if hr_zones:
        merged = dict(HR_ZONES)
        merged.update(hr_zones)
        return merged
    return HR_ZONES


def parse_workout_segments(description, workout_type, target_distance=None,
                           target_duration=None, hr_zones=None):
    """Parse workout description into structured segments.

    Returns list of dicts with keys:
        name, intensity, duration_type, duration_value,
        target_type, target_hr_low, target_hr_high, target_speed_low, target_speed_high

    If hr_zones is provided, overrides the module-level HR_ZONES defaults.
    """
    if not description:
        return [_default_step(workout_type, target_distance, target_duration, hr_zones=hr_zones)]

    zones = _resolve_hr_zones(hr_zones)
    desc = description.lower()

    # MAF test
    if "maf" in desc and ("137" in desc or "maf" in desc):
        return _parse_maf(description, target_duration, zones)

    # 5K Time Trial
    if "5k" in desc and ("time trial" in desc or "all-out" in desc):
        return _parse_5k_tt(description, zones)

    # Tempo runs: "Xmi at tempo" or "Nx1mi at tempo"
    if "tempo" in desc:
        return _parse_tempo(description, target_distance, zones)

    # Hill repeats
    if "hill repeat" in desc or "hill workout" in desc:
        return _parse_hills(description, zones)

    # Easy + strides
    if "strides" in desc:
        return _parse_easy_strides(description, target_distance, zones)

    # Long run
    if workout_type == "long_run":
        return _parse_long_run(description, target_distance, zones)

    # Back-to-back
    if workout_type == "back_to_back":
        return _parse_long_run(description, target_distance, zones)

    return [_default_step(workout_type, target_distance, target_duration, hr_zones=hr_zones)]


def _default_step(workout_type, distance=None, duration=None, hr_zones=None):
    """Fallback: single active step."""
    zones = _resolve_hr_zones(hr_zones)
    step = {
        "name": workout_type.replace("_", " ").title(),
        "intensity": "ACTIVE",
        "target_type": "HEART_RATE",
        "target_hr_low": zones[2][0],
        "target_hr_high": zones[2][1],
    }
    if distance:
        step["duration_type"] = "DISTANCE"
        step["duration_value"] = _miles_to_cm(distance)
    elif duration:
        step["duration_type"] = "TIME"
        step["duration_value"] = _minutes_to_ms(duration)
    else:
        step["duration_type"] = "OPEN"
        step["duration_value"] = 0
    return step


def _parse_maf(description, target_duration, zones=None):
    """MAF test: warmup + steady MAF effort."""
    zones = zones or HR_ZONES
    steps = []
    warmup_match = re.search(r"warm\s*up\s+(\d+)\s*min", description, re.IGNORECASE)
    warmup_min = int(warmup_match.group(1)) if warmup_match else 10
    steps.append({
        "name": "Warmup",
        "intensity": "WARMUP",
        "duration_type": "TIME",
        "duration_value": _minutes_to_ms(warmup_min),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[1][1],
    })
    maf_min = (target_duration or 30)
    steps.append({
        "name": "MAF Test",
        "intensity": "ACTIVE",
        "duration_type": "TIME",
        "duration_value": _minutes_to_ms(maf_min),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[2][0] - 2,
        "target_hr_high": zones[2][1] + 2,
    })
    return steps


def _parse_5k_tt(description, zones=None):
    """5K Time Trial: warmup + 5K all-out + cooldown."""
    zones = zones or HR_ZONES
    steps = []
    wu_match = re.search(r"(\d+)\s*mi\s*warmup", description, re.IGNORECASE)
    wu_miles = float(wu_match.group(1)) if wu_match else 1.0
    steps.append({
        "name": "Warmup",
        "intensity": "WARMUP",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(wu_miles),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[2][1],
    })
    steps.append({
        "name": "5K Time Trial",
        "intensity": "ACTIVE",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(3.1),
        "target_type": "OPEN",
    })
    cd_match = re.search(r"(\d+)\s*mi\s*cooldown", description, re.IGNORECASE)
    cd_miles = float(cd_match.group(1)) if cd_match else 1.0
    steps.append({
        "name": "Cooldown",
        "intensity": "COOLDOWN",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(cd_miles),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[2][1],
    })
    return steps


def _parse_tempo(description, target_distance, zones=None):
    """Tempo run: warmup + tempo segments + cooldown."""
    zones = zones or HR_ZONES
    steps = []
    desc = description.lower()

    wu_match = re.search(r"(\d+)\s*mi\s*warmup", description, re.IGNORECASE)
    wu_miles = float(wu_match.group(1)) if wu_match else 2.0
    steps.append({
        "name": "Warmup",
        "intensity": "WARMUP",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(wu_miles),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[2][1],
    })

    interval_match = re.search(r"(\d+)\s*x\s*(\d+)\s*mi", desc)
    if interval_match:
        reps = int(interval_match.group(1))
        rep_dist = float(interval_match.group(2))
        for i in range(reps):
            steps.append({
                "name": f"Tempo {i+1}/{reps}",
                "intensity": "ACTIVE",
                "duration_type": "DISTANCE",
                "duration_value": _miles_to_cm(rep_dist),
                "target_type": "HEART_RATE",
                "target_hr_low": zones[3][0],
                "target_hr_high": zones[3][1],
            })
            if i < reps - 1:
                rec_match = re.search(r"(\d+)\s*min\s*(jog|recovery)", desc)
                rec_min = int(rec_match.group(1)) if rec_match else 2
                steps.append({
                    "name": "Recovery Jog",
                    "intensity": "RECOVERY",
                    "duration_type": "TIME",
                    "duration_value": _minutes_to_ms(rec_min),
                    "target_type": "HEART_RATE",
                    "target_hr_low": zones[1][0],
                    "target_hr_high": zones[2][1],
                })
    else:
        tempo_match = re.search(r"(\d+)\s*mi\s*(at\s*)?tempo", desc)
        tempo_miles = float(tempo_match.group(1)) if tempo_match else 4.0
        steps.append({
            "name": "Tempo",
            "intensity": "ACTIVE",
            "duration_type": "DISTANCE",
            "duration_value": _miles_to_cm(tempo_miles),
            "target_type": "HEART_RATE",
            "target_hr_low": zones[3][0],
            "target_hr_high": zones[3][1],
        })

    cd_match = re.search(r"(\d+)\s*mi\s*cooldown", description, re.IGNORECASE)
    cd_miles = float(cd_match.group(1)) if cd_match else 1.0
    steps.append({
        "name": "Cooldown",
        "intensity": "COOLDOWN",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(cd_miles),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[2][1],
    })
    return steps


def _parse_hills(description, zones=None):
    """Hill repeats: warmup + repeats + cooldown."""
    zones = zones or HR_ZONES
    steps = []
    desc = description.lower()

    wu_match = re.search(r"(\d+)\s*mi\s*warmup", description, re.IGNORECASE)
    wu_miles = float(wu_match.group(1)) if wu_match else 2.0
    steps.append({
        "name": "Warmup",
        "intensity": "WARMUP",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(wu_miles),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[2][1],
    })

    rep_match = re.search(r"(\d+)\s*x\s*(\d+)\s*(sec|min)", desc)
    if rep_match:
        reps = int(rep_match.group(1))
        dur = int(rep_match.group(2))
        unit = rep_match.group(3)
        dur_min = dur / 60 if unit == "sec" else dur

        for i in range(reps):
            steps.append({
                "name": f"Hill {i+1}/{reps}",
                "intensity": "ACTIVE",
                "duration_type": "TIME",
                "duration_value": _minutes_to_ms(dur_min),
                "target_type": "HEART_RATE",
                "target_hr_low": zones[4][0],
                "target_hr_high": zones[4][1],
            })
            steps.append({
                "name": "Jog Down",
                "intensity": "RECOVERY",
                "duration_type": "TIME",
                "duration_value": _minutes_to_ms(dur_min),
                "target_type": "HEART_RATE",
                "target_hr_low": zones[1][0],
                "target_hr_high": zones[2][1],
            })

    cd_match = re.search(r"(\d+)\s*mi\s*cooldown", description, re.IGNORECASE)
    cd_miles = float(cd_match.group(1)) if cd_match else 1.0
    steps.append({
        "name": "Cooldown",
        "intensity": "COOLDOWN",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(cd_miles),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[1][0],
        "target_hr_high": zones[2][1],
    })
    return steps


def _parse_easy_strides(description, target_distance, zones=None):
    """Easy run + strides at end."""
    zones = zones or HR_ZONES
    steps = []
    desc = description.lower()

    main_match = re.search(r"(\d+)\s*mi\s*easy", desc)
    main_miles = float(main_match.group(1)) if main_match else (target_distance or 5)

    stride_match = re.search(r"(\d+)\s*x\s*100m\s*strides", desc)
    num_strides = int(stride_match.group(1)) if stride_match else 6

    steps.append({
        "name": "Easy Run",
        "intensity": "ACTIVE",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(main_miles - 0.5),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[2][0],
        "target_hr_high": zones[2][1],
    })

    for i in range(num_strides):
        steps.append({
            "name": f"Stride {i+1}",
            "intensity": "ACTIVE",
            "duration_type": "DISTANCE",
            "duration_value": _miles_to_cm(0.062),
            "target_type": "HEART_RATE",
            "target_hr_low": zones[4][0],
            "target_hr_high": zones[5][1],
        })
        if i < num_strides - 1:
            steps.append({
                "name": "Easy Jog",
                "intensity": "RECOVERY",
                "duration_type": "DISTANCE",
                "duration_value": _miles_to_cm(0.062),
                "target_type": "HEART_RATE",
                "target_hr_low": zones[1][0],
                "target_hr_high": zones[2][1],
            })

    return steps


def _parse_long_run(description, target_distance, zones=None):
    """Long run: single steady effort."""
    zones = zones or HR_ZONES
    distance = target_distance or 10
    return [{
        "name": f"{distance}mi Long Run",
        "intensity": "ACTIVE",
        "duration_type": "DISTANCE",
        "duration_value": _miles_to_cm(distance),
        "target_type": "HEART_RATE",
        "target_hr_low": zones[2][0],
        "target_hr_high": zones[2][1],
    }]


def workout_to_fit_steps(segments):
    """Convert parsed segments to FIT WorkoutStepMessages."""
    steps = []
    intensity_map = {
        "WARMUP": Intensity.WARMUP,
        "ACTIVE": Intensity.ACTIVE,
        "RECOVERY": Intensity.RECOVERY,
        "COOLDOWN": Intensity.COOLDOWN,
        "REST": Intensity.REST,
    }
    for i, seg in enumerate(segments):
        step = WorkoutStepMessage()
        step.message_index = i
        step.workout_step_name = seg["name"][:16]  # FIT limits step name length
        step.intensity = intensity_map.get(seg["intensity"], Intensity.ACTIVE)

        # Duration
        dur_type = seg.get("duration_type", "OPEN")
        if dur_type == "DISTANCE":
            step.duration_type = WorkoutStepDuration.DISTANCE
            step.duration_distance = float(seg["duration_value"])
        elif dur_type == "TIME":
            step.duration_type = WorkoutStepDuration.TIME
            step.duration_time = float(seg["duration_value"])
        else:
            step.duration_type = WorkoutStepDuration.OPEN

        # Target
        tgt_type = seg.get("target_type", "OPEN")
        if tgt_type == "HEART_RATE":
            step.target_type = WorkoutStepTarget.HEART_RATE
            step.custom_target_heart_rate_low = seg["target_hr_low"] + 100
            step.custom_target_heart_rate_high = seg["target_hr_high"] + 100
        elif tgt_type == "SPEED":
            step.target_type = WorkoutStepTarget.SPEED
            step.custom_target_speed_low = seg.get("target_speed_low", 0)
            step.custom_target_speed_high = seg.get("target_speed_high", 0)
        else:
            step.target_type = WorkoutStepTarget.OPEN

        steps.append(step)

    return steps


def build_fit_file(workout_dict, steps):
    """Create a FIT file from workout metadata and step messages."""
    builder = FitFileBuilder(auto_define=True)

    file_id = FileIdMessage()
    file_id.type = FileType.WORKOUT
    file_id.manufacturer = Manufacturer.GARMIN.value
    file_id.product = 65534
    file_id.serial_number = int(datetime.now().timestamp()) & 0xFFFFFFFF
    # time_created expects Unix timestamp in milliseconds (fit-tool applies epoch offset internally)
    import time
    file_id.time_created = int(time.time() * 1000)

    workout = WorkoutMessage()
    workout.sport = Sport.RUNNING
    workout.sub_sport = SubSport.GENERIC
    title = workout_dict.get("title", "Workout")
    workout.workout_name = title[:24]  # FIT limits workout name
    workout.num_valid_steps = len(steps)

    builder.add(file_id)
    builder.add(workout)
    for step in steps:
        builder.add(step)

    fit_file = builder.build()
    return fit_file.to_bytes()


def export_workout_fit(workout_dict, output_dir, hr_zones=None):
    """Full pipeline: parse description → build FIT → write file.

    If hr_zones is provided (dict mapping zone number to (low, high) tuples),
    overrides the module-level HR_ZONES defaults.

    Returns dict with file path and metadata.
    """
    os.makedirs(output_dir, exist_ok=True)

    segments = parse_workout_segments(
        workout_dict.get("description", ""),
        workout_dict.get("workout_type", "easy_run"),
        workout_dict.get("target_distance_miles"),
        workout_dict.get("target_duration_minutes"),
        hr_zones=hr_zones,
    )
    steps = workout_to_fit_steps(segments)
    fit_bytes = build_fit_file(workout_dict, steps)

    date = workout_dict.get("scheduled_date", "unknown")
    title = workout_dict.get("title", "workout").replace(" ", "_").replace("/", "-")
    filename = f"{date}_{title}.fit"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "wb") as f:
        f.write(fit_bytes)

    return {
        "file": filepath,
        "date": date,
        "title": workout_dict.get("title"),
        "workout_type": workout_dict.get("workout_type"),
        "steps": len(steps),
        "size_bytes": len(fit_bytes),
    }


def export_week_fits(week_workouts, output_dir, hr_zones=None):
    """Export FIT files for all runnable workouts in a week.

    Skips rest days and cross-training.
    """
    skip_types = {"rest", "cross_train"}
    results = []

    for w in week_workouts:
        if w.get("workout_type") in skip_types:
            continue
        try:
            result = export_workout_fit(w, output_dir, hr_zones=hr_zones)
            results.append(result)
        except Exception as e:
            results.append({
                "date": w.get("scheduled_date"),
                "title": w.get("title"),
                "error": str(e),
            })

    return results
