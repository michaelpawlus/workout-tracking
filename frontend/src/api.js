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

async function requestRaw(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
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

  parseImage: (file, prescribedWorkout = null) => {
    const formData = new FormData();
    formData.append("file", file);
    if (prescribedWorkout) {
      formData.append("prescribed_workout", JSON.stringify(prescribedWorkout));
    }
    return requestRaw("/workouts/parse-image", {
      method: "POST",
      body: formData,
    });
  },

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

  // Training Plans
  generatePlan: (data) =>
    request("/plans/generate", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getPlans: () => request("/plans"),

  getPlan: (id) => request(`/plans/${id}`),

  updatePlan: (id, data) =>
    request(`/plans/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getPlanBenchmarks: (planId) => request(`/plans/${planId}/benchmarks`),

  recordBenchmarkResult: (planId, benchmarkId, data) =>
    request(`/plans/${planId}/benchmarks/${benchmarkId}/result`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getPlanProgress: (planId) => request(`/plans/${planId}/progress`),

  // Strava
  getStravaAuthUrl: () => request("/strava/auth-url"),
  getStravaStatus: () => request("/strava/status"),
  getStravaActivities: (perPage = 30) => request(`/strava/activities?per_page=${perPage}`),
  importStravaActivity: (activityId) =>
    request(`/strava/import/${activityId}`, { method: "POST" }),
  disconnectStrava: () =>
    request("/strava/disconnect", { method: "DELETE" }),
};
