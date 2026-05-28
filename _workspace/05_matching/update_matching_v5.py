#!/usr/bin/env python3
"""v5 universe 신규 대형주 매칭 직접 추가 + 탈락 종목 제거

신규 23종 → LLM 없이 명확한 케이스 직접 매핑.
"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNI = os.path.join(ROOT, '03_universe', 'universe.json')
MM = os.path.join(ROOT, '05_matching', 'matching_matrix.json')

# 신규 23종 매칭 정의 (idea_id → intensity)
NEW_MAPPINGS = {
    '005930': {'name': '삼성전자', 'ideas': [
        {'id': 1, 'intensity': 'direct', 'evidence': 'HBM·DDR5·고대역폭 메모리, AI 데이터센터 핵심'},
        {'id': 11, 'intensity': 'indirect', 'evidence': '삼성그룹 지주 구조·합병 가능성 (수동적 거버넌스)'},
    ]},
    '005380': {'name': '현대차', 'ideas': [
        {'id': 6, 'intensity': 'direct', 'evidence': '자율주행·로보택시·소프트웨어 정의 차량 (모셔날)'},
        {'id': 11, 'intensity': 'indirect', 'evidence': '현대차그룹 지배구조 개편 진행'},
    ]},
    '005490': {'name': 'POSCO홀딩스', 'ideas': [
        {'id': 13, 'intensity': 'indirect', 'evidence': 'K-조선·방산용 후판·강재 공급'},
        {'id': 11, 'intensity': 'direct', 'evidence': 'POSCO그룹 지주, 자사주 정책 가능성'},
    ]},
    '009150': {'name': '삼성전기', 'ideas': [
        {'id': 1, 'intensity': 'indirect', 'evidence': 'MLCC·AP 패키지 기판 (AI 서버용)'},
        {'id': 2, 'intensity': 'perception', 'evidence': 'AI 에이전트 디바이스 부품'},
    ]},
    '009540': {'name': 'HD한국조선해양', 'ideas': [
        {'id': 13, 'intensity': 'direct', 'evidence': 'K-조선 메인 (LNG·메가캐리어)'},
        {'id': 11, 'intensity': 'indirect', 'evidence': 'HD현대그룹 지주'},
    ]},
    '010120': {'name': 'LS ELECTRIC', 'ideas': [
        {'id': 4, 'intensity': 'direct', 'evidence': '전력기기·변압기·스위치기어 (AI 데이터센터 핵심)'},
        {'id': 11, 'intensity': 'indirect', 'evidence': 'LS그룹 자회사'},
    ]},
    '012450': {'name': '한화에어로스페이스', 'ideas': [
        {'id': 13, 'intensity': 'direct', 'evidence': 'K-방산 메인 (자주포·LIG 합병)'},
        {'id': 10, 'intensity': 'direct', 'evidence': '발사체·위성 (위성통신 인프라)'},
    ]},
    '016360': {'name': '삼성증권', 'ideas': [
        {'id': 11, 'intensity': 'direct', 'evidence': '삼성그룹 금융, 밸류업 수혜'},
    ]},
    '028050': {'name': '삼성E&A', 'ideas': [
        {'id': 3, 'intensity': 'direct', 'evidence': 'SMR·소형원전 EPC 사업'},
        {'id': 4, 'intensity': 'indirect', 'evidence': '데이터센터 EPC 인접'},
    ]},
    '028260': {'name': '삼성물산', 'ideas': [
        {'id': 11, 'intensity': 'direct', 'evidence': '삼성그룹 지주·합병 핵심'},
        {'id': 3, 'intensity': 'perception', 'evidence': '건설 부문 SMR·발전 EPC 가능성'},
    ]},
    '032830': {'name': '삼성생명', 'ideas': [
        {'id': 11, 'intensity': 'direct', 'evidence': '밸류업 핵심 (PBR 낮은 보험 대표주, 배당수익률 3%+)'},
        {'id': 12, 'intensity': 'perception', 'evidence': '내수 금융·생명보험 (안정 배당주)'},
    ]},
    '034020': {'name': '두산에너빌리티', 'ideas': [
        {'id': 3, 'intensity': 'direct', 'evidence': 'SMR 주기기 (NuScale·X-energy 협력)'},
        {'id': 4, 'intensity': 'direct', 'evidence': '대형 가스터빈·발전 핵심 설비'},
    ]},
    '035420': {'name': 'NAVER', 'ideas': [
        {'id': 2, 'intensity': 'direct', 'evidence': 'HyperClova X·AI 에이전트 (한국 대표 LLM)'},
        {'id': 1, 'intensity': 'indirect', 'evidence': 'AI 인프라 자체 운영'},
    ]},
    '042660': {'name': '한화오션', 'ideas': [
        {'id': 13, 'intensity': 'direct', 'evidence': 'K-조선 (한화 인수 후 방산함정 강화)'},
    ]},
    '047810': {'name': '한국항공우주', 'ideas': [
        {'id': 13, 'intensity': 'direct', 'evidence': 'K-방산 (FA-50 수출 폭증)'},
        {'id': 10, 'intensity': 'direct', 'evidence': '인공위성·발사체 사업'},
    ]},
    '055550': {'name': '신한지주', 'ideas': [
        {'id': 11, 'intensity': 'direct', 'evidence': '밸류업 핵심 (자사주 소각·배당수익률 4%+)'},
        {'id': 12, 'intensity': 'perception', 'evidence': '내수 금융 (안정 배당주)'},
    ]},
    '066570': {'name': 'LG전자', 'ideas': [
        {'id': 1, 'intensity': 'indirect', 'evidence': 'AI 가전·서버용 모니터'},
        {'id': 11, 'intensity': 'indirect', 'evidence': 'LG그룹 자회사 (LG 지주)'},
    ]},
    '068270': {'name': '셀트리온', 'ideas': [
        {'id': 7, 'intensity': 'perception', 'evidence': '바이오시밀러·면역 (비만약 보조)'},
        {'id': 8, 'intensity': 'perception', 'evidence': '항체치료 (CRISPR 인접)'},
    ]},
    '105560': {'name': 'KB금융', 'ideas': [
        {'id': 11, 'intensity': 'direct', 'evidence': '밸류업 대표주 (PBR 0.5 미만, 자사주 매입, 배당수익률 4%+)'},
        {'id': 12, 'intensity': 'perception', 'evidence': '내수 금융 (안정 배당주 대표)'},
    ]},
    '207940': {'name': '삼성바이오로직스', 'ideas': [
        {'id': 7, 'intensity': 'indirect', 'evidence': 'GLP-1 CDMO·삼성그룹 바이오 핵심'},
        {'id': 8, 'intensity': 'indirect', 'evidence': '유전자 치료 CDMO'},
    ]},
    '267260': {'name': 'HD현대일렉트릭', 'ideas': [
        {'id': 4, 'intensity': 'direct', 'evidence': '변압기·전력기기 (AI 데이터센터 미국 수출 폭증)'},
        {'id': 11, 'intensity': 'indirect', 'evidence': 'HD현대 자회사'},
    ]},
    '298040': {'name': '효성중공업', 'ideas': [
        {'id': 4, 'intensity': 'direct', 'evidence': '초고압 변압기 (미국 데이터센터 수출 강세)'},
        {'id': 11, 'intensity': 'indirect', 'evidence': '효성그룹 분할 후 핵심 사업회사'},
    ]},
    '373220': {'name': 'LG에너지솔루션', 'ideas': [
        {'id': 6, 'intensity': 'indirect', 'evidence': '전기차·자율주행용 배터리 (GM·테슬라·현대 공급)'},
        {'id': 11, 'intensity': 'indirect', 'evidence': 'LG그룹 자회사'},
    ]},
}


def main():
    print('[1/3] universe.json 승격…')
    full = json.load(open(os.path.join(ROOT, '03_universe', 'universe_full.json'), encoding='utf-8'))
    with open(UNI, 'w', encoding='utf-8') as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    new_universe = full['universe']
    new_tickers = set(x['ticker'] for x in new_universe)
    print(f'  universe.json 갱신 ({len(new_tickers)}종)')

    print('[2/3] matching_matrix.json 업데이트…')
    mm = json.load(open(MM, encoding='utf-8'))
    matrix = mm['matrix']

    # 1) 탈락 종목 제거
    removed_from_matching = set()
    for entry in matrix:
        before = len(entry['matched_tickers'])
        entry['matched_tickers'] = [m for m in entry['matched_tickers'] if m['ticker'] in new_tickers]
        removed_count = before - len(entry['matched_tickers'])
        if removed_count:
            print(f"  #{entry['idea_id']} {entry['idea_theme'][:30]}: -{removed_count}종")
            for m in [m for m in entry['matched_tickers'] if m['ticker'] not in new_tickers]:
                removed_from_matching.add(m['ticker'])

    # 2) 신규 종목 매핑 추가
    added_count = 0
    for tkr, info in NEW_MAPPINGS.items():
        if tkr not in new_tickers:
            continue
        for theme_match in info['ideas']:
            iid = theme_match['id']
            entry = next((e for e in matrix if e['idea_id'] == iid), None)
            if not entry:
                continue
            # 이미 있는지 확인
            existing = next((m for m in entry['matched_tickers'] if m['ticker'] == tkr), None)
            if existing:
                continue
            entry['matched_tickers'].append({
                'ticker': tkr, 'name': info['name'],
                'intensity': theme_match['intensity'],
                'axes': {'사업의 내용': {'matched': True, 'evidence': theme_match['evidence']}},
            })
            added_count += 1
    print(f'  신규 매핑 추가: {added_count}건')

    # 3) summary 갱신
    summary = mm.get('summary', {})
    summary['version'] = 'v5'
    summary['match_count_by_idea'] = {}
    multi_matched = {}
    uncovered = set(new_tickers)
    for entry in matrix:
        c = len(entry['matched_tickers'])
        summary['match_count_by_idea'][f"#{entry['idea_id']} {entry['idea_theme'][:30]}"] = c
        for m in entry['matched_tickers']:
            t = m['ticker']
            uncovered.discard(t)
            multi_matched.setdefault(t, []).append(entry['idea_id'])
    summary['uncovered_count'] = len(uncovered)
    summary['multi_matched_count'] = sum(1 for v in multi_matched.values() if len(v) >= 2)
    summary['n_idea_matched'] = sum(1 for c in summary['match_count_by_idea'].values() if c > 0)

    mm['summary'] = summary
    mm['version'] = 'v5 (대형주 자동 포함 universe + 신규 23종 매칭 직접 추가)'
    mm['as_of'] = '2026-05-27'
    mm['multi_matched_tickers'] = {t: ideas for t, ideas in multi_matched.items() if len(ideas) >= 2}
    # uncovered 갱신
    if 'uncovered_tickers' in mm and isinstance(mm['uncovered_tickers'], dict):
        mm['uncovered_tickers']['list_flat'] = sorted(uncovered)

    with open(MM, 'w', encoding='utf-8') as f:
        json.dump(mm, f, ensure_ascii=False, indent=2)
    print(f'  저장: {MM}')
    print()
    print(f'[3/3] 요약')
    print(f'  v5 universe: {len(new_tickers)}종')
    print(f'  매칭 종목 (uncovered 제외): {len(new_tickers) - len(uncovered)}종')
    print(f'  uncovered: {len(uncovered)}종')
    print(f'  multi-matched: {summary["multi_matched_count"]}종')
    print()
    for k, v in summary['match_count_by_idea'].items():
        print(f'  {k}: {v}종')


if __name__ == '__main__':
    main()
