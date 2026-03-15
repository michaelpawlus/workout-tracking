import base64
import json
import anthropic
from database import get_db

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-5-20250929"


def _get_exercise_list():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name, display_name, category, primary_metric FROM exercises ORDER BY category, display_name"
        ).fetchall()
    return [dict(r) for r in rows]


def _get_recent_history(limit=20):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT w.id, w.date, w.workout_type, w.duration_minutes, w.notes,
                   e.display_name, e.category,
                   we.sets, we.reps, we.weight_lbs, we.time_seconds,
                   we.rounds_completed, we.distance_meters, we.notes as exercise_notes
            FROM workouts w
            JOIN workout_exercises we ON we.workout_id = w.id
            JOIN exercises e ON e.id = we.exercise_id
            ORDER BY w.date DESC, w.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def _get_personal_records():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT e.display_name, e.category, pr.record_type, pr.value, pr.date_achieved
            FROM personal_records pr
            JOIN exercises e ON e.id = pr.exercise_id
            ORDER BY e.display_name
        """).fetchall()
    return [dict(r) for r in rows]


def _build_context():
    history = _get_recent_history()
    prs = _get_personal_records()
    exercises = _get_exercise_list()

    parts = []
    parts.append("## Available Exercises in Database")
    parts.append("Categories: " + ", ".join(sorted(set(e["category"] for e in exercises))))
    for ex in exercises:
        parts.append(f"- {ex['display_name']} (name: {ex['name']}, category: {ex['category']}, metric: {ex['primary_metric']})")

    if history:
        parts.append("\n## Recent Workout History (last 20 exercise entries)")
        for h in history:
            line = f"- {h['date']}: {h['display_name']}"
            if h["sets"] and h["reps"] and h["weight_lbs"]:
                line += f" {h['sets']}x{h['reps']} @ {h['weight_lbs']} lbs"
            elif h["sets"] and h["reps"]:
                line += f" {h['sets']}x{h['reps']}"
            elif h["time_seconds"]:
                line += f" {h['time_seconds']}s"
            elif h["rounds_completed"]:
                line += f" {h['rounds_completed']} rounds"
            if h["exercise_notes"]:
                line += f" ({h['exercise_notes']})"
            parts.append(line)
    else:
        parts.append("\n## Workout History: No previous workouts recorded yet. Use conservative weight suggestions.")

    if prs:
        parts.append("\n## Current Personal Records")
        for pr in prs:
            parts.append(f"- {pr['display_name']} {pr['record_type']}: {pr['value']} (set {pr['date_achieved']})")

    return "\n".join(parts)


def generate_workout(user_request: str) -> dict:
    context = _build_context()

    system_prompt = f"""You are a workout programming assistant. Generate workouts based on the user's request and their training history.

{context}

IMPORTANT RULES:
1. Only use exercises from the Available Exercises list above.
2. Use the exercise 'name' field (snake_case) when referencing exercises in your structured output.
3. Base weight/rep suggestions on the user's history when available.
4. If no history exists for an exercise, suggest conservative starting weights.
5. Include warm-up suggestions when appropriate.

Respond with a JSON object in this exact format:
{{
  "workout_name": "Short descriptive name",
  "workout_type": "strength" or "hiit" or "metcon" or "cardio",
  "estimated_duration_minutes": <number>,
  "description": "Brief overview of the workout",
  "sections": [
    {{
      "name": "Section name (e.g., Warm-Up, Strength, Metcon)",
      "type": "warmup" or "strength" or "metcon" or "accessory",
      "format": "Description of format (e.g., 5x5, AMRAP 20, 21-15-9)",
      "exercises": [
        {{
          "exercise_name": "snake_case_name from database",
          "display_name": "Human readable name",
          "sets": <number or null>,
          "reps": <number or null>,
          "weight_suggestion_lbs": <number or null>,
          "time_seconds": <number or null>,
          "distance_meters": <number or null>,
          "notes": "any coaching notes"
        }}
      ]
    }}
  ],
  "coaching_notes": "Overall tips for this workout"
}}

