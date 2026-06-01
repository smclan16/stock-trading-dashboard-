"""수익률 관리 — 평가손익·누적·KOSPI 대비·샤프·MDD + 테마/종목 기여도"""
import streamlit as st
import pandas as pd
import datetime
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import db, perf, loader, costs, auth, theme
st.set_page_config(page_title='수익률 관리', page_icon='📈', layout='wide')
auth.require_login()
auth.logout_button()
theme.apply()  # 테마는 메인 대시보드 토글로 일괄 제어 (세션 전역)
st.title('📈 수익률 관리')
st.caption(f'{costs.explain_cost_model()}')

positions = db.get_positions()
if not positions:
    st.info('보유 종목이 없습니다. **"📝 포트폴리오 입력"** 페이지에서 체결 내역을 먼저 등록하세요.')
    st.stop()

# 평가용 종가 갱신 — 최근 130일 + KOSPI
@st.cache_data(ttl=300, show_spinner='📡 KRX 가격 데이터 수집 중…')
def fetch_data(tickers_tuple: tuple, days: int):
    tickers = list(tickers_tuple)
    price_history = perf.fetch_close_prices(tickers, days=days)
    kospi = perf.fetch_kospi_history(days=days)
    return price_history, kospi

# 기간 선택
period_opt = st.selectbox('평가 기간', ['1M', '3M', '6M', 'YTD', '1Y', 'ALL'], index=2)
days_map = {'1M': 30, '3M': 90, '6M': 180, 'YTD': 365, '1Y': 252, 'ALL': 504}
days = days_map[period_opt]

tickers = tuple(sorted(positions.keys()))
price_history, kospi = fetch_data(tickers, days)

# 최근 종가 (각 ticker의 마지막 값)
latest_prices = {}
for t, ph in price_history.items():
    if ph:
        latest_prices[t] = ph[max(ph.keys())]

# 평가
evals = perf.evaluate_positions(positions, latest_prices)
if not evals:
    st.error('가격 데이터 수집 실패. KRX 응답 확인 필요.')
    st.stop()

# ─── 상단 KPI (배당·수수료·세금 반영) ──────────────────────────────
total_cost = sum(p['cost'] for p in evals)
total_mkt = sum(p['market_value'] for p in evals)
total_pnl = total_mkt - total_cost
total_pnl_pct = (total_mkt / total_cost - 1) * 100 if total_cost > 0 else 0
total_realized = sum(p.get('realized_pnl', 0) for p in evals)
total_dividend = sum(p.get('dividend_total', 0) for p in evals)
total_pl_all = total_pnl + total_realized + total_dividend

col1, col2, col3, col4 = st.columns(4)
col1.metric('총 매수금액', f'{total_cost:,.0f}원')
col2.metric('총 평가금액', f'{total_mkt:,.0f}원')
col3.metric('평가손익', f'{total_pnl:,.0f}원', f'{total_pnl_pct:+.2f}%')
col4.metric('💰 누적 배당', f'{total_dividend:,.0f}원')

col5, col6, col7 = st.columns(3)
col5.metric('실현손익', f'{total_realized:,.0f}원')
col6.metric('총 손익 (평가+실현+배당)', f'{total_pl_all:,.0f}원')
col7.metric('총 손익률', f'{(total_pl_all/total_cost*100 if total_cost > 0 else 0):+.2f}%')

# ─── 보유 종목 평가 ──────────────────────────────
st.subheader('💼 보유 종목 평가')
edf = pd.DataFrame(evals)
display_cols = ['ticker', 'name', 'shares', 'avg_price', 'cur_price', 'cost', 'market_value', 'pnl', 'pnl_pct', 'theme_id']
display_df = edf[display_cols].rename(columns={
    'ticker': '티커', 'name': '종목명', 'shares': '수량',
    'avg_price': '평균단가', 'cur_price': '현재가',
    'cost': '매수금액', 'market_value': '평가금액',
    'pnl': '평가손익', 'pnl_pct': '수익률(%)',
    'theme_id': '테마',
})
st.dataframe(display_df, hide_index=True, use_container_width=True,
             column_config={
                 '평균단가': st.column_config.NumberColumn(format='%.0f원'),
                 '현재가': st.column_config.NumberColumn(format='%.0f원'),
                 '매수금액': st.column_config.NumberColumn(format='%d원'),
                 '평가금액': st.column_config.NumberColumn(format='%d원'),
                 '평가손익': st.column_config.NumberColumn(format='%d원'),
                 '수익률(%)': st.column_config.NumberColumn(format='%+.2f%%'),
             })

# ─── 일별 평가 시계열 ──────────────────────────────
st.divider()
st.subheader('📊 누적 수익률 시계열')

trades = db.list_trades()
# 평가 일자 = 가격 데이터 있는 일자
all_dates = set()
for ph in price_history.values():
    all_dates.update(ph.keys())
all_dates = sorted(all_dates)

if not all_dates:
    st.warning('가격 데이터 없음')
