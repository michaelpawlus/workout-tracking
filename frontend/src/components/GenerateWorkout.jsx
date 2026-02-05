import { useState } from "react";
import { api } from "../api";

export default function GenerateWorkout({ onGenerated }) {
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const workout = await api.generateWorkout(prompt);
      onGenerated(workout);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="card">
        <h2>Generate a Workout</h2>
        <form onSubmit={handleSubmit}>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder='e.g. "45-minute upper body strength workout" or "20-minute AMRAP metcon"'
            disabled={loading}
          />
          <div className="btn-group">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !prompt.trim()}
            >
              {loading ? "Generating..." : "Generate Workout"}
            </button>
          </div>
        </form>
        {loading && (
          <div className="loading">
            <div className="spinner" />
            Building your workout...
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      <div className="card">
        <h3>Quick prompts</h3>
        {[
          "30-minute full body strength workout",
          "20-minute AMRAP metcon",
          "45-minute upper body push/pull",
          "Quick 15-minute HIIT finisher",
          "Heavy squat day with accessories",
        ].map((q) => (
          <button
            key={q}
            className="btn btn-secondary"
            style={{ marginRight: 8, marginBottom: 8 }}
            onClick={() => setPrompt(q)}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
