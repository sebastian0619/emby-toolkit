// src/components/TestDraggable.vue

<template>
  <div class="test-container">
    <h3>Draggable Test Component (using vuedraggable)</h3>
    
    <!-- ★★★ 2. 组件标签是小写的 <draggable> ★★★ -->
    <draggable
      v-model="myList"
      item-key="id"
      class="list-group"
      tag="ul"
      handle=".handle"
      animation="300"
    >
      <template #item="{ element }">
        <li class="list-item">
          <span class="handle">⠿</span>
          {{ element.name }}
        </li>
      </template>
    </draggable>
    
    <pre>{{ myList }}</pre>
  </div>
</template>

<script setup>
import { ref } from 'vue';
// ★★★ 1. 从 'vuedraggable' 导入，并且是默认导入（没有花括号） ★★★
import draggable from 'vuedraggable';

const myList = ref([
  { id: 1, name: 'Item 1' },
  { id: 2, name: 'Item 2' },
  { id: 3, name: 'Item 3' },
]);
</script>

<style scoped>
.test-container {
  padding: 20px;
  border: 2px solid #ccc;
  margin: 20px;
}
.list-group {
  list-style-type: none;
  padding: 0;
}
.list-item {
  padding: 10px;
  margin: 5px 0;
  border: 1px solid #ddd;
  background-color: #f9f9f9;
  display: flex;
  align-items: center;
}
.handle {
  cursor: grab;
  margin-right: 10px;
  font-size: 1.2em;
}
pre {
  margin-top: 20px;
  background-color: #eee;
  padding: 10px;
}
</style>