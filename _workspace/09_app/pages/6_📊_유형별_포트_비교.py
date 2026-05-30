"""5개 투자성향 유형별 포트폴리오 비교 + 동시 시뮬레이션"""
import streamlit as st
import pandas as pd
import json, os, sys, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import db, perf, costs, auth, theme
st.set_page_config(page_title='유형별 포트 비교', page_icon='📊', layout='wide')
auth.require_login()
auth.logout_button()
theme.toggle(default=True); theme.apply()
st.title('📊 5개 투자성향 유형별 포트폴리오 비교')
st.caption('각 유형의 모델 포트 + 다중 시뮬레이션 동시 운영·수익률 비교')

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILES = ['안정형', '안정추구형', '위험중립형', '적극투자형', '공격투자형']

# ─── 1. 5개 유형별 모델 포트 비교 ──────────────────────────────
st.header('1️⃣ 모델 포트폴리오 비교')

cols = st.columns(5)
loaded = {}
for i, name in enumerate(PROFILES):
    path = os.path.join(WS, '08_signals', f'theme_portfolio_{name}.json')
    if os.path.exists(path):
        try:
            tp = json.load(open(path, encoding='utf-8'))
            loaded[name] = tp
            with cols[i]:
                st.metric(name, f'{tp["n_holdings"]}종', delta=f'equity {tp["equity_pct"]}%')
        except Exception as e:
            cols[i].error(f'{name} 로드 실패')
    else:
        cols[i].warning(f'{name} 산출 X')

if not loaded:
    st.error('5개 유형별 산출물 없음. 로컬에서 `python3 _workspace/07_portfolio/multi_profile_generate.py` 실행 후 GitHub push 필요.')
    st.stop()

# 유형 선택 → 상세 표
selected_type = st.selectbox('상세 보기', list(loaded.keys()))
if selected_type:
    tp = loaded[selected_type]
    st.markdown(f'### {selected_type} — {tp["n_holdings"]}종목')
    h = tp.get('holdings', [])
    hdf = pd.DataFrame([{
        '#': i + 1, '티커': x['ticker'], '종목명': x['name'],
        '테마': f"#{x['idea_id']}" if x['idea_id'] != 'default' else 'default',
        '비중(%)': x.get('allocation_pct', 0),
        '자본대비(%)': x.get('capital_weight_pct', 0),
        '매력도': x.get('attractiveness', 0),
        '기술점수': x.get('tech_score', '-'),
    } for i, x in enumerate(h)])
    st.dataframe(hdf, hide_index=True, use_container_width=True,
                 column_config={
                     '비중(%)': st.column_config.NumberColumn(format='%.2f%%'),
                     '자본대비(%)': st.column_config.NumberColumn(format='%.2f%%'),
                     '매력도': st.column_config.NumberColumn(format='%.1f'),
                 })

# 5개 유형 종목 매트릭스 비교
st.markdown('### 5개 유형 보유 종목 매트릭스')
all_tickers = set()
ticker_names = {}
for name, tp in loaded.items():
    for h in tp['holdings']:
        all_tickers.add(h['ticker'])
        ticker_names[h['ticker']] = h['name']

matrix_rows = []
for tkr in sorted(all_tickers):
    row = {'티커': tkr, '종목명': ticker_names[tkr]}
    for name in PROFILES:
        if name not in loaded:
            row[name] = '-'
            continue
        h = next((x for x in loaded[name]['holdings'] if x['ticker'] == tkr), None)
        row[name] = f'{h["allocation_pct"]:.2f}%' if h else '-'
    matrix_rows.append(row)

st.dataframe(pd.DataFrame(matrix_rows), hide_index=True, use_container_width=True)


# ─── 2. 다중 시뮬레이션 ──────────────────────────────
st.divider()
st.header('2️⃣ 5개 유형 동시 시뮬레이션')

st.markdown('### 🚀 일괄 시작 (5개 유형 한꺼번에)')

with st.form('multi_sim'):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        sim_start = st.date_input('시작 일자', value=datetime.date.today())
    with col_b:
        sim_capital = st.number_input('각 시뮬레이션 자본 (억원)', min_value=0.1, value=1.0, step=0.1)
    with col_c:
        sim_label = st.text_input('이름 prefix', value=f'5유형 {datetime.date.today().strftime("%y%m%d")}')

    selected_profiles = st.multiselect(
        '시뮬레이션할 유형 선택', PROFILES, default=PROFILES,
    )
    submitted = st.form_submit_button('🚀 선택한 유형 모두 시작', type='primary', use_container_width=True)

    if submitted:
        if not selected_profiles:
            st.error('최소 1개 유형 선택')
        else:
            created = []
            for pname in selected_profiles:
                if pname not in loaded:
                    st.warning(f'{pname} 산출물 없음 — 건너뜀')
                    continue
                tp = loaded[pname]
                cap_won = sim_capital * 1e8
                sim_id = db.create_simulation(
                    name=f'{sim_label} — {pname}',
                    start_date=sim_start.strftime('%Y%m%d'),
                    start_capital=cap_won,
                    notes=f'{pname} 자동 추종 (equity {tp["equity_pct"]}%)',
                )
                # 1차 매수 등록 (75%)
                n = 0
                for h in tp['holdings']:
                    if not h.get('indicators') or not h['indicators'].get('close'):
                        continue
                    price = h['indicators']['close']
                    cap_pct = h.get('capital_weight_pct', 0)
                    budget = cap_won * cap_pct / 100
                    first_won = budget * 0.75
                    shares = int(first_won / price) if price > 0 else 0
                    if shares <= 0 and budget >= price:
                        shares = 1
                    if shares <= 0:
                        continue
                    c = costs.calc_trade_cost(price, shares, 'BUY')
                    theme_id = 'default' if h['idea_id'] == 'default' else f'#{h["idea_id"]}'
                    db.add_sim_trade(
                        sim_id=sim_id, trade_date=sim_start.strftime('%Y%m%d'),
                        ticker=h['ticker'], name=h['name'],
                        action='BUY', shares=shares, price=price,
                        fee=c['fee'] + c['slippage'], tax=c['tax'],
                        theme_id=theme_id, signal_type='ENTRY_1ST',
                        note=f'{pname} 1차 75% (비중 {cap_pct:.2f}%)',
                    )
                    n += 1
                created.append((pname, sim_id, n))
                st.success(f'✅ {pname} 시뮬레이션 #{sim_id} 시작 ({n}종 매수)')
            if created:
                st.info(f'총 {len(created)}개 시뮬레이션 생성. 아래 비교 차트에서 확인.')
                st.rerun()


