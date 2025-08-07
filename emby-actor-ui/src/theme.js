// src/theme.js

export const themes = {
  // ================= 主题一: 赛博科技 =================
  default: {
    name: '赛博科技',
    light: {
      custom: {
        '--card-bg-color': 'rgba(255, 255, 255, 0.85)',
        '--card-border-color': 'rgba(0, 0, 0, 0.1)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.08)',
        '--accent-color': '#007aff', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(0, 122, 255, 0.2)',
        '--text-color': '#1a1a1a',
      },
      naive: {
        common: { primaryColor: '#007aff', bodyColor: '#f0f2f5' },
        Card: { color: '#ffffff' },
        Layout: { siderColor: '#f5f7fa' },
        Menu: {
          itemTextColor: '#4c5b6a', itemIconColor: '#4c5b6a', itemTextColorHover: 'var(--n-common-primary-color)', itemIconColorHover: 'var(--n-common-primary-color)', itemTextColorActive: 'var(--n-common-primary-color)', itemIconColorActive: 'var(--n-common-primary-color)', itemTextColorActiveHover: 'var(--n-common-primary-color)', itemIconColorActiveHover: 'var(--n-common-primary-color)',
        }
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(26, 27, 30, 0.7)',
        '--card-border-color': 'rgba(255, 255, 255, 0.1)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.3)',
        '--accent-color': '#00a1ff', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(0, 161, 255, 0.4)',
        '--text-color': '#ffffff',
      },
      naive: {
        common: { primaryColor: '#00a1ff', bodyColor: '#101014', cardColor: '#1a1a1e' },
        Card: { color: '#1a1a1e' },
        Layout: { siderColor: '#101418' },
        Menu: {
          itemTextColor: '#a8aeb3', itemIconColor: '#a8aeb3', itemTextColorHover: '#ffffff', itemIconColorHover: '#ffffff', itemTextColorActive: 'var(--n-common-primary-color)', itemIconColorActive: 'var(--n-common-primary-color)', itemTextColorActiveHover: 'var(--n-common-primary-color)', itemIconColorActiveHover: 'var(--n-common-primary-color)',
        }
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
        '--accent-color': '#e91e63', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(233, 30, 99, 0.3)',
        '--text-color': '#1a1a1a',
      },
      naive: {
        common: { primaryColor: '#e91e63', bodyColor: '#f0f2f5' },
        Card: { color: 'rgba(255, 255, 255, 0.6)' },
        Layout: { siderColor: 'rgba(250, 245, 255, 0.7)' },
        Menu: {
          itemTextColor: '#6c5f78', itemIconColor: '#6c5f78', itemTextColorActive: 'var(--n-common-primary-color)', itemIconColorActive: 'var(--n-common-primary-color)',
        }
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(30, 30, 30, 0.5)',
        '--card-border-color': 'rgba(255, 255, 255, 0.15)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.4)',
        '--accent-color': '#c33cff', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(195, 60, 255, 0.5)',
        '--text-color': '#f0f0f0',
      },
      naive: {
        common: { primaryColor: '#c33cff', bodyColor: '#101014', cardColor: 'rgba(30, 30, 30, 0.5)' },
        Card: { color: 'rgba(30, 30, 30, 0.5)' },
        Layout: { siderColor: 'rgba(15, 10, 30, 0.6)' },
        Menu: {
          itemTextColor: '#b0a4c7', itemIconColor: '#b0a4c7', itemTextColorActive: '#ffffff', itemIconColorActive: '#ffffff',
        }
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
        '--accent-color': '#ff3d8d', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(255, 61, 141, 0.4)',
        '--text-color': '#1a1a1a',
      },
      naive: {
        common: { primaryColor: '#ff3d8d', bodyColor: '#f0f2f5' },
        Card: { color: 'rgba(240, 230, 255, 0.8)' },
        Layout: { siderColor: '#f2eaff' },
        Menu: {
          itemTextColor: '#6c5f78', itemIconColor: '#6c5f78', itemTextColorActive: 'var(--n-common-primary-color)', itemIconColorActive: 'var(--n-common-primary-color)',
        }
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(29, 15, 54, 0.75)',
        '--card-border-color': 'rgba(255, 110, 199, 0.5)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.5)',
        '--accent-color': '#00f7ff', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(0, 247, 255, 0.6)',
        '--text-color': '#f5f5f5',
      },
      naive: {
        common: { primaryColor: '#00f7ff', bodyColor: '#101014', cardColor: 'rgba(29, 15, 54, 0.75)' },
        Card: { color: 'rgba(29, 15, 54, 0.75)' },
        Layout: { siderColor: '#160b2f' },
        Menu: {
          itemTextColor: '#b39ff3', itemIconColor: '#b39ff3', itemTextColorActive: '#ffffff', itemIconColorActive: '#ffffff',
        }
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
        '--accent-color': '#0d6efd', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(13, 110, 253, 0.3)',
        '--text-color': '#061a40',
      },
      naive: {
        common: { primaryColor: '#0d6efd', bodyColor: '#f0f2f5' },
        Card: { color: 'rgba(230, 245, 255, 0.85)' },
        Layout: { siderColor: '#e6f7ff' },
        Menu: {
          itemTextColor: '#061a40', itemIconColor: '#061a40', itemTextColorActive: 'var(--n-common-primary-color)', itemIconColorActive: 'var(--n-common-primary-color)',
        }
      }
    },
    dark: {
      custom: {
        '--card-bg-color': 'rgba(10, 25, 47, 0.8)',
        '--card-border-color': 'rgba(100, 255, 218, 0.3)',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.4)',
        '--accent-color': '#64ffda', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(100, 255, 218, 0.5)',
        '--text-color': '#ccd6f6',
      },
      naive: {
        common: { primaryColor: '#64ffda', bodyColor: '#0a192f', cardColor: 'rgba(10, 25, 47, 0.8)' },
        Card: { color: 'rgba(10, 25, 47, 0.8)' },
        Layout: { siderColor: '#0a192f' },
        Menu: {
          itemTextColor: '#8892b0', itemIconColor: '#8892b0', itemTextColorActive: 'var(--n-common-primary-color)', itemIconColorActive: 'var(--n-common-primary-color)',
        }
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
        '--accent-color': '#d93025', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(217, 48, 37, 0.4)',
        '--text-color': '#000000',
      },
      naive: {
        common: { primaryColor: '#d93025', bodyColor: '#f0f0f0' },
        Card: { color: '#f0f0f0' },
        Layout: { siderColor: '#e6e6e6' },
        Menu: {
          itemTextColor: '#333333', itemIconColor: '#333333', itemTextColorActive: '#ffffff', itemIconColorActive: '#ffffff', itemTextColorActiveHover: '#ffffff', itemIconColorActiveHover: '#ffffff',
        }
      }
    },
    dark: {
      custom: {
        '--card-bg-color': '#2c2c2c',
        '--card-border-color': '#000000',
        '--card-shadow-color': 'rgba(0, 0, 0, 0.7)',
        '--accent-color': '#ffcc00', // ★★★ 灵魂归位！★★★
        '--accent-glow-color': 'rgba(255, 204, 0, 0.5)',
        '--text-color': '#ffffff',
      },
      naive: {
        common: { primaryColor: '#ffcc00', bodyColor: '#1e1e1e', cardColor: '#2c2c2c' },
        Card: { color: '#2c2c2c' },
        Layout: { siderColor: '#3a3a3a' },
        Menu: {
          itemTextColor: '#e0e0e0', itemIconColor: '#e0e0e0', itemTextColorActive: '#000000', itemIconColorActive: '#000000', itemTextColorActiveHover: '#000000', itemIconColorActiveHover: '#000000',
        }
      }
    }
  },
};