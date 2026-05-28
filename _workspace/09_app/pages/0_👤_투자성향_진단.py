"""투자성향 진단 — 6항목 설문 → 5단계 유형 분류 → constraints 갱신"""
import streamlit as st
import json, os, sys, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import db

st.set_page_config(page_title='투자성향 진단', page_icon='👤', layout='wide')
st.title('👤 투자성향 진단')
st.caption('6항목 설문 → 5단계 유형 분류 → equity_pct·단일·섹터·변동성 한도 자동 산출')

# ─── 5단계 유형·제약 매트릭스 ───────────────────────
TYPES = [
    ('안정형',     0, 39,  20, 100, 10, 25, 15),
    ('안정추구형', 40, 59, 40, 100, 12, 28, 20),
    ('위험중립형', 60, 74, 60, 100, 13, 30, 25),
    ('적극투자형', 75, 89, 100, 150, 15, 30, 30),
    ('공격투자형', 90, 100, 120, 200, 20, 35, 40),
]

WEIGHTS = {
    '위험감내': 0.30,
    '투자기간': 0.20,
    '손실허용': 0.20,
    '목표수익': 0.15,
    '투자경험': 0.10,
    '유동성': 0.05,
}

CONSTRAINTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    '01_profile', 'constraints.json'
)

# ─── 현재 적용 ───────────────────────
current_type = '-'
current_equity_min = 0
current_equity_max = 0
if os.path.exists(CONSTRAINTS_PATH):
    try:
        cur = json.load(open(CONSTRAINTS_PATH, encoding='utf-8'))
        current_type = cur.get('investor_type', '-')
        current_equity_min = cur.get('equity_pct_min', 0)
        current_equity_max = cur.get('equity_pct_max', 0)
    except Exception:
        pass

col_now1, col_now2 = st.columns(2)
col_now1.metric('현재 적용 유형', current_type)
col_now2.metric('현재 equity 범위', f'{current_equity_min}~{current_equity_max}%')

st.divider()

# ─── 설문 폼 ───────────────────────
st.subheader('📋 설문 (각 항목 0~100점)')

with st.form('profile_form'):
    col1, col2 = st.columns(2)

    with col1:
        st.markdown('#### 위험감내도 (30%)')
        q1 = st.select_slider(
            '원금 손실을 어느 정도까지 감수할 수 있나요?',
            options=[0, 25, 50, 75, 100],
            value=75,
            format_func=lambda x: {
                0: '0 — 절대 손실 불가',
                25: '25 — 약간 (5% 이내)',
                50: '50 — 보통 (10~20%)',
                75: '75 — 큰 손실 OK (30%+)',
                100: '100 — 전액 손실도 감수',
            }[x],
        )

        st.markdown('#### 투자기간 (20%)')
        q2 = st.select_slider(
            '투자 자금의 활용 기간은?',
            options=[0, 25, 50, 75, 100],
            value=75,
            format_func=lambda x: {
                0: '0 — 1년 이내',
                25: '25 — 1~3년',
                50: '50 — 3~5년',
                75: '75 — 5~10년',
                100: '100 — 10년 이상',
            }[x],
        )

        st.markdown('#### 손실허용 (20%)')
        q3 = st.select_slider(
            '단기 -20% 손실 시 행동은?',
            options=[0, 25, 50, 75, 100],
            value=75,
            format_func=lambda x: {
                0: '0 — 즉시 청산',
                25: '25 — 부분 청산',
                50: '50 — 관망',
                75: '75 — 일부 추가매수',
                100: '100 — 전량 추가매수',
            }[x],
        )

    with col2:
        st.markdown('#### 목표수익 (15%)')
        q4 = st.select_slider(
            '연 목표 수익률은?',
            options=[0, 25, 50, 75, 100],
            value=75,
            format_func=lambda x: {
                0: '0 — 5% 이내 (예금+α)',
                25: '25 — 5~10%',
                50: '50 — 10~20%',
                75: '75 — 20~30%',
                100: '100 — 30%+',
            }[x],
        )

        st.markdown('#### 투자경험 (10%)')
        q5 = st.select_slider(
            '주식·ETF 투자 경험은?',
            options=[0, 25, 50, 75, 100],
            value=50,
            format_func=lambda x: {
                0: '0 — 신규',
                25: '25 — 1~3년',
                50: '50 — 3~5년',
                75: '75 — 5~10년',
                100: '100 — 10년+',
            }[x],
        )

        st.markdown('#### 유동성 (5%)')
        q6 = st.select_slider(
            '투자 자금의 인출 필요성은?',
            options=[0, 25, 50, 75, 100],
            value=75,
            format_func=lambda x: {
                0: '0 — 6개월 내 인출 가능성',
                25: '25 — 1년 내',
                50: '50 — 1~3년',
                75: '75 — 3~5년',
                100: '100 — 5년+ 묶어둘 수 있음',
            }[x],
        )

    name_input = st.text_input('투자자 이름 (선택)', value='')
    submitted = st.form_submit_button('🧮 진단 + constraints.json 적용', type='primary', use_container_width=True)

