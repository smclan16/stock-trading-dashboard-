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

# v10 유형별 차별화 매트릭스:
# - min_mcap_eok: 종목 시총 필터 (안정형 1조+ = 대형 가치주, 공격형 무제한)
# - max_tech_vol: 변동성 임계 (안정형 35% = 저변동, 공격형 무제한)
# - n_themes: 핵심 테마 수
# - default_pct: 매크로 무관 종목 비중 (안정형 30% = 안전, 공격형 5%)
# - mcap_blend: 7단계 시총 가중 (안정형 0.8 = 대형 우선)
# - axis_weights: 6축+기술점수 가중 (안정형 = 펀더+리스크역수, 공격형 = 모멘텀+기술)
PROFILES = [
    # 안정형: 배당 boost 0.20 (배당 5%면 ×2.0) + 과열 penalty 0.6 (52w 100% ×0.4)
    {'name': '안정형', 'score': 30, 'e_min': 20, 'e_max': 100, 'single': 10, 'sector': 25, 'vol': 15,
     'min_mcap_eok': 5000, 'n_themes': 6, 'default_pct': 25, 'max_tech_vol': 70, 'mcap_blend': 0.8,
     'axis_weights': 'fundamental=0.45,risk_inv=0.25,theme=0.10,catalyst=0.10,momentum=0.05,tech=0.05',
     'dividend_boost': 0.20, 'overheating_penalty': 0.6, 'momentum_boost': 0.0},
    {'name': '안정추구형', 'score': 50, 'e_min': 40, 'e_max': 100, 'single': 12, 'sector': 28, 'vol': 20,
     'min_mcap_eok': 3000, 'n_themes': 7, 'default_pct': 20, 'max_tech_vol': 85, 'mcap_blend': 0.7,
     'axis_weights': 'fundamental=0.35,risk_inv=0.15,theme=0.15,catalyst=0.10,momentum=0.10,tech=0.15',
     'dividend_boost': 0.12, 'overheating_penalty': 0.3, 'momentum_boost': 0.0},
    {'name': '위험중립형', 'score': 65, 'e_min': 60, 'e_max': 100, 'single': 13, 'sector': 30, 'vol': 25,
     'min_mcap_eok': 1500, 'n_themes': 8, 'default_pct': 15, 'max_tech_vol': 100, 'mcap_blend': 0.6,
     'axis_weights': 'fundamental=0.25,risk_inv=0.10,theme=0.15,catalyst=0.10,momentum=0.20,tech=0.20',
     'dividend_boost': 0.05, 'overheating_penalty': 0.0, 'momentum_boost': 0.0},
    {'name': '적극투자형', 'score': 80, 'e_min': 100, 'e_max': 150, 'single': 15, 'sector': 30, 'vol': 30,
     'min_mcap_eok': 500, 'n_themes': 8, 'default_pct': 10, 'max_tech_vol': 0, 'mcap_blend': 0.5,
     'axis_weights': 'fundamental=0.15,risk_inv=0.05,theme=0.20,catalyst=0.10,momentum=0.25,tech=0.25',
     'dividend_boost': 0.0, 'overheating_penalty': 0.0, 'momentum_boost': 0.15},
    {'name': '공격투자형', 'score': 95, 'e_min': 120, 'e_max': 200, 'single': 20, 'sector': 35, 'vol': 40,
     'min_mcap_eok': 0, 'n_themes': 10, 'default_pct': 5, 'max_tech_vol': 0, 'mcap_blend': 0.3,
     'axis_weights': 'fundamental=0.10,risk_inv=0.00,theme=0.20,catalyst=0.10,momentum=0.30,tech=0.30',
     'dividend_boost': 0.0, 'overheating_penalty': 0.0, 'momentum_boost': 0.30},
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

        # 2. 7단계 portfolio 생성 (유형별 mcap_blend 적용)
        cmd = ['python3', os.path.join(WS, '07_portfolio', 'compose_portfolio.py'),
               '--constraints-file', cpath,
               '--output-suffix', suffix,
               '--weighting', 'hybrid', '--mcap-blend', str(p['mcap_blend'])]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WS, timeout=300)
        if r.returncode != 0:
            print(f'  ❌ portfolio 실패: {r.stderr[-300:]}')
            continue
        pf_path = os.path.join(WS, '07_portfolio', f'portfolio{suffix}.json')
        if os.path.exists(pf_path):
            pf = json.load(open(pf_path, encoding='utf-8'))
            print(f'  ✅ portfolio{suffix}.json — {pf["n_holdings"]}종, 단일 최대 {pf["constraint_checks"]["max_single_stock"]["actual_max"]:.2f}%')

        # 3. 8단계 theme_portfolio 생성 (유형별 시총·변동성·테마수·default 비중 적용)
        cmd2 = ['python3', os.path.join(WS, '08_signals', 'build_theme_portfolio.py'),
                '--pf-file', pf_path,
                '--output-suffix', suffix,
                '--n-themes', str(p['n_themes']),
                '--min-mcap-eok', str(p['min_mcap_eok']),
                '--default-pct', str(p['default_pct']),
                '--axis-weights', p['axis_weights'],
                '--dividend-boost', str(p.get('dividend_boost', 0)),
                '--overheating-penalty', str(p.get('overheating_penalty', 0)),
                '--momentum-boost', str(p.get('momentum_boost', 0)),
                '--rebalance-on-shortfall']
        if p['max_tech_vol'] > 0:
            cmd2.extend(['--max-tech-vol', str(p['max_tech_vol'])])
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
