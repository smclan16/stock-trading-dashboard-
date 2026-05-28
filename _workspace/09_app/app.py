"""투자 의사결정 자동화 대시보드 — Streamlit MVP"""
import streamlit as st
import pandas as pd
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 에러 발생 시 사용자에게 명확한 메시지 (Streamlit Cloud의 generic error 회피)
try:
    from lib import loader, db
except Exception as e:
    st.error(f'⚠ 모듈 로드 실패: {type(e).__name__}: {e}')
    st.code(traceback.format_exc())
    st.stop()

st.set_page_config(
    page_title='Investment Pipeline Dashboard',
    page_icon='📊',
    layout='wide',
    initial_sidebar_state='expanded',
)

# CSS
st.markdown("""
<style>
.metric-card {background: #f7f9fc; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0;}
.theme-badge {background: #e6f0ff; color: #0050b3; padding: 0.2rem 0.5rem; border-radius: 0.3rem; font-size: 0.85em;}
</style>
""", unsafe_allow_html=True)

st.title('📊 투자 의사결정 자동화 대시보드')
st.caption('8단계 Top-Down 파이프라인 — 매크로 → 유니버스 → 아이디어 → 매칭 → 리서치 → 포트폴리오 → 시그널 → HITL')

# ─── 사이드바: 상태 요약 ──────────────────────────────
with st.sidebar:
    st.header('📅 운영 상태')
    daily = loader.load('daily_signals')
    if daily:
        asof = daily.get('as_of', '-')
        st.metric('최근 시그널일', asof)
        counts = daily.get('counts', {})
        if 'ENTRY_1ST' in counts:
            st.success(f'🟢 신규 진입 {counts["ENTRY_1ST"]}건')
        # v8 청산 시그널 (3종)
        exit_full = counts.get('EXIT_MA60_FULL', 0) + counts.get('EXIT_MA60', 0)  # 하위호환
        exit_partial = counts.get('EXIT_MA20_PARTIAL', 0)
        exit_remainder = counts.get('EXIT_MA60_REMAINDER', 0)
        if exit_full:
            st.error(f'🔴 MA60 청산 100% — {exit_full}건')
        if exit_partial:
            st.warning(f'🟠 MA20 청산 50% (약세장) — {exit_partial}건')
        if exit_remainder:
            st.error(f'🔴 MA60 잔여 청산 — {exit_remainder}건')
        adds = counts.get('ADD_2ND', 0) + counts.get('ADD_3RD', 0)
        if adds:
            st.info(f'➕ 분할 추가 {adds}건')
        if 'WATCH' in counts:
            st.write(f'👁 관찰 {counts["WATCH"]}건')
    st.divider()
    positions = db.get_positions()
    st.metric('현재 보유 종목 수', len(positions))
    info = loader.file_info('theme_portfolio')
    if info:
        st.caption(f'최신 포트: {info["mtime"]}')
    st.divider()
    st.markdown('### 페이지')
    st.caption('좌측 메뉴에서 페이지 선택')

# ─── 메인: 4개 탭으로 핵심 정보 ──────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(['🏠 홈', '🎯 오늘의 시그널', '💼 보유 현황', '🧭 파이프라인 요약'])

