"""블룸버그 단말기 스타일 스킨 (순수 CSS, 디자인 전용).

⚠️ 이 모듈은 **시각 스타일만** 담당한다. 데이터·계산·컴포넌트·라우팅은 절대 건드리지 않는다.
디자인 스코프: design/SCOPE_MATRIX.md 참조 (앰버 #ffb000 / 다크 고정 / 청록↑·적색↓).

되돌리기:
  - 런타임: 사이드바 토글 OFF → 즉시 원본 라이트 테마 복귀 (CSS 미주입)
  - 영구: app.py 에서 `theme.apply()` 호출 줄만 제거하면 끝 (다른 파일 변경 없음)
"""
from __future__ import annotations
import streamlit as st

# ── 팔레트 (design/SCOPE_MATRIX.md 확정값) ─────────────────────
BG        = "#0d1117"   # 페이지 배경
BG_PANEL  = "#0a0a0a"   # 사이드바 등 패널
BG_CARD   = "#11161d"   # 카드/표 헤더
GRID      = "#2a2a2a"   # 구분선·테두리
TEXT      = "#e0e0e0"   # 본문
AMBER     = "#ffb000"   # 강조 (블룸버그 시그니처)
UP        = "#00ff7f"   # 상승/긍정
DOWN      = "#ff4040"   # 하락/부정
MONO      = "'JetBrains Mono','Roboto Mono','IBM Plex Mono','SF Mono',Menlo,Consolas,monospace"

_STATE_KEY = "bloomberg_theme_on"

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');

/* ── 전역 배경/텍스트 ───────────────────────────── */
.stApp, [data-testid="stAppViewContainer"] {{ background: {BG}; color: {TEXT}; }}
[data-testid="stHeader"] {{ background: {BG}; }}
.block-container {{ padding-top: 2.2rem; padding-bottom: 1rem; }}

/* 정보 밀도↑ : 컨테이너/요소 간격 축소 */
[data-testid="stVerticalBlock"] {{ gap: 0.55rem; }}

/* 헤더는 산세리프 유지 + 앰버 강조 */
h1, h2, h3, h4 {{ color: {TEXT}; letter-spacing: .2px; }}
h1 {{ border-bottom: 1px solid {GRID}; padding-bottom: .35rem; }}
.stCaption, [data-testid="stCaptionContainer"] {{ color: #8a929c; }}

/* ── 사이드바 ──────────────────────────────────── */
[data-testid="stSidebar"] {{ background: {BG_PANEL}; border-right: 1px solid {GRID}; }}
[data-testid="stSidebar"] * {{ color: {TEXT}; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    border-left: 3px solid {AMBER}; background: rgba(255,176,0,.06);
}}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: {AMBER} !important; font-weight: 600; }}

