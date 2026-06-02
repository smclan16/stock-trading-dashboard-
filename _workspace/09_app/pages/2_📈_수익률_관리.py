"""수익률 관리 — 평가손익·누적·KOSPI 대비·샤프·MDD + 테마/종목 기여도"""
import streamlit as st
import pandas as pd
import datetime
import os, sys
import requests
import urllib.parse
import re

# App-specific lib path (_workspace/09_app)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import db, perf, loader, costs, auth, theme

# Core lib path (_workspace/lib) for config
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib"))
import config

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

# ─── 보유 종목 상세 지표 & 관련 뉴스 ──────────────────────────────
st.divider()
st.subheader('🔍 보유 종목 상세 지표 & 관련 뉴스')

# 뉴스 가져오기용 함수 캐싱 (최대 10분)
@st.cache_data(ttl=600, show_spinner='📰 실시간 뉴스 수집 중…')
def fetch_news(keyword: str, display: int = 5):
    items = []
    
    # 1. 네이버 뉴스 API 시도 (API 키 존재 시)
    client_id = config.get("NAVER_CLIENT_ID")
    client_secret = config.get("NAVER_CLIENT_SECRET")
    if client_id and client_secret:
        url = f"https://openapi.naver.com/v1/search/news.json?query={urllib.parse.quote(keyword)}&display={display}&sort=sim"
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "User-Agent": "Mozilla/5.0"
        }
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                raw_items = res.json().get("items", [])
                for rit in raw_items:
                    items.append({
                        "title": clean_html(rit.get("title", "")),
                        "link": rit.get("link", ""),
                        "pubDate": rit.get("pubDate", ""),
                        "description": clean_html(rit.get("description", ""))
                    })
        except Exception:
            pass
            
    # 2. 키가 없거나 실패 시 구글 뉴스 RSS 피드로 폴백 (API 키 불필요)
    if not items:
        import xml.etree.ElementTree as ET
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                for item in root.findall(".//item")[:display]:
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    pub_date = item.findtext("pubDate", "")
                    
                    source = ""
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        title = parts[0]
                        source = parts[1]
                    
                    items.append({
                        "title": title,
                        "link": link,
                        "pubDate": pub_date,
                        "description": f"출처: {source}" if source else ""
                    })
        except Exception:
            pass
            
    return items

def clean_html(text: str) -> str:
    text = re.sub(r'<[^>]*>', '', text)
    text = text.replace("&quot;", '"').replace("&lt;", '<').replace("&gt;", '>').replace("&amp;", '&').replace("&apos;", "'")
    return text

# 종목 선택 dropdown
ticker_options = {f"{positions[t]['ticker']} {positions[t]['name']}": t for t in tickers}
selected_stock = st.selectbox(
    '상세 분석할 보유 종목 선택',
    options=['(선택하세요)'] + list(ticker_options.keys()),
    key='holding_detail_select',
)

