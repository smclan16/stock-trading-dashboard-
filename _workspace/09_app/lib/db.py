"""SQLite — 거래(체결) 이력 + 일별 평가 캐시"""
import sqlite3
import os
import datetime
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'portfolio.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    action TEXT NOT NULL,        -- BUY / SELL / DIVIDEND
    shares INTEGER NOT NULL,
    price REAL NOT NULL,         -- DIVIDEND 시: 주당 배당금
    fee REAL DEFAULT 0,
    tax REAL DEFAULT 0,
    theme_id TEXT,                -- #1, #3, default 등
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_cache (
    ticker TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close REAL NOT NULL,
    PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS portfolio_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS simulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    start_date TEXT NOT NULL,
    start_capital REAL NOT NULL,
    status TEXT DEFAULT 'active',     -- active / paused / stopped
    last_synced TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulated_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    action TEXT NOT NULL,           -- BUY / SELL / DIVIDEND / CREDIT_INTEREST
    shares INTEGER NOT NULL,
    price REAL NOT NULL,            -- DIVIDEND 시 주당 배당금, CREDIT_INTEREST 시 누적 이자(주식수=1)
    fee REAL DEFAULT 0,
    tax REAL DEFAULT 0,
    theme_id TEXT,
    signal_type TEXT,               -- ENTRY_1ST / ADD_2ND / EXIT_MA60_FULL 등
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sim_id) REFERENCES simulations(id)
);

CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_sim_trades_sim ON simulated_trades(sim_id);
CREATE INDEX IF NOT EXISTS idx_sim_trades_date ON simulated_trades(trade_date);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_trade(trade_date: str, ticker: str, name: str, action: str,
              shares: int, price: float, fee: float = 0, tax: float = 0,
              theme_id: str = None, note: str = None) -> int:
    with get_conn() as conn:
        c = conn.execute(
            """INSERT INTO trades (trade_date, ticker, name, action, shares, price, fee, tax, theme_id, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_date, ticker, name, action, shares, price, fee, tax, theme_id, note),
        )
        return c.lastrowid


def delete_trade(trade_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))


def list_trades(ticker: str = None) -> list:
    with get_conn() as conn:
        if ticker:
            rows = conn.execute("SELECT * FROM trades WHERE ticker = ? ORDER BY trade_date DESC, id DESC", (ticker,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM trades ORDER BY trade_date DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


def get_positions() -> dict:
    """현재 보유 포지션 계산.
    BUY/SELL → shares, avg_price, realized_pnl 갱신
    DIVIDEND → dividend_total 누적 (현금 입금, shares 변화 X)
    """
    trades = list_trades()
    positions = {}
    for t in sorted(trades, key=lambda x: (x['trade_date'], x['id'])):
        tkr = t['ticker']
        p = positions.setdefault(tkr, {
            'ticker': tkr, 'name': t['name'], 'shares': 0,
            'total_buy_amount': 0.0, 'total_buy_shares': 0,
            'total_sell_amount': 0.0, 'total_sell_shares': 0,
            'realized_pnl': 0.0, 'avg_price': 0.0,
            'dividend_total': 0.0,
            'theme_id': t['theme_id'],
            'first_buy_date': None, 'last_trade_date': None,
        })
        p['name'] = t['name'] or p['name']
        p['theme_id'] = t['theme_id'] or p['theme_id']
        p['last_trade_date'] = t['trade_date']
        if t['action'] == 'BUY':
            p['total_buy_amount'] += t['shares'] * t['price'] + (t['fee'] or 0)
            p['total_buy_shares'] += t['shares']
            p['shares'] += t['shares']
            if p['first_buy_date'] is None:
                p['first_buy_date'] = t['trade_date']
        elif t['action'] == 'SELL':
            sell_amount = t['shares'] * t['price'] - (t['fee'] or 0) - (t['tax'] or 0)
            avg = p['total_buy_amount'] / p['total_buy_shares'] if p['total_buy_shares'] > 0 else 0
            cost = t['shares'] * avg
            p['realized_pnl'] += sell_amount - cost
            p['shares'] -= t['shares']
            p['total_sell_amount'] += sell_amount
            p['total_sell_shares'] += t['shares']
            # 매도분 원가만큼 total_buy 비례 차감 (재매수 시 평균단가 정확성)
            p['total_buy_amount'] = max(0.0, p['total_buy_amount'] - cost)
            p['total_buy_shares'] = max(0, p['total_buy_shares'] - t['shares'])
        elif t['action'] == 'DIVIDEND':
            # 현금배당: shares × 주당배당금
            p['dividend_total'] += t['shares'] * t['price']
        if p['total_buy_shares'] > 0 and p['shares'] > 0:
            p['avg_price'] = p['total_buy_amount'] / p['total_buy_shares']
    return {k: v for k, v in positions.items() if v['shares'] > 0}


def cache_price(ticker: str, trade_date: str, close: float):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO price_cache (ticker, trade_date, close) VALUES (?, ?, ?)",
                     (ticker, trade_date, close))


def get_cached_prices(ticker: str) -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT trade_date, close FROM price_cache WHERE ticker = ? ORDER BY trade_date",
                            (ticker,)).fetchall()
    return {r['trade_date']: r['close'] for r in rows}


def set_meta(key: str, value: str):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES (?, ?)", (key, value))


def get_meta(key: str, default: str = None) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM portfolio_meta WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else default


# ─── 시뮬레이션 ────────────────────────────────────────
def create_simulation(name: str, start_date: str, start_capital: float, notes: str = None) -> int:
    with get_conn() as conn:
        c = conn.execute(
            "INSERT INTO simulations (name, start_date, start_capital, notes, last_synced) VALUES (?, ?, ?, ?, ?)",
            (name, start_date, start_capital, notes, start_date),
        )
        return c.lastrowid


def list_simulations() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM simulations ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_simulation(sim_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM simulations WHERE id = ?", (sim_id,)).fetchone()
    return dict(row) if row else None


def delete_simulation(sim_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM simulated_trades WHERE sim_id = ?", (sim_id,))
        conn.execute("DELETE FROM simulations WHERE id = ?", (sim_id,))


def update_simulation(sim_id: int, **fields):
    if not fields:
        return
    sets = ', '.join(f'{k} = ?' for k in fields.keys())
    with get_conn() as conn:
        conn.execute(f"UPDATE simulations SET {sets} WHERE id = ?", list(fields.values()) + [sim_id])


def add_sim_trade(sim_id: int, trade_date: str, ticker: str, name: str, action: str,
                  shares: int, price: float, fee: float = 0, tax: float = 0,
                  theme_id: str = None, signal_type: str = None, note: str = None) -> int:
    with get_conn() as conn:
        c = conn.execute(
            """INSERT INTO simulated_trades
               (sim_id, trade_date, ticker, name, action, shares, price, fee, tax, theme_id, signal_type, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sim_id, trade_date, ticker, name, action, shares, price, fee, tax, theme_id, signal_type, note),
        )
        return c.lastrowid


