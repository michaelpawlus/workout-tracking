import { useState, useEffect } from "react";
import { api } from "../api";

const MODALITIES = [
  { id: "running", label: "Running" },
  { id: "lifting", label: "Lifting" },
  { id: "metcon", label: "MetCon" },
  { id: "gymnastics", label: "Gymnastics" },
];

function PlanGenerator({ onCreated }) {
  const [goal, setGoal] = useState("");
  const [startDate, setStartDate] = useState(new Date().toISOString().split("T")[0]);
  const [totalWeeks, setTotalWeeks] = useState(12);
  const [mesocycleWeeks, setMesocycleWeeks] = useState(4);
  const [modalities, setModalities] = useState(["lifting", "metcon"]);
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  function toggleModality(id) {
    setModalities((prev) =>
      prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]
    );
  }

  async function handleGenerate(e) {
    e.preventDefault();
    if (!goal.trim() || modalities.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.generatePlan({
        goal,
        total_weeks: totalWeeks,
        modalities,
        start_date: startDate,
        mesocycle_weeks: mesocycleWeeks,
        notes,
      });
      onCreated(result.plan_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h2>Create Training Plan</h2>
      <form onSubmit={handleGenerate}>
        <div style={{ marginBottom: 12 }}>
          <label style={{ display: "block", fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: 4 }}>Goal</label>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder='e.g. "Build strength for a CrossFit competition in 12 weeks" or "Improve 5K time while maintaining lifting gains"'
            rows={2}
            disabled={loading}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 12 }}>
          <div>
            <label style={{ display: "block", fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: 4 }}>Start Date</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
              style={{ width: "100%", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "var(--radius)", color: "var(--text)", padding: "8px 12px", fontSize: "0.875rem" }}
              disabled={loading} />
          </div>
          <div>
            <label style={{ display: "block", fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: 4 }}>Total Weeks</label>
            <input type="number" value={totalWeeks} onChange={(e) => setTotalWeeks(parseInt(e.target.value) || 4)} min={4} max={52}
              style={{ width: "100%", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "var(--radius)", color: "var(--text)", padding: "8px 12px", fontSize: "0.875rem" }}
              disabled={loading} />
          </div>
          <div>
            <label style={{ display: "block", fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: 4 }}>Cycle Length</label>
            <input type="number" value={mesocycleWeeks} onChange={(e) => setMesocycleWeeks(parseInt(e.target.value) || 4)} min={3} max={6}
              style={{ width: "100%", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "var(--radius)", color: "var(--text)", padding: "8px 12px", fontSize: "0.875rem" }}
              disabled={loading} />
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ display: "block", fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: 8 }}>Modalities</label>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {MODALITIES.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => toggleModality(m.id)}
                className={`btn ${modalities.includes(m.id) ? "btn-primary" : "btn-secondary"}`}
                style={{ padding: "6px 14px", fontSize: "0.8125rem" }}
                disabled={loading}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ display: "block", fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: 4 }}>Additional Notes (optional)</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="Any constraints, equipment availability, injury concerns, etc."
            rows={2} disabled={loading} />
        </div>

        <button type="submit" className="btn btn-primary" disabled={loading || !goal.trim() || modalities.length === 0}>
          {loading ? "Generating Plan..." : "Generate Plan"}
        </button>
      </form>
      {loading && (
        <div className="loading"><div className="spinner" /> Building your training plan...</div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function WeekCard({ week }) {
  const isBuild = week.week_type === "build";
  const borderColor = isBuild ? "var(--accent)" : "var(--green)";
  const completedBenchmarks = (week.benchmarks || []).filter((b) => b.completed).length;
  const totalBenchmarks = (week.benchmarks || []).length;

  return (
    <div className="card" style={{ borderLeftWidth: 3, borderLeftColor: borderColor }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>Week {week.week_number}</h3>
        <span className={`badge ${isBuild ? "badge-strength" : "badge-cardio"}`}>
          {week.week_type}
        </span>
      </div>
      {week.focus && (
        <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 8 }}>{week.focus}</p>
      )}
      {totalBenchmarks > 0 && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 4 }}>
            Benchmarks ({completedBenchmarks}/{totalBenchmarks})
          </p>
          {week.benchmarks.map((bm) => (
            <div key={bm.id} className="exercise-row" style={{ opacity: bm.completed ? 0.7 : 1 }}>
              <span className="exercise-name" style={{ fontSize: "0.875rem" }}>
                {bm.completed ? "\u2713 " : ""}{bm.benchmark_name}
              </span>
              <span className="exercise-detail">
                {bm.completed ? `Result: ${bm.result_value}` : bm.target_value ? `Target: ${bm.target_value}` : "Pending"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BenchmarkProgress({ benchmarks }) {
  // Group benchmarks by name to show trends
  const grouped = {};
  for (const bm of benchmarks) {
    if (!grouped[bm.benchmark_name]) grouped[bm.benchmark_name] = [];
    grouped[bm.benchmark_name].push(bm);
  }

  const names = Object.keys(grouped);
  if (names.length === 0) return null;

  return (
    <div className="card">
      <h2>Benchmark Progress</h2>
      {names.map((name) => {
        const entries = grouped[name];
        const completed = entries.filter((e) => e.completed);
        if (completed.length < 1) return null;
        const latest = completed[completed.length - 1];
        const first = completed[0];
        const trend = completed.length > 1
          ? ((latest.result_value - first.result_value) / first.result_value * 100).toFixed(1)
          : null;

        return (
          <div key={name} style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
              <span className="exercise-name">{name}</span>
              {trend !== null && (
                <span style={{ color: parseFloat(trend) >= 0 ? "var(--green)" : "var(--red)", fontSize: "0.875rem", fontWeight: 600 }}>
                  {parseFloat(trend) >= 0 ? "+" : ""}{trend}%
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {entries.map((e, i) => (
                <span key={i} style={{
                  fontSize: "0.75rem",
                  padding: "2px 8px",
                  borderRadius: "var(--radius)",
                  background: e.completed ? "var(--surface-hover)" : "transparent",
                  border: "1px solid var(--border)",
                  color: e.completed ? "var(--text)" : "var(--text-muted)",
                }}>
                  Wk{e.week_number}: {e.completed ? e.result_value : "-"}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PlanDetail({ planId, onBack }) {
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [recordingId, setRecordingId] = useState(null);
  const [resultValue, setResultValue] = useState("");
  const [resultNotes, setResultNotes] = useState("");
  const [progressAnalysis, setProgressAnalysis] = useState(null);
  const [analyzingProgress, setAnalyzingProgress] = useState(false);

  async function loadPlan() {
    try {
      const data = await api.getPlan(planId);
      setPlan(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadPlan(); }, [planId]);

  async function handleRecordResult(benchmarkId) {
    if (!resultValue) return;
    try {
      await api.recordBenchmarkResult(planId, benchmarkId, {
        result_value: parseFloat(resultValue),
        result_notes: resultNotes,
      });
      setRecordingId(null);
      setResultValue("");
      setResultNotes("");
      loadPlan();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleAnalyzeProgress() {
    setAnalyzingProgress(true);
    try {
      const result = await api.getPlanProgress(planId);
      setProgressAnalysis(result.analysis);
    } catch (err) {
      setError(err.message);
    } finally {
      setAnalyzingProgress(false);
    }
  }

  if (loading) return <div className="loading"><div className="spinner" /> Loading plan...</div>;
  if (error) return <div className="error">{error}</div>;
  if (!plan) return null;

  const allBenchmarks = (plan.weeks || []).flatMap((w) =>
    (w.benchmarks || []).map((b) => ({ ...b, week_number: w.week_number, week_type: w.week_type }))
  );

  return (
    <div>
      <button className="btn btn-secondary" onClick={onBack} style={{ marginBottom: 16 }}>
        &larr; All Plans
      </button>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ marginBottom: 0 }}>{plan.name}</h2>
          <span className={`badge ${plan.status === "active" ? "badge-cardio" : "badge-strength"}`}>
            {plan.status}
          </span>
        </div>
        <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginTop: 8 }}>{plan.goal}</p>
        <p style={{ color: "var(--text-muted)", fontSize: "0.8125rem", marginTop: 4 }}>
          {plan.start_date} to {plan.end_date} &middot; {plan.total_weeks} weeks &middot; {plan.mesocycle_weeks}-week cycles
        </p>
      </div>

      <BenchmarkProgress benchmarks={allBenchmarks} />

      <div style={{ marginBottom: 16 }}>
        <button className="btn btn-primary" onClick={handleAnalyzeProgress} disabled={analyzingProgress}>
          {analyzingProgress ? "Analyzing..." : "Analyze Progress"}
        </button>
      </div>

      {progressAnalysis && (
        <div className="card">
          <h2>Progress Analysis</h2>
          <p className="analysis-text">{progressAnalysis}</p>
        </div>
      )}

      {(plan.weeks || []).map((week) => (
        <div key={week.id}>
          <WeekCard week={week} />
          {(week.benchmarks || []).filter((b) => !b.completed).map((bm) => (
            <div key={bm.id} style={{ marginLeft: 16, marginBottom: 8 }}>
              {recordingId === bm.id ? (
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="number"
                    value={resultValue}
                    onChange={(e) => setResultValue(e.target.value)}
                    placeholder="Result"
                    style={{ width: 100, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "var(--radius)", color: "var(--text)", padding: "6px 8px", fontSize: "0.8125rem" }}
                  />
                  <input
                    type="text"
                    value={resultNotes}
                    onChange={(e) => setResultNotes(e.target.value)}
                    placeholder="Notes (optional)"
                    style={{ flex: 1, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "var(--radius)", color: "var(--text)", padding: "6px 8px", fontSize: "0.8125rem" }}
                  />
                  <button className="btn btn-primary" style={{ padding: "6px 12px", fontSize: "0.8125rem" }}
                    onClick={() => handleRecordResult(bm.id)}>Save</button>
                  <button className="btn btn-secondary" style={{ padding: "6px 12px", fontSize: "0.8125rem" }}
                    onClick={() => setRecordingId(null)}>Cancel</button>
                </div>
              ) : (
                <button className="btn btn-secondary" style={{ padding: "4px 10px", fontSize: "0.75rem" }}
                  onClick={() => setRecordingId(bm.id)}>
                  Log: {bm.benchmark_name}
                </button>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function TrainingPlans() {
  const [view, setView] = useState("list"); // list | create | detail
  const [plans, setPlans] = useState([]);
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [loading, setLoading] = useState(true);

  async function loadPlans() {
    setLoading(true);
    try {
      const data = await api.getPlans();
      setPlans(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadPlans(); }, []);

  if (view === "create") {
    return (
      <div>
        <button className="btn btn-secondary" onClick={() => setView("list")} style={{ marginBottom: 16 }}>
          &larr; Back
        </button>
        <PlanGenerator onCreated={(id) => { setSelectedPlanId(id); setView("detail"); loadPlans(); }} />
      </div>
    );
  }

  if (view === "detail" && selectedPlanId) {
    return <PlanDetail planId={selectedPlanId} onBack={() => { setView("list"); loadPlans(); }} />;
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2>Training Plans</h2>
        <button className="btn btn-primary" onClick={() => setView("create")}>New Plan</button>
      </div>

      {loading && <div className="loading"><div className="spinner" /> Loading plans...</div>}

      {!loading && plans.length === 0 && (
        <div className="empty-state">
          <p>No training plans yet.</p>
          <p style={{ fontSize: "0.875rem", marginTop: 8 }}>Create a plan to get structured programming with benchmark tracking.</p>
        </div>
      )}

      {plans.map((plan) => (
        <div key={plan.id} className="card" style={{ cursor: "pointer" }}
          onClick={() => { setSelectedPlanId(plan.id); setView("detail"); }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 600, color: "var(--text)", textTransform: "none", letterSpacing: "normal" }}>{plan.name}</h3>
            <span className={`badge ${plan.status === "active" ? "badge-cardio" : "badge-strength"}`}>
              {plan.status}
            </span>
          </div>
          <p style={{ color: "var(--text-muted)", fontSize: "0.8125rem", marginTop: 4 }}>
            {plan.goal}
          </p>
          <p style={{ color: "var(--text-muted)", fontSize: "0.75rem", marginTop: 4 }}>
            {plan.total_weeks} weeks &middot; {plan.start_date} to {plan.end_date}
          </p>
        </div>
      ))}
    </div>
  );
}