Respond ONLY with the JSON object, no other text."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": user_request}],
        system=system_prompt,
    )

    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3].strip()

    return json.loads(text)


def _workout_parse_system_prompt(context: str, prescribed_workout: dict | None = None) -> str:
    prescribed_section = ""
    if prescribed_workout:
        prescribed_section = f"""
## Prescribed Workout Being Logged
The user was performing this workout:
{json.dumps(prescribed_workout, indent=2)}
"""

    return f"""You are a workout logging assistant. Parse the user's workout description into structured data.

{context}
{prescribed_section}

IMPORTANT RULES:
1. Map exercises to the 'name' field (snake_case) from the Available Exercises list.
2. If you're unsure which exercise the user means, include a "clarification_needed" field.
3. Extract all quantitative data: sets, reps, weights, times, rounds, distances.
4. If the user references the prescribed workout (e.g., "finished as prescribed", "used the suggested weights"), use those values.
5. Recognize CrossFit/SugarWOD formats: AMRAP, EMOM, rounds for time, 21-15-9 rep schemes, "Rx" / "scaled", named WODs (Fran, Murph, Cindy, etc).
6. If the input is an image/photo of a whiteboard or phone screenshot, extract the workout exactly as written.

Respond with a JSON object in this exact format:
{{
  "workout_type": "strength" or "hiit" or "metcon" or "cardio",
  "duration_minutes": <number or null>,
  "notes": "any general notes about the workout",
  "exercises": [
    {{
      "exercise_name": "snake_case_name from database",
      "display_name": "Human readable name",
      "sets": <number or null>,
      "reps": <number or null>,
      "weight_lbs": <number or null>,
      "time_seconds": <number or null>,
      "rounds_completed": <number or null>,
      "distance_meters": <number or null>,
      "notes": "any notes for this exercise"
    }}
  ],
  "clarifications_needed": ["question1", "question2"] or [],
  "possible_prs": [
    {{
      "exercise_name": "snake_case_name",
      "record_type": "1RM" or "5RM" or "max_reps" or "best_time",
      "value": <number>,
      "reason": "Why this might be a PR"
    }}
  ] or []
}}

Respond ONLY with the JSON object, no other text."""


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)


def parse_workout_log(user_input: str, prescribed_workout: dict | None = None) -> dict:
    context = _build_context()
    system_prompt = _workout_parse_system_prompt(context, prescribed_workout)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": user_input}],
        system=system_prompt,
    )

    return _parse_llm_json(response.content[0].text)


def parse_workout_image(image_bytes: bytes, media_type: str, prescribed_workout: dict | None = None) -> dict:
    context = _build_context()
    system_prompt = _workout_parse_system_prompt(context, prescribed_workout)

    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": "Parse this workout image into structured data.",
                },
            ],
        }],
        system=system_prompt,
    )

    return _parse_llm_json(response.content[0].text)


def analyze_progress(user_question: str) -> str:
    context = _build_context()

    # Get more history for progress analysis
    with get_db() as conn:
        rows = conn.execute("""
            SELECT w.date, w.workout_type, w.duration_minutes,
                   e.display_name, e.name,
                   we.sets, we.reps, we.weight_lbs, we.time_seconds,
                   we.rounds_completed, we.distance_meters
            FROM workouts w
            JOIN workout_exercises we ON we.workout_id = w.id
            JOIN exercises e ON e.id = we.exercise_id
            ORDER BY w.date DESC
            LIMIT 100
        """).fetchall()
    extended_history = [dict(r) for r in rows]

    system_prompt = f"""You are a fitness progress analyst. Answer the user's question about their training progress using their workout data.

{context}

## Extended History (last 100 exercise entries)
{json.dumps(extended_history, indent=2, default=str)}

Provide clear, data-driven analysis. Include specific numbers, trends, and actionable recommendations.
Be encouraging but honest. If data is insufficient, say so."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": user_question}],
        system=system_prompt,
    )

    return response.content[0].text


def generate_training_plan(goal: str, total_weeks: int, modalities: list[str],
                           start_date: str, mesocycle_weeks: int = 4, notes: str = "") -> dict:
    context = _build_context()

    system_prompt = f"""You are a training plan designer. Create a structured mesocycle training plan.

