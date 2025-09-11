<!-- src/components/settings/MediaCleanupSettingsPage.vue (新文件) -->
<template>
  <n-spin :show="loading">
    <n-space vertical :size="24">
      <n-card :bordered="false">
        <template #header>
          <span style="font-size: 1.2em; font-weight: bold;">媒体清理决策规则</span>
        </template>
        <p style="margin-top: 0; color: #888;">
          当检测到多版本或重复项时，系统将按照以下规则顺序（从上到下）进行比较，以决定保留哪个版本。<br>
          拖拽规则可以调整优先级。第一个能区分出优劣的规则将决定结果。
        </p>
      </n-card>

      <!-- 规则列表 -->
      <draggable
        v-model="rules"
        item-key="id"
        handle=".drag-handle"
        class="rules-list"
      >
        <template #item="{ element: rule }">
          <n-card class="rule-card" :key="rule.id">
            <div class="rule-content">
              <n-icon class="drag-handle" :component="DragHandleIcon" size="20" />
              <div class="rule-details">
                <span class="rule-name">{{ getRuleDisplayName(rule.id) }}</span>
                <n-text :depth="3" class="rule-description">{{ getRuleDescription(rule.id) }}</n-text>
              </div>
              <n-space class="rule-actions">
                <n-button v-if="rule.id !== 'filesize'" text @click="openEditModal(rule)">
                  <template #icon><n-icon :component="EditIcon" /></template>
                </n-button>
                <n-switch v-model:value="rule.enabled" />
              </n-space>
            </div>
          </n-card>
        </template>
      </draggable>

      <div style="display: flex; justify-content: flex-end; gap: 12px; margin-top: 16px;">
        <n-button @click="fetchRules">重置更改</n-button>
        <n-button type="primary" @click="saveRules" :loading="saving">保存规则</n-button>
      </div>

      <!-- 优先级编辑弹窗 -->
      <n-modal v-model:show="showEditModal" preset="card" style="width: 500px;" title="编辑优先级">
        <p style="margin-top: 0; color: #888;">
          拖拽下方的标签来调整关键字的优先级。排在越上面的关键字，代表版本越好。
        </p>
        <draggable
          v-model="currentEditingRule.priority"
          item-key="item"
          class="priority-tags-list"
        >
          <template #item="{ element: tag }">
            <n-tag class="priority-tag" type="info" size="large">{{ tag }}</n-tag>
          </template>
        </draggable>
        <template #footer>
          <n-button @click="showEditModal = false">完成</n-button>
        </template>
      </n-modal>

    </n-space>
  </n-spin>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';
import { 
  NCard, NSpace, NSwitch, NButton, useMessage, NSpin, NIcon, NModal, NTag, NText
} from 'naive-ui';
import draggable from 'vuedraggable';
import { 
  Pencil as EditIcon, Move as DragHandleIcon,
} from '@vicons/ionicons5';

const message = useMessage();

const loading = ref(true);
const saving = ref(false);
const showEditModal = ref(false);

const rules = ref([]);
const currentEditingRule = ref({ priority: [] });

const RULE_METADATA = {
  quality: {
    name: "按质量",
    description: "比较文件名中的质量标签 (如 Remux, BluRay)。"
  },
  resolution: {
    name: "按分辨率",
    description: "比较视频的分辨率 (如 2160p, 1080p)。"
  },
  filesize: {
    name: "按文件大小",
    description: "如果以上规则都无法区分，则保留文件体积更大的版本。"
  }
};

const getRuleDisplayName = (id) => RULE_METADATA[id]?.name || id;
const getRuleDescription = (id) => RULE_METADATA[id]?.description || '未知规则';

const fetchRules = async () => {
  loading.value = true;
  try {
    const response = await axios.get('/api/cleanup/rules');
    rules.value = response.data;
  } catch (error) {
    message.error('加载清理规则失败！');
  } finally {
    loading.value = false;
  }
};

const saveRules = async () => {
  saving.value = true;
  try {
    await axios.post('/api/cleanup/rules', rules.value);
    message.success('清理规则已成功保存！');
  } catch (error) {
    message.error('保存规则失败，请检查后端日志。');
  } finally {
    saving.value = false;
  }
};

const openEditModal = (rule) => {
  currentEditingRule.value = rule;
  showEditModal.value = true;
};

onMounted(fetchRules);
</script>

<style scoped>
.rules-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rule-card {
  cursor: pointer;
}
.rule-content {
  display: flex;
  align-items: center;
  gap: 16px;
}
.drag-handle {
  cursor: grab;
  color: #888;
}
.rule-details {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
}
.rule-name {
  font-weight: bold;
}
.rule-description {
  font-size: 0.9em;
}
.rule-actions {
  margin-left: auto;
}
.priority-tags-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background-color: var(--n-color-embedded);
  padding: 12px;
  border-radius: 8px;
}
.priority-tag {
  cursor: grab;
  width: 100%;
  justify-content: center;
}
</style>