if selected_stock != '(선택하세요)':
    sel_ticker = ticker_options[selected_stock]
    stock_name = positions[sel_ticker]['name']
    
    # 데이터 로드
    univ_data = loader.load('universe')
    research_data = loader.load('research_scores')
    tech_data = loader.load('technical_scores')
    
    univ_list = univ_data.get('universe', []) if univ_data else []
    research_rows = research_data.get('rows', []) if research_data else []
    tech_scores = tech_data.get('scores', {}) if tech_data else {}
    
    # 해당 종목 찾기
    u_stock = next((x for x in univ_list if x['ticker'] == sel_ticker), None)
    r_stock = next((x for x in research_rows if x['ticker'] == sel_ticker), None)
    t_stock = tech_scores.get(sel_ticker)
    
    st.markdown(f"### {stock_name} ({sel_ticker})")
    
    tab_fund, tab_tech, tab_res, tab_news = st.tabs([
        '📊 기본 및 펀더멘털', 
        '📐 기술적 분석 지표', 
        '🎯 리서치 매력도', 
        '📰 실시간 관련 뉴스'
    ])
    
    with tab_fund:
        if u_stock:
            metrics = u_stock.get('metrics') or {}
            scores = u_stock.get('scores') or {}
            
            st.markdown('#### 📊 펀더멘털 기본 지표')
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('시가총액', f"{metrics.get('mcap_eok', 0):,.0f}억원")
            c2.metric('PER', f"{metrics.get('per', 0):.1f}배" if metrics.get('per') else '-')
            c3.metric('PBR', f"{metrics.get('pbr', 0):.2f}배" if metrics.get('pbr') else '-')
            c4.metric('EV/EBITDA', f"{metrics.get('ev_ebitda', 0):.1f}" if metrics.get('ev_ebitda') else '-')
            
            c5, c6, c7, c8 = st.columns(4)
            c5.metric('ROE', f"{metrics.get('roe', 0):.1f}%" if metrics.get('roe') else '-')
            c6.metric('ROA', f"{metrics.get('roa', 0):.1f}%" if metrics.get('roa') else '-')
            c7.metric('영업이익률', f"{metrics.get('opm', 0):.1f}%" if metrics.get('opm') else '-')
            c8.metric('20일 평균 거래대금', f"{metrics.get('turnover20_eok', 0):,.0f}억원" if metrics.get('turnover20_eok') else '-')
            
            st.markdown('#### 🧮 유니버스 5팩터 점수 (섹터중립 z-score)')
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            sc1.metric('밸류', f"{scores.get('value', 0):.3f}")
            sc2.metric('퀄리티', f"{scores.get('quality', 0):.3f}")
            sc3.metric('목표가 리비전', f"{scores.get('tp_rev', 0):.3f}")
            sc4.metric('ROE 리비전', f"{scores.get('roe_rev', 0):.3f}")
            sc5.metric('거래대금', f"{scores.get('turnover', 0):.3f}")
        else:
            st.warning('유니버스에 해당 종목 데이터가 없습니다. (최근 스크리닝 제외 종목일 수 있음)')

    with tab_tech:
        if t_stock:
            indicators = t_stock.get('indicators') or {}
            
            st.markdown('#### 📐 주요 기술 지표')
            col_ts, col_signal = st.columns(2)
            col_ts.metric('종합 기술 점수', f"{t_stock.get('tech_score', 0)} / 100")
            if t_stock.get('ma60_exit_signal'):
                col_signal.error('🚨 MA60 하향 돌파 (청산 시그널 발생)')
            else:
                col_signal.success('🟢 MA60 상향 보유 상태')
                
            pc1, pc2, pc3, pc4, pc5 = st.columns(5)
            pc1.metric('현재가', f"{indicators.get('close', 0):,.0f}원")
            pc2.metric('MA5', f"{indicators.get('ma5', 0):,.0f}원")
            pc3.metric('MA20', f"{indicators.get('ma20', 0):,.0f}원")
            pc4.metric('MA60', f"{indicators.get('ma60', 0):,.0f}원")
            pc5.metric('MA120', f"{indicators.get('ma120', 0):,.0f}원")
            
            pm1, pm2, pm3, pm4, pm5 = st.columns(5)
            pm1.metric('MA60 이격', f"{indicators.get('ma60_margin_pct', 0):+.1f}%")
            pm2.metric('6M 수익률', f"{indicators.get('ret_6m_pct', 0):+.1f}%")
            pm3.metric('RSI(14)', f"{indicators.get('rsi14', 0):.1f}")
            pm4.metric('52주 위치', f"{indicators.get('pos_52w_pct', 0):.1f}%")
            pm5.metric('연간 변동성', f"{indicators.get('sigma_annual_pct', 0):.1f}%")
        else:
            st.warning('기술 지표 데이터가 없습니다.')

    with tab_res:
        if r_stock:
            st.markdown('#### 🎯 6축 매력도 점수')
            rc1, rc2, rc3, rc4, rc5, rc6 = st.columns(6)
            rc1.metric('펀더(35)', f"{r_stock.get('fundamental', 0):.1f}")
            rc2.metric('모멘(25)', f"{r_stock.get('momentum', 0):.1f}")
            rc3.metric('테마(15)', f"{r_stock.get('theme', 0):.1f}")
            rc4.metric('Catalyst(15)', f"{r_stock.get('catalyst', 0):.1f}")
            rc5.metric('리스크역(10)', f"{r_stock.get('risk_inv', 0):.1f}")
            rc6.metric('🏆 총합', f"{r_stock.get('total_score', 0):.1f}")
            
            with st.expander('점수 세부 구성'):
                detail_rows = [
                    {'영역': '밸류 점수', '값': r_stock.get('val_pts', '-')},
                    {'영역': '퀄리티 점수', '값': r_stock.get('qual_pts', '-')},
                    {'영역': '성장 점수', '값': r_stock.get('growth_pts', '-')},
                    {'영역': '목표가↑ 점수', '값': r_stock.get('tp_pts', '-')},
                    {'영역': 'ROE리비전 점수', '값': r_stock.get('roe_rev_pts', '-')},
                    {'영역': '거래대금↑ 점수', '값': r_stock.get('tov_pts', '-')},
                    {'영역': '테마 raw', '값': r_stock.get('theme_raw', '-')},
                    {'영역': '뉴스 성장(30d vs 180d)', '값': f"{r_stock.get('news_growth_pct', 0):.0f}%" if r_stock.get('news_growth_pct') is not None else '-'},
                    {'영역': '뉴스 점수', '값': r_stock.get('news_pts', '-')},
                    {'영역': 'DART 긍정', '값': r_stock.get('dart_pos_pts', '-')},
                    {'영역': 'DART 수주', '값': r_stock.get('dart_contract_pts', '-')},
                    {'영역': '리스크 점수', '값': r_stock.get('risk_score', '-')},
                    {'영역': '매크로 베타', '값': r_stock.get('macro_beta', '-')},
                ]
                st.dataframe(pd.DataFrame(detail_rows), hide_index=True, use_container_width=True)
                
            if r_stock.get('risk_triggered'):
                st.warning(f"⚠️ 리스크 트리거: {', '.join(r_stock['risk_triggered'])}")
        else:
            st.warning('기업 리서치 점수 데이터가 없습니다.')

    with tab_news:
        st.markdown(f"#### 📰 '{stock_name}' 관련 실시간 뉴스")
        news_items = fetch_news(stock_name)
        if not news_items:
            news_items = fetch_news(f"{stock_name} 주식")
            
        if news_items:
            for item in news_items:
                title = item.get('title', '')
                desc = item.get('description', '')
                link = item.get('link', '')
                pub_date = item.get('pubDate', '')
                
                try:
                    import email.utils
                    parsed_date = email.utils.parsedate_to_datetime(pub_date)
                    date_str = parsed_date.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    date_str = pub_date
                
                st.markdown(
                    f"""
                    <div style="background-color: {theme.BG_CARD}; padding: 0.7rem 0.85rem; border-radius: 4px; border: 1px solid {theme.GRID}; border-left: 3px solid {theme.AMBER}; margin-bottom: 0.6rem;">
                        <span style="color: #8c9ba5; font-size: 0.72rem;">{date_str}</span>
                        <div style="margin: 0.15rem 0; font-size: 0.9rem; font-weight: bold;"><a href="{link}" target="_blank" style="color: {theme.AMBER}; text-decoration: none;">{title}</a></div>
                        <p style="margin: 0.3rem 0 0 0; font-size: 0.82rem; color: {theme.TEXT}; line-height: 1.35;">{desc}</p>
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
        else:
            st.info('실시간 관련 뉴스를 찾을 수 없습니다.')

st.divider()
st.caption('💡 매일 종가 후 자동으로 KRX 가격을 수집해 평가 갱신. 첫 로딩은 5~10초 소요.')
