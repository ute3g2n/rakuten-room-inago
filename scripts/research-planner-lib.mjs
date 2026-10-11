const PRODUCT_HEADING = '## 候補一覧';
const POSTS_HEADING = '## 直近の投稿';
const ID_PATTERN = /P\d{2,}/;

function splitCells(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return null;
  return trimmed.slice(1, -1).split('|').map((cell) => cell.trim());
}

function isSeparator(cells) {
  return cells?.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function tableAfterHeading(markdown, heading) {
  const lines = markdown.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === heading);
  if (start < 0) throw new Error(`見出し「${heading}」が見つかりません`);

  for (let index = start + 1; index < lines.length; index += 1) {
    const headers = splitCells(lines[index]);
    if (!headers) {
      if (/^#{1,6}\s/.test(lines[index])) break;
      continue;
    }
    const separator = splitCells(lines[index + 1] ?? '');
    if (!isSeparator(separator) || separator.length !== headers.length) continue;

    const rows = [];
    for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
      const cells = splitCells(lines[rowIndex]);
      if (!cells) break;
      if (cells.length !== headers.length) {
        throw new Error(`「${heading}」の表で列数が一致しません: ${lines[rowIndex]}`);
      }
      rows.push(Object.fromEntries(headers.map((header, cellIndex) => [header, cells[cellIndex]])));
    }
    return rows;
  }
  throw new Error(`見出し「${heading}」の下にMarkdown表が見つかりません`);
}

export function parseProductLedger(markdown) {
  const rows = tableAfterHeading(markdown, PRODUCT_HEADING)
    .filter((row) => ID_PATTERN.test(row.ID ?? ''));
  if (rows.length === 0) throw new Error('商品台帳から商品行を読み取れません');

  const ids = new Set();
  for (const row of rows) {
    const id = row.ID.match(ID_PATTERN)?.[0];
    if (ids.has(id)) throw new Error(`商品台帳にID ${id} が重複しています`);
    ids.add(id);
    if (!row.URL || !row['商品名']) throw new Error(`${id}: 商品名またはURLが空です`);
    row.ID = id;
  }
  return rows;
}

export function parseCompletedPosts(markdown) {
  const rows = tableAfterHeading(markdown, POSTS_HEADING);
  const completedIds = [];
  const plannedIds = [];
  for (const row of rows) {
    const id = row['商品']?.match(ID_PATTERN)?.[0];
    if (!id) continue;
    if (row['実施'] === '実施済み') completedIds.push(id);
    else if (row['実施'] === '計画') plannedIds.push(id);
  }
  return {
    completedIds: [...new Set(completedIds)],
    repeatedCompletedIds: [...new Set(completedIds.filter((id, index) => completedIds.indexOf(id) !== index))],
    plannedIds: [...new Set(plannedIds)],
  };
}

export function normalizeProductUrl(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error(`URLを解釈できません: ${rawUrl}`);
  }
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error(`HTTP(S)以外のURLです: ${rawUrl}`);
  url.hash = '';
  url.search = '';
  url.hostname = url.hostname.toLowerCase();
  if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/+$/, '');
  return url.toString();
}

function validIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value ?? '')) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

