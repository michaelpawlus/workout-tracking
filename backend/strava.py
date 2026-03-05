import os
import time
import requests
from database import get_db

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REDIRECT_URI = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:8000/api/strava/callback")
STRAVA_API = "https://www.strava.com/api/v3"
STRAVA_AUTH = "https://www.strava.com/oauth"


def get_auth_url() -> str:
    return (
        f"{STRAVA_AUTH}/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=activity:read_all"
    )


def exchange_code(code: str) -> dict:
    resp = requests.post(f"{STRAVA_AUTH}/token", data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    data = resp.json()
    _save_tokens(data["access_token"], data["refresh_token"], data["expires_at"])
    return data


def _save_tokens(access_token: str, refresh_token: str, expires_at: int):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO strava_tokens (id, access_token, refresh_token, expires_at)
               VALUES (1, ?, ?, ?)""",
            (access_token, refresh_token, expires_at),
        )


def _get_tokens() -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM strava_tokens WHERE id = 1").fetchone()
    return dict(row) if row else None


def _refresh_if_needed() -> str | None:
    tokens = _get_tokens()
    if not tokens:
        return None
    if tokens["expires_at"] < int(time.time()) + 60:
        resp = requests.post(f"{STRAVA_AUTH}/token", data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        data = resp.json()
        _save_tokens(data["access_token"], data["refresh_token"], data["expires_at"])
        return data["access_token"]
    return tokens["access_token"]


def is_connected() -> bool:
    return _get_tokens() is not None


def disconnect():
    with get_db() as conn:
        conn.execute("DELETE FROM strava_tokens")


def get_activities(per_page: int = 30) -> list[dict]:
    token = _refresh_if_needed()
    if not token:
        raise RuntimeError("Strava not connected")
    resp = requests.get(f"{STRAVA_API}/athlete/activities", headers={
        "Authorization": f"Bearer {token}",
    }, params={"per_page": per_page})
    resp.raise_for_status()
    return resp.json()


def get_activity_detail(activity_id: int) -> dict:
    token = _refresh_if_needed()
    if not token:
        raise RuntimeError("Strava not connected")
    resp = requests.get(f"{STRAVA_API}/activities/{activity_id}", headers={
        "Authorization": f"Bearer {token}",
    })
    resp.raise_for_status()
    return resp.json()


def strava_to_workout_text(activity: dict) -> str:
    """Convert a Strava activity to natural language for parsing by the existing LLM pipeline."""
    sport = activity.get("sport_type", activity.get("type", "Workout"))
    name = activity.get("name", "")
    distance_m = activity.get("distance", 0)
    duration_s = activity.get("moving_time", activity.get("elapsed_time", 0))
    elevation = activity.get("total_elevation_gain", 0)
    avg_hr = activity.get("average_heartrate")
    max_hr = activity.get("max_heartrate")

    distance_mi = round(distance_m / 1609.34, 2) if distance_m else 0
    duration_min = round(duration_s / 60, 1) if duration_s else 0

    parts = [f"{sport}: {name}" if name else sport]
    if distance_mi:
        parts.append(f"Distance: {distance_mi} miles ({round(distance_m)}m)")
    if duration_min:
        parts.append(f"Duration: {duration_min} minutes")
    if distance_mi and duration_min:
        pace = duration_min / distance_mi if distance_mi > 0 else 0
        parts.append(f"Pace: {int(pace)}:{int((pace % 1) * 60):02d} per mile")
    if elevation:
        parts.append(f"Elevation gain: {round(elevation)}m")
    if avg_hr:
        parts.append(f"Avg HR: {avg_hr} bpm")
    if max_hr:
        parts.append(f"Max HR: {max_hr} bpm")

    return "\n".join(parts)
