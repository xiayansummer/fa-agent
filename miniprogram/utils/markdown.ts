/**
 * 轻量 markdown → HTML，供 wx <rich-text nodes="..."> 渲染。
 * 只处理 LLM chat 输出最常见的几种：
 *   - 段落 / 换行
 *   - **粗体** *斜体* `code`
 *   - - 无序列表 / 1. 有序列表
 *   - GFM 表格 | a | b |
 *   - 水平线 ---
 * 不依赖三方库，输出字符串可直接喂给 rich-text。
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// rich-text 不响应外部 wxss class，必须 inline style。
const STYLE = {
  p:      'style="margin:0 0 10rpx 0;"',
  strong: 'style="font-weight:700;"',
  em:     'style="font-style:italic;"',
  code:   'style="background:#f4f4f5;color:#be185d;padding:2rpx 8rpx;border-radius:4rpx;font-family:monospace;"',
  ul:     'style="margin:8rpx 0 12rpx 0;padding-left:36rpx;"',
  ol:     'style="margin:8rpx 0 12rpx 0;padding-left:36rpx;"',
  li:     'style="margin:4rpx 0;"',
  h1:     'style="font-weight:700;font-size:32rpx;margin:16rpx 0 8rpx 0;"',
  h2:     'style="font-weight:700;font-size:30rpx;margin:16rpx 0 8rpx 0;"',
  h3:     'style="font-weight:700;font-size:28rpx;margin:16rpx 0 8rpx 0;"',
  hr:     'style="border:none;border-top:1rpx solid #e5e7eb;margin:16rpx 0;"',
  table:  'style="border-collapse:collapse;margin:12rpx 0;font-size:24rpx;width:100%;"',
  th:     'style="border:1rpx solid #e5e7eb;padding:8rpx 12rpx;text-align:left;background:#f9fafb;font-weight:600;"',
  td:     'style="border:1rpx solid #e5e7eb;padding:8rpx 12rpx;text-align:left;vertical-align:top;"',
};

function renderInline(s: string): string {
  let out = escapeHtml(s);
  out = out.replace(/`([^`]+)`/g, `<code ${STYLE.code}>$1</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, `<strong ${STYLE.strong}>$1</strong>`);
  out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, `$1<em ${STYLE.em}>$2</em>`);
  return out;
}

function isTableSep(line: string): boolean {
  // | --- | :--- | ---: | :---: |
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line);
}

function splitTableRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map(c => c.trim());
}

export function mdToHtml(md: string): string {
  if (!md) return '';
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // 空行 → 段落分隔
    if (trimmed === '') { i++; continue; }

    // 水平线
    if (/^-{3,}\s*$/.test(trimmed) || /^\*{3,}\s*$/.test(trimmed)) {
      out.push(`<hr ${STYLE.hr}/>`); i++; continue;
    }

    // 表格
    if (trimmed.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const headers = splitTableRow(line);
      i += 2;
      const bodyRows: string[][] = [];
      while (i < lines.length && lines[i].trim().includes('|') && !isTableSep(lines[i])) {
        bodyRows.push(splitTableRow(lines[i])); i++;
      }
      out.push(`<table ${STYLE.table}>`);
      out.push('<thead><tr>' + headers.map(h => `<th ${STYLE.th}>${renderInline(h)}</th>`).join('') + '</tr></thead>');
      if (bodyRows.length) {
        out.push('<tbody>');
        for (const row of bodyRows) {
          out.push('<tr>' + row.map(c => `<td ${STYLE.td}>${renderInline(c)}</td>`).join('') + '</tr>');
        }
        out.push('</tbody>');
      }
      out.push('</table>');
      continue;
    }

    if (/^[-*+]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, '')); i++;
      }
      out.push(`<ul ${STYLE.ul}>` + items.map(t => `<li ${STYLE.li}>${renderInline(t)}</li>`).join('') + '</ul>');
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, '')); i++;
      }
      out.push(`<ol ${STYLE.ol}>` + items.map(t => `<li ${STYLE.li}>${renderInline(t)}</li>`).join('') + '</ol>');
      continue;
    }

    const hMatch = /^(#{1,6})\s+(.+)$/.exec(trimmed);
    if (hMatch) {
      const level = Math.min(hMatch[1].length, 3);
      const style = (STYLE as any)[`h${level}`] || STYLE.h3;
      out.push(`<h${level} ${style}>${renderInline(hMatch[2])}</h${level}>`);
      i++; continue;
    }

    // 段落
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== ''
           && !/^[-*+]\s+/.test(lines[i].trim())
           && !/^\d+\.\s+/.test(lines[i].trim())
           && !/^#{1,6}\s+/.test(lines[i].trim())
           && !/^-{3,}\s*$/.test(lines[i].trim())
           && !(lines[i].includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1]))) {
      para.push(lines[i]); i++;
    }
    out.push(`<p ${STYLE.p}>` + para.map(l => renderInline(l)).join('<br/>') + '</p>');
  }

  return out.join('');
}
