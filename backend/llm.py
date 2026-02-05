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


def parse_workout_log(user_input: str, prescribed_workout: dict | None = None) -> dict:
    context = _build_context()

    prescribed_section = ""
    if prescribed_workout:
        prescribed_section = f"""
## Prescribed Workout Being Logged
The user was performing this workout:
{json.dumps(prescribed_workout, indent=2)}
"""

    system_prompt = f"""You are a workout logging assistant. Parse the user's natural language workout description into structured data.

{context}
{prescribed_section}

IMPORTANT RULES:
1. Map exercises to the 'name' field (snake_case) from the Available Exercises list.
2. If you're unsure which exercise the user means, include a "clarification_needed" field.
3. Extract all quantitative data: sets, reps, weights, times, rounds, distances.
4. If the user references the prescribed workout (e.g., "finished as prescribed", "used the suggested weights"), use those values.

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

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": user_input}],
        system=system_prompt,
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3].strip()

    return json.loads(text)


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
