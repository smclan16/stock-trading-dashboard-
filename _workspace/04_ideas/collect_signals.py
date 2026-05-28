#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""투자 아이디어 발굴 — 약한 신호 자동 수집기 (11개 신호 통합, 한국 보강).

3단계 깔때기 중 [1단계: 약한 신호] 자동화:

  [연구·기술 — 글로벌]
    · arXiv 논문 수 증가율(%)            — 30d vs 180d
    · Google Patents 출원 수 증가율(%)   — 6M vs 24M (R&D 실투자)
  [개발자·얼리어답터 — 글로벌]
    · GitHub 신규 repo 증가율(%)         — 30d vs 180d
    · HackerNews 게시물 증가율(%)        — 30d vs 180d (VC·테크 트래픽)
  [대중]
    · Wikipedia EN Pageviews 증가율(%)   — 4w vs 12w (글로벌 대중)
    · Wikipedia KO Pageviews 증가율(%)   — 4w vs 12w (한국 대중)
    · Google Trends 증가율(%)            — 4w vs 12w (pytrends 설치 시)
  [미디어 — 한국]
    · 네이버 뉴스 mention 증가율(%)       — 30d vs 180d
  [사업화·정책 — 미국]
    · SEC EDGAR 10-K mention 증가율(%)   — 3M vs 12M
    · federalregister.gov mention 증가율(%) — 90d vs 365d
  [정책 — 한국]
    · 국회 의안 발의 증가율(%)            — 6M vs 24M

입력: keywords.json (수동 큐레이션, 또는 DEFAULT_KEYWORDS)
출력: signals_raw.json (LLM이 5점 평가·4분류 시 참조)

신호↔5점 평가 매핑:
  · Earliness        ← GitHub·HN·arXiv (대중 인지 전, 얼리어답터만 인지)
  · Durability       ← Patents·arXiv (R&D·연구 누적 = 장기 산업화 가능성)
  · Capital Inflow   ← HN(VC 트래픽) + SEC EDGAR (Capex 공시) + LLM 리서치(ETF Flow)
  · Verifiability    ← SEC EDGAR + Wiki KO + 네이버 뉴스 (한국 시장 사업화 인식)
  · Policy Momentum  ← federalregister.gov(미국) + **국회 의안(한국)** + LLM 리서치(EU)
