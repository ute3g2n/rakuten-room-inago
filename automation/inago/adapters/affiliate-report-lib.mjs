const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DATE_TIME_RE = /^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?$/;

export function jstNow(date = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date).map(({ type, value }) => [type, value]));
  return { date: `${parts.year}-${parts.month}-${parts.day}`, hour: Number(parts.hour) };
}

export function previousDays(today, count, firstActiveDate) {
  if (!DATE_RE.test(today) || !DATE_RE.test(firstActiveDate) || !Number.isInteger(count) || count < 1) {
    throw new Error('日付または確認日数が不正です');
  }
  const start = new Date(`${today}T00:00:00Z`);
  const dates = [];
  for (let offset = 1; offset <= count; offset += 1) {
    const day = new Date(start);
    day.setUTCDate(day.getUTCDate() - offset);
    const key = day.toISOString().slice(0, 10);
    if (key >= firstActiveDate) dates.push(key);
  }
  return dates;
}

export function recentOrderMonths(today, targetDates, firstActiveDate) {
  if (!DATE_RE.test(today) || !DATE_RE.test(firstActiveDate)
    || !Array.isArray(targetDates) || targetDates.some((date) => !DATE_RE.test(date))) {
    throw new Error('注文明細の対象日が不正です');
  }
  const currentMonth = today.slice(0, 7);
  const previous = new Date(`${currentMonth}-01T00:00:00Z`);
  previous.setUTCMonth(previous.getUTCMonth() - 1);
  const candidates = [currentMonth, previous.toISOString().slice(0, 7),
    ...targetDates.map((date) => date.slice(0, 7))];
  return [...new Set(candidates)].filter((month) => month >= firstActiveDate.slice(0, 7));
}

function numberFromCell(value) {
  const normalized = value.replace(/[\s,，¥￥円]/g, '').trim();
  if (!/^-?\d+$/.test(normalized)) throw new Error(`数値を読めません: ${value}`);
  return Number(normalized);
}

export function parseDailyTable(headers, rows, month) {
  const required = {
    '発生日': 'date',
    '成果報酬': 'commissionJpyUnconfirmed',
    'クリック数': 'clicks',
    '売上件数': 'ordersUnconfirmed',
    '売上金額': 'salesJpyUnconfirmed',
  };
  const indices = Object.fromEntries(Object.entries(required).map(([label, key]) => [key, headers.indexOf(label)]));
  if (Object.values(indices).some((index) => index < 0)) throw new Error('日別レポートの列構成が変わりました');
  const result = new Map();
  for (const cells of rows) {
    const date = cells[indices.date]?.trim();
    if (!DATE_RE.test(date || '') || !date.startsWith(`${month}-`)) continue;
    if (result.has(date)) throw new Error(`日別レポートに日付の重複があります: ${date}`);
    result.set(date, {
      date,
      commissionJpyUnconfirmed: numberFromCell(cells[indices.commissionJpyUnconfirmed]),
      clicks: numberFromCell(cells[indices.clicks]),
      ordersUnconfirmed: numberFromCell(cells[indices.ordersUnconfirmed]),
      salesJpyUnconfirmed: numberFromCell(cells[indices.salesJpyUnconfirmed]),
    });
  }
  if (result.size === 0) throw new Error('日別レポートの行を取得できませんでした');
  return result;
}

