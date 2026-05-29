"""8단계 파이프라인 상세 — 단계별 로직·조건·산출물 표"""
import streamlit as st
import pandas as pd
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import loader

st.set_page_config(page_title='파이프라인 상세', page_icon='🧭', layout='wide')
st.title('🧭 파이프라인 상세 — 8단계 로직·조건·산출물')
st.caption('각 단계의 로직·통과조건·산식을 표로 정리. 하단 산출물은 현재 시점 데이터.')

stage = st.selectbox('단계 선택', [
    '1️⃣ 투자성향 진단',
    '2️⃣ 매크로·자산배분',
    '3️⃣ 유니버스 스크리닝',
    '4️⃣ 투자 아이디어 발굴',
    '5️⃣ 종목 매칭',
    '6️⃣ 기업 리서치 (6축 매력도)',
    '7️⃣ 포트폴리오 구성',
    '8️⃣ 트레이딩 시그널 + 최종 포트',
])

st.divider()


# ════════════════════════════════════════════════════════════════
if stage.startswith('1'):
    st.header('1️⃣ 투자성향 진단')
    st.markdown('**담당 에이전트:** `investment-profiler`')

    st.markdown('### 📋 입력 (6항목 설문 + 가중치)')
    st.dataframe(pd.DataFrame([
        {'항목': '위험감내도', '가중': '30%', '예시': '원금손실 감수 정도'},
        {'항목': '투자기간', '가중': '20%', '예시': '단기(1년) ~ 장기(10년+)'},
        {'항목': '손실허용', '가중': '20%', '예시': '-10%까지 OK ~ -50% OK'},
        {'항목': '목표수익', '가중': '15%', '예시': '연 5% ~ 30%+'},
        {'항목': '투자경험', '가중': '10%', '예시': '신규 ~ 10년+'},
        {'항목': '유동성', '가중': '5%', '예시': '6개월 내 인출 / 5년+'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 산식')
    st.code('총점 = Σ(항목점수 × 가중치)  (0~100)', language='text')

    st.markdown('### 🏷 5단계 유형 분류·제약')
    st.dataframe(pd.DataFrame([
        {'유형': '안정형', '점수': '0-39', 'equity_pct': '20-100%', '단일': '10%', '섹터': '25%', '변동성': '15%'},
        {'유형': '안정추구형', '점수': '40-59', 'equity_pct': '40-100%', '단일': '12%', '섹터': '28%', '변동성': '20%'},
        {'유형': '위험중립형', '점수': '60-74', 'equity_pct': '60-100%', '단일': '13%', '섹터': '30%', '변동성': '25%'},
        {'유형': '적극투자형 ⭐', '점수': '75-89', 'equity_pct': '100-150%', '단일': '15%', '섹터': '30%', '변동성': '30%'},
        {'유형': '공격투자형', '점수': '90-100', 'equity_pct': '120-200%', '단일': '20%', '섹터': '35%', '변동성': '40%'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 📤 현재 적용')
    d = loader.load('profile')
    if d:
        col1, col2 = st.columns(2)
        col1.metric('투자자 유형', d.get('investor_type', '-'))
        col1.metric('주식 비중 범위', f"{d.get('equity_pct_min', 0)}~{d.get('equity_pct_max', 0)}%")
        col2.metric('단일 종목 한도', f"{d.get('max_single_stock_pct', 0)}%")
        col2.metric('섹터 한도', f"{d.get('max_sector_pct', 0)}%")
        col2.metric('변동성 한도', f"{d.get('max_annual_volatility', 0)}%")
        with st.expander('상세 JSON'):
            st.json(d)


# ════════════════════════════════════════════════════════════════
elif stage.startswith('2'):
    st.header('2️⃣ 매크로·자산배분')
    st.markdown('**담당 에이전트:** `macro-allocator`')

    st.markdown('### 📋 입력 (FRED API)')
    st.dataframe(pd.DataFrame([
        {'지표': 'VIXCLS', '의미': 'CBOE 변동성 지수', '컷오프': 'Low <20 / Mod 20-30 / High ≥30'},
        {'지표': 'DGS10', '의미': '미국채 10년물 금리', '컷오프': 'Low <3.5% / Mod 3.5-4.5% / High ≥4.5%'},
        {'지표': 'FEDFUNDS', '의미': '연준 기준금리', '모니터링': '참고'},
        {'지표': 'UNRATE', '의미': '실업률', '모니터링': '참고'},
        {'지표': 'CPIAUCSL', '의미': '소비자물가지수', '모니터링': '참고'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 W_macro 3×3 매트릭스')
    st.dataframe(pd.DataFrame([
        {'VIX \\ US10Y': 'Low (<3.5%)', 'US10Y Low': '1.0 (초위험선호)', 'US10Y Mod': '0.8', 'US10Y High': '0.5 (중립)'},
        {'VIX \\ US10Y': 'Mod (20-30)', 'US10Y Low': '0.7', 'US10Y Mod': '0.5', 'US10Y High': '0.3'},
        {'VIX \\ US10Y': 'High (≥30)', 'US10Y Low': '0.3', 'US10Y Mod': '0.2', 'US10Y High': '0.0 (현금)'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 equity_pct 계산')
    st.code('equity_pct = equity_min + (equity_max − equity_min) × W_macro\n예: 적극투자형 (100~150) + W=0.5 → equity 125%', language='text')

    st.markdown('### 📤 현재 적용')
    d = loader.load('allocation')
    profile = loader.load('profile')
    cur_type = profile.get('investor_type', '-') if profile else '-'
    e_min = profile.get('equity_pct_min', '-') if profile else '-'
    e_max = profile.get('equity_pct_max', '-') if profile else '-'
    if d:
        # basis에서 매크로 raw 값 추출
        b = d.get('basis', {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric('👤 현재 투자성향', cur_type)
        col1.metric('equity 허용 범위', f'{e_min}~{e_max}%')
        col2.metric('VIX', b.get('vix', d.get('vix', '-')))
        col2.metric('US10Y', f"{b.get('us10y', d.get('us10y', 0))}%" if (b.get('us10y') or d.get('us10y')) else '-')
        col3.metric('W_macro', d.get('w_macro', '-'))
        col3.metric('레짐', d.get('regime', '-'))
        col4.metric('🎯 적용 주식 비중', f"{d.get('equity_pct', 0)}%")
        col4.metric('현금/신용', f"{d.get('cash_pct', 0)}%")
        st.caption(f"📐 산식: equity = {e_min} + ({e_max}-{e_min}) × W_macro({d.get('w_macro')}) = **{d.get('equity_pct')}%**")
        with st.expander('상세 JSON'):
            st.json(d)


# ════════════════════════════════════════════════════════════════
elif stage.startswith('3'):
    st.header('3️⃣ 유니버스 스크리닝')
    st.markdown('**담당 에이전트:** `universe-screener`')

    st.markdown('### 🛑 하드필터 (순차 통과)')
    st.dataframe(pd.DataFrame([
        {'#': 1, '조건': '거래정지 제외', '산식': '거래량 > 0'},
        {'#': 2, '조건': '관리종목 제외', '산식': 'KRX 소속부 ∉ {관리/투자주의/투자위험}'},
        {'#': 3, '조건': '20일 평균 거래대금 ≥ 30억원', '산식': 'mean(daily_turnover, 20d) ≥ 30억'},
        {'#': 4, '조건': '컨센서스 보유', '산식': 'FnSpace 영업이익 추정 non-null'},
        {'#': 5, '조건': '컨센서스 조회 경계 (비용)', '산식': '시총 상위 MAX_FNSPACE=1000만 fnspace 조회'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 5팩터 (섹터중립 z-score, ±5σ winsor)')
    st.dataframe(pd.DataFrame([
        {'#': 1, '팩터': '밸류', '산식': 'mean(z(−PER), z(−PBR), z(−EV/EBITDA))'},
        {'#': 2, '팩터': '퀄리티', '산식': 'z(ROE + ROA − 부채/100 + 영익률)'},
        {'#': 3, '팩터': '목표가↑(1M)', '산식': 'z((TP_now/TP_1M − 1) × 100), TP=종가×(1+괴리율/100)'},
        {'#': 4, '팩터': 'Fwd ROE↑(1M)', '산식': 'z((ROE_F12M_now / ROE_F12M_1M − 1) × 100)'},
        {'#': 5, '팩터': '거래대금↑ (회전율)', '산식': 'z((recent_1M_회전율 / prior_1M_회전율 − 1) × 100) | 회전율 = 거래대금/시총'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 레짐별 가중치')
    st.dataframe(pd.DataFrame([
        {'레짐': '위험선호', '밸류': '10%', '퀄리티': '15%', '목표가↑': '25%', 'Fwd ROE↑': '25%', '거래대금↑': '25%'},
        {'레짐': '중립 ⭐', '밸류': '20%', '퀄리티': '20%', '목표가↑': '20%', 'Fwd ROE↑': '20%', '거래대금↑': '20%'},
        {'레짐': '위험회피', '밸류': '30%', '퀄리티': '30%', '목표가↑': '15%', 'Fwd ROE↑': '15%', '거래대금↑': '10%'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🎁 대형주 시총 보너스 (v5)')
    st.dataframe(pd.DataFrame([
        {'시총': '≥ 30조원', '점수 보너스': '+0.50'},
        {'시총': '≥ 10조원', '점수 보너스': '+0.30'},
        {'시총': '≥ 3조원', '점수 보너스': '+0.15'},
        {'시총': '< 3조원', '점수 보너스': '0'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🏁 최종 선정')
    st.code('Top 100 = 5팩터 합산점수 상위 80 + 시총 상위 20 자동 포함', language='text')

    st.markdown('### 📤 현재 적용')
    d = loader.load('universe')
    if d:
        meta = d.get('meta', {})
        col1, col2, col3 = st.columns(3)
        col1.metric('총 종목', len(d.get('universe', [])))
        col1.metric('레짐', meta.get('regime', '-'))
        col2.metric('필터 후 풀', meta.get('filter_log', {}).get('20일평균거래대금 ≥ 30억', '-'))
        col2.metric('컨센서스 보유', meta.get('filter_log', {}).get('애널리스트 컨센서스 존재', '-'))
        col3.metric('KOSPI', meta.get('market_dist', {}).get('KOSPI', '-'))
        col3.metric('KOSDAQ', meta.get('market_dist', {}).get('KOSDAQ', '-'))

        univ = d.get('universe', [])
        st.markdown(f'#### 유니버스 전체 {len(univ)}종 (스크롤 또는 검색)')
        # 검색·시장·섹터 필터
        fc1, fc2, fc3 = st.columns([2, 1, 1])
        q = fc1.text_input('🔍 종목명·티커 검색', key='univ_q', placeholder='예: 삼성전자, 005930')
        markets = sorted({x.get('market', '-') for x in univ if x.get('market')})
        sectors = sorted({x.get('sector', '-') for x in univ if x.get('sector')})
        sel_market = fc2.multiselect('시장', markets, default=markets, key='univ_m')
        sel_sector = fc3.multiselect('섹터', sectors, default=sectors, key='univ_s')

        rows = []
        for x in univ:
            if sel_market and x.get('market') not in sel_market:
                continue
            if sel_sector and x.get('sector') not in sel_sector:
                continue
            if q and q.strip():
                k = q.strip().lower()
                if k not in (x.get('name', '') or '').lower() and k not in (x.get('ticker', '') or ''):
                    continue
            rows.append({
                '순위': x['rank'], '티커': x['ticker'], '종목명': x['name'],
                '시장': x.get('market', '-'), '섹터': x.get('sector', '-'),
                '시총(억)': (x.get('metrics') or {}).get('mcap_eok', 0),
                '종합': (x.get('scores') or {}).get('total', 0),
                'mcap auto': '✅' if x.get('auto_included_by_mcap') else '',
            })
        udf = pd.DataFrame(rows)
        st.caption(f'표시 {len(udf)}종 / 전체 {len(univ)}종')
        st.dataframe(udf, hide_index=True, use_container_width=True, height=600,
                     column_config={
                         '시총(억)': st.column_config.NumberColumn(format='%d'),
                         '종합': st.column_config.NumberColumn(format='%.3f'),
                     })


# ════════════════════════════════════════════════════════════════
elif stage.startswith('4'):
    st.header('4️⃣ 투자 아이디어 발굴')
    st.markdown('**담당 에이전트:** `idea-generator`')

    st.markdown('### 📋 트랙 A — 글로벌 키워드 변화 추적 (6 소스, 10 신호)')
    st.dataframe(pd.DataFrame([
        {'차원': '연구·기술', '소스': 'arXiv', '윈도우': '30d vs 180d'},
        {'차원': '연구·기술', '소스': 'Google Patents', '윈도우': '6M vs 24M'},
        {'차원': '개발자', '소스': 'GitHub 신규 repo', '윈도우': '30d vs 180d'},
        {'차원': '얼리어답터', '소스': 'HackerNews 언급', '윈도우': '30d vs 180d'},
        {'차원': '대중', '소스': 'Wikipedia EN/KO 페이지뷰', '윈도우': '4w vs 12w'},
        {'차원': '미디어 (한국)', '소스': '네이버 뉴스', '윈도우': '30d vs 180d'},
        {'차원': '사업화 (미국)', '소스': 'SEC EDGAR 10-K mention', '윈도우': '90d vs 365d'},
        {'차원': '정책 (미국)', '소스': 'federalregister.gov', '윈도우': '90d vs 365d'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 📋 트랙 B — 한국 자동 발굴 (NEW)')
    st.dataframe(pd.DataFrame([
        {'Stage': 1, '입력': '3단계 universe.json', '산출': '섹터별 5팩터 composite z-score 상위'},
        {'Stage': 2, '입력': 'DART 공시 (60일)', '산출': '자사주취득/소각·M&A·수주 빈도 30d vs 60d'},
        {'Stage': 3, '입력': '위 둘 + LLM', '산출': '한국 테마 명명 (예: #11 거버넌스·#12 K-내수·#13 K-방산조선)'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 5점 평가 (각 0~5, 총 25)')
    st.dataframe(pd.DataFrame([
        {'항목': 'Durability', '의미': '구조적 지속성 (단기 유행 X)'},
        {'항목': 'Capital Inflow', '의미': '자금 유입 (Capex 증가율 등)'},
        {'항목': 'Verifiability', '의미': '실적 검증 가능성 (매출 수치 가시)'},
        {'항목': 'Earliness', '의미': '초기성 (이미 늦었으면 감점)'},
        {'항목': 'Policy Momentum', '의미': '정책 지원·규제'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🏷 4분류')
    st.dataframe(pd.DataFrame([
        {'분류': '핵심 장기 테마 후보', '조건': 'total ≥ 18 AND 모든 항목 ≥ 3 AND Durability ≥ 4', '비중 multi': '×1.5'},
        {'분류': '관찰 리스트', '조건': '13 ≤ total ≤ 17  OR  (≥18 인데 Durability<4)', '비중 multi': '×1.0'},
        {'분류': '검증 부족', '조건': 'Earliness ≥ 4 AND (Capital ≤ 2 OR Verifiability ≤ 2)', '비중 multi': '×0.5'},
        {'분류': '아직 약한 아이디어', '조건': 'total < 10 OR Durability ≤ 1', '비중 multi': '×0'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 비중 산출 (recompute_allocation.py)')
    st.code('weighted_score = total × category_multiplier\nallocation_pct = (weighted / Σ) × 90    # uncovered 10% 고정\n매칭 0건 = 0%', language='text')

    st.markdown('### 📤 현재 적용')
    d = loader.load('ideas')
    if d:
        ideas = d.get('ideas', [])
        st.metric('총 테마 수', len(ideas))
        idf = pd.DataFrame([{
            'ID': i.get('id') or i.get('idea_id'),
            '테마': (i.get('theme') or '-')[:40],
            '카테고리': i.get('category', '-'),
            '총점': i.get('total_score', '-'),
            '비중(%)': i.get('allocation_pct', 0),
        } for i in ideas])
        idf = idf.sort_values('비중(%)', ascending=False)
        st.dataframe(idf, hide_index=True, use_container_width=True,
                     column_config={'비중(%)': st.column_config.NumberColumn(format='%.2f%%')})
        if 'default_picks' in d:
            with st.expander(f'default_picks ({d["default_picks"].get("allocation_pct", 0)}%)'):
                st.json(d['default_picks'])


# ════════════════════════════════════════════════════════════════
elif stage.startswith('5'):
    st.header('5️⃣ 종목 매칭')
    st.markdown('**담당 에이전트:** `idea-matcher`')

    st.markdown('### 📋 4축 매트릭스 (OR 결합 — 한 축이라도 관련 있으면 포함)')
    st.dataframe(pd.DataFrame([
        {'#': '①', '축': '사업의 내용', '판정': '주요 사업영역이 아이디어 수혜 영역과 연계'},
        {'#': '②', '축': '제품/서비스 아이템', '판정': '판매·공급 제품이 아이디어 키워드와 일치'},
        {'#': '③', '축': '시장의 인식', '판정': '컨센서스·언론·투자자가 그 테마 수혜주로 인식'},
        {'#': '④', '축': '종속/관계 기업', '판정': '종속·관계·지분 보유 회사가 수혜 영역에 속함'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🏷 intensity 라벨 (점수 X, 가중치만 활용)')
    st.dataframe(pd.DataFrame([
        {'intensity': 'direct', '가중치': '1.0', '예시': '#1 AI인프라 → SK하이닉스 (HBM 직접 생산)'},
        {'intensity': 'indirect', '가중치': '0.7', '예시': '#1 AI인프라 → 삼성SDS (클라우드 서비스)'},
        {'intensity': 'value_chain', '가중치': '0.4', '예시': '#1 AI인프라 → 한미반도체 (장비)'},
        {'intensity': 'perception', '가중치': '0.3', '예시': '#1 AI인프라 → 통신주 (이미지 연관)'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 📤 현재 적용')
    d = loader.load('matching')
    if d:
        summary = d.get('summary', {})
        col1, col2, col3 = st.columns(3)
        col1.metric('매칭 테마 수', summary.get('n_idea_matched', 0))
        col2.metric('매칭 종목 수', sum(summary.get('match_count_by_idea', {}).values()))
        col3.metric('Multi-매칭', summary.get('multi_matched_count', 0))

        st.markdown('#### 아이디어별 매칭 종목 수')
        counts = summary.get('match_count_by_idea', {})
        cdf = pd.DataFrame([{'테마': k, '종목수': v} for k, v in counts.items()])
        st.bar_chart(cdf.set_index('테마')['종목수'])


# ════════════════════════════════════════════════════════════════
elif stage.startswith('6'):
    st.header('6️⃣ 기업 리서치 (6축 매력도)')
    st.markdown('**담당 에이전트:** `company-researcher`')

    st.markdown('### 🔢 6축 산식 (총 100점)')
    st.dataframe(pd.DataFrame([
        {'축': '펀더멘털', '가중': '35', '세부': '밸류 12 + 퀄리티 13 + 성장 10', '데이터': '3단계 5팩터 재활용'},
        {'축': '모멘텀·리비전', '가중': '25', '세부': '목표가↑ 9 + ROE↑ 8 + 거래대금↑ 8', '데이터': 'FnSpace consensus'},
        {'축': '테마 적합도', '가중': '15', '세부': 'intensity × multi_boost × √(alloc_sum/10)', '데이터': '4·5단계'},
        {'축': '이벤트·Catalyst', '가중': '15', '세부': '뉴스↑ 8 + DART 긍정 4 + 수주 3', '데이터': 'Naver News·DART'},
        {'축': '리스크 역수', '가중': '10', '세부': '(100 − DART 부정 합산) / 10', '데이터': 'DART 키워드'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### ⚠ DART 부정 키워드 점수 (리스크 역수 계산용)')
    st.dataframe(pd.DataFrame([
        {'카테고리': '횡령·배임', '점수': '+30'},
        {'카테고리': '부도·회생', '점수': '+30'},
        {'카테고리': '상장폐지', '점수': '+25'},
        {'카테고리': '감자', '점수': '+20'},
        {'카테고리': '소송', '점수': '+15'},
        {'카테고리': '사채만기 전 취득', '점수': '+10'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 매크로 베타 (default_picks 필터)')
    st.code('β = Cov(stock, KOSPI) / Var(KOSPI)  | KRX 26주 weekly 회귀\ndefault_picks 후보 조건: β < 0.7 (매크로 무관성)', language='text')

    st.markdown('### 📤 현재 적용')
    d = loader.load('research_scores')
    if d:
        rows = d.get('rows', [])
        st.metric('점수화 종목', len(rows))
        st.markdown('#### 매력도 Top 20')
        rdf = pd.DataFrame([{
            '티커': r['ticker'], '종목명': r['name'], '섹터': r.get('sector', '-'),
            '펀더(35)': round(r.get('fundamental', 0), 1),
            '모멘(25)': round(r.get('momentum', 0), 1),
            '테마(15)': round(r.get('theme', 0), 1),
            'Cat(15)': round(r.get('catalyst', 0), 1),
            '리스역(10)': round(r.get('risk_inv', 0), 1),
            '총점': round(r.get('total_score', 0), 2),
        } for r in sorted(rows, key=lambda x: -x.get('total_score', 0))[:20]])
        st.dataframe(rdf, hide_index=True, use_container_width=True)


# ════════════════════════════════════════════════════════════════
elif stage.startswith('7'):
    st.header('7️⃣ 포트폴리오 구성 (모델 포트)')
    st.markdown('**담당 에이전트:** `portfolio-optimizer`')

    st.markdown('### 🔢 비중 산식 (3가지 weighting 옵션)')
    st.dataframe(pd.DataFrame([
        {'옵션': 'score', '산식': 'research_score × intensity_factor', '용도': '매력도 우선'},
        {'옵션': 'mcap', '산식': '시총 비례', '용도': 'KOSPI 추적'},
        {'옵션': 'hybrid ⭐ (현재)', '산식': 'mcap^0.5 × score^0.5 (기하평균)', '용도': '매력도 + 시총 균형'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔢 분배 절차 (option C — 사후 재분배)')
    st.code(
        '1차 분배:\n'
        '  For idea i:\n'
        '    For ticker t in matched(i):\n'
        '      weighted_score = research[t].total × intensity_factor[t.intensity]\n'
        '      ticker_w_in_idea = (weighted_score / Σ) × idea_alloc(i)\n'
        '      weights[t] += ticker_w_in_idea\n\n'
        '2차 사후 재분배:\n'
        '  surviving = {t: weights[t] ≥ min_weight}    # 0.5% 미만 제거\n'
        '  surviving으로 idea_alloc 정확 보존 재분배\n\n'
        '3차 제약 강제:\n'
        '  · 시총별 cap (15/10/5%) → 초과분 매력도 비례 재분배\n'
        '  · 섹터 cap (30%) → 섹터 비례 축소\n'
        '  · 자본대비 = weight × equity_pct / 100',
        language='text'
    )

    st.markdown('### 📏 시총별 차등 cap (단일 종목 한도)')
    st.dataframe(pd.DataFrame([
        {'시총 구간': '대형 (≥ 1조원)', 'cap': '15%'},
        {'시총 구간': '중형 (1천억 ~ 1조원)', 'cap': '10%'},
        {'시총 구간': '소형 (< 1천억원)', 'cap': '5%'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 📤 현재 적용')
    d = loader.load('portfolio')
    if d:
        cc = d.get('constraint_checks', {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric('보유 종목', d.get('n_holdings', 0))
        col2.metric('단일 최대', f"{cc.get('max_single_stock', {}).get('actual_max', 0):.2f}%")
        col3.metric('섹터 최대', f"{cc.get('max_sector', {}).get('actual_max_pct', 0):.2f}%")
        col4.metric('변동성 추정', f"{cc.get('max_annual_volatility', {}).get('estimated_pct', 0):.2f}%")

        st.markdown('#### 종목 Top 20')
        h = sorted(d.get('holdings', []), key=lambda x: -x.get('weight_pct', 0))[:20]
        hdf = pd.DataFrame([{
            '티커': x['ticker'], '종목명': x['name'], '섹터': x.get('sector', '-'),
            '시총': x.get('size_tier', '-'),
            '비중(%)': x.get('weight_pct', 0),
            '자본대비(%)': x.get('capital_weight_pct', 0),
            '매력도': x.get('attractiveness', 0),
            '매칭': '+'.join(f"#{a}" for a in x.get('matched_ideas', []) or []) or 'default',
        } for x in h])
        st.dataframe(hdf, hide_index=True, use_container_width=True)


# ════════════════════════════════════════════════════════════════
elif stage.startswith('8'):
    st.header('8️⃣ 트레이딩 시그널 + 최종 포트폴리오')
    st.markdown('**담당 에이전트:** `signal-generator`')

    st.markdown('### 📋 기술분석 7지표 (각 종목별 점수)')
    st.dataframe(pd.DataFrame([
        {'#': 1, '지표': '정배열', '가중': '20', '판정': 'MA5 > MA20 > MA60 > MA120 (모두 충족 시 +3 보너스)'},
        {'#': 2, '지표': 'MA60 이격', '가중': '15', '판정': 'close vs MA60: 5~25% 이격 sweet spot, ≥40% 과열'},
        {'#': 3, '지표': '모멘텀', '가중': '15', '판정': '6M > 0% (8점) + 6M > KOSPI (7점)'},
        {'#': 4, '지표': '거래량', '가중': '10', '판정': '5d/20d 평균 비율 1.0~1.5x sweet spot'},
        {'#': 5, '지표': '52주 위치', '가중': '15', '판정': '40~85% 추세추종 최적, ≥95% 천장 부근 감점'},
        {'#': 6, '지표': 'RSI(14)', '가중': '10', '판정': '50~70 강화, <30 과매도 감점, >80 과매수 감점'},
        {'#': 7, '지표': '변동성', '가중': '15', '판정': 'σ/median 0.8~1.2 sweet spot, ≥1.5 과변동 감점'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🏗 최종 포트 구성 (테마 비중 골격)')
    st.code(
        '1. 4단계 ideas.json에서 비중 큰 상위 N개 테마 선정 (기본 8)\n'
        '2. 미선택 테마 비중을 상위 N개에 비례 재분배 (합 90%)\n'
        '3. 각 테마에서 결합점수 최고 종목 선정:\n'
        '   결합점수 = 0.5 × 매력도(0~100) + 0.5 × 기술점수(0~100)\n'
        '   intensity 가중 (direct 1.0 > indirect 0.7 > value_chain 0.4 > perception 0.3)\n'
        '4. 단일 cap 초과 테마는 점수 비례로 K개 분할\n'
        '5. default_picks 4종 (각 2.5%, 합 10%) 추가\n'
        '6. 자본대비 = weight × equity_pct / 100',
        language='text'
    )

    st.markdown('### 🟢 진입 4조건 (모두 충족 시 1차 매수)')
    st.dataframe(pd.DataFrame([
        {'#': 1, '조건': '추세', '산식': 'close > MA(60)', '의미': '60일 이평선 위'},
        {'#': 2, '조건': '절대 모멘텀', '산식': '6M 수익률 > 0%', '의미': '6개월 상승세'},
        {'#': 3, '조건': '상대 강도', '산식': '종목 6M > KOSPI 6M', '의미': '시장 대비 outperform'},
        {'#': 4, '조건': '상대 변동성', '산식': 'σ_annual < median(univ σ) × 2.5', '의미': '과도 변동 차단'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### ➕ 분할 매수 (v8: 75/15/10)')
    st.dataframe(pd.DataFrame([
        {'단계': '1차', '비율': '75%', '트리거': '즉시 (4조건 충족 시)', '추가 조건': '—'},
        {'단계': '2차', '비율': '15%', '트리거': '1차 진입가 대비 −7%', '추가 조건': 'MA60 위 + exit_stage=0'},
        {'단계': '3차', '비율': '10%', '트리거': '1차 진입가 대비 −12%', '추가 조건': 'MA60 위 + exit_stage=0'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 🔴 청산 트리거 (v8 — KOSPI 강세장 필터)')
    st.dataframe(pd.DataFrame([
        {'시그널': 'EXIT_MA20_PARTIAL', '트리거': 'close < MA(20) + KOSPI 6M < 0 (약세장)', '액션': '50% 청산', '비고': 'exit_stage=1 진입'},
        {'시그널': 'EXIT_MA60_FULL', '트리거': 'close < MA(60), exit_stage=0', '액션': '100% 청산', '비고': '단번에 전량'},
        {'시그널': 'EXIT_MA60_REMAINDER', '트리거': 'close < MA(60), exit_stage=1', '액션': '잔여 50% 청산', '비고': 'MA20 청산 후 최종'},
    ]), hide_index=True, use_container_width=True)
    st.caption('💡 강세장(KOSPI 6M ≥ 0%)에서는 MA20 청산 무효 → MA60 하향까지 보유 (Buy & Hold 추적력 유지)')

    st.markdown('### 💸 매매비용 (키움증권 기준)')
    st.dataframe(pd.DataFrame([
        {'항목': '매수 수수료', '비율': '0.015%'},
        {'항목': '매도 수수료', '비율': '0.015%'},
        {'항목': '매도 증권거래세', '비율': '0.18% (KOSPI/KOSDAQ 동일, 농특세 포함)'},
        {'항목': '슬리피지 (지정가)', '비율': '0.05% (보수)'},
        {'항목': '**왕복 합**', '비율': '**약 0.28%**'},
        {'항목': '신용 이자', '비율': '연 6% (자기자본 100% 초과분 일별)'},
    ]), hide_index=True, use_container_width=True)

    st.markdown('### 📤 현재 적용')
    tp = loader.load('theme_portfolio')
    if tp:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric('총 종목', tp.get('n_holdings', 0))
        col2.metric('핵심 테마', tp.get('n_themes_selected', 0))
        col3.metric('default', tp.get('n_default_picks', 0))
        col4.metric('합계', f"{tp.get('total_weight_pct', 0):.2f}%")

        st.markdown('#### 최종 포트폴리오')
        h = tp.get('holdings', [])
        hdf = pd.DataFrame([{
            '#': i + 1, '티커': x['ticker'], '종목명': x['name'],
            '테마': f"#{x['idea_id']}" if x['idea_id'] != 'default' else 'default',
            '비중(%)': x.get('allocation_pct', 0),
            '자본대비(%)': x.get('capital_weight_pct', 0),
            '매력도': x.get('attractiveness', 0),
            '기술점수': x.get('tech_score', '-'),
            '결합': x.get('combined_score', '-'),
            '강도': x.get('intensity', '-'),
        } for i, x in enumerate(h)])
        st.dataframe(hdf, hide_index=True, use_container_width=True)


st.divider()
st.caption('💡 각 단계 산출물은 `_workspace/0X_*/` 디렉토리에 JSON·MD·DOCX로 저장됨')
