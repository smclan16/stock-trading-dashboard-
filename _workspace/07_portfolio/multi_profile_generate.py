#!/usr/bin/env python3
"""5개 투자성향 유형별 portfolio + theme_portfolio 일괄 생성

각 유형:
  안정형 / 안정추구형 / 위험중립형 / 적극투자형 / 공격투자형

산출:
  _workspace/01_profile/constraints_{유형}.json
  _workspace/07_portfolio/portfolio_{유형}.json + model_portfolio_{유형}.md
  _workspace/08_signals/theme_portfolio_{유형}.json + theme_portfolio_{유형}.md
"""
import os, sys, json, subprocess, datetime

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 5단계 유형 정의
PROFILES = [
    {'name': '안정형',     'score': 30, 'e_min': 20,  'e_max': 100, 'single': 10, 'sector': 25, 'vol': 15},
    {'name': '안정추구형', 'score': 50, 'e_min': 40,  'e_max': 100, 'single': 12, 'sector': 28, 'vol': 20},
    {'name': '위험중립형', 'score': 65, 'e_min': 60,  'e_max': 100, 'single': 13, 'sector': 30, 'vol': 25},
    {'name': '적극투자형', 'score': 80, 'e_min': 100, 'e_max': 150, 'single': 15, 'sector': 30, 'vol': 30},
    {'name': '공격투자형', 'score': 95, 'e_min': 120, 'e_max': 200, 'single': 20, 'sector': 35, 'vol': 40},
]


def write_constraints(profile):
    path = os.path.join(WS, '01_profile', f"constraints_{profile['name']}.json")
    data = {
        'investor_type': profile['name'],
        'investor_score': profile['score'],
        'equity_pct_min': profile['e_min'],
        'equity_pct_max': profile['e_max'],
        'max_single_stock_pct': profile['single'],
        'max_sector_pct': profile['sector'],
        'max_annual_volatility': profile['vol'],
        'excluded_tickers': [],
        'excluded_sectors': [],
        'esg_filter': False,
        'profile_name': profile['name'],
        'updated_at': datetime.datetime.now().isoformat(),
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def main():
    print(f'=== 5개 유형별 산출 시작 ===\n')

    for p in PROFILES:
        name = p['name']
        suffix = f'_{name}'
        print(f'─── {name} (점수 {p["score"]}, equity {p["e_min"]}~{p["e_max"]}%) ───')

        # 1. constraints 파일 생성
        cpath = write_constraints(p)
        print(f'  ✅ constraints_{name}.json 생성')

        # 2. 7단계 portfolio 생성
        cmd = ['python3', os.path.join(WS, '07_portfolio', 'compose_portfolio.py'),
               '--constraints-file', cpath,
               '--output-suffix', suffix,
               '--weighting', 'hybrid', '--mcap-blend', '0.5']
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WS, timeout=300)
        if r.returncode != 0:
            print(f'  ❌ portfolio 실패: {r.stderr[-300:]}')
            continue
        # 산출 종목 수 표시
        pf_path = os.path.join(WS, '07_portfolio', f'portfolio{suffix}.json')
        if os.path.exists(pf_path):
            pf = json.load(open(pf_path, encoding='utf-8'))
            print(f'  ✅ portfolio{suffix}.json — {pf["n_holdings"]}종, 단일 최대 {pf["constraint_checks"]["max_single_stock"]["actual_max"]:.2f}%')

        # 3. 8단계 theme_portfolio 생성
        cmd2 = ['python3', os.path.join(WS, '08_signals', 'build_theme_portfolio.py'),
                '--pf-file', pf_path,
                '--output-suffix', suffix,
                '--n-themes', '8']
        r2 = subprocess.run(cmd2, capture_output=True, text=True, cwd=WS, timeout=300)
        if r2.returncode != 0:
            print(f'  ❌ theme_portfolio 실패: {r2.stderr[-300:]}')
            continue
        tp_path = os.path.join(WS, '08_signals', f'theme_portfolio{suffix}.json')
        if os.path.exists(tp_path):
            tp = json.load(open(tp_path, encoding='utf-8'))
            print(f'  ✅ theme_portfolio{suffix}.json — {tp["n_holdings"]}종 (테마 {tp["n_themes_selected"]} + default {tp["n_default_picks"]})')
        print()

    print('=== 완료 ===')
    print('산출 파일:')
    for p in PROFILES:
        n = p['name']
        print(f"  - 01_profile/constraints_{n}.json")
        print(f"  - 07_portfolio/portfolio_{n}.json")
        print(f"  - 08_signals/theme_portfolio_{n}.json")


if __name__ == '__main__':
    main()