function parseCsv(csv) {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  const input = csv.replace(/^\uFEFF/, '');
  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    if (quoted) {
      if (character === '"' && input[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') quoted = false;
      else field += character;
    } else if (character === '"') quoted = true;
    else if (character === ',') {
      row.push(field);
      field = '';
    } else if (character === '\n') {
      row.push(field.replace(/\r$/, ''));
      rows.push(row);
      row = [];
      field = '';
    } else field += character;
  }
  if (quoted) throw new Error('注文詳細CSVの引用符が閉じていません');
  if (field || row.length) {
    row.push(field.replace(/\r$/, ''));
    rows.push(row);
  }
  return rows;
}

const STATUS_LABELS = { 0: '未確定', 1: '確定', 2: '破棄' };
const LINK_TYPE_LABELS = {
  0: 'その他', 1: '商品リンク', 2: 'ショップリンク（画像）', 3: 'ショップリンク（テキスト）',
  4: 'ショップリンク（メール）', 5: 'ショップリンク（検索窓）', 6: '楽天アフィリエイトバナー（画像）',
  7: '楽天アフィリエイトバナー（テキスト）', 8: '楽天アフィリエイトバナー（メール）',
  9: '楽天アフィリエイトバナー（検索窓）', 10: '楽天モーションウィジェット',
  11: 'リアルタイムランキングウィジェット', 12: '楽天売れ筋ランキングリンク',
  13: '楽天ダイナミックアド', 14: 'お手軽ジャンルカスタマイズリンク', 15: '売れ筋アイテムリンク',
  16: '楽天セレクトウィジェット', 17: '楽天動画チャンネル', 18: 'その他',
  19: '楽天商品動画リンク', 20: '楽天ブログパーツ', 21: 'お気に入りブックマークリンク',
  22: '楽天ウェブサービス', 23: 'その他',
};

function codeLabel(value, labels, fieldName) {
  const normalized = value.trim();
  const code = normalized.match(/^\d+/)?.[0];
  if (code && Object.hasOwn(labels, code)) return labels[code];
  if (normalized) return normalized.replace(/^\d+\s*-\s*/, '');
  throw new Error(`注文詳細CSVの${fieldName}が空です`);
}

export function parseOrderDetailCsv(csv, month) {
  const rows = parseCsv(csv);
  const headerIndex = rows.findIndex((row) => row.includes('date') && row.includes('item_name'));
  if (headerIndex < 0) throw new Error('注文詳細CSVの列構成が変わりました');
  const headers = rows[headerIndex];
  const required = {
    date: 'occurredAt', rewards: 'commissionJpy', amount: 'salesJpy', genre_name: 'genre',
    shop_name: 'shopName', item_name: 'productName', status: 'status', link_type: 'linkType',
  };
  const indices = Object.fromEntries(Object.entries(required).map(([label, key]) => [key, headers.indexOf(label)]));
  if (Object.values(indices).some((index) => index < 0)) throw new Error('注文詳細CSVの列構成が変わりました');

  return rows.slice(headerIndex + 1).filter((cells) => cells.some((cell) => cell.trim())).map((cells) => {
    const occurredAt = (cells[indices.occurredAt] || '').trim().replaceAll('/', '-');
    if (!DATE_TIME_RE.test(occurredAt) || !occurredAt.startsWith(`${month}-`)) {
      throw new Error(`注文詳細CSVに対象月外または不正な発生日時があります: ${occurredAt || '(空欄)'}`);
    }
    const text = (key) => (cells[indices[key]] || '').trim();
    return {
      occurredAt,
      commissionJpy: numberFromCell(cells[indices.commissionJpy] || ''),
      salesJpy: numberFromCell(cells[indices.salesJpy] || ''),
      genre: text('genre'),
      shopName: text('shopName'),
      productName: text('productName'),
      status: codeLabel(text('status'), STATUS_LABELS, 'ステータス'),
      linkType: codeLabel(text('linkType'), LINK_TYPE_LABELS, 'リンクタイプ'),
    };
  });
}

export function sameMetrics(a, b) {
  return a?.clicks === b?.clicks
    && a?.ordersUnconfirmed === b?.ordersUnconfirmed
    && a?.salesJpyUnconfirmed === b?.salesJpyUnconfirmed
    && a?.commissionJpyUnconfirmed === b?.commissionJpyUnconfirmed
    && a?.status === b?.status;
}

export function sameOrderDetails(a, b) {
  if (!a || !b) return false;
  return a.status === b.status && JSON.stringify(a.orders) === JSON.stringify(b.orders);
}