/* ── st.metric : 격자 박스 + 모노 숫자 ───────────── */
[data-testid="stMetric"] {{
    background: {BG_CARD}; border: 1px solid {GRID}; border-radius: 4px;
    padding: .55rem .7rem;
}}
[data-testid="stMetricLabel"] {{ color: #8a929c; }}
[data-testid="stMetricValue"] {{ font-family: {MONO}; color: {AMBER}; font-weight: 600; }}
[data-testid="stMetricDelta"] {{ font-family: {MONO}; }}

/* ── 표 (dataframe) : 다크 + 명확한 격자 + 모노 ───── */
[data-testid="stDataFrame"], [data-testid="stTable"] {{ font-family: {MONO}; }}
[data-testid="stDataFrame"] {{ border: 1px solid {GRID}; }}

/* ── 탭 ───────────────────────────────────────── */
[data-baseweb="tab-list"] {{ border-bottom: 1px solid {GRID}; gap: 2px; }}
[data-baseweb="tab"] {{ color: #8a929c; }}
[aria-selected="true"][data-baseweb="tab"] {{ color: {AMBER}; border-bottom-color: {AMBER}; }}

/* ── 구분선 : 얇은 1px ───────────────────────────── */
hr {{ border-color: {GRID}; margin: .6rem 0; }}

/* ── 알림 박스 : 좌측 시멘틱 라인 + 다크 ─────────── */
[data-testid="stAlert"] {{ background: {BG_CARD}; border-radius: 3px; }}
[data-testid="stNotification"] {{ background: {BG_CARD}; }}
div[data-baseweb="notification"] {{ background: {BG_CARD} !important; }}

/* ── 입력/버튼/expander ─────────────────────────── */
.stButton > button {{
    background: {BG_CARD}; color: {AMBER}; border: 1px solid {AMBER};
    border-radius: 3px; font-weight: 600;
}}
.stButton > button:hover {{ background: {AMBER}; color: {BG}; }}
[data-testid="stExpander"] {{ border: 1px solid {GRID}; border-radius: 4px; background: {BG_CARD}; }}
[data-testid="stExpander"] summary {{ color: {TEXT}; }}
.stTextInput input, .stNumberInput input {{
    background: {BG_CARD}; color: {TEXT}; border-color: {GRID};
}}

/* ── 셀렉트박스 : 클릭 가능함이 분명하도록 어포던스 강화 ───── */
.stSelectbox label, .stMultiSelect label {{ color: {AMBER}; font-weight: 600; }}
.stSelectbox div[data-baseweb="select"] > div,
.stMultiSelect div[data-baseweb="select"] > div {{
    background: {BG_CARD}; color: {TEXT};
    border: 1px solid #3d4754; border-radius: 4px;
    box-shadow: inset 0 0 0 1px rgba(255,176,0,.08);
    cursor: pointer; transition: border-color .12s ease;
}}
.stSelectbox div[data-baseweb="select"] > div:hover,
.stMultiSelect div[data-baseweb="select"] > div:hover {{
    border-color: {AMBER}; box-shadow: 0 0 0 1px rgba(255,176,0,.35);
}}
.stSelectbox div[data-baseweb="select"] > div:focus-within,
.stMultiSelect div[data-baseweb="select"] > div:focus-within {{
    border-color: {AMBER};
}}
/* 우측 드롭다운 화살표(캐럿) 앰버로 — '열린다'는 신호 */
.stSelectbox [data-baseweb="select"] svg,
.stMultiSelect [data-baseweb="select"] svg {{ fill: {AMBER}; color: {AMBER}; }}
/* 펼쳐진 옵션 목록(팝오버) 다크 + 선택/호버 강조 */
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="menu"] {{ background: {BG_CARD}; border: 1px solid {GRID}; }}
[data-baseweb="popover"] li[role="option"] {{ color: {TEXT}; }}
[data-baseweb="popover"] li[role="option"]:hover,
[data-baseweb="popover"] li[aria-selected="true"] {{
    background: rgba(255,176,0,.14); color: {AMBER};
}}

/* 코드 블록 */
.stCodeBlock, code {{ background: {BG_PANEL} !important; }}

/* 시멘틱 헬퍼 (필요 시 마크다운에서 사용) */
.bb-up {{ color: {UP}; font-family: {MONO}; }}
.bb-down {{ color: {DOWN}; font-family: {MONO}; }}
.bb-amber {{ color: {AMBER}; }}
</style>
"""


def toggle(default: bool = True) -> bool:
    """메인 대시보드 사이드바에 블룸버그 테마 ON/OFF 토글을 그린다.

    상태(_STATE_KEY)는 세션 전역으로 유지되므로, 여기서 한 번 설정하면
    모든 페이지가 apply()를 통해 동일하게 적용한다. 개별 페이지는
    toggle()을 호출하지 않고 apply()만 호출한다.
    """
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = default
    with st.sidebar:
        st.toggle("🖥 블룸버그 스타일", key=_STATE_KEY,
                  help="끄면 즉시 기본(라이트) 테마로 복귀합니다. "
                       "이 설정은 모든 페이지에 일괄 적용됩니다.")
    return st.session_state[_STATE_KEY]


def apply(enabled: bool | None = None) -> None:
    """테마 CSS를 주입한다. enabled=None 이면 세션 상태(토글)를 따른다."""
    if enabled is None:
        enabled = st.session_state.get(_STATE_KEY, True)
    if enabled:
        st.markdown(_CSS, unsafe_allow_html=True)
