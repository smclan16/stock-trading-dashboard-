"""운영 가이드 — 매일/주간 작업 + 스크립트 실행 가이드"""
import streamlit as st
import subprocess, shutil
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import loader, auth, theme
st.set_page_config(page_title='운영 가이드', page_icon='⚙️', layout='wide')
auth.require_login()
auth.logout_button()
theme.apply()  # 테마는 메인 대시보드 토글로 일괄 제어 (세션 전역)
st.title('⚙️ 운영 가이드 — 매일/주간 작업')

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_python():
    """현재 Streamlit이 실행 중인 Python 환경의 인터프리터를 찾는다.
    sys.executable이 패키지가 설치된 환경을 가리키면 그대로 사용하고,
    아니면 PATH에서 pandas가 설치된 python3을 탐색한다."""
    # 1차: 현재 프로세스의 Python (Streamlit이 실행 중인 환경)
    try:
        r = subprocess.run(
            [sys.executable, '-c', 'import pandas; print("OK")'],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and 'OK' in r.stdout:
            return sys.executable
    except Exception:
        pass

    # 2차: PATH에서 python3 탐색
    for candidate in ['python3', 'python']:
        path = shutil.which(candidate)
        if path:
            try:
                r = subprocess.run(
                    [path, '-c', 'import pandas; print("OK")'],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and 'OK' in r.stdout:
                    return path
            except Exception:
                continue

    # 3차: 폴백 — sys.executable (에러는 실행 시 표시)
    return sys.executable


def _run_script(label, cmd, python_exe, env, timeout=600):
    """스크립트 한 건 실행. (성공여부, 메시지) 반환."""
    parts = cmd.split()
    script_path = os.path.join(WS, parts[0])
    args = parts[1:]
    full = [python_exe, script_path] + args

    if not os.path.exists(script_path):
        return False, f'스크립트 파일이 없습니다: {parts[0]}'

    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout, cwd=WS, env=env)
        if r.returncode == 0:
            return True, r.stdout[-800:] if r.stdout else ''
        else:
            err_msg = (r.stderr or r.stdout or '')[-800:]
            return False, f'종료코드 {r.returncode}\n{err_msg}'
    except subprocess.TimeoutExpired:
        return False, f'타임아웃 ({timeout}초 초과)'
    except Exception as e:
        return False, str(e)


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
    python_exe = _find_python()
    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.join(WS, 'lib') + os.pathsep + env.get('PYTHONPATH', '')
    with st.spinner('compute_daily_signals.py 실행 중…'):
        ok, msg = _run_script('daily', '08_signals/compute_daily_signals.py', python_exe, env, timeout=300)
        if ok:
            st.success('✅ 일일 시그널 갱신 완료')
            if msg:
                st.code(msg)
            loader.load.clear()
        else:
            st.error(f'실행 실패 — {msg}')
            st.caption(f'사용된 Python: `{python_exe}`')

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
    continue_on_error = st.checkbox(
        '오류 발생 시에도 다음 단계 계속 실행',
        value=True,
        help='체크하면 한 단계가 실패해도 나머지 단계를 계속 실행합니다.',
    )

    if st.button('▶ 매크로 → 검증 → 최종 포트 전체 재실행', type='secondary'):
        python_exe = _find_python()
        env = os.environ.copy()
        env['PYTHONPATH'] = os.path.join(WS, 'lib') + os.pathsep + env.get('PYTHONPATH', '')

        st.caption(f'🐍 Python: `{python_exe}`')

        steps = [
            ('① 매크로 갱신', '02_macro/fetch_macro.py'),
            ('② 비중 재계산', '04_ideas/recompute_allocation.py'),
            ('③ 리서치 점수', '06_research/compute_research_scores.py'),
            ('④ 모델 포트', '07_portfolio/compose_portfolio.py'),
            ('⑤ 로직 검증', 'validation/validate_logic.py'),
            ('⑥ 트레이딩 시그널', '08_signals/compute_trading_signals.py'),
            ('⑦ 기술 점수', '08_signals/compute_technical_scores.py --top-n 10'),
            ('⑧ 테마 포트', '08_signals/build_theme_portfolio.py --n-themes 8'),
            ('⑨ 일일 시그널', '08_signals/compute_daily_signals.py'),
        ]

        results = []
        progress = st.progress(0, text='준비 중…')
        for i, (label, cmd) in enumerate(steps):
            progress.progress((i) / len(steps), text=f'{label} 실행 중…')
            with st.spinner(f'{label} 실행 중…'):
                ok, msg = _run_script(label, cmd, python_exe, env)
                results.append((label, ok, msg))
                if ok:
                    st.success(f'✅ {label}')
                else:
                    st.error(f'❌ {label}')
                    with st.expander(f'에러 상세 — {label}'):
                        st.code(msg)
                    if not continue_on_error:
                        st.warning('⛔ 오류로 중단됨. "오류 발생 시에도 계속 실행" 옵션을 켜면 나머지 단계를 계속할 수 있습니다.')
                        break

        progress.progress(1.0, text='완료')
        loader.load.clear()

        # 결과 요약
        n_ok = sum(1 for _, ok, _ in results if ok)
        n_fail = sum(1 for _, ok, _ in results if not ok)
        if n_fail == 0:
            st.success(f'🎉 전체 {n_ok}/{len(steps)} 단계 성공!')
        else:
            st.warning(f'⚠️ {n_ok}개 성공, {n_fail}개 실패 — 실패 항목의 에러 상세를 확인하세요.')
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

