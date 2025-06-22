// frontend/src/api.js

// Fetch session by ID
export async function fetchSession(sessionId) {
  try {
    const res = await fetch(`/session/${sessionId}`)
    if (!res.ok) throw new Error(`Failed to fetch session: ${res.status}`)
    return await res.json()
  } catch (err) {
    console.error('[API ERROR] fetchSession:', err)
    return null
  }
}

// Update selected tree path
export async function postSelectionPath(path, sessionId) {
  try {
    await fetch(`${import.meta.env.VITE_API_URL}/update-path`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, path })
    })
  } catch (err) {
    console.warn('[API WARN] postSelectionPath failed:', err)
  }
}
