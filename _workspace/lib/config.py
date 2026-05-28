#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""환경변수/.env 로더 (python-dotenv 의존성 없이 동작).

우선순위: 실제 환경변수(os.environ) > _workspace/.env 파일.
키는 코드/산출물에 하드코딩하지 않고 .env 또는 셸 환경변수로만 주입한다.
"""
import os

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _parse_env_file(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get(key, default=None):
    """우선순위: 환경변수 > Streamlit secrets > .env 파일."""
    if os.environ.get(key):
        return os.environ[key]
    # Streamlit Cloud secrets (secrets.toml의 [api_keys] 섹션)
    try:
        import streamlit as st
        if hasattr(st, 'secrets'):
            if 'api_keys' in st.secrets and key in st.secrets['api_keys']:
                return st.secrets['api_keys'][key]
            if key in st.secrets:
                return st.secrets[key]
    except Exception:
        pass
    return _parse_env_file(_ENV_PATH).get(key, default)


def require(key):
    v = get(key)
    if not v:
        raise RuntimeError(
            f"키 '{key}' 가 설정되지 않았습니다. _workspace/.env 에 '{key}=...' 를 작성하거나 "
            f"환경변수로 export 하세요."
        )
    return v


def status():
    """키 설정 여부를 노출 없이(마스킹) 보고."""
    def mask(v):
        if not v:
            return "(미설정)"
        return f"설정됨 (len={len(v)}, …{v[-4:]})"
    return {
        "FNSPACE_API_KEY": mask(get("FNSPACE_API_KEY")),
        "FINNHUB_API_KEY": mask(get("FINNHUB_API_KEY")),
        "KRX_API_KEY": mask(get("KRX_API_KEY")),
        "DART_API_KEY": mask(get("DART_API_KEY")),
    }


if __name__ == "__main__":
    for k, v in status().items():
        print(f"{k}: {v}")
