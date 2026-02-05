const API = "http://localhost:8000/api";

async function request(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

export const api = {
  // Exercises
  getExercises: () => request("/exercises"),

  // Workout generation
  generateWorkout: (prompt) =>
    request("/workouts/generate", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),

  // Workout logging
  parseLog: (userInput, prescribedWorkout = null) =>
    request("/workouts/parse-log", {
      method: "POST",
      body: JSON.stringify({
        user_input: userInput,
        prescribed_workout: prescribedWorkout,
      }),
    }),

  saveWorkout: (data) =>
    request("/workouts/save", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // History
  getWorkouts: (limit = 20) => request(`/workouts?limit=${limit}`),
  getWorkout: (id) => request(`/workouts/${id}`),

  // PRs
  getPRs: () => request("/prs"),
  confirmPR: (data) =>
    request("/prs/confirm", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Progress
  analyzeProgress: (question) =>
    request("/progress/analyze", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

  // Benchmarks
  getBenchmarks: () => request("/benchmarks"),
};
