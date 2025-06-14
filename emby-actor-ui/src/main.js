// src/main.js

import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import naive from 'naive-ui';
// 全局样式
import './assets/global.css';

const pinia = createPinia();
const app = createApp(App);

// 严格按照 Pinia -> Router -> Naive UI 的顺序注册
app.use(pinia);
app.use(router);
app.use(naive);

app.mount('#app');