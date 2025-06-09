<template>
  <div class="max-w-xl mx-auto mt-10 p-6 bg-white rounded-xl shadow">
    <h2 class="text-xl font-bold mb-4">Start Recon Session</h2>

    <form @submit.prevent="startSession" class="space-y-4">
      <input
        v-model="query"
        class="w-full border p-2 rounded"
        placeholder="Talk to HR at Delta"
      />
      <button
        class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
        type="submit"
      >
        Start Recon
      </button>
    </form>

    <div v-if="showMetadata" class="mt-6 space-y-2">
      <p class="text-gray-700">📡 <strong>Status:</strong> {{ metadata.status || 'Loading...' }}</p>
      <p class="text-gray-700">🗣️ <strong>Query:</strong> {{ metadata.query || 'Loading...' }}</p>
      <p class="text-sm text-gray-500"><strong>🕒 Created:</strong> {{ metadata.created_at || '...' }}</p>
      <p class="text-gray-500 text-sm"><strong>ID:</strong> {{ sessionId }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { initializeApp } from 'firebase/app'
import { getDatabase, ref as dbRef, onValue } from 'firebase/database'
import { getAnalytics } from 'firebase/analytics'

const query = ref('')
const sessionId = ref(null)
const session = ref(null)
const metadata = ref({})
const showMetadata = ref(false)

// ✅ Firebase config
const firebaseConfig = {
  apiKey: "AIzaSyA02CfTBLQ_sFXtIfMjatJA-7G_f0Z2myA",
  authDomain: "x-dial-realtime.firebaseapp.com",
  databaseURL: "https://x-dial-realtime-default-rtdb.firebaseio.com",
  projectId: "x-dial-realtime",
  storageBucket: "x-dial-realtime.firebasestorage.app",
  messagingSenderId: "439266499379",
  appId: "1:439266499379:web:98ffe7f10840e8cdb2bc46",
  measurementId: "G-DR0TL59ZX8"
}

// ✅ Init Firebase
const app = initializeApp(firebaseConfig)
const db = getDatabase(app)
const analytics = getAnalytics(app)

async function startSession() {
  console.log("▶️ startSession triggered")

  const response = await fetch('http://127.0.0.1:8000/start-recon', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: 'demo-user', query: query.value })
  })

  const data = await response.json()
  sessionId.value = data.session_id
  localStorage.setItem('latestSession', data.session_id)
  showMetadata.value = true

  const firebaseSessionRef = dbRef(db, `/sessions/${data.session_id}`)
  onValue(firebaseSessionRef, (snapshot) => {
    const result = snapshot.val() || {}
    session.value = result

    metadata.value = {
      query: result.query,
      status: result.status,
      created_at: result.created_at
    }

    console.log("📡 Metadata:", metadata.value)
  })
}
</script>

<style scoped>
body {
  font-family: sans-serif;
}
</style>
