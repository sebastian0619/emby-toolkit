// src/theme.js (究极完整补完版)

// 这是我们所有主题的定义中心
// 每个主题都包含 light 和 dark 两种模式
// 每种模式又包含 custom (给全局CSS用) 和 naive (给NaiveUI用) 两部分
export const themes = {
  // ================= 主题一: 赛博科技 =================
  default: {
    name: '赛博科技',
    light: {
      custom: {
        '--card-bg-color': 'rgba(255, 255, 255, 0.85)',
        '--card-border-color': 'rgba(0, 0, 0, 0.1)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.08)',
        '--accent-color': '#007aff',
        '--accent-glow-color': 'rgba(0, 122, 255, 0.2)',
        '--text-color': '#1a1a1a',
        '--sider-bg-color': '#f5f7fa',
        '--menu-item-text-color': '#4c5b6a',
        '--menu-item-text-active-color': '#007aff',
        '--menu-item-bg-active-color': 'rgba(0, 122, 255, 0.1)',
      },
      naive: {
        common: { primaryColor: '#007aff', bodyColor: '#f0f2f5' },
        Card: { color: '#ffffff', titleTextColor: '#007aff' },
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(26, 27, 30, 0.7)',
        '--card-border-color': 'rgba(255, 255, 255, 0.1)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.3)',
        '--accent-color': '#00a1ff',
        '--accent-glow-color': 'rgba(0, 161, 255, 0.4)',
        '--text-color': '#ffffff',
        '--sider-bg-color': '#101418',
        '--menu-item-text-color': '#a8aeb3',
        '--menu-item-text-active-color': '#ffffff',
        '--menu-item-bg-active-color': 'rgba(0, 161, 255, 0.2)',
      },
      naive: {
        common: { primaryColor: '#00a1ff', bodyColor: '#101014', cardColor: '#1a1a1e' },
        Card: { color: '#1a1a1e', titleTextColor: '#00a1ff' },
      }
    }
  },

  // ================= 主题二: 玻璃拟态 =================
  glass: {
    name: '玻璃拟态',
    light: {
      custom: {
        '--card-bg-color': 'rgba(255, 255, 255, 0.6)',
        '--card-border-color': 'rgba(0, 0, 0, 0.1)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.1)',
        '--accent-color': '#e91e63',
        '--accent-glow-color': 'rgba(233, 30, 99, 0.3)',
        '--text-color': '#1a1a1a',
        '--sider-bg-color': 'rgba(250, 245, 255, 0.7)',
        '--menu-item-text-color': '#6c5f78',
        '--menu-item-text-active-color': '#e91e63',
        '--menu-item-bg-active-color': 'rgba(233, 30, 99, 0.1)',
      },
      naive: {
        common: { primaryColor: '#e91e63', bodyColor: '#f0f2f5' },
        Card: { color: 'rgba(255, 255, 255, 0.6)', titleTextColor: '#e91e63' },
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(30, 30, 30, 0.5)',
        '--card-border-color': 'rgba(255, 255, 255, 0.15)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.4)',
        '--accent-color': '#c33cff',
        '--accent-glow-color': 'rgba(195, 60, 255, 0.5)',
        '--text-color': '#f0f0f0',
        '--sider-bg-color': 'rgba(15, 10, 30, 0.6)',
        '--menu-item-text-color': '#b0a4c7',
        '--menu-item-text-active-color': '#ffffff',
        '--menu-item-bg-active-color': 'rgba(195, 60, 255, 0.25)',
      },
      naive: {
        common: { primaryColor: '#c33cff', bodyColor: '#101014', cardColor: 'rgba(30, 30, 30, 0.5)' },
        Card: { color: 'rgba(30, 30, 30, 0.5)', titleTextColor: '#c33cff' },
      }
    }
  },

  // ================= 主题三: 落日浪潮 =================
  synthwave: {
    name: '落日浪潮',
    light: {
      custom: {
        '--card-bg-color': 'rgba(240, 230, 255, 0.8)',
        '--card-border-color': 'rgba(228, 90, 216, 0.6)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.1)',
        '--accent-color': '#ff3d8d',
        '--accent-glow-color': 'rgba(255, 61, 141, 0.4)',
        '--text-color': '#1a1a1a',
        '--sider-bg-color': '#f2eaff',
        '--menu-item-text-color': '#6c5f78',
        '--menu-item-text-active-color': '#ff3d8d',
        '--menu-item-bg-active-color': 'rgba(255, 61, 141, 0.1)',
      },
      naive: {
        common: { primaryColor: '#ff3d8d', bodyColor: '#f0f2f5' },
        Card: { color: 'rgba(240, 230, 255, 0.8)', titleTextColor: '#ff3d8d' },
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(29, 15, 54, 0.75)',
        '--card-border-color': 'rgba(255, 110, 199, 0.5)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.5)',
        '--accent-color': '#00f7ff',
        '--accent-glow-color': 'rgba(0, 247, 255, 0.6)',
        '--text-color': '#f5f5f5',
        '--sider-bg-color': '#160b2f',
        '--menu-item-text-color': '#b39ff3',
        '--menu-item-text-active-color': '#ffffff',
        '--menu-item-bg-active-color': 'rgba(0, 247, 255, 0.2)',
      },
      naive: {
        common: { primaryColor: '#00f7ff', bodyColor: '#101014', cardColor: 'rgba(29, 15, 54, 0.75)' },
        Card: { color: 'rgba(29, 15, 54, 0.75)', titleTextColor: '#00f7ff' },
      }
    }
  },

  // ================= 主题四: 全息机甲 =================
  holo: {
    name: '全息机甲',
    light: {
      custom: {
        '--card-bg-color': 'rgba(230, 245, 255, 0.85)',
        '--card-border-color': 'rgba(20, 120, 220, 0.4)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.08)',
        '--accent-color': '#0d6efd',
        '--accent-glow-color': 'rgba(13, 110, 253, 0.3)',
        '--text-color': '#061a40',
        '--sider-bg-color': '#e6f7ff',
        '--menu-item-text-color': '#061a40',
        '--menu-item-text-active-color': '#0d6efd',
        '--menu-item-bg-active-color': 'rgba(13, 110, 253, 0.1)',
      },
      naive: {
        common: { primaryColor: '#0d6efd', bodyColor: '#f0f2f5' },
        Card: { color: 'rgba(230, 245, 255, 0.85)', titleTextColor: '#0d6efd' },
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(10, 25, 47, 0.8)',
        '--card-border-color': 'rgba(100, 255, 218, 0.3)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.4)',
        '--accent-color': '#64ffda',
        '--accent-glow-color': 'rgba(100, 255, 218, 0.5)',
        '--text-color': '#ccd6f6',
        '--sider-bg-color': '#0a192f',
        '--menu-item-text-color': '#8892b0',
        '--menu-item-text-active-color': '#64ffda',
        '--menu-item-bg-active-color': 'rgba(100, 255, 218, 0.1)',
      },
      naive: {
        common: { primaryColor: '#64ffda', bodyColor: '#0a192f', cardColor: 'rgba(10, 25, 47, 0.8)' },
        Card: { color: 'rgba(10, 25, 47, 0.8)', titleTextColor: '#64ffda' },
      }
    }
  },

  // ================= 主题五: 美漫硬派 =================
  comic: {
    name: '美漫硬派',
    light: {
      custom: {
        '--card-bg-color': '#f0f0f0',
        '--card-border-color': '#000000',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.2)',
        '--accent-color': '#d93025',
        '--accent-glow-color': 'rgba(217, 48, 37, 0.4)',
        '--text-color': '#000000',
        '--sider-bg-color': '#e6e6e6',
        '--menu-item-text-color': '#333333',
        '--menu-item-text-active-color': '#ffffff',
        '--menu-item-bg-active-color': '#d93025',
      },
      naive: {
        common: { primaryColor: '#d93025', bodyColor: '#f0f0f0' },
        Card: { color: '#f0f0f0', titleTextColor: '#d93025' },
      }
    },
    dark: {
      custom: {
        '--card-bg-color': '#2c2c2c',
        '--card-border-color': '#000000',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.7)',
        '--accent-color': '#ffcc00',
        '--accent-glow-color': 'rgba(255, 204, 0, 0.5)',
        '--text-color': '#ffffff',
        '--sider-bg-color': '#3a3a3a',
        '--menu-item-text-color': '#e0e0e0',
        '--menu-item-text-active-color': '#000000',
        '--menu-item-bg-active-color': '#ffcc00',
      },
      naive: {
        common: { primaryColor: '#ffcc00', bodyColor: '#1e1e1e', cardColor: '#2c2c2c' },
        Card: { color: '#2c2c2c', titleTextColor: '#ffcc00' },
      }
    }
  },
};