import { useState, useEffect } from "react";
import { api } from "../api";

export default function PRBoard() {
  const [prs, setPrs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getPRs().then(setPrs).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="loading">
        <div className="spinner" />
        Loading PRs...
      </div>
    );
  }

  if (prs.length === 0) {
    return (
      <div className="empty-state">
        <p>No personal records yet.</p>
        <p style={{ fontSize: "0.875rem", marginTop: 8 }}>
          PRs are detected automatically when you log workouts.
        </p>
      </div>
    );
  }

  // Group by exercise
  const grouped = {};
  for (const pr of prs) {
    const key = pr.display_name;
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(pr);
  }

  return (
    <div>
      <h2 style={{ marginBottom: 16 }}>Personal Records</h2>
      {Object.entries(grouped).map(([name, records]) => (
        <div key={name} className="card">
          <h2 style={{ marginBottom: 8 }}>{name}</h2>
          {records.map((pr, i) => (
            <div key={i} className="pr-card" style={{ marginBottom: 4 }}>
              <div>
                <span style={{ color: "var(--text-muted)", fontSize: "0.8125rem" }}>
                  {pr.record_type}
                </span>
              </div>
              <div style={{ textAlign: "right" }}>
                <span className="pr-value">{pr.value}</span>
                <span style={{ color: "var(--text-muted)", fontSize: "0.75rem", marginLeft: 8 }}>
                  {pr.date_achieved}
                </span>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
