<!-- src/components/ThemeEditor.vue -->
<template>
  <n-modal :show="show" preset="card" style="width: 90%; max-width: 800px;" title="主题设计工坊" :bordered="false" size="huge" :mask-closable="false" @update:show="handleClose">
    <template #header-extra><n-button @click="handleReset" size="small" secondary>重置为默认</n-button></template>
    <div v-if="editableTheme && editableTheme.naive && editableTheme.custom">
      <n-tabs type="line" animated>
        <n-tab-pane name="colors" tab="核心色彩">
          <n-grid :cols="2" :x-gap="24">
            <n-gi><n-form-item label="UI主色调 (Naive UI)"><n-color-picker v-model:value="editableTheme.naive.common.primaryColor" /></n-form-item></n-gi>
            <n-gi><n-form-item label="卡片标题色 (自定义)"><n-color-picker v-model:value="editableTheme.custom['--accent-color']" /></n-form-item></n-gi>
            <n-gi><n-form-item label="主题辉光色 (Glow)"><n-color-picker v-model:value="editableTheme.custom['--accent-glow-color']" /></n-form-item></n-gi>
          </n-grid>
        </n-tab-pane>
        <n-tab-pane name="sidebar" tab="侧边栏与菜单">
          <n-grid :cols="2" :x-gap="24">
            <n-gi><n-form-item label="侧边栏背景"><n-color-picker v-model:value="editableTheme.naive.Layout.siderColor" /></n-form-item></n-gi>
            <n-gi><n-form-item label="菜单文字"><n-color-picker v-model:value="editableTheme.naive.Menu.itemTextColor" /></n-form-item></n-gi>
            <n-gi><n-form-item label="菜单图标"><n-color-picker v-model:value="editableTheme.naive.Menu.itemIconColor" /></n-form-item></n-gi>
            <n-gi><n-form-item label="菜单激活文字"><n-color-picker v-model:value="editableTheme.naive.Menu.itemTextColorActive" /></n-form-item></n-gi>
          </n-grid>
        </n-tab-pane>
        <n-tab-pane name="cards" tab="卡片样式">
           <n-grid :cols="2" :x-gap="24">
            <n-gi><n-form-item label="卡片背景"><n-color-picker v-model:value="editableTheme.custom['--card-bg-color']" /></n-form-item></n-gi>
            <n-gi><n-form-item label="卡片边框"><n-color-picker v-model:value="editableTheme.custom['--card-border-color']" /></n-form-item></n-gi>
            <n-gi><n-form-item label="卡片阴影"><n-color-picker v-model:value="editableTheme.custom['--card-shadow-color']" /></n-form-item></n-gi>
            <n-gi><n-form-item label="卡片文字"><n-color-picker v-model:value="editableTheme.custom['--text-color']" /></n-form-item></n-gi>
          </n-grid>
        </n-tab-pane>
      </n-tabs>
    </div>
    <div v-else class="fullscreen-container"><n-spin size="large" /><p style="margin-left: 12px;">正在准备刻刀...</p></div>
    <template #footer><n-space justify="end"><n-button @click="handleClose">取消</n-button><n-button type="primary" @click="handleSave">保存并应用</n-button></n-space></template>
  </n-modal>
</template>
<script setup>
import { ref, watch } from 'vue';
import { NModal, NButton, NSpace, NTabs, NTabPane, NGrid, NGi, NFormItem, NColorPicker, NSpin, useMessage } from 'naive-ui';
import { themes } from '../theme.js';
import { cloneDeep } from 'lodash-es';
const props = defineProps({ show: Boolean, initialTheme: Object, isDark: Boolean, });
const emit = defineEmits(['update:show', 'save', 'update:preview']);
const message = useMessage();
const editableTheme = ref(null);
watch(() => props.show, (newVal) => {
  if (newVal && props.initialTheme) {
    const currentModeTheme = props.initialTheme[props.isDark ? 'dark' : 'light'];
    editableTheme.value = cloneDeep(currentModeTheme);
  }
}, { immediate: true });
watch(editableTheme, (newVal) => { if (props.show && newVal) { emit('update:preview', newVal); } }, { deep: true });
const handleSave = () => { emit('save', editableTheme.value); };
const handleClose = () => { emit('update:show', false); };
const handleReset = () => {
    const defaultTheme = themes.default[props.isDark ? 'dark' : 'light'];
    editableTheme.value = cloneDeep(defaultTheme);
    message.info('已重置为默认主题样式。');
};
</script>