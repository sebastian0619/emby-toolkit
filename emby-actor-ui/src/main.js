// src/main.js

import { createApp, h } from 'vue';
import { createPinia } from 'pinia'
import App from './App.vue'; // 你的根组件
import router from './router'; // 你配置的 Vue Router 实例

// 1. 导入整个 Naive UI 库，用于 app.use() 全局注册组件
import naive from 'naive-ui';
import Sortable from 'sortablejs'
// 全局样式
import './assets/global.css'

// 2. 单独导入需要在应用最顶层包裹的 Provider 组件
//    NConfigProvider 通常在 App.vue 或一个专门的布局组件中处理主题等，
//    但如果你希望在 main.js 中统一处理，也可以移到这里。
//    为了保持与你之前 App.vue 结构的一致性（NConfigProvider 在 App.vue 中），
//    我们这里只包裹 Message, Notification, Dialog Provider。
import {
  NMessageProvider,
  NNotificationProvider,
  NDialogProvider
  // 如果需要，也可以在这里导入 NConfigProvider, darkTheme, zhCN, dateZhCN
  // 但通常 NConfigProvider 放在 App.vue 或 AppShell.vue 中更方便管理主题切换
} from 'naive-ui';
window.Sortable = Sortable
const pinia = createPinia();
// 创建 Vue 应用实例
// 我们使用渲染函数 h() 来在顶层包裹 Provider 组件
const app = createApp({
  setup() {
    // 这里返回一个渲染函数，它会渲染 Provider 组件，并将 App 作为其子组件
    return () =>
      h(NMessageProvider, null, {
        default: () =>
          h(NNotificationProvider, null, {
            default: () =>
              h(NDialogProvider, null, {
                default: () => h(App) // App.vue 是所有 Provider 的最终子内容
              })
          })
      });
  }
});

// 3. 使用 Naive UI 插件，这将全局注册所有 Naive UI 组件和指令
//    这样你就可以在任何 .vue 文件的模板中直接使用 <n-button>, <n-input> 等
app.use(naive);

// 4. 使用 Vue Router 插件
app.use(router);
app.use(pinia);

// 5. 挂载应用
app.mount('#app');