{context}

IMPORTANT RULES:
1. Follow a build/build/build/deload mesocycle pattern (default 4-week cycles, configurable).
   - Build weeks increase intensity/volume progressively.
   - Deload weeks reduce volume by ~40-50%.
2. Schedule benchmark tests at the END of each build week (not deload weeks) so progress can be tracked.
3. Recurring benchmarks should be consistent across cycles for comparison:
   - Running: mile time trial, 5K time trial
   - Lifting: 1RM or 5RM on key lifts (back squat, deadlift, bench press, overhead press, clean)
   - MetCon: named WODs (Fran, Cindy, Murph), custom timed WODs
   - AMRAP: standard AMRAP tests (e.g., max pull-ups in 2 min, max burpees in 5 min)
4. Only include benchmarks relevant to the selected modalities.
5. Use exercises from the Available Exercises list.
6. Scale suggestions based on user's history and PRs when available.

Respond with JSON:
{{
  "plan_name": "Descriptive plan name",
  "goal": "Restated goal",
  "total_weeks": <number>,
  "mesocycle_weeks": <number>,
  "weeks": [
    {{
      "week_number": <1-based>,
      "week_type": "build" or "deload",
      "focus": "Brief focus description",
      "notes": "Training notes for this week",
      "benchmarks": [
        {{
          "benchmark_name": "e.g. Back Squat 1RM",
          "benchmark_type": "time_trial" or "max_lift" or "timed_wod" or "amrap",
          "target_value": <number or null>,
          "notes": "Description of the benchmark test"
        }}
      ]
    }}
  ],
  "notes": "Overall plan notes and coaching advice"
}}

Respond ONLY with the JSON object."""

    user_msg = f"""Create a {total_weeks}-week training plan.
Goal: {goal}
Modalities: {', '.join(modalities)}
Start date: {start_date}
Mesocycle length: {mesocycle_weeks} weeks
Additional notes: {notes or 'None'}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": user_msg}],
        system=system_prompt,
    )

    return _parse_llm_json(response.content[0].text)


