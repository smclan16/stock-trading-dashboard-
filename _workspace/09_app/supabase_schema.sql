-- ============================================================
-- Supabase 스키마 — 멀티유저 거래/시뮬 데이터 + 공유 시세 캐시
-- Supabase SQL Editor 에 붙여넣고 실행.
-- ============================================================

-- ─── 사용자 거래(체결) 이력 ───────────────────────────
create table if not exists public.trades (
    id          bigint generated always as identity primary key,
    user_id     uuid not null,
    trade_date  date not null,
    ticker      text not null,
    name        text,
    action      text not null,          -- BUY / SELL / DIVIDEND
    shares      bigint not null,
    price       double precision not null,
    fee         double precision default 0,
    tax         double precision default 0,
    theme_id    text,
    note        text,
    created_at  timestamptz default now()
);
create index if not exists idx_trades_user_date   on public.trades(user_id, trade_date);
create index if not exists idx_trades_user_ticker on public.trades(user_id, ticker);

-- ─── 사용자별 메타(key-value) ─────────────────────────
create table if not exists public.portfolio_meta (
    user_id uuid not null,
    key     text not null,
    value   text,
    primary key (user_id, key)
);

-- ─── 시뮬레이션 ───────────────────────────────────────
create table if not exists public.simulations (
    id            bigint generated always as identity primary key,
    user_id       uuid not null,
    name          text,
    start_date    date not null,
    start_capital double precision not null,
    status        text default 'active',   -- active / paused / stopped
    last_synced   text,
    notes         text,
    created_at    timestamptz default now()
);
create index if not exists idx_sim_user on public.simulations(user_id);

create table if not exists public.simulated_trades (
    id          bigint generated always as identity primary key,
    user_id     uuid not null,
    sim_id      bigint not null references public.simulations(id) on delete cascade,
    trade_date  date not null,
    ticker      text not null,
    name        text,
    action      text not null,          -- BUY / SELL / DIVIDEND / CREDIT_INTEREST
    shares      bigint not null,
    price       double precision not null,
    fee         double precision default 0,
    tax         double precision default 0,
    theme_id    text,
    signal_type text,                   -- ENTRY_1ST / ADD_2ND / EXIT_MA60_FULL ...
    note        text,
    created_at  timestamptz default now()
);
create index if not exists idx_sim_trades_user_sim  on public.simulated_trades(user_id, sim_id);
create index if not exists idx_sim_trades_user_date on public.simulated_trades(user_id, trade_date);

-- ─── 공유 시세 캐시 (user_id 없음 — 만인 공통) ─────────
create table if not exists public.price_cache (
    ticker     text not null,
    trade_date date not null,
    close      double precision not null,
    primary key (ticker, trade_date)
);

-- ============================================================
-- RLS — 모든 테이블 활성화 (방어선)
-- 앱은 service_role(서버사이드)로 연결해 user_id를 직접 스코프하므로
-- 실제 격리는 lib/db.py 단일 계층이 강제. RLS는 anon/authenticated 키
-- 노출 대비 최소 권한(접근 차단)으로 둔다.
-- ============================================================
alter table public.trades            enable row level security;
alter table public.portfolio_meta    enable row level security;
alter table public.simulations       enable row level security;
alter table public.simulated_trades  enable row level security;
alter table public.price_cache       enable row level security;

-- 별도 정책을 만들지 않으면 anon/authenticated 는 접근 불가(기본 deny).
-- service_role 은 RLS 를 우회하므로 앱은 정상 동작.
-- (추후 사용자 JWT 발급으로 전환 시, 아래 형태의 정책을 추가)
--
-- create policy "own rows" on public.trades for all
--   to authenticated
--   using  ( (auth.jwt() ->> 'sub') = user_id::text )
--   with check ( (auth.jwt() ->> 'sub') = user_id::text );
