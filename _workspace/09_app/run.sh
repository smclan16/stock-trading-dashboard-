#!/bin/bash
# Streamlit 대시보드 실행 스크립트
cd "$(dirname "$0")"
echo "🚀 Streamlit 대시보드 실행…"
echo "   브라우저: http://localhost:8501"
echo ""
python3 -m streamlit run app.py --server.port 8501 --server.headless false
