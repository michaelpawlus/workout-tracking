import { useState } from "react";
import { api } from "../api";

export default function ProgressAnalysis() {
  const [question, setQuestion] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.analyzeProgress(question);
      setAnalysis(result.analysis);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="card">
        <h2>Progress Analysis</h2>
        <form onSubmit={handleSubmit}>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder='Ask about your progress, e.g. "How is my squat progressing?" or "What does my training frequency look like?"'
            rows={3}
            disabled={loading}
          />
          <div className="btn-group">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !question.trim()}
            >
              {loading ? "Analyzing..." : "Analyze"}
            </button>
          </div>
        </form>
        {loading && (
          <div className="loading">
            <div className="spinner" />
            Analyzing your training data...
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      <div className="card">
        <h3>Example questions</h3>
        {[
          "How's my squat progressing?",
          "What's my training frequency by muscle group?",
          "Am I ready to test a new 1RM on bench?",
          "What areas am I neglecting?",
        ].map((q) => (
          <button
            key={q}
            className="btn btn-secondary"
            style={{ marginRight: 8, marginBottom: 8 }}
            onClick={() => setQuestion(q)}
          >
            {q}
          </button>
        ))}
      </div>

      {analysis && (
        <div className="card">
          <h2>Analysis</h2>
          <div className="analysis-text">{analysis}</div>
        </div>
      )}
    </div>
  );
}
