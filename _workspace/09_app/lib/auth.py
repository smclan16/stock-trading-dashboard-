"""인증 — Streamlit 네이티브 Google 로그인 + 사용자 식별.

st.login("google") 이 OAuth 왕복을 서버사이드에서 처리(쿠키 기반).
사용자 식별자(user_id)는 Google 계정의 안정적 subject(sub)를 uuid5 로 결정적 변환.
"""
import uuid
import streamlit as st

# 고정 네임스페이스 — google sub → user_id(uuid) 결정적 매핑용. 변경 금지.
_NS = uuid.UUID("6f3b1c2a-9d4e-5f60-8a71-2b3c4d5e6f70")


def _logged_in() -> bool:
    user = getattr(st, "user", None)
    return bool(user and getattr(user, "is_logged_in", False))


def require_login():
    """미로그인 시 로그인 화면을 띄우고 페이지 실행을 중단."""
    if _logged_in():
        return
    st.title("🔒 로그인")
    st.write("투자 대시보드를 사용하려면 Google 계정으로 로그인하세요.")
    st.button("Google로 로그인", type="primary", on_click=st.login, args=("google",))
    st.stop()


def current_user_id() -> str:
    """현재 로그인 사용자의 안정적 user_id(uuid 문자열)."""
    sub = st.user.get("sub") or st.user.get("email")
    if not sub:
        st.error("사용자 식별 정보를 가져오지 못했습니다. 다시 로그인하세요.")
        st.stop()
    return str(uuid.uuid5(_NS, str(sub)))


def current_user_email() -> str:
    return st.user.get("email", "") if _logged_in() else ""


def logout_button():
    """사이드바에 사용자 정보 + 로그아웃 버튼."""
    with st.sidebar:
        st.caption(f"👤 {current_user_email()}")
        st.button("로그아웃", on_click=st.logout)
        with st.expander("내 사용자 ID (데이터 이관용)"):
            st.code(current_user_id(), language=None)
