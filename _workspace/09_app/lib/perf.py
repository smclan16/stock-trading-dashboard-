"""수익률·위험 지표 계산 (배당·매매비용·신용이자 반영)"""
import math
import datetime
from typing import Optional
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'lib'))


def get_krx():
    from datasource import KRXMarket
    return KRXMarket()


def _yfinance_close_prices(tickers: list, days: int = 130) -> dict:
    """yfinance bulk download — 종목당 1회, KRX 429 시 fallback."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    price_map = {t: {} for t in tickers}
    period_arg = f'{int(days * 1.5)}d'  # 평일 N일 ≈ 달력 1.5N일
    for tkr in tickers:
        for suffix in ['.KS', '.KQ']:
            try:
                df = yf.Ticker(f"{tkr}{suffix}").history(period=period_arg, auto_adjust=False)
                if df.empty:
                    continue
                for ts, row in df['Close'].dropna().items():
                    ds = ts.strftime('%Y%m%d')
                    price_map[tkr][ds] = float(row)
                break  # 성공하면 다음 종목
            except Exception:
                continue
    return price_map


def fetch_close_prices(tickers: list, days: int = 130) -> dict:
    """일별 종가 시계열. v17: KRX 시도 1회 후 실패 시 yfinance bulk.
    Streamlit Cloud에서는 KRX 429 → yfinance 자동 사용.
    """
    # v17: KRX 호출 자체 skip (Streamlit Cloud는 차단됨), yfinance만 사용
    return _yfinance_close_prices(tickers, days)


def fetch_kospi_history(days: int = 130) -> dict:
    """KOSPI 일별 지수. v17: yfinance ^KS11 사용 (KRX 차단 회피)."""
    try:
        import yfinance as yf
        period_arg = f'{int(days * 1.5)}d'
        df = yf.Ticker('^KS11').history(period=period_arg, auto_adjust=False)
        return {ts.strftime('%Y%m%d'): float(row) for ts, row in df['Close'].dropna().items()}
    except Exception:
        return {}


def evaluate_positions(positions: dict, latest_prices: dict) -> list:
    """보유 포지션 평가 — 평가손익 + 실현 + 배당 통합
    Returns: [{... , dividend_total}]
    """
    out = []
    for tkr, p in positions.items():
        cur = latest_prices.get(tkr)
        if not cur:
            continue
        cost = p['avg_price'] * p['shares']
        mkt = cur * p['shares']
        pnl = mkt - cost
        pnl_pct = (cur / p['avg_price'] - 1) * 100 if p['avg_price'] > 0 else 0
        dividend = p.get('dividend_total', 0)
        out.append({
            'ticker': tkr, 'name': p['name'], 'shares': p['shares'],
            'avg_price': round(p['avg_price'], 2), 'cur_price': cur,
            'cost': round(cost, 0), 'market_value': round(mkt, 0),
            'pnl': round(pnl, 0), 'pnl_pct': round(pnl_pct, 2),
            'realized_pnl': round(p['realized_pnl'], 0),
            'dividend_total': round(dividend, 0),
            'total_return': round(pnl + p['realized_pnl'] + dividend, 0),
            'theme_id': p.get('theme_id'),
            'first_buy_date': p.get('first_buy_date'),
        })
    out.sort(key=lambda x: -x['market_value'])
    return out


def daily_portfolio_value(positions_history: list, price_history: dict, dates: list,
                          credit_interest_pct: float = 6.0,
                          base_capital: float = None) -> dict:
    """일별 포트폴리오 평가액 시계열 (배당 + 신용이자 반영)
    positions_history: list of trades (action: BUY/SELL/DIVIDEND)
    credit_interest_pct: 신용 이자 연율 (기본 6%)
    base_capital: 자기자본 기준액 (이 액수 초과한 cost_basis에 신용이자 부과)
    """
    out = {}
    holdings = {}
    cost_basis = 0.0
    realized = 0.0
    dividend_cum = 0.0
    credit_interest_cum = 0.0
    trade_idx = 0
    sorted_trades = sorted(positions_history, key=lambda x: (x['trade_date'], x.get('id', 0)))
    prev_date = None

    for d in dates:
        while trade_idx < len(sorted_trades) and sorted_trades[trade_idx]['trade_date'] <= d:
            t = sorted_trades[trade_idx]
            tkr = t['ticker']
            if t['action'] == 'BUY':
                holdings[tkr] = holdings.get(tkr, 0) + t['shares']
                cost_basis += t['shares'] * t['price'] + (t.get('fee') or 0)
            elif t['action'] == 'SELL':
                holdings[tkr] = holdings.get(tkr, 0) - t['shares']
                cost_basis -= t['shares'] * t['price']
                realized += (t['shares'] * t['price'] - (t.get('fee') or 0) - (t.get('tax') or 0))
            elif t['action'] == 'DIVIDEND':
                dividend_cum += t['shares'] * t['price']
            trade_idx += 1

        # 신용 이자 (cost_basis가 base_capital 초과한 부분에 일별)
        if base_capital and cost_basis > base_capital and prev_date:
            try:
                d1 = datetime.datetime.strptime(prev_date, '%Y%m%d')
                d2 = datetime.datetime.strptime(d, '%Y%m%d')
                days_diff = max(1, (d2 - d1).days)
            except Exception:
                days_diff = 1
            margin = cost_basis - base_capital
            credit_interest_cum += margin * credit_interest_pct / 100 / 365 * days_diff
        prev_date = d

        mkt_val = 0
        for tkr, sh in holdings.items():
            if sh <= 0:
                continue
            cls = None
            ph = price_history.get(tkr, {})
            for dd in sorted(ph.keys(), reverse=True):
                if dd <= d:
                    cls = ph[dd]
                    break
            if cls:
                mkt_val += sh * cls
        total_v = mkt_val + realized + dividend_cum - credit_interest_cum
        out[d] = {
            'date': d, 'market_value': round(mkt_val, 0),
            'cost_basis': round(cost_basis, 0),
            'realized_pnl': round(realized, 0),
            'dividend_cum': round(dividend_cum, 0),
            'credit_interest_cum': round(credit_interest_cum, 0),
            'total_value': round(total_v, 0),
        }
    return out


def calc_perf_metrics(daily_values: dict, kospi: dict) -> dict:
    """누적 수익률·KOSPI 대비·샤프·MDD"""
    if not daily_values:
        return {}
    dates = sorted(daily_values.keys())
    if len(dates) < 2:
        return {}
    # 누적
    start_v = daily_values[dates[0]]['cost_basis']
    end_v = daily_values[dates[-1]]['market_value'] + daily_values[dates[-1]]['realized_pnl']
    if start_v <= 0:
        return {}
    cum_ret = (end_v / start_v - 1) * 100

    # KOSPI 동기간
    kospi_dates = sorted([d for d in kospi.keys() if d >= dates[0]])
    if len(kospi_dates) >= 2:
        kospi_ret = (kospi[kospi_dates[-1]] / kospi[kospi_dates[0]] - 1) * 100
    else:
        kospi_ret = None

    # 일별 수익률
    rets = []
    prev = start_v
    for d in dates[1:]:
        cur = daily_values[d]['market_value'] + daily_values[d]['realized_pnl']
        if prev > 0:
            rets.append((cur / prev - 1))
        prev = cur

    sharpe = None
    if len(rets) >= 5:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
        sigma = math.sqrt(var)
        if sigma > 0:
            sharpe = round(mu / sigma * math.sqrt(252), 2)

    # MDD
    peak = start_v
    mdd = 0
    for d in dates:
        v = daily_values[d]['market_value'] + daily_values[d]['realized_pnl']
        peak = max(peak, v)
        dd = (v - peak) / peak * 100 if peak > 0 else 0
        mdd = min(mdd, dd)

    days_held = (datetime.datetime.strptime(dates[-1], '%Y%m%d') - datetime.datetime.strptime(dates[0], '%Y%m%d')).days
    annualized = ((1 + cum_ret / 100) ** (365 / days_held) - 1) * 100 if days_held > 0 else None

    return {
        'period_start': dates[0], 'period_end': dates[-1], 'days': days_held,
        'cum_return_pct': round(cum_ret, 2),
        'annualized_pct': round(annualized, 2) if annualized else None,
        'kospi_return_pct': round(kospi_ret, 2) if kospi_ret is not None else None,
        'excess_vs_kospi_pct': round(cum_ret - kospi_ret, 2) if kospi_ret is not None else None,
        'sharpe_annual': sharpe,
        'mdd_pct': round(mdd, 2),
        'cost_basis': round(start_v, 0),
        'market_value': round(daily_values[dates[-1]]['market_value'], 0),
        'realized_pnl': round(daily_values[dates[-1]]['realized_pnl'], 0),
        'total_value': round(daily_values[dates[-1]]['market_value'] + daily_values[dates[-1]]['realized_pnl'], 0),
    }


def attribution_by_theme(positions_eval: list) -> dict:
    """테마별 기여도 (현재 평가손익 기준)"""
    out = {}
    for p in positions_eval:
        t = p.get('theme_id') or 'unknown'
        out.setdefault(t, {'pnl': 0, 'cost': 0, 'mkt': 0, 'tickers': []})
        out[t]['pnl'] += p['pnl']
        out[t]['cost'] += p['cost']
        out[t]['mkt'] += p['market_value']
        out[t]['tickers'].append(p['ticker'])
    for t in out:
        if out[t]['cost'] > 0:
            out[t]['pnl_pct'] = round(out[t]['pnl'] / out[t]['cost'] * 100, 2)
        else:
            out[t]['pnl_pct'] = 0
    return out
