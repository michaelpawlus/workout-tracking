function formatExerciseDetail(ex) {
  const parts = [];
  if (ex.sets && ex.reps) parts.push(`${ex.sets}x${ex.reps}`);
  else if (ex.sets) parts.push(`${ex.sets} sets`);
  else if (ex.reps) parts.push(`${ex.reps} reps`);
  if (ex.weight_suggestion_lbs) parts.push(`@ ${ex.weight_suggestion_lbs} lbs`);
  if (ex.time_seconds) parts.push(`${ex.time_seconds}s`);
  if (ex.distance_meters) parts.push(`${ex.distance_meters}m`);
  return parts.join(" ");
}

export default function ActiveWorkout({ workout, onStartLog, onDiscard }) {
  return (
    <div>
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2>{workout.workout_name}</h2>
          <span className={`badge badge-${workout.workout_type}`}>
            {workout.workout_type}
          </span>
        </div>
        <p style={{ color: "var(--text-muted)", marginBottom: 16, fontSize: "0.875rem" }}>
          ~{workout.estimated_duration_minutes} min &middot; {workout.description}
        </p>

        {workout.sections?.map((section, i) => (
          <div key={i} className="section-block">
            <h3>
              {section.name}
              {section.format && (
                <span style={{ fontWeight: 400, textTransform: "none", marginLeft: 8, letterSpacing: 0 }}>
                  ({section.format})
                </span>
              )}
            </h3>
            {section.exercises?.map((ex, j) => (
              <div key={j} className="exercise-row">
                <span className="exercise-name">{ex.display_name}</span>
                <span className="exercise-detail">{formatExerciseDetail(ex)}</span>
              </div>
            ))}
          </div>
        ))}

        {workout.coaching_notes && (
          <p style={{ color: "var(--text-muted)", fontSize: "0.8125rem", marginTop: 16, fontStyle: "italic" }}>
            {workout.coaching_notes}
          </p>
        )}

        <div className="btn-group">
          <button className="btn btn-primary" onClick={onStartLog}>
            Done - Log Results
          </button>
          <button className="btn btn-danger" onClick={onDiscard}>
            Discard
          </button>
        </div>
      </div>
    </div>
  );
}
