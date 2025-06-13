<template>
  <div class="login-container">
    <n-card title="登录" class="login-card">
      <n-form @submit.prevent="handleLogin">
        <n-form-item-row label="用户名">
          <n-input v-model:value="credentials.username" placeholder="请输入用户名" />
        </n-form-item-row>
        <n-form-item-row label="密码">
          <n-input
            type="password"
            show-password-on="mousedown"
            v-model:value="credentials.password"
            placeholder="请输入密码"
          />
        </n-form-item-row>
        <n-button type="primary" attr-type="submit" block :loading="loading">
          登 录
        </n-button>
      </n-form>
    </n-card>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router'; // ★★★ 1. 导入 useRouter ★★★
import { NCard, NForm, NFormItemRow, NInput, NButton, useMessage } from 'naive-ui';
import { useAuthStore } from '../stores/auth';

const router = useRouter(); // ★★★ 2. 获取 router 实例 ★★★
const credentials = ref({
  username: 'admin',
  password: '',
});
const loading = ref(false);
const message = useMessage();
const authStore = useAuthStore();

async function handleLogin() {
  if (!credentials.value.username || !credentials.value.password) {
    message.error('请输入用户名和密码');
    return;
  }
  loading.value = true;
  try {
    await authStore.login(credentials.value);
    message.success('登录成功！');
    
    // ★★★ 3. 登录成功后，跳转到后台主页 ★★★
    // 我们跳转到 'actions-status' 路由，这是你的默认后台页面之一
    router.push({ name: 'actions-status' }); 

  } catch (error) {
    if (error.response && error.response.data.error) {
      message.error(`登录失败: ${error.response.data.error}`);
    } else {
      message.error('登录失败，请检查网络或联系管理员');
    }
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  width: 100%;
}
.login-card {
  width: 100%;
  max-width: 400px;
}
</style>