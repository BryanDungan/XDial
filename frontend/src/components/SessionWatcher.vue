<template>
  <div class="p-4 max-w-2xl mx-auto text-white">
    <h1 class="text-2xl font-bold mb-4">Start Recon Session</h1>

    <!-- Input & Button -->
    <div class="flex gap-2 mb-4">
      <input
        v-model="query"
        @keyup.enter="startSession"
        type="text"
        placeholder="Enter your query"
        class="border px-3 py-2 rounded w-full bg-gray-800 text-white"
      />
      <button
        @click="startSession"
        class="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700"
      >
        Start Recon
      </button>
    </div>

    <!-- Session Info -->
    <div v-if="session" class="mt-6 space-y-2 text-sm text-gray-300">
      <div>🧠 <strong>Query:</strong> {{ session.query }}</div>
      <div>⏰ <strong>Created:</strong> {{ session.created_at }}</div>
      <div>🆔 <strong>ID:</strong> {{ session.session_id || session.id }}</div>
    </div>

    <!-- Tree Visualizer -->
    <ReconTreeVisualizer
      v-if="session && session.tree"
      :session="session"
      :tree="session.tree"
    />
  </div>
</template>

<script setup>
import { ref, watchEffect } from 'vue'
import ReconTreeVisualizer from './ReconTreeVisualizer.vue'
import { fetchSession } from '@/api'

const session = ref(null)
const query = ref('')

// Optional debugging to confirm state changes
watchEffect(() => {
  console.log('🧠 Session Updated:', session.value)
})

async function startSession() {
  if (!query.value.trim()) return;

  try {
    const res = await fetch(`${import.meta.env.VITE_API_URL}/start-recon`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: query.value,
        user_id: 'user-123'
      })
    });

    const data = await res.json()
    session.value = data // Set session from backend response

    // 🔄 Wait and refresh tree (optional)
    setTimeout(async () => {
      const refreshed = await fetchSession(data.session_id)
      if (refreshed?.query) {
        session.value = { ...session.value, ...refreshed }
      }
    }, 2000)

  } catch (err) {
    console.error('Failed to start recon:', err)
  }
}
</script>
