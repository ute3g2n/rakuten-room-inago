import test from 'node:test';
import assert from 'node:assert/strict';
import {
  jstNow,
  parseDailyTable,
  parseOrderDetailCsv,
  previousDays,
  recentOrderMonths,
  sameMetrics,
  sameOrderDetails,
} from '../scripts/affiliate-report-lib.mjs';

test('日本時間の前日から7日を登録日で切る', () => {
  assert.deepEqual(previousDays('2026-09-26', 7, '2026-09-23'),
    ['2026-09-25', '2026-09-24', '2026-09-23']);
  assert.deepEqual(previousDays('2026-09-23', 7, '2026-09-23'), []);
  assert.deepEqual(previousDays('2026-10-02', 7, '2026-09-23').slice(0, 3),
    ['2026-10-01', '2026-09-30', '2026-09-29']);
});

test('UTCから日本時間の日付と時刻を得る', () => {
  assert.deepEqual(jstNow(new Date('2026-09-23T02:30:00Z')), { date: '2026-09-23', hour: 11 });
});

test('日別表の0と金額を読む。合計行と別月は無視する', () => {
  const headers = ['発生日', '成果報酬', 'クリック数', '売上件数', '売上金額'];
  const rows = [
    ['合計', '1,234', '3', '1', '12,345'],
    ['2026-09-23', '0', '0', '0', '0'],
    ['2026-09-24', '¥1,234', '3', '1', '12,345円'],
    ['2026-08-31', '4', '1', '1', '4'],
  ];
  const parsed = parseDailyTable(headers, rows, '2026-09');
  assert.equal(parsed.size, 2);
  assert.deepEqual(parsed.get('2026-09-23'), {
    date: '2026-09-23', commissionJpyUnconfirmed: 0, clicks: 0,
    ordersUnconfirmed: 0, salesJpyUnconfirmed: 0,
  });
  assert.equal(parsed.get('2026-09-24').salesJpyUnconfirmed, 12345);
});

test('読み込み中の空表を0として扱わない', () => {
  const headers = ['発生日', '成果報酬', 'クリック数', '売上件数', '売上金額'];
  assert.throws(() => parseDailyTable(headers, [['対象データが見つかりませんでした。']], '2026-09'));
  assert.throws(() => parseDailyTable(['発生日', 'クリック数'], [['2026-09-23', '0']], '2026-09'));
  assert.throws(() => previousDays('2026-09-23', 0, '2026-09-23'));
});

test('同値は履歴へ追記しない判定にする', () => {
  const a = { clicks: 0, ordersUnconfirmed: 0, salesJpyUnconfirmed: 0,
    commissionJpyUnconfirmed: 0, status: 'observed' };
  assert.equal(sameMetrics(a, { ...a, observedAt: 'later' }), true);
  assert.equal(sameMetrics(a, { ...a, clicks: 1 }), false);
  assert.equal(sameMetrics(a, { ...a, status: 'unavailable' }), false);
});

test('注文明細CSVから10件を超える全商品・ショップ・状態を取得する', () => {
  const rows = Array.from({ length: 12 }, (_, index) => {
    const status = index === 0 ? '0' : index === 1 ? '1' : '2';
    const product = index === 0 ? '商品, カンマ入り' : `商品${index}`;
    return `2026/09/24 12:${String(index).padStart(2, '0')},${index + 1},1.0,${1000 + index},家電,店${index},"${product}",${status},1,3,`;
  });
  const csv = `\uFEFF"注文別成果: 2026.09"\n\n発生日,成果報酬,料率,売上金額,ジャンル名,ショップ名,商品名,ステータス,リンクタイプ,デバイスタイプ,計測ID\ndate,rewards,rate,amount,genre_name,shop_name,item_name,status,link_type,device_type,measurement_id\n${rows.join('\n')}\n`;
  const parsed = parseOrderDetailCsv(csv, '2026-09');
  assert.equal(parsed.length, 12);
  assert.deepEqual(parsed[0], {
    occurredAt: '2026-09-24 12:00', commissionJpy: 1, salesJpy: 1000,
    genre: '家電', shopName: '店0', productName: '商品, カンマ入り',
    status: '未確定', linkType: '商品リンク',
  });
  assert.equal(parsed[1].status, '確定');
  assert.equal(parsed[2].status, '破棄');
});

test('正常取得した注文明細CSVのデータ行なしは確認済み0件として扱う', () => {
  const csv = '発生日,成果報酬,料率,売上金額,ジャンル名,ショップ名,商品名,ステータス,リンクタイプ,デバイスタイプ,計測ID\ndate,rewards,rate,amount,genre_name,shop_name,item_name,status,link_type,device_type,measurement_id\n';
  assert.deepEqual(parseOrderDetailCsv(csv, '2026-09'), []);
});

test('注文明細CSVの列構成変更と対象月外データを黙って通さない', () => {
  assert.throws(() => parseOrderDetailCsv('date,rewards\n2026-09-24,1\n', '2026-09'));
  const csv = 'date,rewards,amount,genre_name,shop_name,item_name,status,link_type\n2026-08-31 23:59,1,100,家電,店,商品,0,1\n';
  assert.throws(() => parseOrderDetailCsv(csv, '2026-09'));
});

test('注文明細は当月と前月を再取得する', () => {
  assert.deepEqual(recentOrderMonths('2026-10-09', ['2026-10-08'], '2026-09-23'), ['2026-10', '2026-09']);
  assert.deepEqual(recentOrderMonths('2026-09-24', ['2026-09-23'], '2026-09-23'), ['2026-09']);
});

test('注文明細は観測時刻を除いて同値比較する', () => {
  const orders = [{
    occurredAt: '2026-09-24 12:34', commissionJpy: 123, salesJpy: 4980,
    genre: '家電', shopName: '店', productName: '商品', status: '未確定', linkType: '商品リンク',
  }];
  assert.equal(sameOrderDetails(
    { status: 'observed', orders, observedAt: 'first' },
    { status: 'observed', orders: structuredClone(orders), observedAt: 'later' },
  ), true);
  assert.equal(sameOrderDetails(
    { status: 'observed', orders, observedAt: 'first' },
    { status: 'observed', orders: [{ ...orders[0], status: '確定' }], observedAt: 'later' },
  ), false);
});
