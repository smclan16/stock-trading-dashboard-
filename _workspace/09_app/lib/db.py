"""Supabase — 사용자별 거래(체결) 이력 + 시뮬 + 공유 시세 캐시.

모든 사용자 데이터 함수는 현재 로그인 사용자(auth.current_user_id())로 스코프된다.
함수 시그니처는 기존 SQLite 버전과 동일 — 페이지 코드 변경 불필요.
positions 계산 로직은 기존과 100% 동일.
"""
from lib.supa import get_client
from lib import auth


def _uid() -> str:
    return auth.current_user_id()


def _ymd(s):
    """Supabase date('2026-05-04') → 앱 표준 'YYYYMMDD'(대시 없음).
    앱 전체가 strftime('%Y%m%d') 형식을 쓰고 가격/KOSPI 딕셔너리 키와 비교하므로
    DB에서 읽은 date 값의 대시를 제거해 형식을 일치시킨다."""
    return s.replace("-", "") if isinstance(s, str) else s


# ─── 거래(체결) ────────────────────────────────────────
def add_trade(trade_date: str, ticker: str, name: str, action: str,
              shares: int, price: float, fee: float = 0, tax: float = 0,
              theme_id: str = None, note: str = None) -> int:
    res = get_client().table("trades").insert({
        "user_id": _uid(), "trade_date": trade_date, "ticker": ticker,
        "name": name, "action": action, "shares": shares, "price": price,
        "fee": fee, "tax": tax, "theme_id": theme_id, "note": note,
    }).execute()
    return res.data[0]["id"]


def delete_trade(trade_id: int):
    get_client().table("trades").delete().eq("id", trade_id).eq("user_id", _uid()).execute()


def list_trades(ticker: str = None) -> list:
    q = get_client().table("trades").select("*").eq("user_id", _uid())
    if ticker:
        q = q.eq("ticker", ticker)
    res = q.order("trade_date", desc=True).order("id", desc=True).execute()
    rows = res.data or []
    for r in rows:
        r["trade_date"] = _ymd(r.get("trade_date"))
    return rows


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


# ─── 시세 캐시 (공유 — user_id 없음) ──────────────────
def cache_price(ticker: str, trade_date: str, close: float):
    get_client().table("price_cache").upsert({
        "ticker": ticker, "trade_date": trade_date, "close": close,
    }).execute()


def get_cached_prices(ticker: str) -> dict:
    res = get_client().table("price_cache").select("trade_date, close") \
        .eq("ticker", ticker).order("trade_date").execute()
    return {_ymd(r['trade_date']): r['close'] for r in (res.data or [])}


# ─── 사용자 메타 (key-value) ──────────────────────────
def set_meta(key: str, value: str):
    get_client().table("portfolio_meta").upsert({
        "user_id": _uid(), "key": key, "value": value,
    }).execute()


def get_meta(key: str, default: str = None) -> str:
    res = get_client().table("portfolio_meta").select("value") \
        .eq("user_id", _uid()).eq("key", key).limit(1).execute()
    return res.data[0]["value"] if res.data else default


# ─── 시뮬레이션 ────────────────────────────────────────
def create_simulation(name: str, start_date: str, start_capital: float, notes: str = None) -> int:
    res = get_client().table("simulations").insert({
        "user_id": _uid(), "name": name, "start_date": start_date,
        "start_capital": start_capital, "notes": notes, "last_synced": start_date,
    }).execute()
    return res.data[0]["id"]


def list_simulations() -> list:
    res = get_client().table("simulations").select("*") \
        .eq("user_id", _uid()).order("id", desc=True).execute()
    rows = res.data or []
    for r in rows:
        r["start_date"] = _ymd(r.get("start_date"))
    return rows


def get_simulation(sim_id: int) -> dict:
    res = get_client().table("simulations").select("*") \
        .eq("id", sim_id).eq("user_id", _uid()).limit(1).execute()
    if not res.data:
        return None
    row = res.data[0]
    row["start_date"] = _ymd(row.get("start_date"))
    return row


def delete_simulation(sim_id: int):
    uid = _uid()
    sb = get_client()
    # simulated_trades 는 sim_id FK(on delete cascade) 지만 user_id 스코프로 명시 삭제
    sb.table("simulated_trades").delete().eq("sim_id", sim_id).eq("user_id", uid).execute()
    sb.table("simulations").delete().eq("id", sim_id).eq("user_id", uid).execute()


def update_simulation(sim_id: int, **fields):
    if not fields:
        return
    get_client().table("simulations").update(fields) \
        .eq("id", sim_id).eq("user_id", _uid()).execute()


def add_sim_trade(sim_id: int, trade_date: str, ticker: str, name: str, action: str,
                  shares: int, price: float, fee: float = 0, tax: float = 0,
                  theme_id: str = None, signal_type: str = None, note: str = None) -> int:
    res = get_client().table("simulated_trades").insert({
        "user_id": _uid(), "sim_id": sim_id, "trade_date": trade_date,
        "ticker": ticker, "name": name, "action": action, "shares": shares,
        "price": price, "fee": fee, "tax": tax, "theme_id": theme_id,
        "signal_type": signal_type, "note": note,
    }).execute()
    return res.data[0]["id"]


def list_sim_trades(sim_id: int) -> list:
    res = get_client().table("simulated_trades").select("*") \
        .eq("sim_id", sim_id).eq("user_id", _uid()) \
        .order("trade_date").order("id").execute()
    rows = res.data or []
    for r in rows:
        r["trade_date"] = _ymd(r.get("trade_date"))
    return rows


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
