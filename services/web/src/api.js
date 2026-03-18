const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function api(path, options) {
  const res = await fetch(API_URL + path, options);
  if (!res.ok) {
    let detail = "";
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j); } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}
