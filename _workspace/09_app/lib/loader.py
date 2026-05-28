"""8단계 파이프라인 산출물 JSON 로더 (캐시)"""
import json
import os
from typing import Any
import streamlit as st

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATHS = {
    'profile': os.path.join(WS, '01_profile', 'constraints.json'),
    'allocation': os.path.join(WS, '02_macro', 'allocation.json'),
    'universe': os.path.join(WS, '03_universe', 'universe.json'),
    'ideas': os.path.join(WS, '04_ideas', 'ideas.json'),
    'matching': os.path.join(WS, '05_matching', 'matching_matrix.json'),
    'research_scores': os.path.join(WS, '06_research', 'research_scores.json'),
    'research_ranking': os.path.join(WS, '06_research', 'ranking.json'),
    'portfolio': os.path.join(WS, '07_portfolio', 'portfolio.json'),
    'technical_scores': os.path.join(WS, '08_signals', 'technical_scores.json'),
    'signals': os.path.join(WS, '08_signals', 'signals.json'),
    'theme_portfolio': os.path.join(WS, '08_signals', 'theme_portfolio.json'),
    'entry_plan': os.path.join(WS, '08_signals', 'entry_order_plan.json'),
    'daily_signals': os.path.join(WS, '08_signals', 'daily_signals.json'),
    'validation': os.path.join(WS, 'validation', 'logic_validation_report.json'),
    'backtest': os.path.join(WS, 'validation', 'backtest_full_results.json'),
    'backtest_v6': os.path.join(WS, 'validation', 'backtest_v6_24m_results.json'),
    'backtest_v7': os.path.join(WS, 'validation', 'backtest_v7_24m_results.json'),
    'backtest_v8': os.path.join(WS, 'validation', 'backtest_v8_24m_results.json'),
}


@st.cache_data(ttl=10)
def load(name: str) -> Any:
    path = PATHS.get(name)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f'로드 실패 ({name}): {e}')
        return None


def file_info(name: str):
    path = PATHS.get(name)
    if not path or not os.path.exists(path):
        return None
    import datetime
    stat = os.stat(path)
    return {
        'path': path,
        'size_kb': round(stat.st_size / 1024, 1),
        'mtime': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
    }