if submitted:
    # 가중점수 계산
    answers = {'위험감내': q1, '투자기간': q2, '손실허용': q3,
               '목표수익': q4, '투자경험': q5, '유동성': q6}
    weighted_score = sum(answers[k] * w for k, w in WEIGHTS.items())

    # 유형 분류
    matched_type = None
    for tname, lo, hi, e_min, e_max, max_single, max_sector, max_vol in TYPES:
        if lo <= weighted_score <= hi:
            matched_type = (tname, e_min, e_max, max_single, max_sector, max_vol)
            break

    if not matched_type:
        st.error('점수 분류 실패')
    else:
        tname, e_min, e_max, max_single, max_sector, max_vol = matched_type

        # constraints.json 갱신
        new_constraints = {
            'investor_type': tname,
            'investor_score': round(weighted_score, 2),
            'equity_pct_min': e_min,
            'equity_pct_max': e_max,
            'max_single_stock_pct': max_single,
            'max_sector_pct': max_sector,
            'max_annual_volatility': max_vol,
            'excluded_tickers': [],
            'excluded_sectors': [],
            'esg_filter': False,
            'profile_name': name_input or 'default',
            'updated_at': datetime.datetime.now().isoformat(),
            'survey_answers': answers,
            'survey_weights': WEIGHTS,
        }
        try:
            os.makedirs(os.path.dirname(CONSTRAINTS_PATH), exist_ok=True)
            json.dump(new_constraints, open(CONSTRAINTS_PATH, 'w', encoding='utf-8'),
                     ensure_ascii=False, indent=2)
            st.success(f'✅ **{tname}** 진단 완료 (점수 {weighted_score:.2f}) → constraints.json 적용됨')

            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric('유형', tname)
            col_b.metric('점수', f'{weighted_score:.1f}')
            col_c.metric('equity 범위', f'{e_min}~{e_max}%')
            col_d.metric('단일 한도', f'{max_single}%')

            st.markdown('### 📊 산출 제약')
            import pandas as pd
            st.dataframe(pd.DataFrame([
                {'항목': '주식 비중 (equity)', '값': f'{e_min}~{e_max}%', '의미': '매크로 W에 따라 이 범위 내 결정'},
                {'항목': '단일 종목 한도', '값': f'{max_single}%', '의미': '한 종목 최대 비중'},
                {'항목': '섹터 한도', '값': f'{max_sector}%', '의미': '한 섹터 최대 비중'},
                {'항목': '연환산 변동성 한도', '값': f'{max_vol}%', '의미': '포트폴리오 σ 상한'},
            ]), hide_index=True, use_container_width=True)

            # 해당 유형 theme_portfolio가 사전 산출되어 있는지 확인
            WS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            tp_path = os.path.join(WS_ROOT, '08_signals', f'theme_portfolio_{tname}.json')
            if os.path.exists(tp_path):
                tp = json.load(open(tp_path, encoding='utf-8'))
                st.success(
                    f'🎯 **{tname}** 유형의 모델 포트폴리오가 사전 산출되어 있습니다 — {tp["n_holdings"]}종목, equity {tp["equity_pct"]}%\n\n'
                    f'**바로 시뮬레이션 가능**: 🤖 자동매매 시뮬레이션 페이지 → 새 시뮬레이션 시작 시 "{tname}" 유형이 자동 선택됩니다.'
                )
                # 5개 유형 비교 페이지 안내
                st.info('💡 **여러 유형 동시 비교**: 📊 유형별 포트 비교 페이지 → 일괄 시뮬레이션 시작')
            else:
                st.warning(
                    f'⚠ {tname} 유형 사전 산출물 없음 — 로컬에서 다음 실행 필요:\n'
                    f'`python3 _workspace/07_portfolio/multi_profile_generate.py`'
                )
        except Exception as e:
            st.error(f'저장 실패: {e}\n\n*Streamlit Cloud는 파일 시스템이 휘발성이라 재배포 시 초기화됩니다.*')

st.divider()

# ─── 5단계 유형 안내 ───────────────────────
st.subheader('🏷 5단계 유형 분류표')
import pandas as pd
st.dataframe(pd.DataFrame([
    {'유형': t[0], '점수 범위': f'{t[1]}-{t[2]}', 'equity_pct': f'{t[3]}-{t[4]}%',
     '단일': f'{t[5]}%', '섹터': f'{t[6]}%', '변동성': f'{t[7]}%'}
    for t in TYPES
]), hide_index=True, use_container_width=True)

st.caption('💡 점수 = Σ(답변 × 가중치). 위험감내 30%·투자기간/손실허용 각 20%·목표수익 15%·경험 10%·유동성 5%')
