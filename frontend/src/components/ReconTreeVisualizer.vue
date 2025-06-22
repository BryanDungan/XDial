
<template>
  <div class="p-4 bg-zinc-900 border border-zinc-700 rounded shadow-md max-w-3xl mx-auto">
    <h2 class="text-xl font-semibold mb-3 text-white">📞 Phone Tree</h2>

    <!-- Show current session ID for debugging -->
    <p class="text-sm text-zinc-400 mb-2">
      🆔 <strong>Session ID:</strong> {{ session?.session_id || session?.id }}
    </p>

    <!-- Breadcrumb trail -->
    <div v-if="selectedPath.length" class="mb-4 text-sm text-zinc-400">
      🔍 Path:
      <span v-for="(label, i) in selectedLabels" :key="i">
        {{ label }} <span v-if="i < selectedLabels.length - 1">›</span>
      </span>
    </div>

    <button
      v-if="selectedPath.length"
      @click="goBack"
      class="text-sm text-blue-400 hover:underline mb-3"
    >
      ← Back
    </button>

    <button
      @click="startCrawl"
      class="bg-purple-600 text-white px-4 py-2 rounded hover:bg-purple-700 mb-4 ml-3"
    >
      ▶️ Start Crawler
    </button>

    <!-- Show the raw keys of the tree always -->
    <p class="text-sm text-green-300 mb-2">
      🌲 Tree Keys: {{ Object.keys(tree || {}) }}
    </p>

    <!-- Always show the raw tree JSON -->
    <pre class="bg-zinc-800 p-2 text-xs text-gray-400 overflow-auto max-h-64">
{{ JSON.stringify(tree, null, 2) }}
    </pre>

    <!-- Still allow normal rendering to happen -->
    <ul>
      <TreeNode
        v-for="(value, key) in tree"
        :key="key"
        :node="value"
        :path="key"
        @select="handleSelect"
      />
    </ul>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import TreeNode from './TreeNode.vue'
import { postSelectionPath, fetchSession } from '@/api'

const props = defineProps({
  session: Object,
  tree: Object
})

const selectedPath = ref([])
const selectedLabels = ref([])

function goBack() {
  selectedPath.value.pop()
  selectedLabels.value.pop()
}

async function startCrawl() {
  const sessionId = props.session?.session_id || props.session?.id
  const phone = props.session?.resolved_number || props.session?.number || "1-800-221-1212"
  const query = props.session?.query || ""

  if (!sessionId || !phone) return

  try {
    await fetch(`${import.meta.env.VITE_API_URL}/start-crawl`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, phone_number: phone, query })
    })
    console.log('✅ Crawl triggered for session:', sessionId)
  } catch (err) {
    console.warn('🚨 Crawl start failed:', err)
  }
}

function handleSelect({ path, label }) {
  console.log('📍 Node selected:', path, label)
  selectedPath.value.push(path)
  selectedLabels.value.push(label)

  const sessionId = props.session?.session_id || props.session?.id
  if (sessionId) {
    postSelectionPath(selectedPath.value.join('.'), sessionId)
  }
}

// 🔄 Periodically fetch updated session tree from backend
let intervalId = null

onMounted(() => {
  const sessionId = props.session?.session_id || props.session?.id
  if (!sessionId) return

  intervalId = setInterval(async () => {
    try {
      const refreshed = await fetchSession(sessionId)
      if (refreshed?.tree) {
        props.session.tree = refreshed.tree
        console.log('🔄 Tree auto-refreshed')
      }
    } catch (err) {
      console.warn('❌ Failed to refresh tree:', err)
    }
  }, 5000) // every 5 seconds
})

onBeforeUnmount(() => {
  if (intervalId) clearInterval(intervalId)
})
</script>
=======
// /frontend/src/components/ReconTreeVisualizer.vue
<template>
  <div class="mt-10 bg-white rounded-lg shadow p-6">
    <h2 class="text-lg font-bold mb-4">📞 Phone Tree</h2>

    <!-- ✅ Session Metadata Preview -->
    <div v-if="metadata" class="mb-4 text-sm text-gray-600">
      <p><strong>🗣️ Query:</strong> {{ metadata.query }}</p>
      <p><strong>📡 Status:</strong> {{ metadata.status }}</p>
      <p><strong>📅 Created:</strong> {{ metadata.created_at }}</p>
    </div>

    <ul class="pl-4 border-l-2 border-gray-300">
      <TreeNode v-for="node in tree" :key="node.key" :node="node" />
    </ul>

    <pre class="text-xs text-gray-400">{{ tree }}</pre>
  </div>
</template>


<script setup>
import { ref, onMounted } from 'vue'
import { getDatabase, ref as dbRef, onValue } from 'firebase/database'
import { getApp } from 'firebase/app'
import TreeNode from './TreeNode.vue'

const tree = ref([])
const metadata = ref({})
const sessionId = localStorage.getItem('latestSession')

onMounted(() => {
  if (!sessionId) return

  const db = getDatabase(getApp())
  const sessionRef = dbRef(db, `/sessions/${sessionId}`)

  onValue(sessionRef, (snapshot) => {
    const fullData = snapshot.val() || {}

    // 🧠 Split metadata vs tree
    metadata.value = {
      query: fullData.query,
      status: fullData.status,
      created_at: fullData.created_at
    }

    if (fullData.tree && typeof fullData.tree === 'object') {
      tree.value = Object.keys(fullData.tree).map((key) => ({
        key,
        ...fullData.tree[key]
      }))
    } else {
      tree.value = []
    }

    console.log("🌳 Tree:", tree.value)
    console.log("📋 Metadata:", metadata.value)
  })
})
</script>


<style scoped>
ul li::marker {
  content: '';
}
</style>