with tab1:
    st.subheader('대시보드 홈')

    col1, col2, col3, col4 = st.columns(4)

    # 매크로 (constraints의 현재 유형 + allocation 기반)
    alloc = loader.load('allocation')
    profile = loader.load('profile')
    cur_type = profile.get('investor_type', '-') if profile else '-'
    if alloc:
        col1.metric(f'주식 비중 ({cur_type})', f"{alloc.get('equity_pct', 0)}%",
                    delta=alloc.get('regime', '-'))
    # 유니버스
    uni = loader.load('universe')
    if uni:
        n = len(uni.get('universe', []))
        meta = uni.get('meta', {})
        col2.metric('유니버스', f'{n}종', delta=meta.get('regime', '-'))
    # 포트폴리오
    pf = loader.load('portfolio')
    if pf:
        col3.metric('모델 포트', f"{pf.get('n_holdings', 0)}종",
                    delta=pf.get('investor_type', '-'))
    # 최종 포트
    tp = loader.load('theme_portfolio')
    if tp:
        col4.metric('최종 포트 (테마 골격)', f"{tp.get('n_holdings', 0)}종",
                    delta=f"{tp.get('n_themes_selected', 0)} 테마")

    st.divider()

    # 최종 포트폴리오 표
    if tp:
        st.markdown('### 🎯 최종 포트폴리오 (테마 비중 골격 + default)')
        holdings = tp.get('holdings', [])
        df = pd.DataFrame([{
            '#': i + 1,
            '티커': h['ticker'],
            '종목명': h['name'],
            '섹터': h.get('sector', '-'),
            '시총': h.get('size_tier', '-'),
            '테마': f"#{h['idea_id']}" if h['idea_id'] != 'default' else 'default',
            '비중(%)': h['allocation_pct'],
            '자본대비(%)': h['capital_weight_pct'],
            '매력도': h.get('attractiveness', 0),
            '기술점수': h.get('tech_score', '-'),
            '결합': h.get('combined_score', '-'),
        } for i, h in enumerate(holdings)])
        st.dataframe(df, hide_index=True, use_container_width=True,
                     column_config={
                         '비중(%)': st.column_config.NumberColumn(format='%.2f%%'),
                         '자본대비(%)': st.column_config.NumberColumn(format='%.2f%%'),
                         '매력도': st.column_config.NumberColumn(format='%.1f'),
                     })

        # 테마별 분포 차트
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown('#### 테마별 비중')
            theme_df = df.groupby('테마')['비중(%)'].sum().reset_index().sort_values('비중(%)', ascending=False)
            st.bar_chart(theme_df.set_index('테마')['비중(%)'])
        with col_b:
            st.markdown('#### 섹터별 비중')
            sec_df = df.groupby('섹터')['비중(%)'].sum().reset_index().sort_values('비중(%)', ascending=False)
            st.bar_chart(sec_df.set_index('섹터')['비중(%)'])


with tab2:
    st.subheader('🎯 오늘의 시그널 (HITL 검토용)')
    if daily:
        st.caption(f'시그널 기준일: {daily.get("as_of", "-")}')
        # 액션 시그널
        actions = [s for s in daily.get('signals', []) if s['signal'] != 'WATCH']
        watches = [s for s in daily.get('signals', []) if s['signal'] == 'WATCH']

        if actions:
            st.markdown(f'### 🚨 액션 시그널 ({len(actions)}건)')
            for s in actions:
                emoji = {
                    'ENTRY_1ST': '🟢', 'ADD_2ND': '➕', 'ADD_3RD': '➕',
                    'EXIT_MA60_FULL': '🔴', 'EXIT_MA60_REMAINDER': '🔴',
                    'EXIT_MA20_PARTIAL': '🟠',
                    'EXIT_MA60': '🔴', 'EXIT_TRAILING': '🔴',  # 하위호환
                }.get(s['signal'], '⚪')
                with st.expander(f"{emoji} **{s['ticker']} {s['name']}** — {s['signal']} ({s.get('close',0):,.0f}원)"):
                    col1, col2 = st.columns(2)
                    col1.markdown(f"**액션:** {s.get('action', '-')}")
                    col1.markdown(f"**종가:** {s.get('close', 0):,.0f}원")
                    if 'ma60' in s:
                        col1.markdown(f"**MA60:** {s['ma60']:,.0f}원")
                    if 'weight_pct' in s:
                        col2.markdown(f"**비중:** {s['weight_pct']:.2f}%")
                        col2.markdown(f"**자본대비:** {s.get('capital_weight_pct', 0):.2f}%")
                    if 'pnl_pct' in s:
                        col2.markdown(f"**평가손익:** {s['pnl_pct']:+.2f}%")
                    st.info(f"**사유:** {s.get('reason', '-')}")
                    # 교체 후보
                    repls = s.get('replacement_candidates') or []
                    if repls:
                        st.markdown('**교체 후보 (같은 테마, MA60 위):**')
                        repls_df = pd.DataFrame(repls)
                        st.dataframe(repls_df, hide_index=True, use_container_width=True)
        else:
            st.info('오늘은 액션 시그널 없음')

        if watches:
            st.markdown(f'### 👁 관찰 종목 ({len(watches)}건)')
            wdf = pd.DataFrame([{
                '티커': s['ticker'], '종목': s['name'],
                '종가': s.get('close', 0),
                'MA60': s.get('ma60', '-'),
                '상태': 'P{} pnl{:+.1f}%'.format(s['phase'], s.get('pnl_pct', 0)) if 'phase' in s else '미보유',
                '사유': s.get('reason', '-'),
            } for s in watches])
            st.dataframe(wdf, hide_index=True, use_container_width=True)
    else:
        st.warning('일일 시그널 데이터가 없습니다. `compute_daily_signals.py` 실행이 필요합니다.')