# ─── 3. 비교 차트 ──────────────────────────────
st.divider()
st.header('3️⃣ 시뮬레이션 수익률 비교 차트')

sims = db.list_simulations()
if not sims:
    st.info('시뮬레이션이 없습니다. 위에서 일괄 시작하세요.')
else:
    sim_filter = st.text_input('🔍 시뮬레이션 이름 필터 (prefix)', value='5유형')
    filtered = [s for s in sims if sim_filter in (s['name'] or '')] if sim_filter else sims

    if not filtered:
        st.warning(f"'{sim_filter}' 포함 시뮬레이션 없음")
    else:
        st.write(f'표시 대상: {len(filtered)}개')

        # 모든 보유 종목 합집합
        all_tickers_sim = set()
        for s in filtered:
            for t in db.list_sim_trades(s['id']):
                all_tickers_sim.add(t['ticker'])

        if all_tickers_sim:
            @st.cache_data(ttl=600, show_spinner='📡 KRX 가격 수집…')
            def fetch_all(tickers_tuple: tuple, days: int):
                tickers = list(tickers_tuple)
                return perf.fetch_close_prices(tickers, days=days), perf.fetch_kospi_history(days=days)

            price_history, kospi = fetch_all(tuple(sorted(all_tickers_sim)), 130)

            # 시뮬레이션별 KPI
            kpi_rows = []
            chart_data = {}
            for s in filtered:
                sim_id = s['id']
                trades = db.list_sim_trades(sim_id)
                if not trades:
                    continue
                first_buy = min(t['trade_date'] for t in trades if t['action'] == 'BUY')
                dates = sorted({d for ph in price_history.values() for d in ph.keys() if d >= first_buy})
                if not dates:
                    continue
                daily_v = perf.daily_portfolio_value(trades, price_history, dates,
                                                     credit_interest_pct=costs.CREDIT_INTEREST_PCT_ANNUAL,
                                                     base_capital=s['start_capital'])
                metrics = perf.calc_perf_metrics(daily_v, kospi)
                last_v = daily_v[max(daily_v.keys())]
                kpi_rows.append({
                    '시뮬레이션': s['name'],
                    '시작일': s['start_date'],
                    '자본(억)': round(s['start_capital'] / 1e8, 2),
                    '평가액': last_v.get('market_value', 0),
                    '실현': last_v.get('realized_pnl', 0),
                    '배당': last_v.get('dividend_cum', 0),
                    '신용이자': last_v.get('credit_interest_cum', 0),
                    '수익률(%)': metrics.get('cum_return_pct'),
                    'KOSPI(%)': metrics.get('kospi_return_pct'),
                    '초과(%p)': metrics.get('excess_vs_kospi_pct'),
                    '샤프': metrics.get('sharpe_annual'),
                    'MDD(%)': metrics.get('mdd_pct'),
                })
                # 차트용
                for d, v in daily_v.items():
                    chart_data.setdefault(s['name'], {})[d] = (v['total_value'] / v['cost_basis'] - 1) * 100 if v['cost_basis'] > 0 else 0

            if kpi_rows:
                st.markdown('### KPI 비교')
                kdf = pd.DataFrame(kpi_rows)
                st.dataframe(kdf, hide_index=True, use_container_width=True,
                             column_config={
                                 '평가액': st.column_config.NumberColumn(format='%d원'),
                                 '실현': st.column_config.NumberColumn(format='%d원'),
                                 '배당': st.column_config.NumberColumn(format='%d원'),
                                 '신용이자': st.column_config.NumberColumn(format='%d원'),
                                 '수익률(%)': st.column_config.NumberColumn(format='%+.2f%%'),
                                 'KOSPI(%)': st.column_config.NumberColumn(format='%+.2f%%'),
                                 '초과(%p)': st.column_config.NumberColumn(format='%+.2f'),
                                 '샤프': st.column_config.NumberColumn(format='%.2f'),
                                 'MDD(%)': st.column_config.NumberColumn(format='%+.2f'),
                             })

            if chart_data:
                st.markdown('### 누적 수익률 시계열 비교')
                df_long = pd.DataFrame([
                    {'date': pd.to_datetime(d), '시뮬레이션': name, '수익률(%)': v}
                    for name, dvs in chart_data.items() for d, v in dvs.items()
                ])
                pivot = df_long.pivot_table(index='date', columns='시뮬레이션', values='수익률(%)').sort_index().ffill()
                st.line_chart(pivot)

st.divider()
st.caption('💡 5개 유형은 동일 시점에 시작했을 때 각 투자성향 제약에 따른 종목·비중 차이를 보여줍니다. KOSPI 대비 outperform 지속성을 추적하세요.')