def analyze_run_feedback(prescribed: dict, actual: dict,
                         weekly_context: dict | None = None,
                         trend_data: list[dict] | None = None,
                         benchmarks: list[dict] | None = None,
                         race_info: dict | None = None,
                         athlete_targets: dict | None = None,
                         nutrition_data: dict | None = None) -> dict:
    """Analyze a completed run against the prescribed workout and return structured feedback."""

    system_prompt = """You are an experienced ultramarathon coach analyzing a training run for an athlete preparing for a 100-mile race (Burning River 100, July 25, 2026, sub-24hr goal).

COACHING PRINCIPLES:
- 80/20 rule: 80% of runs should be easy (conversational pace), 20% quality (tempo/hills/intervals)
- Most common ultra training mistake: running easy days too fast. Easy means EASY.
- Back-to-back long runs build fatigue resistance — the key ultra adaptation
- For runs >90 min: athlete should practice race nutrition (~200 cal/hr)
- HR drift in long runs: >10% drift in last third signals insufficient aerobic base
- During taper: feeling flat/sluggish is NORMAL and expected. Don't panic.
- MAF test improvement (more distance at same HR) = aerobic fitness improving
- Pace decay in long runs should decrease over training cycle

NUTRITION COACHING:
- <60 min runs: water only, no fuel needed
- 60-90 min: light fueling (1 gel or equivalent ~100 cal around 45 min)
- >90 min: structured fueling every 30-45 min, targeting ~200-250 cal/hr
- Electrolytes/sodium critical for runs >60 min, especially in heat
- Pre-run: 40-100g carbs 2-3hr before long runs; lighter for shorter runs
- Post-run: protein + carbs within 30-60 min for long runs
- Bonking = ran out of glycogen. Means under-fueled, not under-trained.
- GI issues are common — use training runs to practice nutrition, not race day
- If athlete reports bonking or GI issues, flag it prominently in feedback

Respond with JSON only:
{
    "compliance_score": <0-100 float>,
    "pace_feedback": "...",
    "hr_feedback": "...",
    "distance_feedback": "...",
    "overall_feedback": "...",
    "nutrition_feedback": "...",
    "weekly_mileage": {"target": <num>, "completed": <num>, "remaining": <num>},
    "warnings": ["warning1", "warning2"],
    "race_readiness": "On track / Needs attention / Behind"
}"""

    user_msg_parts = [
        f"## Prescribed Workout\n{json.dumps(prescribed, indent=2, default=str)}",
        f"\n## Actual Run Data\n{json.dumps(actual, indent=2, default=str)}",
    ]
    if weekly_context:
        user_msg_parts.append(f"\n## Weekly Context\n{json.dumps(weekly_context, indent=2, default=str)}")
    if trend_data:
        user_msg_parts.append(f"\n## Recent Trend Data (last 10 runs)\n{json.dumps(trend_data, indent=2, default=str)}")
    if benchmarks:
        user_msg_parts.append(f"\n## Benchmark Results\n{json.dumps(benchmarks, indent=2, default=str)}")
    if race_info:
        user_msg_parts.append(f"\n## Race Info\n{json.dumps(race_info, indent=2, default=str)}")
    if athlete_targets:
        user_msg_parts.append(f"\n## Current Athlete Targets\n{json.dumps(athlete_targets, indent=2, default=str)}")
    if nutrition_data:
        user_msg_parts.append(f"\n## Nutrition Report\n{json.dumps(nutrition_data, indent=2, default=str)}")

    user_msg_parts.append("\nAnalyze this run and provide structured feedback.")

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": "\n".join(user_msg_parts)}],
        system=system_prompt,
    )

    return _parse_llm_json(response.content[0].text)


def analyze_strava_screenshot(image_bytes: bytes, media_type: str,
                               prescribed_workout: dict | None = None) -> dict:
    """Extract running metrics from a Strava screenshot."""

    system_prompt = """You are a data extraction assistant. Extract running metrics from this Strava screenshot.

Return JSON only:
{
    "distance_miles": <float>,
    "duration_minutes": <float>,
    "avg_pace_min_per_mile": <float>,
    "avg_heart_rate": <int or null>,
    "max_heart_rate": <int or null>,
    "elevation_gain_ft": <float or null>,
    "calories": <int or null>,
    "splits": [{"mile": 1, "pace": "9:30", "hr": 135}],
    "activity_name": "...",
    "date": "YYYY-MM-DD",
    "notes": "any additional observations"
}

Extract ALL visible data. Convert units if needed (km to miles, m to ft). If a field is not visible, use null."""

    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": image_data},
        },
        {"type": "text", "text": "Extract all running metrics from this Strava screenshot."},
    ]

    if prescribed_workout:
        content.append({
            "type": "text",
            "text": f"This was the prescribed workout: {json.dumps(prescribed_workout, default=str)}",
        })

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
        system=system_prompt,
    )

    return _parse_llm_json(response.content[0].text)


def analyze_plan_progress(plan_data: dict, benchmarks: list[dict]) -> str:
    context = _build_context()

    system_prompt = f"""You are a training progress analyst. Analyze benchmark results across a training plan's mesocycles.

{context}

## Training Plan
{json.dumps(plan_data, indent=2, default=str)}

## Benchmark Results
{json.dumps(benchmarks, indent=2, default=str)}

Compare benchmark results across cycles. Identify:
1. Improvements and their magnitude (percentage gains)
2. Plateaus or regressions
3. Which modalities are progressing well vs. need attention
4. Recommendations for the next training cycle

Be specific with numbers. Use encouraging but honest tone."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": "Analyze my training plan progress based on the benchmark results."}],
        system=system_prompt,
    )

    return response.content[0].text
