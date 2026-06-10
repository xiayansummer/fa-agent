Component({
  properties: {
    visible: { type: Boolean, value: false },
    agentTitle: { type: String, value: '' },
    body: { type: String, value: '' },
  },
  data: {
    editText: '',
    submitting: false,
  },
  observers: {
    'visible': function(visible) {
      // 每次打开都是新一轮审核：强制复位 submitting，并用最新 body 填充编辑框。
      // 否则上一次提交后 submitting 残留为 true，重开会直接卡在「提交中...」。
      if (visible) {
        this.setData({ submitting: false, editText: this.data.body });
      }
    },
    'body': function(body) {
      if (this.data.visible && !this.data.submitting) {
        this.setData({ editText: body });
      }
    },
  },
  methods: {
    onClose() {
      this.triggerEvent('close');
    },
    onEditInput(e) {
      this.setData({ editText: e.detail.value });
    },
    onApprove() {
      // 直接通过原内容
      this.setData({ submitting: true });
      this.triggerEvent('action', { action: 'approve' });
    },
    onReject() {
      this.setData({ submitting: true });
      this.triggerEvent('action', { action: 'reject' });
    },
    onSaveDraft() {
      // modify 但不立即通过
      this.setData({ submitting: true });
      this.triggerEvent('action', { action: 'modify', final: this.data.editText });
    },
    onApproveModified() {
      // modify 并通过
      this.setData({ submitting: true });
      this.triggerEvent('action', { action: 'modify_and_approve', final: this.data.editText });
    },
    resetSubmitting() {
      this.setData({ submitting: false });
    },
  },
});
