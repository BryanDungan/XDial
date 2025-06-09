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