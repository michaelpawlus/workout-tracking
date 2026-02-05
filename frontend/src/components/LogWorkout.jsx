import { useState } from "react";
import { api } from "../api";

export default function LogWorkout({ activeWorkout, onSaved }) {
  const [input, setInput] = useState("");
  const [parsed, setParsed] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function handleParse(e) {
    e.preventDefault();
    if (!input.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.parseLog(input, activeWorkout);
      setParsed(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const result = await api.saveWorkout({
        workout_type: parsed.workout_type,
        duration_minutes: parsed.duration_minutes,
        notes: parsed.notes,
        prescribed_workout: activeWorkout,
        exercises: parsed.exercises,
      });

      // Handle PR confirmations
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

      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="card">
        <h2>Log Your Workout</h2>
        {activeWorkout && (
          <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 12 }}>
            Logging results for: <strong>{activeWorkout.workout_name}</strong>
          </p>
        )}
        <form onSubmit={handleParse}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              activeWorkout
                ? 'Describe how it went, e.g. "Finished in 22 minutes, used 135 on squats and 95 on overhead press"'
                : 'Describe your workout, e.g. "Bench press 5x5 at 205, then 3x10 dumbbell rows at 60 lbs"'
            }
            rows={4}
            disabled={loading}
          />
          <div className="btn-group">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !input.trim()}
            >
              {loading ? "Parsing..." : "Parse Workout"}
            </button>
          </div>
        </form>
        {loading && (
          <div className="loading">
            <div className="spinner" />
            Understanding your workout...
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      {parsed && (
        <div className="card">
          <h2>Review Parsed Workout</h2>
          <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
            <span className={`badge badge-${parsed.workout_type}`}>
              {parsed.workout_type}
            </span>
            {parsed.duration_minutes && (
              <span style={{ color: "var(--text-muted)", fontSize: "0.875rem" }}>
                {parsed.duration_minutes} min
              </span>
            )}
          </div>

          {parsed.clarifications_needed?.length > 0 && (
            <div className="error" style={{ background: "rgba(234, 179, 8, 0.1)", borderColor: "rgba(234, 179, 8, 0.3)", color: "var(--yellow)" }}>
              <strong>Clarification needed:</strong>
              <ul style={{ marginTop: 4, paddingLeft: 20 }}>
                {parsed.clarifications_needed.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </div>
          )}

          {parsed.exercises?.map((ex, i) => (
            <div key={i} className="exercise-row">
              <span className="exercise-name">{ex.display_name}</span>
              <span className="exercise-detail">
                {[
                  ex.sets && ex.reps && `${ex.sets}x${ex.reps}`,
                  ex.weight_lbs && `@ ${ex.weight_lbs} lbs`,
                  ex.time_seconds && `${ex.time_seconds}s`,
                  ex.rounds_completed && `${ex.rounds_completed} rounds`,
                  ex.distance_meters && `${ex.distance_meters}m`,
                ]
                  .filter(Boolean)
                  .join(" ")}
              </span>
            </div>
          ))}

          {parsed.possible_prs?.length > 0 &&
            parsed.possible_prs.map((pr, i) => (
              <div key={i} className="confirm-pr">
                <span className="confirm-pr-text">
                  Possible PR: {pr.exercise_name.replace(/_/g, " ")} {pr.record_type} = {pr.value} lbs
                </span>
              </div>
            ))}

          {parsed.notes && (
            <p style={{ color: "var(--text-muted)", fontSize: "0.8125rem", marginTop: 12, fontStyle: "italic" }}>
              {parsed.notes}
            </p>
          )}

          <div className="btn-group">
            <button
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving || (parsed.clarifications_needed?.length > 0)}
            >
              {saving ? "Saving..." : "Save Workout"}
            </button>
            <button className="btn btn-secondary" onClick={() => setParsed(null)}>
              Re-parse
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
