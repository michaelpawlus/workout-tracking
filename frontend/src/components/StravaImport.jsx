import { useState, useEffect } from "react";
import { api } from "../api";

export default function StravaImport({ onImported }) {
  const [connected, setConnected] = useState(false);
  const [activities, setActivities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(null);
  const [parsed, setParsed] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function checkStatus() {
    try {
      const status = await api.getStravaStatus();
      setConnected(status.connected);
      if (status.connected) {
        const acts = await api.getStravaActivities();
        setActivities(acts);
      }
    } catch {
      // Strava not configured
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { checkStatus(); }, []);

  async function handleConnect() {
    try {
      const data = await api.getStravaAuthUrl();
      window.location.href = data.url;
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDisconnect() {
    try {
      await api.disconnectStrava();
      setConnected(false);
      setActivities([]);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleImport(activityId) {
    setImporting(activityId);
    setError(null);
    try {
      const result = await api.importStravaActivity(activityId);
      setParsed(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setImporting(null);
    }
  }

  async function handleSave() {
    if (!parsed) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.saveWorkout({
        workout_type: parsed.workout_type,
        duration_minutes: parsed.duration_minutes,
        notes: parsed.notes,
        exercises: parsed.exercises,
        source: "strava",
      });

      if (parsed.possible_prs?.length > 0) {
        for (const pr of parsed.possible_prs) {
          await api.confirmPR({
            exercise_name: pr.exercise_name,
            record_type: pr.record_type,
            value: pr.value,
            workout_id: result.workout_id,
          });
        }
      }

      setParsed(null);
      if (onImported) onImported();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="loading"><div className="spinner" /> Checking Strava...</div>;

  function formatDuration(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m > 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m ${s}s`;
  }

  function formatDistance(meters) {
    return meters > 1000 ? `${(meters / 1609.34).toFixed(2)} mi` : `${Math.round(meters)}m`;
  }

  return (
    <div>
      <div className="card">
        <h2>Strava Import</h2>

        {!connected ? (
          <div>
            <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 12 }}>
              Connect your Strava account to import runs, rides, and other activities.
            </p>
            <button className="btn btn-primary" onClick={handleConnect}>
              Connect Strava
            </button>
          </div>
        ) : (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <span style={{ color: "var(--green)", fontSize: "0.875rem" }}>Connected</span>
              <button className="btn btn-danger" onClick={handleDisconnect} style={{ padding: "4px 12px", fontSize: "0.8125rem" }}>
                Disconnect
              </button>
            </div>
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      {parsed && (
        <div className="card">
          <h2>Review Imported Activity</h2>
          {parsed.strava_activity && (
            <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 8 }}>
              {parsed.strava_activity.name} ({parsed.strava_activity.type})
            </p>
          )}
          <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
            <span className={`badge badge-${parsed.workout_type}`}>{parsed.workout_type}</span>
            {parsed.duration_minutes && (
              <span style={{ color: "var(--text-muted)", fontSize: "0.875rem" }}>{parsed.duration_minutes} min</span>
            )}
          </div>

          {parsed.exercises?.map((ex, i) => (
            <div key={i} className="exercise-row">
              <span className="exercise-name">{ex.display_name}</span>
              <span className="exercise-detail">
                {[
                  ex.time_seconds && `${ex.time_seconds}s`,
                  ex.distance_meters && `${ex.distance_meters}m`,
                  ex.sets && ex.reps && `${ex.sets}x${ex.reps}`,
                  ex.weight_lbs && `@ ${ex.weight_lbs} lbs`,
                ].filter(Boolean).join(" ")}
              </span>
            </div>
          ))}

          <div className="btn-group">
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save Workout"}
            </button>
            <button className="btn btn-secondary" onClick={() => setParsed(null)}>Cancel</button>
          </div>
        </div>
      )}

      {connected && activities.length > 0 && !parsed && (
        <div className="card">
          <h2>Recent Activities</h2>
          {activities.map((act) => (
            <div key={act.id} className="exercise-row" style={{ alignItems: "center" }}>
              <div>
                <span className="exercise-name">{act.name}</span>
                <span style={{ display: "block", fontSize: "0.75rem", color: "var(--text-muted)" }}>
                  {act.sport_type || act.type} &middot; {new Date(act.start_date_local || act.start_date).toLocaleDateString()}
                  {act.distance ? ` \u00b7 ${formatDistance(act.distance)}` : ""}
                  {act.moving_time ? ` \u00b7 ${formatDuration(act.moving_time)}` : ""}
                </span>
              </div>
              <button
                className="btn btn-secondary"
                style={{ padding: "4px 10px", fontSize: "0.75rem", whiteSpace: "nowrap" }}
                onClick={() => handleImport(act.id)}
                disabled={importing === act.id}
              >
                {importing === act.id ? "..." : "Import"}
              </button>
            </div>
          ))}
        </div>
      )}

      {connected && activities.length === 0 && !loading && (
        <div className="empty-state">No recent Strava activities found.</div>
      )}
    </div>
  );
}
