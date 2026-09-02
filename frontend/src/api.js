const BASE = import.meta.env.VITE_API_URL || "/api";

export async function runQuery(question) {
  const response = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

export async function fetchSchema() {
  const response = await fetch(`${BASE}/schema`);
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}