else:
    # 첫 매수일 이후만
    first_buy = min(p.get('first_buy_date') for p in positions.values() if p.get('first_buy_date'))
    eval_dates = [d for d in all_dates if d >= first_buy]

    if not eval_dates:
        st.warning('첫 매수일 이후 가격 데이터 없음')
    else:
        # 신용이자: 사용자 수동 입력은 신용 사용 여부 확인 불가 → 0으로 가정 (실 매매에서 입력 시 정확)
        base_cap = total_cost
        daily_v = perf.daily_portfolio_value(trades, price_history, eval_dates,
                                              credit_interest_pct=costs.CREDIT_INTEREST_PCT_ANNUAL,
                                              base_capital=base_cap,
                                              equity_pct=100.0)  # 신용 없음 가정
        metrics = perf.calc_perf_metrics(daily_v, kospi)

        # 누적 신용이자 표시
        last_v = daily_v[max(daily_v.keys())]
        if last_v.get('credit_interest_cum', 0) > 0:
            st.info(f"💸 누적 신용 이자 (연 {costs.CREDIT_INTEREST_PCT_ANNUAL}%): "
                    f"{last_v['credit_interest_cum']:,.0f}원 (자기자본 {base_cap:,.0f}원 초과 부분)")

        # KPI
        if metrics:
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            mc1.metric('누적 수익률', f"{metrics['cum_return_pct']:+.2f}%")
            if metrics.get('annualized_pct') is not None:
                mc2.metric('연환산', f"{metrics['annualized_pct']:+.2f}%")
            if metrics.get('kospi_return_pct') is not None:
                mc3.metric('KOSPI 동기간', f"{metrics['kospi_return_pct']:+.2f}%")
                mc4.metric('초과 (vs KOSPI)', f"{metrics['excess_vs_kospi_pct']:+.2f}%p")
            if metrics.get('sharpe_annual') is not None:
                mc5.metric('샤프지수 (연환산)', f"{metrics['sharpe_annual']}")
            mc6, mc7 = st.columns(2)
            mc6.metric('MDD (최대낙폭)', f"{metrics['mdd_pct']:.2f}%")
            mc7.metric('보유 기간', f"{metrics['days']}일")

        # 차트
        df_daily = pd.DataFrame([
            {'date': d, 'total_value': v['total_value'], 'cost_basis': v['cost_basis'],
             'dividend_cum': v.get('dividend_cum', 0),
             'credit_interest_cum': v.get('credit_interest_cum', 0)}
            for d, v in daily_v.items()
        ])
        df_daily['date_dt'] = pd.to_datetime(df_daily['date'])
        df_daily = df_daily.sort_values('date_dt')
        df_daily['수익률(%)'] = (df_daily['total_value'] / df_daily['cost_basis'] - 1) * 100

        # KOSPI 비교
        if kospi:
            kospi_df = pd.DataFrame([{'date': d, 'close': v} for d, v in kospi.items()])
            kospi_df['date_dt'] = pd.to_datetime(kospi_df['date'])
            kospi_df = kospi_df.sort_values('date_dt')
            # 첫 평가일 기준 정규화
            if not kospi_df.empty:
                first_kospi = kospi_df.iloc[0]['close']
                kospi_df['KOSPI 수익률(%)'] = (kospi_df['close'] / first_kospi - 1) * 100

        col_a, col_b = st.columns([2, 1])
        with col_a:
            chart_df = df_daily[['date_dt', '수익률(%)']].rename(columns={'date_dt': '일자'})
            chart_df = chart_df.set_index('일자')
            if kospi and not kospi_df.empty:
                kospi_merge = kospi_df[['date_dt', 'KOSPI 수익률(%)']].rename(columns={'date_dt': '일자'}).set_index('일자')
                chart_df = chart_df.join(kospi_merge, how='left').ffill()
            st.line_chart(chart_df)

        with col_b:
            st.markdown('**평가액 추이**')
            value_df = df_daily[['date_dt', 'total_value']].rename(columns={'date_dt': '일자', 'total_value': '평가액'}).set_index('일자')
            st.line_chart(value_df)

# ─── 테마별·종목별 기여도 ──────────────────────────────
st.divider()
st.subheader('🧭 테마별·종목별 기여도')

attr = perf.attribution_by_theme(evals)
attr_df = pd.DataFrame([{
    '테마': k, '종목 수': len(v['tickers']),
    '매수금액': round(v['cost'], 0),
    '평가금액': round(v['mkt'], 0),
    '손익': round(v['pnl'], 0),
    '수익률(%)': v['pnl_pct'],
    '종목': ', '.join(v['tickers']),
} for k, v in sorted(attr.items(), key=lambda x: -x[1]['pnl'])])

col1, col2 = st.columns(2)
with col1:
    st.markdown('### 테마별 기여도')
    st.dataframe(attr_df, hide_index=True, use_container_width=True,
                 column_config={
                     '매수금액': st.column_config.NumberColumn(format='%d원'),
                     '평가금액': st.column_config.NumberColumn(format='%d원'),
                     '손익': st.column_config.NumberColumn(format='%d원'),
                     '수익률(%)': st.column_config.NumberColumn(format='%+.2f%%'),
                 })
    # 손익 바 차트
    if not attr_df.empty:
        chart_attr = attr_df.set_index('테마')['손익']
        st.bar_chart(chart_attr)

with col2:
    st.markdown('### 종목별 기여도 (Top 10)')
    top10 = edf.sort_values('pnl', ascending=False)
    top_df = top10[['ticker', 'name', 'pnl', 'pnl_pct']].head(10).rename(columns={
        'ticker': '티커', 'name': '종목명', 'pnl': '손익', 'pnl_pct': '수익률(%)',
    })
    st.dataframe(top_df, hide_index=True, use_container_width=True,
                 column_config={
                     '손익': st.column_config.NumberColumn(format='%d원'),
                     '수익률(%)': st.column_config.NumberColumn(format='%+.2f%%'),
                 })

st.divider()
st.caption('💡 매일 종가 후 자동으로 KRX 가격을 수집해 평가 갱신. 첫 로딩은 5~10초 소요.')
