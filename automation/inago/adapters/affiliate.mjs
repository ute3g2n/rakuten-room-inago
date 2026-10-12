const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const MONTH_RE = /^\d{4}-\d{2}$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})$/;

function assertDate(value, label) {
  if (typeof value !== 'string' || !DATE_RE.test(value)
    || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
    throw new Error(`${label} must be YYYY-MM-DD`);
  }
  return value;
}

function addDays(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function monthBefore(month) {
  const first = new Date(`${month}-01T00:00:00Z`);
  first.setUTCMonth(first.getUTCMonth() - 1);
  return first.toISOString().slice(0, 7);
}

function metricValue(value, label) {
  if (value === null) return null;
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative safe integer or null`);
  }
  return value;
}

export function previousDailyDates(targetDate, firstActiveDate, limit = 7) {
  assertDate(targetDate, 'targetDate');
  assertDate(firstActiveDate, 'firstActiveDate');
  if (!Number.isInteger(limit) || limit < 1 || limit > 7) {
    throw new Error('daily report limit must be between 1 and 7');
  }
  const dates = [];
  for (let offset = 1; offset <= limit; offset += 1) {
    const candidate = addDays(targetDate, -offset);
    if (candidate < firstActiveDate) break;
    dates.push(candidate);
  }
  return dates;
}

export function orderMonths(targetDate, firstActiveDate) {
  assertDate(targetDate, 'targetDate');
  assertDate(firstActiveDate, 'firstActiveDate');
  const current = targetDate.slice(0, 7);
  const prior = monthBefore(current);
  const first = firstActiveDate.slice(0, 7);
  return [...new Set([current, prior])].filter((month) => month >= first);
}

export function normalizeDailyResponse({
  status, observedAt, expectedDates, rows, emptyMeansZero = false, errorCode = null,
}) {
  if (!['observed', 'partial', 'unavailable', 'not_present'].includes(status)) {
    throw new Error('daily response status is unsupported');
  }
  if (typeof observedAt !== 'string' || !ISO_RE.test(observedAt)
    || Number.isNaN(Date.parse(observedAt))) {
    throw new Error('daily observedAt must be an offset-aware timestamp');
  }
  if (!Array.isArray(expectedDates) || expectedDates.length > 7
    || expectedDates.some((date) => !DATE_RE.test(date))) {
    throw new Error('expectedDates must contain at most seven ISO dates');
  }
  if (status !== 'observed' && status !== 'partial') {
    return { status, observedAt, rows: [], errorCode: errorCode || 'source_unavailable' };
  }
  if (!Array.isArray(rows)) throw new Error('daily rows must be an array');
  if (rows.length === 0 && emptyMeansZero !== true) {
    return {
      status: 'unavailable', observedAt, rows: [],
      errorCode: errorCode || 'empty_report_does_not_prove_zero',
    };
  }
  const expected = new Set(expectedDates);
  const byDate = new Map();
  for (const row of rows) {
    const date = assertDate(row?.date, 'daily row date');
    if (!expected.has(date)) throw new Error('daily row date is outside requested dates');
    if (byDate.has(date)) throw new Error('daily report contains a duplicate date');
    byDate.set(date, {
      date,
      clicks: metricValue(row.clicks, 'clicks'),
      salesCount: metricValue(row.salesCount, 'salesCount'),
      salesYen: metricValue(row.salesYen, 'salesYen'),
      commissionUnconfirmedYen: metricValue(
        row.commissionUnconfirmedYen, 'commissionUnconfirmedYen',
      ),
    });
  }
  if (rows.length === 0) {
    for (const date of expectedDates) {
      byDate.set(date, {
        date, clicks: 0, salesCount: 0, salesYen: 0, commissionUnconfirmedYen: 0,
      });
    }
  } else {
    for (const date of expectedDates) {
      if (!byDate.has(date)) {
        byDate.set(date, {
          date, clicks: null, salesCount: null, salesYen: null,
          commissionUnconfirmedYen: null,
        });
      }
    }
  }
  const orderedRows = expectedDates.map((date) => byDate.get(date));
  const hasMissingDay = orderedRows.some((row) => Object.values(row).slice(1).some((v) => v === null));
  return {
    status: hasMissingDay || status === 'partial' ? 'partial' : 'observed',
    observedAt,
    rows: orderedRows,
    errorCode: null,
  };
}

function parseCsv(csv) {
  if (typeof csv !== 'string') throw new Error('order CSV must be text');
  const input = csv.replace(/^\uFEFF/, '');
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
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
  if (quoted) throw new Error('order CSV has an unclosed quote');
  if (field || row.length) {
    row.push(field.replace(/\r$/, ''));
    rows.push(row);
  }
  return rows;
}

function nullableAmount(raw, label) {
  const value = String(raw ?? '').replace(/[\s,，¥￥円]/g, '').trim();
  if (!value) return null;
  if (!/^-?\d+$/.test(value)) throw new Error(`${label} must be an integer or empty`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error(`${label} is out of range`);
  return parsed;
}

function statusLabel(raw) {
  const text = String(raw ?? '').trim();
  const code = text.match(/^\d+/)?.[0];
  const labels = { '0': '未確定', '1': '確定', '2': '破棄' };
  if (code && Object.hasOwn(labels, code)) return labels[code];
  if (text) return text.replace(/^\d+\s*-\s*/, '');
  return null;
}

export function normalizeOrderCsv(csv, month) {
  if (typeof month !== 'string' || !MONTH_RE.test(month)) throw new Error('month must be YYYY-MM');
  const rows = parseCsv(csv);
  const headerIndex = rows.findIndex((row) => row.includes('date') && row.includes('item_name'));
  if (headerIndex < 0) throw new Error('order CSV columns changed');
  const headers = rows[headerIndex];
  const required = ['date', 'rewards', 'amount', 'shop_name', 'item_name', 'status', 'link_type'];
  if (required.some((header) => !headers.includes(header))) throw new Error('order CSV columns changed');
  const index = Object.fromEntries(headers.map((name, i) => [name, i]));
  const text = (cells, name) => String(cells[index[name]] ?? '').trim() || null;
  const orders = [];
  for (const cells of rows.slice(headerIndex + 1).filter((cells) => cells.some((cell) => cell.trim()))) {
    const rawDate = String(cells[index.date] ?? '').trim().replaceAll('/', '-');
    if (!/^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?$/.test(rawDate)
      || !rawDate.startsWith(`${month}-`)) {
      throw new Error('order CSV contains an invalid or out-of-scope date');
    }
    const orderRef = text(cells, 'order_id') || text(cells, 'order_ref');
    const occurredAt = rawDate.includes(' ')
      ? `${rawDate.replace(' ', 'T')}${rawDate.length === 16 ? ':00' : ''}+09:00`
      : `${rawDate}T00:00:00+09:00`;
    orders.push({
      orderRef,
      isTemporaryRef: orderRef === null,
      occurredAt,
      shopName: text(cells, 'shop_name'),
      productName: text(cells, 'item_name'),
      itemCode: text(cells, 'item_code'),
      productUrl: text(cells, 'item_url') || text(cells, 'product_url'),
      salesYen: nullableAmount(cells[index.amount], 'sales amount'),
      commissionYen: nullableAmount(cells[index.rewards], 'unconfirmed commission'),
      orderStatus: statusLabel(cells[index.status]),
      linkType: text(cells, 'link_type'),
      window: `affiliate-orders:${month}`,
    });
  }
  return { status: 'observed', month, orders };
}
