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
      value: [],  // [{ label, action: 'approve' | 'modify' | 'reject', primary: bool }]
    },
    showStatus: { type: String, value: '' },  // '已通过' / '已拒绝' / ''
    inlineEditable: { type: Boolean, value: false },  // 是否允许内联编辑
  },
  data: {
    editing: false,
    editText: '',
    submitting: false,
  },
  observers: {
    'body': function(newBody) {
      if (!this.data.editing) this.setData({ editText: newBody });
    },
    'showStatus': function(status) {
      // 审核完成（已通过/已拒绝）后退出编辑态并清掉 submitting，
      // 否则编辑区不会随 showStatus 隐藏，按钮会永远卡在"提交中..."
      if (status) this.setData({ editing: false, submitting: false });
    },
  },
  methods: {
    onAction(e) {
      const action = e.currentTarget.dataset.action;

      if (action === 'modify' && this.data.inlineEditable) {
        // 进入编辑模式
        this.setData({ editing: true, editText: this.data.body });
        return;
      }

      if (action === 'submit_modify') {
        // 提交编辑后的内容
        this.setData({ submitting: true });
        this.triggerEvent('action', { action: 'modify', final: this.data.editText });
        return;
      }

      if (action === 'cancel_modify') {
        this.setData({ editing: false, editText: this.data.body });
        return;
      }

      this.setData({ submitting: true });
      this.triggerEvent('action', { action });
    },

    onEditInput(e) {
      this.setData({ editText: e.detail.value });
    },

    // 父组件可以调用 reset 重置 submitting 状态
    resetSubmitting() {
      this.setData({ submitting: false, editing: false });
    },
  },
});
