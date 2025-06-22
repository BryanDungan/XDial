<template>
  <li class="mb-2">

    <button
      @click="toggle"
      :class="[
        'flex items-center gap-2 py-1 px-3 rounded w-full text-left',
        node.selected ? 'bg-blue-600 text-white' : 'hover:bg-gray-800'
      ]"
    >
      📞 {{ node.label }}
    </button>


    <ul v-if="node.children && Object.keys(node.children).length > 0" class="ml-4">
      <TreeNode
        v-for="(child, key) in node.children"
        :key="key"
        :node="child"
        :path="`${path}.${key}`"
        @select="$emit('select', `${path}.${key}`, child.label)"
      />

    <span>📞 {{ node.label }}</span>
    <ul v-if="node.children && node.children.length">
      <TreeNode v-for="child in node.children" :key="child.key" :node="child" />

    </ul>
  </li>
</template>

<script setup>

console.log("🔹 Rendering TreeNode:", node);
import { ref, computed } from 'vue'

const props = defineProps({
    node: Object,
    path: String
})
const emit = defineEmits(['select'])

const expanded = ref(false)

function toggle() {
  expanded.value = !expanded.value
  emit('select', props.node.key)
}

const hasChildren = computed(() =>
  props.node?.children && Object.keys(props.node.children).length > 0
)

const childArray = computed(() =>
  hasChildren.value ? Object.values(props.node.children) : []
)
</script>

defineProps({
  node: Object
})
</script>

