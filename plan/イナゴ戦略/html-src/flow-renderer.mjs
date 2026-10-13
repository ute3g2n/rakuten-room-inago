import { expandedFlows, branchDescriptions } from './flow-descriptions.mjs';

const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const roles = {
  S: { name: '決めた手順を実行するプログラム', fill: '#e9f5f0', stroke: '#39735d' },
  A: { name: '文章や意味を考えるAI', fill: '#eef0fb', stroke: '#59649b' },
  H: { name: '本人が行う操作・判断', fill: '#fff2de', stroke: '#9a6b30' },
};
const width = 1200;
const left = 42;
const nodeWidth = 1028;
const textLeft = left + 25;
const textWidth = nodeWidth - 50;
const lineHeight = 29;

// Conservative glyph widths keep complete sentences inside SVG nodes without truncation.
export function wrapText(value, maxWidth, fontSize) {
  const lines = [];
  let line = ''; let occupied = 0;
  for (const char of value) {
    const advance = /[\u0020-\u007e]/.test(char) ? fontSize * .65 : fontSize;
    if (occupied + advance > maxWidth && line) { lines.push(line); line = ''; occupied = 0; }
    line += char; occupied += advance;
  }
  if (line) lines.push(line);
  return lines;
}
const t = (x, y, value, size = 20, weight = 400, anchor = 'start') => `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" text-anchor="${anchor}" fill="#203c34">${esc(value)}</text>`;
const paragraph = (x, y, lines, size = 20) => lines.map((line, i) => t(x, y + i * lineHeight, line, size)).join('');
const arrow = (id, path, conditional = false) => `<path d="${path}" fill="none" stroke="#61766e" stroke-width="2.5"${conditional ? ' stroke-dasharray="6 5"' : ''} marker-end="url(#task-${id}-arrow)"/>`;

export function renderReadableFlow(flow) {
  const nodes = expandedFlows[flow.id];
  if (!nodes) throw new Error(`No full-sentence descriptions for ${flow.id}`);
  const id = flow.id;
  const isCopyFlow = id === 'F04';
  const headline = wrapText(flow.title, 1120, 24);
  let y = 40 + (headline.length - 1) * lineHeight;
  let content = paragraph(40, 40, headline, 24);
  content += t(40, y + 38, '緑：プログラムが実行します。　青：AIが考えます。　橙：本人が操作・判断します。', 18);
  y += 78;
  const geometry = [];
  nodes.forEach((node, index) => {
    const main = wrapText(node.sentence, textWidth, 20);
    const rule = wrapText(node.condition, textWidth, 19);
    const height = 134 + (main.length + rule.length) * lineHeight;
    geometry.push({ y, height, middle: y + height / 2 });
    const palette = roles[node.role];
    content += `<g class="diagram-node" data-node="${id}-${index + 1}"><rect x="${left}" y="${y}" width="${nodeWidth}" height="${height}" rx="12" fill="${palette.fill}" stroke="${palette.stroke}" stroke-width="1.6"/>`;
    const heading = wrapText(`手順${index + 1}　${node.actor}（${node.component}）`, textWidth, 20);
    if (heading.length !== 1) throw new Error(`Node heading needs another line: ${id}-${index + 1}`);
    content += t(textLeft, y + 31, heading[0], 20, 700);
    content += t(textLeft, y + 61, '何のために、何をどうするか', 15, 700);
    content += paragraph(textLeft, y + 91, main, 20);
    const ruleY = y + 91 + main.length * lineHeight + 9;
    content += t(textLeft, ruleY, '条件・注意点', 15, 700);
    content += paragraph(textLeft, ruleY + 29, rule, 19) + '</g>';
    // The copy flow ends normally at step 6; step 7 is a failure-only retry.
    // Its arrows touch both node borders instead of floating in the gap.
    const hasNext = index < nodes.length - 1 && (!isCopyFlow || index < 5);
    if (hasNext) content += arrow(id, `M 556 ${y + height} V ${y + height + (isCopyFlow ? 44 : 35)}`, false);
    y += height + 44;
  });
  if (isCopyFlow) {
    content += arrow(id, `M 1070 ${geometry[2].middle} H 1116 V ${geometry[6].middle} H 1070`, true);
    content += arrow(id, `M 1070 ${geometry[6].y + geometry[6].height - 26} H 1170 V ${geometry[0].middle} H 1070`, true);
  }
  if (id === 'F03') {
    content += arrow(id, `M 1070 ${geometry[6].middle} H 1134 V ${geometry[4].middle} H 1070`, true);
    content += arrow(id, `M 1070 ${geometry[6].y + geometry[6].height - 26} H 1170 V ${geometry[0].middle} H 1070`, true);
  }
  if (id === 'F02') content += arrow(id, `M 1070 ${geometry[3].middle} H 1134 V ${geometry[2].middle} H 1074`, true);
  if (id === 'F00') {
    const lines = wrapText('日次制御プログラムは、前日の管理反映が完了した作業を日次フロー全体図の手順2へ渡し、今日のテーマ選択と語句更新を始めます。', 978, 20);
    content += arrow(id, 'M 556 '+(y-44)+' V '+(y-9));
    content += '<rect class="flow-transition" x="42" y="'+y+'" width="1028" height="'+(lines.length*29+42)+'" rx="10" fill="#e6edf8" stroke="#59649b"/>';
    content += paragraph(67,y+29,lines,20); y+=lines.length*29+68;
  }
  if (id === 'F03') content += arrow(id, `M 1070 ${geometry[6].middle} H 1134 V ${geometry[1].middle} H 1074`);
  if (branchDescriptions[id]) {
    content += t(42, y + 4, '分かれ道・戻り道の説明', 20, 700); y += 39;
    for (const description of branchDescriptions[id]) {
      const lines = wrapText(description, 1090, 19);
      content += paragraph(42, y, lines, 19); y += lines.length * lineHeight + 20;
    }
  }
  if (isCopyFlow) {
    content += t(42, y, '実線の矢印は、担当プログラムやAIが手順1から手順6まで順に処理する流れを表します。', 18);
    y += 38;
    content += t(42, y, '破線の矢印は、手順3の生成に失敗した場合だけ手順7へ進み、手順1から依頼し直す流れです。', 18);
    y += 38;
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${y + 22}" role="img" aria-labelledby="task-${id}-title task-${id}-desc" font-family="Yu Gothic, Meiryo, sans-serif"><title id="task-${id}-title">${esc(flow.title)}</title><desc id="task-${id}-desc">${esc(flow.desc)} 各ノードの全文は図の直後の文章表示でも読めます。</desc><defs><marker id="task-${id}-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#61766e"/></marker></defs><rect width="${width}" height="${y + 22}" fill="#fff"/>${content}</svg>`;
}

export function renderFlowTranscript(flow) {
  const nodes = expandedFlows[flow.id];
  return `<details class="flow-transcript"><summary>この図の説明を文章で読む（横スクロール不要）</summary><ol>${nodes.map(node => `<li><p class="flow-person"><b>${esc(node.actor)}</b> <small>${esc(node.component)}</small></p><p>${esc(node.sentence)}</p><p class="flow-rule">${esc(node.condition)}</p></li>`).join('')}</ol>${(branchDescriptions[flow.id] ?? []).map(value => `<p>${esc(value)}</p>`).join('')}</details>`;
}
