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
    'body, visible': function(body, visible) {
      if (visible && !this.data.submitting) {
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