def list_sim_trades(sim_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM simulated_trades WHERE sim_id = ? ORDER BY trade_date, id",
            (sim_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_sim_positions(sim_id: int) -> dict:
    """시뮬레이션 보유 포지션 (manual positions와 동일 로직)"""
    trades = list_sim_trades(sim_id)
    positions = {}
    for t in trades:
        tkr = t['ticker']
        p = positions.setdefault(tkr, {
            'ticker': tkr, 'name': t['name'], 'shares': 0,
            'total_buy_amount': 0.0, 'total_buy_shares': 0,
            'realized_pnl': 0.0, 'avg_price': 0.0,
            'theme_id': t['theme_id'],
            'first_buy_date': None, 'last_trade_date': None,
        })
        p['name'] = t['name'] or p['name']
        p['theme_id'] = t['theme_id'] or p['theme_id']
        p['last_trade_date'] = t['trade_date']
        if t['action'] == 'BUY':
            p['total_buy_amount'] += t['shares'] * t['price'] + (t['fee'] or 0)
            p['total_buy_shares'] += t['shares']
            p['shares'] += t['shares']
            if p['first_buy_date'] is None:
                p['first_buy_date'] = t['trade_date']
        elif t['action'] == 'SELL':
            sell_amount = t['shares'] * t['price'] - (t['fee'] or 0) - (t['tax'] or 0)
            avg = p['total_buy_amount'] / p['total_buy_shares'] if p['total_buy_shares'] > 0 else 0
            cost = t['shares'] * avg
            p['realized_pnl'] += sell_amount - cost
            p['shares'] -= t['shares']
            # 매도분의 원가만큼 total_buy 비례 차감 (재매수 시 평균단가 정확성)
            p['total_buy_amount'] = max(0.0, p['total_buy_amount'] - cost)
            p['total_buy_shares'] = max(0, p['total_buy_shares'] - t['shares'])
        if p['total_buy_shares'] > 0 and p['shares'] > 0:
            p['avg_price'] = p['total_buy_amount'] / p['total_buy_shares']
    return {k: v for k, v in positions.items() if v['shares'] > 0}
