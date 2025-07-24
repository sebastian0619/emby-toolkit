<template>
  <n-form label-placement="left" label-width="auto" :model="configModel">
    <n-form-item label="起始年份">
      <n-input-number v-model:value="configModel.start_year" placeholder="例如: 2010" />
      <n-text depth="3" style="margin-left: 10px;">只追踪此年份之后的作品</n-text>
    </n-form-item>
    <n-form-item label="媒体类型">
      <n-checkbox-group v-model:value="configModel.media_types">
        <n-space>
          <n-checkbox value="Movie" label="电影" />
          <n-checkbox value="TV" label="电视剧" />
        </n-space>
      </n-checkbox-group>
    </n-form-item>
    <n-form-item label="包含的题材">
      <n-select
        v-model:value="configModel.genres_include"
        multiple
        placeholder="留空则包含所有题材"
        :options="genreOptions"
      />
    </n-form-item>
    <n-form-item label="排除的题材">
      <n-select
        v-model:value="configModel.genres_exclude"
        multiple
        placeholder="选择要排除的题材"
        :options="genreOptions"
      />
    </n-form-item>
    <n-form-item label="最低评分">
      <n-input-number v-model:value="configModel.min_rating" :min="0" :max="10" :step="0.1" style="width: 100%;" placeholder="0.0"/>
      <template #feedback>
        <n-text depth="3">
          设置为 0 表示不筛选。系统将自动对发布未满6个月的新影片豁免此规则。
        </n-text>
      </template>
    </n-form-item>
  </n-form>
</template>

<script setup>
import { computed } from 'vue';
import { NForm, NFormItem, NInputNumber, NCheckboxGroup, NCheckbox, NSelect, NSpace, NText } from 'naive-ui';

// [待办] 未来可以从API获取TMDb的题材列表，现在先用硬编码的
const genreOptions = [
  { label: '动作', value: 28 },
  { label: '冒险', value: 12 },
  { label: '动画', value: 16 },
  { label: '喜剧', value: 35 },
  { label: '犯罪', value: 80 },
  { label: '纪录', value: 99 },
  { label: '剧情', value: 18 },
  { label: '家庭', value: 10751 },
  { label: '奇幻', value: 14 },
  { label: '历史', value: 36 },
  { label: '恐怖', value: 27 },
  { label: '音乐', value: 10402 },
  { label: '悬疑', value: 9648 },
  { label: '爱情', value: 10749 },
  { label: '科幻', value: 878 },
  { label: '电视电影', value: 10770 },
  { label: '惊悚', value: 53 },
  { label: '战争', value: 10752 },
  { label: '西部', value: 37 },
  { label: '综艺-真人秀', value: 10767 },
  { label: '综艺-脱口秀', value: 10764 },
];

const props = defineProps({
  // ★★★ 核心修改：将 prop 名改为 modelValue，这是 v-model 的标准用法 ★★★
  modelValue: Object,
});

const emit = defineEmits(['update:modelValue']);

// ★★★ 使用 computed 属性来同步 props 和 emits，这是 v-model 的标准实现方式 ★★★
const configModel = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
});
</script>