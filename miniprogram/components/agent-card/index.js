Component({
  properties: {
    agent: {
      type: String,
      value: 'orchestrator',  // orchestrator | list | content | outreach
    },
    title: { type: String, value: '' },
    body: { type: String, value: '' },  // 卡片正文（普通文本）
    actions: {
      type: Array,
      value: [],  // [{ label, type: 'approve' | 'modify' | 'reject', primary: bool }]
    },
    showStatus: { type: String, value: '' },  // '已通过' / '已拒绝' / ''
  },
  data: {},
  methods: {
    onAction(e) {
      const action = e.currentTarget.dataset.action;
      this.triggerEvent('action', { action });
    },
  },
});
