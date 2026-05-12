import { api } from '../../services/api';

interface FormState {
  name: string;
  agency: string;
  position: string;
  avatar_url: string;
  business_card_url: string;
  familiarity: string;
  relationship_score: number;
  industry_tags: string[];
  stage_pref: string[];
  profile_notes: string;
  birthday: string;
}

const FAMILIARITY_OPTIONS = ['未接触', '加过微信', '见过面', '了解投资偏好', '跟进过我们的项目', '好友'];

interface PageData {
  isEdit: boolean;
  investorId: number;
  /** 从企名片搜索带过来的 person_id：表示"加入我的库" */
  qmingpianPersonId: string;
  form: FormState;
  saving: boolean;
  industryOptions: string[];
  stageOptions: string[];
  familiarityOptions: string[];
}

const INDUSTRY_OPTS = ['消费', 'TMT', '医疗', 'AI', 'SaaS', '硬件', '教育', '金融'];
const STAGE_OPTS = ['天使', 'A轮', 'B轮', 'C轮', 'D轮', 'Pre-IPO'];

Page<PageData, {}>({
  data: {
    isEdit: false,
    investorId: 0,
    qmingpianPersonId: '',
    form: {
      name: '',
      agency: '',
      position: '',
      avatar_url: '',
      business_card_url: '',
      familiarity: '',
      relationship_score: 0,
      industry_tags: [],
      stage_pref: [],
      profile_notes: '',
      birthday: '',
    },
    saving: false,
    industryOptions: INDUSTRY_OPTS,
    stageOptions: STAGE_OPTS,
    familiarityOptions: FAMILIARITY_OPTIONS,
  },

  onLoad(opts: { id?: string; qmingpian_person_id?: string; name?: string; agency?: string }) {
    if (opts.id) {
      const id = parseInt(opts.id);
      this.setData({ isEdit: true, investorId: id });
      this._load();
      wx.setNavigationBarTitle({ title: '编辑投资人' });
    } else if (opts.qmingpian_person_id) {
      // 从企名片搜索"加入我的库"过来：URL 参数是 encodeURIComponent 过的，要 decode
      const personId = decodeURIComponent(opts.qmingpian_person_id);
      const name = opts.name ? decodeURIComponent(opts.name) : '';
      const agency = opts.agency ? decodeURIComponent(opts.agency) : '';
      this.setData({
        qmingpianPersonId: personId,
        'form.name': name,
        'form.agency': agency,
      });
      wx.setNavigationBarTitle({ title: '加入我的库' });
      // 按姓名从企名片 enrich（机构/手机/邮箱/行业）
      this._enrichFromQmingpian(name);
    } else {
      wx.setNavigationBarTitle({ title: '新增投资人' });
    }
  },

  async _enrichFromQmingpian(personName: string) {
    if (!personName) return;
    try {
      const enriched = await api.get<{
        agency?: string;
        position?: string;
        avatar_url?: string;
        business_card_url?: string;
        phone?: string[];
        email?: string[];
        industry?: string;
      }>(`/api/investors/qmingpian/by-name?person_name=${encodeURIComponent(personName)}`,
         { silent: true });
      if (!enriched) return;
      const patch: any = {};
      // 只在表单为空时回填，避免覆盖 IR 已输入的
      if (enriched.agency && !this.data.form.agency) patch['form.agency'] = enriched.agency;
      if (enriched.position && !this.data.form.position) patch['form.position'] = enriched.position;
      if (enriched.avatar_url && !this.data.form.avatar_url) patch['form.avatar_url'] = enriched.avatar_url;
      if (enriched.business_card_url && !this.data.form.business_card_url) patch['form.business_card_url'] = enriched.business_card_url;
      if (Object.keys(patch).length) this.setData(patch);
    } catch {
      // 静默失败
    }
  },

  async _load() {
    try {
      const inv = await api.get<any>(`/api/investors/${this.data.investorId}`);
      this.setData({
        form: {
          name: inv.name || '',
          agency: inv.agency || '',
          position: inv.position || '',
          avatar_url: inv.avatar_url || '',
          business_card_url: inv.business_card_url || '',
          familiarity: inv.familiarity || '',
          relationship_score: inv.relationship_score || 0,
          industry_tags: inv.industry_tags || [],
          stage_pref: inv.stage_pref || [],
          profile_notes: inv.profile_notes || '',
          birthday: inv.birthday || '',
        },
      });
    } catch (e) {/* toast 由 api 处理 */}
  },

  onField(e: WechatMiniprogram.Input) {
    const field = e.currentTarget.dataset.field as keyof FormState;
    this.setData({ [`form.${field}`]: e.detail.value });
  },

  onScoreTap(e: WechatMiniprogram.TouchEvent) {
    const score = parseInt(e.currentTarget.dataset.score as string);
    this.setData({ 'form.relationship_score': score });
  },

  onTagToggle(e: WechatMiniprogram.TouchEvent) {
    const field = e.currentTarget.dataset.field as 'industry_tags' | 'stage_pref';
    const tag = e.currentTarget.dataset.tag as string;
    const list = this.data.form[field] || [];
    const next = list.includes(tag) ? list.filter(t => t !== tag) : [...list, tag];
    this.setData({ [`form.${field}`]: next });
  },

  onFamiliarityTap(e: WechatMiniprogram.TouchEvent) {
    const value = e.currentTarget.dataset.value as string;
    // 再次点击同一个 → 取消（设为 ""）
    const next = this.data.form.familiarity === value ? '' : value;
    this.setData({ 'form.familiarity': next });
  },

  onBirthdayChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ 'form.birthday': e.detail.value as string });
  },

  async onSave() {
    if (!this.data.form.name.trim()) {
      wx.showToast({ title: '姓名必填', icon: 'none' });
      return;
    }

    this.setData({ saving: true });
    try {
      // 过滤空字段
      const payload: any = {};
      Object.entries(this.data.form).forEach(([k, v]) => {
        if (v === '' || v === null || (Array.isArray(v) && v.length === 0)) return;
        payload[k] = v;
      });
      // 确保 name 总在
      payload.name = this.data.form.name;

      if (this.data.isEdit) {
        await api.put(`/api/investors/${this.data.investorId}`, payload);
        wx.showToast({ title: '已保存', icon: 'success' });
      } else {
        // 加入我的库时带 qmingpian_person_id（跳过企名片 add）
        if (this.data.qmingpianPersonId) {
          payload.qmingpian_person_id = this.data.qmingpianPersonId;
        }
        await api.post('/api/investors', payload);
        wx.showToast({ title: '已创建', icon: 'success' });
      }
      setTimeout(() => wx.navigateBack(), 800);
    } catch (e) {/* api toast handled */} finally {
      this.setData({ saving: false });
    }
  },

  async _uploadImage(purpose: 'image'): Promise<string | null> {
    const file = await new Promise<any>((resolve) => {
      wx.chooseImage({
        count: 1,
        sizeType: ['compressed'],
        sourceType: ['album', 'camera'],
        success: r => resolve(r.tempFiles[0]),
        fail: () => resolve(null),
      });
    });
    if (!file) return null;
    if (file.size > 20 * 1024 * 1024) {
      wx.showToast({ title: '图片超过 20MB', icon: 'none' });
      return null;
    }
    wx.showLoading({ title: '上传中...' });
    try {
      const token = await api.post<{ token: string; key: string; upload_url: string }>(
        '/api/upload/token',
        { purpose, filename: file.path.split('/').pop() || 'image.jpg' }
      );
      await new Promise<void>((resolve, reject) => {
        wx.uploadFile({
          url: token.upload_url,
          filePath: file.path,
          name: 'file',
          formData: { token: token.token, key: token.key },
          success: r => r.statusCode === 200 ? resolve() : reject(new Error('upload ' + r.statusCode)),
          fail: (err) => reject(new Error(err.errMsg)),
        });
      });
      const sign = await api.get<{ url: string }>(
        `/api/upload/sign?key=${encodeURIComponent(token.key)}&expires=86400`
      );
      wx.hideLoading();
      return sign.url;
    } catch (e: any) {
      wx.hideLoading();
      wx.showToast({ title: e?.message || '上传失败', icon: 'none' });
      return null;
    }
  },

  async onPickCard() {
    const url = await this._uploadImage('image');
    if (url) this.setData({ 'form.business_card_url': url });
  },

  onClearCard() {
    this.setData({ 'form.business_card_url': '' });
  },

  async onDelete() {
    const ok = await new Promise<boolean>(resolve => {
      wx.showModal({
        title: '确认删除？',
        content: `投资人 ${this.data.form.name} 将被软删除（可恢复）`,
        confirmColor: '#DC2626',
        success: r => resolve(r.confirm),
      });
    });
    if (!ok) return;

    try {
      await api.del(`/api/investors/${this.data.investorId}`);
      wx.showToast({ title: '已删除', icon: 'success' });
      setTimeout(() => {
        // 回到列表（跳两层）
        const pages = getCurrentPages();
        if (pages.length >= 3) {
          wx.navigateBack({ delta: 2 });
        } else {
          wx.navigateBack();
        }
      }, 800);
    } catch (e) {/* toast */}
  },
});