function dayDifference(from, to) {
  return Math.floor((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

function localDateJst(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  return `${parts.find((part) => part.type === 'year').value}-${parts.find((part) => part.type === 'month').value}-${parts.find((part) => part.type === 'day').value}`;
}

export function buildResearchBrief({ products, posts, plan, genreMap, asOf = localDateJst() }) {
  if (!validIsoDate(asOf)) throw new Error(`日付はYYYY-MM-DDで指定してください: ${asOf}`);
  if (!Array.isArray(plan.genres) || plan.genres.length === 0) throw new Error('ジャンル設定が空です');
  if (!(plan.provisionalAcceptanceRate > 0 && plan.provisionalAcceptanceRate <= 1)) {
    throw new Error('採用率の仮置きは0より大きく1以下にしてください');
  }
  if (!(Number.isInteger(plan.freshnessDays) && plan.freshnessDays > 0)) {
    throw new Error('確認期限は正の整数日にしてください');
  }
  if (!(Number.isInteger(plan.maxPagesPerBatch) && plan.maxPagesPerBatch > 0)) {
    throw new Error('1回あたりの商品ページ数は正の整数にしてください');
  }
  const genreIds = new Set();
  for (const genre of plan.genres) {
    if (genreIds.has(genre.id)) throw new Error(`ジャンルID ${genre.id} が重複しています`);
    if (!(Number.isInteger(genre.inventoryTarget) && genre.inventoryTarget >= 0) ||
        !(Number.isInteger(genre.postsPerDay) && genre.postsPerDay >= 0)) {
      throw new Error(`ジャンル「${genre.name}」の目標数が不正です`);
    }
    genreIds.add(genre.id);
  }
  for (const [productId, genreId] of Object.entries(genreMap)) {
    if (!genreIds.has(genreId)) throw new Error(`${productId}: 未定義のジャンルID ${genreId} です`);
  }

  const productIds = new Set(products.map((product) => product.ID));
  const postedIds = new Set(posts.completedIds);
  const canonicalUrls = new Map();
  for (const product of products) {
    const url = normalizeProductUrl(product.URL);
    canonicalUrls.set(url, [...(canonicalUrls.get(url) ?? []), product.ID]);
  }
  const duplicateUrls = [...canonicalUrls.entries()]
    .filter(([, ids]) => ids.length > 1)
    .map(([url, ids]) => ({ url, ids }));
  const duplicateUrlIds = new Set(duplicateUrls.flatMap((entry) => entry.ids));

  const candidates = products.map((product) => {
    const genreId = genreMap[product.ID] ?? null;
    const genre = plan.genres.find((item) => item.id === genreId) ?? null;
    const checkedAt = product['確認日'];
    let freshness = 'fresh';
    let ageDays = null;
    if (!validIsoDate(checkedAt)) freshness = 'missing-date';
    else {
      ageDays = dayDifference(checkedAt, asOf);
      if (ageDays < 0) freshness = 'future-date';
      else if (ageDays >= plan.freshnessDays) freshness = 'stale';
    }
    const posted = postedIds.has(product.ID);
    const eligible = !posted && Boolean(genre) && freshness === 'fresh' && !duplicateUrlIds.has(product.ID);
    return {
      id: product.ID,
      name: product['商品名'],
      url: product.URL,
      genreId,
      genreName: genre?.name ?? null,
      checkedAt: checkedAt || null,
      ageDays,
      freshness,
      posted,
      eligible,
    };
  });

  const genreRows = plan.genres.map((genre, order) => {
    const stocked = candidates.filter((candidate) => candidate.genreId === genre.id && candidate.eligible).length;
    const shortage = Math.max(0, genre.inventoryTarget - stocked);
    const estimatedPages = Math.ceil(shortage / plan.provisionalAcceptanceRate);
    return {
      ...genre,
      stocked,
      shortage,
      coverageDays: genre.postsPerDay === 0 ? null : stocked / genre.postsPerDay,
      estimatedPages,
      estimatedBatches: Math.ceil(estimatedPages / plan.maxPagesPerBatch),
      order,
    };
  });
  const researchQueue = [...genreRows]
    .filter((genre) => genre.shortage > 0)
    .sort((a, b) => (a.coverageDays ?? Infinity) - (b.coverageDays ?? Infinity) ||
      b.postsPerDay - a.postsPerDay || a.order - b.order);
  const postedIdsMissingFromProducts = posts.completedIds.filter((id) => !productIds.has(id));
  const unloggedFinalPosts = candidates
    .filter((candidate) => candidate.posted === false && products.find((row) => row.ID === candidate.id)?.['最終投稿日'])
    .map((candidate) => candidate.id);

  return {
    asOf,
    assumptions: {
      freshnessDays: plan.freshnessDays,
      provisionalAcceptanceRate: plan.provisionalAcceptanceRate,
      maxPagesPerBatch: plan.maxPagesPerBatch,
      caveat: '採用率は初回調査の小標本に基づく作業量の仮置きで、成果や実採用率の予測ではありません。',
    },
    totals: {
      inventoryTarget: genreRows.reduce((total, genre) => total + genre.inventoryTarget, 0),
      stocked: genreRows.reduce((total, genre) => total + genre.stocked, 0),
      shortage: genreRows.reduce((total, genre) => total + genre.shortage, 0),
      estimatedPages: genreRows.reduce((total, genre) => total + genre.estimatedPages, 0),
      estimatedBatches: genreRows.reduce((total, genre) => total + genre.estimatedBatches, 0),
    },
    genres: genreRows,
    researchQueue,
    candidates,
    postedIds: posts.completedIds,
    plannedIds: posts.plannedIds,
    repeatedCompletedIds: posts.repeatedCompletedIds,
    staleIds: candidates.filter((candidate) => !candidate.posted && candidate.freshness === 'stale').map((candidate) => candidate.id),
    undatedIds: candidates.filter((candidate) => !candidate.posted && candidate.freshness === 'missing-date').map((candidate) => candidate.id),
    futureDateIds: candidates.filter((candidate) => !candidate.posted && candidate.freshness === 'future-date').map((candidate) => candidate.id),
    unmappedIds: candidates.filter((candidate) => !candidate.posted && !candidate.genreId).map((candidate) => candidate.id),
    duplicateUrls,
    postedIdsMissingFromProducts,
    unloggedFinalPosts,
    unmappedConfigIds: Object.keys(genreMap).filter((id) => !productIds.has(id)),
    eligibleCandidates: candidates.filter((candidate) => candidate.eligible),
  };
}

export function renderMarkdownBrief(brief) {
  const lines = [
    `# ローカル調査ブリーフ（${brief.asOf}）`,
    '',
    `有効在庫 **${brief.totals.stocked}/${brief.totals.inventoryTarget}件**、不足 **${brief.totals.shortage}件**。仮採用率${Math.round(brief.assumptions.provisionalAcceptanceRate * 100)}%なら、調査ページ目安は **${brief.totals.estimatedPages}ページ**（${brief.assumptions.maxPagesPerBatch}ページ単位で約${brief.totals.estimatedBatches}バッチ）。`,
    '',
    '|優先|ジャンル|日次|在庫目標|有効在庫|不足|確認ページ目安|バッチ目安|',
    '|---:|---|---:|---:|---:|---:|---:|---:|',
    ...brief.researchQueue.map((genre, index) => `|${index + 1}|${genre.name}|${genre.postsPerDay}|${genre.inventoryTarget}|${genre.stocked}|${genre.shortage}|${genre.estimatedPages}|${genre.estimatedBatches}|`),
    '',
    `ジャンル別集計：${brief.genres.map((genre) => `${genre.name} ${genre.stocked}/${genre.inventoryTarget}`).join('、')}`,
    `有効在庫ID：${brief.eligibleCandidates.length ? brief.eligibleCandidates.map((candidate) => candidate.id).join(', ') : 'なし'}。`,
    `投稿済み除外ID：${brief.postedIds.length ? brief.postedIds.join(', ') : 'なし'}。計画中は投稿済み扱いしない：${brief.plannedIds.length ? brief.plannedIds.join(', ') : 'なし'}。`,
  ];
  const checks = [];
  if (brief.staleIds.length) checks.push(`再確認期限超過（${brief.assumptions.freshnessDays}日以上）：${brief.staleIds.join(', ')}`);
  if (brief.undatedIds.length) checks.push(`確認日なし：${brief.undatedIds.join(', ')}`);
  if (brief.futureDateIds.length) checks.push(`確認日が未来：${brief.futureDateIds.join(', ')}`);
  if (brief.unmappedIds.length) checks.push(`ジャンル未設定：${brief.unmappedIds.join(', ')}`);
  if (brief.duplicateUrls.length) checks.push(`同一URLの候補：${brief.duplicateUrls.map((entry) => entry.ids.join('/')).join(', ')}`);
  if (brief.postedIdsMissingFromProducts.length) checks.push(`商品台帳にない投稿済みID：${brief.postedIdsMissingFromProducts.join(', ')}`);
  if (brief.unloggedFinalPosts.length) checks.push(`投稿日はあるが投稿ログ未確認：${brief.unloggedFinalPosts.join(', ')}`);
  if (brief.repeatedCompletedIds.length) checks.push(`投稿ログで複数回実施済み：${brief.repeatedCompletedIds.join(', ')}`);
  if (brief.unmappedConfigIds.length) checks.push(`ジャンル設定に残る台帳外ID：${brief.unmappedConfigIds.join(', ')}`);
  if (checks.length) lines.push('', '要確認:', ...checks.map((item) => `- ${item}`));
  lines.push('', `※ ${brief.assumptions.caveat}`, '');
  return lines.join('\n');
}

export function currentJstDate(now = new Date()) {
  return localDateJst(now);
}
