import { useState, useEffect } from "react";
import { api } from "../api";

export default function History() {
  const [workouts, setWorkouts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    api.getWorkouts(50).then(setWorkouts).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="loading">
        <div className="spinner" />
        Loading history...
      </div>
    );
  }

  if (workouts.length === 0) {
    return (
      <div className="empty-state">
        <p>No workouts logged yet.</p>
        <p style={{ fontSize: "0.875rem", marginTop: 8 }}>
          Generate a workout or log one manually to get started.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ marginBottom: 16 }}>Workout History</h2>
      {workouts.map((w) => (
        <div
          key={w.id}
          className="card"
          style={{ cursor: "pointer" }}
          onClick={() => setExpanded(expanded === w.id ? null : w.id)}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div>
              <span className="workout-date">{w.date}</span>
              <span className={`badge badge-${w.workout_type}`} style={{ marginLeft: 8 }}>
                {w.workout_type}
              </span>
              {w.duration_minutes && (
                <span style={{ color: "var(--text-muted)", fontSize: "0.8125rem", marginLeft: 8 }}>
                  {w.duration_minutes} min
                </span>
              )}
            </div>
            <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>
              {w.exercises?.length || 0} exercises
            </span>
          </div>

          {w.notes && (
            <p style={{ color: "var(--text-muted)", fontSize: "0.8125rem", marginTop: 8 }}>
              {w.notes}
            </p>
          )}

          {expanded === w.id && w.exercises && (
            <div style={{ marginTop: 12 }}>
              {w.exercises.map((ex, i) => (
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
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
