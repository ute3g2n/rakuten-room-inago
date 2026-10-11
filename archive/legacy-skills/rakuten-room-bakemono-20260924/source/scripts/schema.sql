-- ============================================================
-- 楽天ROOM デモ用スキーマ（投稿計画 + リサーチ結果）
-- Supabaseダッシュボード → SQL Editor に貼り付けて Run
-- 何度実行しても壊れない（IF NOT EXISTS / drop policy 済み）
-- ============================================================

-- ------------------------------------------------------------
-- 1. 投稿計画（カレンダーの軸）
-- ------------------------------------------------------------
create table if not exists public.rakuten_posts (
  id            bigint generated always as identity primary key,
  post_date     date        not null,
  post_time     text,
  post_role     text,                             -- 入口 / 関連 / 収益拡張 / 予告
  product_name  text        not null,
  product_url   text,
  price         text,
  hook_type     text,                             -- 問題提起型 / 数字・実績型 / 意外性型
  post_text     text,
  image_url     text,                             -- 商品画像（og:imageを同期時に自動取得）
  status        text        default '計画',        -- 計画 / 実施済み / 見送り
  reaction      text,
  learning      text,
  updated_at    timestamptz default now(),
  constraint rakuten_posts_uniq unique (post_date, product_name)
);
create index if not exists rakuten_posts_date_idx on public.rakuten_posts (post_date);

-- ------------------------------------------------------------
-- 2. リサーチ結果（固定候補リスト＝広告加熱商品）
-- ------------------------------------------------------------
create table if not exists public.rakuten_products (
  id             bigint generated always as identity primary key,
  product_name   text        not null,
  product_url    text        not null,
  priority       text,                            -- A / A- / B / C
  price          text,
  pain_point     text,                            -- 刺さる悩み
  role_fit       text,                            -- 入口 / 関連 / 収益拡張
  heat_evidence  text,                            -- 加熱根拠（✅実ページ確認 など）
  checked_on     date,                            -- 実ページを見た日
  last_posted    text,                            -- 最終投稿日（「YYYY-MM-DD（計画）」等の表記もあるためtext）
  image_url      text,                            -- 商品画像（og:imageを同期時に自動取得）
  listing        text        default '候補',       -- 候補 / 除外
  exclude_reason text,                            -- 除外理由（listing='除外' のとき）
  updated_at     timestamptz default now(),
  constraint rakuten_products_uniq unique (product_url)
);
create index if not exists rakuten_products_priority_idx on public.rakuten_products (priority);

-- ------------------------------------------------------------
-- 既存テーブルへの追加（後から画像を足す場合。新規作成なら不要だが実行しても安全）
-- ------------------------------------------------------------
alter table public.rakuten_posts    add column if not exists image_url text;
alter table public.rakuten_products add column if not exists image_url text;

-- ============================================================
-- RLS：「誰でも読める・誰も書けない」に固定
-- 書き込みは secret キー（RLSをバイパス）を持つローカルの同期スクリプトのみ
-- ============================================================
alter table public.rakuten_posts    enable row level security;
alter table public.rakuten_products enable row level security;

drop policy if exists "anon can read posts" on public.rakuten_posts;
create policy "anon can read posts"
  on public.rakuten_posts for select to anon using (true);

drop policy if exists "anon can read products" on public.rakuten_products;
create policy "anon can read products"
  on public.rakuten_products for select to anon using (true);

-- ※ insert / update / delete のポリシーは意図的に作らない。
--   ポリシーが無い操作はRLSにより拒否される。

-- ============================================================
-- 確認用
-- ============================================================
-- select count(*) from public.rakuten_posts;
-- select priority, count(*) from public.rakuten_products group by priority order by 1;
