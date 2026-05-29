"""Supabase 클라이언트 팩토리 — 서버사이드 service_role 연결."""
import streamlit as st
from supabase import create_client, Client


@st.cache_resource
def get_client() -> Client:
    """service_role 키로 연결한 Supabase 클라이언트(앱 전역 1개).

    Streamlit 은 서버사이드 실행이므로 service_role 키가 브라우저에 노출되지 않는다.
    사용자 격리는 lib/db.py 가 모든 쿼리에 user_id 를 스코프하여 강제한다.
    """
    cfg = st.secrets["supabase"]
    return create_client(cfg["url"], cfg["service_key"])
