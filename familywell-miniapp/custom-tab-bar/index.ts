Component({
  data: {
    active: 0,
    list: [
      { icon: '🏠', text: '首页', pagePath: '/pages/home/home' },
      { icon: '📂', text: '归档', pagePath: '/pages/archive/archive' },
      { icon: '💬', text: '助手', pagePath: '/pages/chat/chat' },
      { icon: '⚙️', text: '设置', pagePath: '/pages/settings/settings' },
    ],
  },
  methods: {
    switchTab(e: any) {
      const index = e.currentTarget.dataset.index
      const item = this.data.list[index]
      wx.switchTab({ url: item.pagePath })
    },
  },
})
