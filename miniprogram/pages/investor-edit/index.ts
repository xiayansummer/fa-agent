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
  /** 投资人标签（写回企名片 updatePersonTag，本地不存） */
  qmingpian_tags: string[];
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
  /** 行业偏好：只读，从企名片 industry_info 拉取 */
  qmingpianIndustries: string[];
  /** 投资人标签初始值（用于判断是否变化，避免无变化时仍调 updatePersonTag） */
  qmingpianTagsOriginal: string[];
  /** 新标签输入框 */
  tagInput: string;
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
      qmingpian_tags: [],
    },
    saving: false,
    industryOptions: INDUSTRY_OPTS,
    stageOptions: STAGE_OPTS,
    familiarityOptions: FAMILIARITY_OPTIONS,
    qmingpianIndustries: [],
    qmingpianTagsOriginal: [],
    tagInput: '',
  },

  onLoad(opts: {
    id?: string;
    qmingpian_person_id?: string;
    name?: string;
    agency?: string;
    position?: string;
    avatar_url?: string;
    business_card_url?: string;
  }) {
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
      const position = opts.position ? decodeURIComponent(opts.position) : '';
      const avatarUrl = opts.avatar_url ? decodeURIComponent(opts.avatar_url) : '';
      const cardUrl = opts.business_card_url ? decodeURIComponent(opts.business_card_url) : '';
      this.setData({
        qmingpianPersonId: personId,
        'form.name': name,
        'form.agency': agency,
        'form.position': position,
        'form.avatar_url': avatarUrl,
        'form.business_card_url': cardUrl,
      });
      wx.setNavigationBarTitle({ title: '加入我的库' });
      // 按姓名从企名片 enrich（机构/手机/邮箱/行业；同时再次拉名片图）
      this._enrichFromQmingpian(name);
      this._loadQmingpianHit(name, agency);
    } else {
      wx.setNavigationBarTitle({ title: '新增投资人' });
    }
  },

  /** 从企名片拉单条快照填充：投资人标签 + 关注行业 + 职务（缺失时回填）。 */
  async _loadQmingpianHit(name: string, agency: string) {
    if (!name) return;
    try {
      const hit = await api.get<{
        position?: string;
        tags?: string[];
        industries?: string[];
      }>(`/api/investors/qmingpian/searchhit?name=${encodeURIComponent(name)}` +
         (agency ? `&agency=${encodeURIComponent(agency)}` : ''),
         { silent: true });
      if (!hit) return;
      const tags = hit.tags || [];
      const industries = hit.industries || [];
      const patch: any = {
        'form.qmingpian_tags': tags,
        qmingpianTagsOriginal: tags.slice(),
        qmingpianIndustries: industries,
      };
      if (hit.position && !this.data.form.position) {
        patch['form.position'] = hit.position;
      }
      this.setData(patch);
    } catch {
      // 静默失败
    }
  },

  async _enrichFromQmingpian(personName: string) {
    if (!personName) return;
    try {
      const enriched = await api.get<{
        agency?: string; phone?: string[]; email?: string[]; industry?: string;
      }>(`/api/investors/qmingpian/by-name?person_name=${encodeURIComponent(personName)}`,
         { silent: true });
      if (!enriched) return;
      const patch: any = {};
      // 注意：只在表单为空时回填，避免覆盖用户已输入的
      if (enriched.agency && !this.data.form.agency) patch['form.agency'] = enriched.agency;
      // phone 字段类型 list — Form 里没有，先不处理（如果将来加 phone 输入框可补）
      if (Object.keys(patch).length) this.setData(patch);
    } catch {
      // 静默失败（人不在 open_id 范围内，或接口暂不可用）
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
          qmingpian_tags: [],
        },
      });
      // 编辑模式下也从企名片拉标签和行业
      this._loadQmingpianHit(inv.name || '', inv.agency || '');
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

  onTagInput(e: WechatMiniprogram.Input) {
    this.setData({ tagInput: e.detail.value });
  },

  onTagAdd() {
    const v = (this.data.tagInput || '').trim();
    if (!v) return;
    const tags = this.data.form.qmingpian_tags || [];
    if (tags.includes(v)) {
      this.setData({ tagInput: '' });
      return;
    }
    this.setData({
      'form.qmingpian_tags': [...tags, v],
      tagInput: '',
    });
  },

  onTagRemove(e: WechatMiniprogram.TouchEvent) {
    const tag = e.currentTarget.dataset.tag as string;
    const tags = this.data.form.qmingpian_tags || [];
    this.setData({
      'form.qmingpian_tags': tags.filter(t => t !== tag),
    });
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
        if (k === 'qmingpian_tags') return; // 单独处理，允许空数组（清空）
        if (v === '' || v === null || (Array.isArray(v) && v.length === 0)) return;
        payload[k] = v;
      });
      // 确保 name 总在
      payload.name = this.data.form.name;
      // qmingpian_tags 仅当与初始值不同时才发，允许 [] 表示清空
      const curTags = this.data.form.qmingpian_tags || [];
      const origTags = this.data.qmingpianTagsOriginal || [];
      const changed = curTags.length !== origTags.length
        || curTags.some((t, i) => t !== origTags[i]);
      if (changed) {
        payload.qmingpian_tags = curTags;
      }

      let resp: any;
      if (this.data.isEdit) {
        resp = await api.put<any>(`/api/investors/${this.data.investorId}`, payload);
      } else {
        // 加入我的库时带 qmingpian_person_id（跳过企名片 add）
        if (this.data.qmingpianPersonId) {
          payload.qmingpian_person_id = this.data.qmingpianPersonId;
        }
        resp = await api.post<any>('/api/investors', payload);
      }
      const warnings: string[] = (resp && resp.qmingpian_warnings) || [];
      if (warnings.length > 0) {
        // 显示首条 warning（企名片同步失败但本地已保存）
        wx.showModal({
          title: '本地已保存，但企名片同步失败',
          content: warnings.join('\n\n'),
          showCancel: false,
          confirmText: '知道了',
        });
      } else {
        wx.showToast({ title: this.data.isEdit ? '已保存' : '已创建', icon: 'success' });
        setTimeout(() => wx.navigateBack(), 800);
        return;
      }
      setTimeout(() => wx.navigateBack(), 1500);
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
