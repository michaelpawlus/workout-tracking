import { useState } from "react";
import GenerateWorkout from "./components/GenerateWorkout";
import ActiveWorkout from "./components/ActiveWorkout";
import LogWorkout from "./components/LogWorkout";
import History from "./components/History";
import PRBoard from "./components/PRBoard";
import ProgressAnalysis from "./components/ProgressAnalysis";
import TrainingPlans from "./components/TrainingPlans";
import StravaImport from "./components/StravaImport";
import "./App.css";

const TABS = [
  { id: "generate", label: "Program" },
  { id: "log", label: "Log" },
  { id: "plans", label: "Plans" },
  { id: "history", label: "History" },
  { id: "prs", label: "PRs" },
  { id: "import", label: "Import" },
  { id: "progress", label: "Progress" },
];

export default function App() {
  const [tab, setTab] = useState("generate");
  const [activeWorkout, setActiveWorkout] = useState(null);

  function handleWorkoutGenerated(workout) {
    setActiveWorkout(workout);
  }

  function handleWorkoutLogged() {
    setActiveWorkout(null);
    setTab("history");
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>WOD Tracker</h1>
        <nav className="tab-nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab-btn ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="app-main">
        {tab === "generate" && !activeWorkout && (
          <GenerateWorkout onGenerated={handleWorkoutGenerated} />
        )}
        {tab === "generate" && activeWorkout && (
          <ActiveWorkout
            workout={activeWorkout}
            onStartLog={() => setTab("log")}
            onDiscard={() => setActiveWorkout(null)}
          />
        )}
        {tab === "log" && (
          <LogWorkout
            activeWorkout={activeWorkout}
            onSaved={handleWorkoutLogged}
          />
        )}
        {tab === "plans" && <TrainingPlans />}
        {tab === "history" && <History />}
        {tab === "prs" && <PRBoard />}
        {tab === "import" && <StravaImport onImported={() => setTab("history")} />}
        {tab === "progress" && <ProgressAnalysis />}
      </main>
    </div>
  );
}