with tab3:
    st.subheader('💼 현재 보유 현황')
    positions = db.get_positions()
    if not positions:
        st.info('보유 종목이 없습니다. **"📝 포트폴리오 입력"** 페이지에서 체결 내역을 입력하세요.')
    else:
        st.markdown('### 보유 포지션')
        # 평가는 페이지 이동 안내 (가격 갱신 별도)
        pdf = pd.DataFrame([{
            '티커': p['ticker'], '종목명': p['name'], '수량': p['shares'],
            '평균단가': round(p['avg_price'], 0),
            '매수금액': round(p['avg_price'] * p['shares'], 0),
            '테마': p.get('theme_id', '-'),
            '실현손익': round(p.get('realized_pnl', 0), 0),
            '최초매수일': p.get('first_buy_date', '-'),
        } for p in positions.values()])
        st.dataframe(pdf, hide_index=True, use_container_width=True,
                     column_config={
                         '평균단가': st.column_config.NumberColumn(format='%d원'),
                         '매수금액': st.column_config.NumberColumn(format='%d원'),
                         '실현손익': st.column_config.NumberColumn(format='%d원'),
                     })
        st.caption('현재가 평가는 **"📈 수익률 관리"** 페이지에서 확인하세요.')


with tab4:
    st.subheader('🧭 파이프라인 상태 요약')
    st.caption('8단계 산출물의 신선도와 핵심 지표를 한눈에 확인. 상세는 좌측 "1️⃣ ~ 8️⃣" 페이지.')

    stages = [
        ('1️⃣ 투자성향', 'profile', lambda d: f"{d.get('investor_type', '-')} / equity {d.get('equity_pct_min', 0)}~{d.get('equity_pct_max', 0)}%"),
        ('2️⃣ 매크로·자산배분', 'allocation', lambda d: f"VIX {d.get('vix', '-')} / equity {d.get('equity_pct', 0)}% / 레짐 {d.get('regime', '-')}"),
        ('3️⃣ 유니버스', 'universe', lambda d: f"{len(d.get('universe', []))}종 / 레짐 {d.get('meta', {}).get('regime', '-')}"),
        ('4️⃣ 투자 아이디어', 'ideas', lambda d: f"{len(d.get('ideas', []))}개 테마 + default {d.get('default_picks', {}).get('allocation_pct', 0)}%"),
        ('5️⃣ 종목 매칭', 'matching', lambda d: f"{d.get('summary', {}).get('n_idea_matched', 0)} 테마 매칭 / multi {d.get('summary', {}).get('multi_matched_count', 0)}"),
        ('6️⃣ 기업 리서치', 'research_scores', lambda d: f"{d.get('n_scored', 0)}종 점수화"),
        ('7️⃣ 포트폴리오', 'portfolio', lambda d: f"{d.get('n_holdings', 0)}종 / 단일 최대 {d.get('constraint_checks', {}).get('max_single_stock', {}).get('actual_max', 0):.2f}%"),
        ('8️⃣ 트레이딩 시그널', 'theme_portfolio', lambda d: f"{d.get('n_holdings', 0)}종 (테마 {d.get('n_themes_selected', 0)} + default {d.get('n_default_picks', 0)})"),
    ]

    for label, key, summary_fn in stages:
        info = loader.file_info(key)
        data = loader.load(key)
        col1, col2, col3 = st.columns([2, 4, 2])
        col1.markdown(f'**{label}**')
        if data:
            try:
                col2.write(summary_fn(data))
            except Exception:
                col2.write('(데이터 구조 확인 필요)')
            col3.caption(f'갱신 {info["mtime"]}' if info else '-')
        else:
            col2.warning('산출물 없음')
            col3.caption('-')

    # 검증 결과
    st.divider()
    st.markdown('### ✅ 정합성 검증')
    val = loader.load('validation')
    if val:
        st.json(val, expanded=False)


st.divider()
st.caption('🔒 면책: 본 자료는 정보 제공 목적의 분석 결과이며 투자 권유가 아닙니다. 모든 매매 결정의 책임은 투자자 본인에게 있습니다.')