"""
import sys, os, json, datetime, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))

# 기본 키워드 큐레이션 — keywords.json 없을 때 사용
# 키워드 차원:
#   arxiv/github/hn/sec/patent/fedreg = 영문 글로벌
#   wiki = 영문 위키 슬러그
#   wiki_ko = 한국어 위키 슬러그
#   naver = 네이버 뉴스 검색 (한국어, 핵심 키워드 단일 권장)
#   assembly = 국회 의안명 검색 (한국어)
DEFAULT_KEYWORDS = [
    {"theme_hint": "AI 인프라·LLM",
     "arxiv": ["large language model"], "wiki": ["Large_language_model"],
     "wiki_ko": ["대형_언어_모델"], "naver": ["HBM 수출"], "assembly": ["인공지능"],
     "trends": ["LLM"], "github": ["LLM"], "hn": ["LLM"],
     "sec": ["large language model"], "patent": ["large language model"],
     "fedreg": ["artificial intelligence"]},
    {"theme_hint": "AI 에이전트",
     "arxiv": ["AI agent"], "wiki": ["Software_agent"],
     "wiki_ko": ["인공지능"], "naver": ["agentic AI"], "assembly": ["AI 에이전트"],
     "trends": ["AI agent"], "github": ["AI agent"], "hn": ["AI agent"],
     "sec": ["AI agent"], "patent": ["autonomous AI agent"], "fedreg": ["AI agent"]},
    {"theme_hint": "SMR·소형원전",
     "arxiv": ["small modular reactor"], "wiki": ["Small_modular_reactor"],
     "wiki_ko": ["소형_모듈_원자로"], "naver": ["NuScale"], "assembly": ["소형원자로"],
     "trends": ["SMR reactor"], "github": [], "hn": ["SMR nuclear"],
     "sec": ["small modular reactor"], "patent": ["small modular reactor"],
     "fedreg": ["small modular reactor"]},
    {"theme_hint": "데이터센터 전력",
     "arxiv": ["data center power"], "wiki": ["Data_center"],
     "wiki_ko": ["데이터_센터"], "naver": ["초고압 변압기"], "assembly": ["데이터센터"],
     "trends": ["data center power"], "github": [], "hn": ["data center power"],
     "sec": ["data center power"], "patent": ["data center cooling"],
     "fedreg": ["data center energy"]},
    {"theme_hint": "휴머노이드 로봇",
     "arxiv": ["humanoid robot"], "wiki": ["Humanoid_robot"],
     "wiki_ko": ["휴머노이드_로봇"], "naver": ["옵티머스 로봇"], "assembly": ["지능형 로봇"],
     "trends": ["humanoid robot"], "github": ["humanoid robot"], "hn": ["humanoid robot"],
     "sec": ["humanoid robot"], "patent": ["humanoid robot"], "fedreg": ["humanoid robotics"]},
    {"theme_hint": "자율주행",
     "arxiv": ["autonomous driving"], "wiki": ["Self-driving_car"],
     "wiki_ko": ["자율_주행_자동차"], "naver": ["FSD 베타"], "assembly": ["자율주행"],
     "trends": ["autonomous vehicle"], "github": ["autonomous driving"],
     "hn": ["autonomous vehicle"], "sec": ["autonomous vehicle"],
     "patent": ["autonomous vehicle"], "fedreg": ["autonomous vehicle"]},
    {"theme_hint": "GLP-1 비만약",
     "arxiv": ["GLP-1 obesity"], "wiki": ["GLP-1_receptor_agonist"],
     "wiki_ko": ["GLP-1"], "naver": ["마운자로"], "assembly": ["비만 치료"],
     "trends": ["GLP-1"], "github": [], "hn": ["GLP-1"],
     "sec": ["GLP-1"], "patent": ["GLP-1 receptor agonist"], "fedreg": ["GLP-1"]},
    {"theme_hint": "유전자 치료(CRISPR)",
     "arxiv": ["CRISPR therapy"], "wiki": ["CRISPR_gene_editing"],
     "wiki_ko": ["크리스퍼"], "naver": ["카스게비"], "assembly": ["유전자치료"],
     "trends": ["gene therapy"], "github": [], "hn": ["CRISPR"],
     "sec": ["gene therapy"], "patent": ["CRISPR"], "fedreg": ["gene editing"]},
    {"theme_hint": "양자 컴퓨팅",
     "arxiv": ["quantum computing"], "wiki": ["Quantum_computing"],
     "wiki_ko": ["양자_컴퓨터"], "naver": ["양자컴퓨터 R&D"], "assembly": ["양자"],
     "trends": ["quantum computing"], "github": ["quantum computing"],
     "hn": ["quantum computing"], "sec": ["quantum computing"],
     "patent": ["quantum computing"], "fedreg": ["quantum information"]},
    {"theme_hint": "위성통신·LEO",
     "arxiv": ["LEO satellite"], "wiki": ["Starlink"],
     "wiki_ko": ["스타링크"], "naver": ["한화 저궤도"], "assembly": ["우주산업"],
     "trends": ["satellite internet"], "github": [], "hn": ["satellite internet"],
     "sec": ["low earth orbit satellite"], "patent": ["LEO satellite constellation"],
     "fedreg": ["low earth orbit satellite"]},
]


def load_keywords():
    p = os.path.join(HERE, "keywords.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return DEFAULT_KEYWORDS


def _g(growth):
    """증가율 표시 헬퍼."""
    if growth is None:
        return "  —  "
    if growth == float("inf"):
        return " +∞%  "
    return f"{growth:+6.1f}%"


def main():
    ws = datasource.WeakSignals()
    themes = load_keywords()
    print(f"[1/3] 키워드 큐레이션 로드: {len(themes)}개 테마 후보\n")
    if ws.github_token:
        print("  GitHub 토큰 감지 → 5000 req/h 한도\n")
    else:
        print("  ⚠️ GITHUB_TOKEN 미설정 → 60 req/h 한도 (테마 30개 이상 시 발급 권장)\n")

    rows = []
    for i, t in enumerate(themes, 1):
        hint = t.get("theme_hint", f"테마{i}")
        print(f"[{i}/{len(themes)}] {hint}")
        rec = {"theme_hint": hint, "arxiv": [], "wiki": [], "wiki_ko": [], "trends": [],
               "github": [], "hn": [], "sec_edgar": [], "patents": [], "federal_register": [],
               "naver_news": [], "assembly_bills": []}

        def _safe(call, label, kw):
            try:
                r = call()
                rec_key = {"arXiv":"arxiv","Wiki":"wiki","GitHub":"github","HN":"hn",
                           "SEC":"sec_edgar","Patent":"patents","FedReg":"federal_register",
                           "Naver":"naver_news","Assembly":"assembly_bills"}[label]
                rec[rec_key].append(r)
                if "error" in r:
                    print(f"  {label:8s} {kw!r:32s} ⚠ {r['error']}")
                else:
                    print(f"  {label:8s} {kw!r:32s} n_r={r.get('n_recent',0):>5d} n_b={r.get('n_base',0):>5d} growth={_g(r.get('growth_pct'))}")
            except Exception as e:
                print(f"  {label:8s} {kw!r:32s} EXC {type(e).__name__}: {str(e)[:60]}")

        for kw in t.get("arxiv", []):
            _safe(lambda kw=kw: ws.arxiv_paper_growth(kw), "arXiv", kw); time.sleep(1.0)
        for art in t.get("wiki", []):
            try:
                r = ws.wiki_pageview_growth(art, project="en.wikipedia")
                rec["wiki"].append(r)
                print(f"  {'Wiki':8s} {art!r:32s} avg_r={r.get('avg_recent',0):>7.0f} avg_b={r.get('avg_base',0):>7.0f} growth={_g(r.get('growth_pct'))}")
            except Exception as e:
                print(f"  Wiki     {art!r:32s} EXC {type(e).__name__}: {str(e)[:60]}")
            time.sleep(0.3)
        for art in t.get("wiki_ko", []):
            try:
                r = ws.wiki_pageview_growth(art, project="ko.wikipedia")
                rec["wiki_ko"].append(r)
                if "error" in r:
                    print(f"  {'Wiki-KO':8s} {art!r:32s} ⚠ {r['error']}")
                else:
                    print(f"  {'Wiki-KO':8s} {art!r:32s} avg_r={r.get('avg_recent',0):>7.0f} avg_b={r.get('avg_base',0):>7.0f} growth={_g(r.get('growth_pct'))}")
            except Exception as e:
                print(f"  Wiki-KO  {art!r:32s} EXC {type(e).__name__}: {str(e)[:60]}")
            time.sleep(0.3)
        for kw in t.get("github", []):
            _safe(lambda kw=kw: ws.github_repo_growth(kw), "GitHub", kw); time.sleep(0.8)
        for kw in t.get("hn", []):
            _safe(lambda kw=kw: ws.hackernews_mention_growth(kw), "HN", kw); time.sleep(0.3)
        for kw in t.get("sec", []):
            _safe(lambda kw=kw: ws.sec_edgar_growth(kw), "SEC", kw); time.sleep(0.6)
        for kw in t.get("patent", []):
            _safe(lambda kw=kw: ws.patent_growth(kw), "Patent", kw); time.sleep(0.6)
        for kw in t.get("fedreg", []):
            _safe(lambda kw=kw: ws.federal_register_growth(kw), "FedReg", kw); time.sleep(0.3)
        for kw in t.get("naver", []):
            _safe(lambda kw=kw: ws.naver_news_growth(kw), "Naver", kw); time.sleep(0.3)
        for kw in t.get("assembly", []):
            _safe(lambda kw=kw: ws.assembly_bill_growth(kw), "Assembly", kw); time.sleep(0.5)

        # Google Trends (옵션)
        kws = t.get("trends", [])
        if kws:
            r = ws.google_trends_growth(kws)
            for k, v in r.items():
                if "error" in v:
                    pass  # 미설치는 묵음
                else:
                    print(f"  Trends  {k!r:32s} growth={_g(v.get('growth_pct'))}")
            rec["trends"] = [{"keyword": k, **v} for k, v in r.items() if "error" not in v]
        print()
        rows.append(rec)

    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "windows": {
            "arxiv": "30d vs 180d", "wiki": "4w vs 12w", "wiki_ko": "4w vs 12w",
            "trends": "4w vs 12w", "github": "30d vs 180d", "hn": "30d vs 180d",
            "sec_edgar": "90d vs 365d (10-K)", "patents": "180d vs 730d (priority date)",
            "federal_register": "90d vs 365d",
            "naver_news": "30d vs 180d", "assembly_bills": "180d vs 730d (22대)",
        },
        "data_source": ("arXiv + Wikipedia(EN/KO) + GitHub + HackerNews + SEC EDGAR + "
                        "Google Patents + federalregister.gov + 네이버 뉴스 + 국회 의안 "
                        "(+ Google Trends optional)"),
        "n_themes": len(themes),
        "signals": rows,
        "factor_mapping": {
            "Earliness":       ["github", "hn", "arxiv"],
            "Durability":      ["patents", "arxiv"],
            "Capital_Inflow":  ["hn", "sec_edgar", "LLM(ETF Flow)"],
            "Verifiability":   ["sec_edgar", "naver_news", "wiki_ko"],
            "Policy_Momentum": ["federal_register", "assembly_bills", "LLM(EU 보강)"],
        },
        "notes": [
            "이 raw 신호는 LLM이 [2단계 구조적 수요] [3단계 자금 흐름] 외부 리서치와 결합하여 5점 평가에 활용",
            "Crunchbase(VC)·한국 정책·ETF Flow 등은 WebFetch/WebSearch로 별도 수집",
            "성장률 +∞ = 베이스라인 0이지만 최근 출현(완전 신규 키워드 — 강한 Earliness 신호)",
            "GitHub: GITHUB_TOKEN .env 등록 시 60→5000 req/h",
        ],
    }
    json.dump(out, open(os.path.join(HERE, "signals_raw.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[3/3] 저장: signals_raw.json ({len(rows)}개 테마)")
    print("다음 단계: LLM이 signals_raw.json + 외부 리서치를 결합해 5점 평가·4분류 → ideas.json 생성")


if __name__ == "__main__":
    main()
