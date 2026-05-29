"""운영 가이드 — 매일/주간 작업 + 스크립트 실행 가이드"""
import streamlit as st
import subprocess, sys
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import loader, auth

st.set_page_config(page_title='운영 가이드', page_icon='⚙️', layout='wide')
auth.require_login()
auth.logout_button()
st.title('⚙️ 운영 가이드 — 매일/주간 작업')

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.markdown("""
## 📅 매일 작업 (종가 후, 약 18:00)

| 순서 | 스크립트 | 용도 |
|---|---|---|
| 1 | `_workspace/08_signals/compute_daily_signals.py` | 일일 시그널 산출 → HITL 큐 |
| 2 | 시그널 검토 → 증권사 HTS/MTS 수동 주문 (지정가 권장) | 매매 집행 |
| 3 | 체결 후 **"📝 포트폴리오 입력"** 페이지에서 체결 등록 | 포지션 갱신 |
| 4 | **"📈 수익률 관리"** 페이지에서 평가 확인 | 일별 모니터링 |
""")

st.info(
    '💡 **자동 실행 안내**\n\n'
    '이 버튼은 **로컬 환경에서만 안정적**입니다 (Streamlit Cloud는 KRX API IP 차단 - 429 에러).\n\n'
    '**권장 운영**:\n'
    '- 🤖 GitHub Actions 자동 실행 (매일 18:30 KST) — `.github/workflows/daily_signals.yml`\n'
    '- 💻 또는 로컬에서: `python3 _workspace/08_signals/compute_daily_signals.py`\n\n'
    'GitHub Actions가 자동 실행하고 JSON을 git push하면 이 앱은 자동 반영됩니다.'
)

if st.button('▶ 일일 시그널 재생성 (로컬·테스트용)', type='secondary'):
    with st.spinner('compute_daily_signals.py 실행 중…'):
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(WS, '08_signals', 'compute_daily_signals.py')],
                capture_output=True, text=True, timeout=300, cwd=WS,
            )
            if r.returncode == 0:
                st.success('✅ 일일 시그널 갱신 완료')
                st.code(r.stdout[-2000:])
                loader.load.clear()
            else:
                st.error('실행 실패 (Streamlit Cloud에서 KRX API 429 에러 가능 — GitHub Actions 활용 권장)')
                st.code(r.stderr[-2000:])
        except Exception as e:
            st.error(f'실행 오류: {e}')

st.divider()
st.markdown("""
## 📆 매주 작업 (금요일 종가 후)

| 순서 | 스크립트 | 용도 |
|---|---|---|
| 1 | `_workspace/02_macro/fetch_macro.py` | FRED 매크로 갱신 (VIX·US10Y) |
| 2 | `_workspace/03_universe/screen_full.py` | universe 재산출 (월 1회 권장 — FnSpace 비용) |
| 3 | `_workspace/04_ideas/collect_signals.py` + `discover_kr_themes.py` | 신호 수집 |
| 4 | `_workspace/04_ideas/recompute_allocation.py` | 비중 자동 계산 |
| 5 | `_workspace/05_matching/collect_company_meta.py` | 회사 메타 |
| 6 | `_workspace/06_research/compute_research_scores.py` | 6축 매력도 |
| 7 | `_workspace/07_portfolio/compose_portfolio.py` | 모델 포트 |
| 8 | `_workspace/validation/validate_logic.py` | 정합성 검증 |
| 9 | `_workspace/08_signals/compute_trading_signals.py` | 부모 시그널 |
| 10 | `_workspace/08_signals/compute_technical_scores.py` | 기술점수 |
| 11 | `_workspace/08_signals/build_theme_portfolio.py --n-themes 8` | 최종 포트 |
""")

with st.expander('🚀 일괄 실행 (주간)'):
    if st.button('▶ 매크로 → 검증 → 최종 포트 전체 재실행', type='secondary'):
        steps = [
            ('macro', '02_macro/fetch_macro.py'),
            ('ideas alloc', '04_ideas/recompute_allocation.py'),
            ('research', '06_research/compute_research_scores.py'),
            ('portfolio', '07_portfolio/compose_portfolio.py'),
            ('validation', 'validation/validate_logic.py'),
            ('signals', '08_signals/compute_trading_signals.py'),
            ('tech scores', '08_signals/compute_technical_scores.py --top-n 10'),
            ('theme port', '08_signals/build_theme_portfolio.py --n-themes 8'),
            ('daily', '08_signals/compute_daily_signals.py'),
        ]
        for label, cmd in steps:
            with st.spinner(f'{label} 실행 중…'):
                parts = cmd.split()
                full = [sys.executable, os.path.join(WS, parts[0])] + parts[1:]
                try:
                    r = subprocess.run(full, capture_output=True, text=True, timeout=600, cwd=WS)
                    if r.returncode == 0:
                        st.success(f'✅ {label}')
                    else:
                        st.warning(f'⚠ {label} 실행 결과 코드 {r.returncode}')
                        st.code(r.stderr[-500:])
                except Exception as e:
                    st.error(f'❌ {label}: {e}')
                    break
        loader.load.clear()
        st.info('🔄 페이지 새로고침으로 결과 확인')

st.divider()
st.markdown("""
## 🛡 HITL 원칙

1. **자동 매매 X** — 시스템은 시그널만 생성. 매매 집행은 사용자 명시 승인 후만.
2. **지정가 권장** — 시장가 매수는 슬리피지 큼. 호가 확인 후 분할 주문.
3. **분할 매수 규칙**: 1차 50% / 2차 25% (-5%, MA60 위) / 3차 25% (-10%, MA60 위).
4. **청산 트리거**: MA60 하향 / 트레일링 진입가 ×0.85 / 포트폴리오 변경.
5. **체결 후 즉시 등록** — 포트폴리오 입력에 반영해야 다음 사이클 정확.

## 🔗 데이터 소스

| 단계 | 소스 | 키 |
|---|---|---|
| 2 | FRED (VIX·US10Y·기타 매크로) | FRED_API_KEY |
| 3·6·8 | KRX Open API | KRX_API_KEY |
| 3·6 | FnSpace (account·consensus) | FNSPACE_API_KEY (종목당 과금) |
| 3·5·6 | Open DART (업종·공시·회사 메타) | DART_API_KEY |
| 4 | arXiv / Wikipedia / HackerNews | (무료) |
| 4 | GitHub Search | GITHUB_TOKEN |
| 4 | 네이버 뉴스 검색 | NAVER_CLIENT_ID/SECRET |
""")
