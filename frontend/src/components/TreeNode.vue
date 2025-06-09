<template>
  <li class="mb-2">
    <span>📞 {{ node.label }}</span>
    <ul v-if="node.children && node.children.length">
      <TreeNode v-for="child in node.children" :key="child.key" :node="child" />
    </ul>
  </li>
</template>

<script setup>
defineProps({
  node: Object
})
</script>
