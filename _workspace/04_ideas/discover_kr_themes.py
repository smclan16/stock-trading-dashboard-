#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4단계 한국 부상 섹터·테마 자동 발굴 (1차안+2차안 통합).

문제의식:
  기존 DEFAULT_KEYWORDS는 글로벌 IT 기술(LLM·SMR·휴머노이드 등) 위주 수동 큐레이션 →
  거버넌스·증권주·배당주·인바운드 관광·K-방산·K-조선 등 한국 고유 테마가 누락됨.

해법 1차안 — 섹터 모멘텀 자동 발굴:
  3단계 universe.json (5팩터 모델 결과)이 이미 종목별 모멘텀 신호를 보유.
  · tp_rev_1m_pct          (목표가 1M 변화율)
  · roe_fwd_rev_1m_pct     (Fwd 12M ROE 1M 변화율)
  · turnover_growth_pct    (거래대금 3M→1M 증가율)
  섹터별 평균을 집계해 부상 섹터 자동 도출.

해법 2차안 — DART 공시 자동 추적:
  DART /list.json 활용 (`DartDisclosure` 클래스)
  · 수주 (단일판매·공급계약) 30d vs 60d: K-방산·K-조선·전력기기 자동 발굴
  · 자사주 취득/소각: 거버넌스·밸류업 모멘텀
  · M&A·분할·합병: 거버넌스 변화 신호
  · 기업가치 제고: 정부 밸류업 정책 호응

산출: _workspace/04_ideas/kr_themes_discovered.json
"""
import sys, os, json, statistics, datetime
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
UNIVERSE_PATH = os.path.join(WS, "03_universe", "universe.json")
META_PATH = os.path.join(WS, "05_matching", "company_meta.json")
OUT_PATH = os.path.join(HERE, "kr_themes_discovered.json")

# 부상 섹터로 인정하기 위한 최소 종목 수 (작은 섹터는 통계 노이즈)
MIN_SECTOR_SIZE = 3
# 신호 평균값 zscore가 이 이상이면 부상 섹터로 표시
THRESHOLD_Z = 0.3


def load_universe():
    d = json.load(open(UNIVERSE_PATH, encoding="utf-8"))
    if isinstance(d, dict) and "universe" in d:
        return d["universe"]
    return d  # list 형식 fallback


def aggregate_by_sector(universe):
    """섹터별 평균 신호 집계."""
    by_sec = defaultdict(list)
    for x in universe:
        sec = x.get("sector", "미분류")
        m = x.get("metrics") or {}
        if not m:  # universe.json 4팩터 시절 list 형식 fallback
            m = {"tp_rev_1m_pct": None, "roe_fwd_rev_1m_pct": None, "turnover_growth_pct": None}
        by_sec[sec].append({
            "ticker": x["ticker"],
            "name": x.get("name"),
            "total_score": (x.get("scores") or {}).get("total"),
            "tp_rev": m.get("tp_rev_1m_pct"),
            "roe_rev": m.get("roe_fwd_rev_1m_pct"),
            "turnover": m.get("turnover_growth_pct"),
        })

    rows = []
    for sec, members in by_sec.items():
        if len(members) < MIN_SECTOR_SIZE:
            continue  # 표본 부족 섹터 제외
        tp_vals = [m["tp_rev"] for m in members if m["tp_rev"] is not None]
        roe_vals = [m["roe_rev"] for m in members if m["roe_rev"] is not None]
        tov_vals = [m["turnover"] for m in members if m["turnover"] is not None]
        total_vals = [m["total_score"] for m in members if m["total_score"] is not None]

        def _stat(vs):
            return statistics.mean(vs) if vs else None

        rows.append({
            "sector": sec,
            "n_members": len(members),
            "avg_total_score": _stat(total_vals),
            "avg_tp_rev_pct": _stat(tp_vals),
            "avg_roe_rev_pct": _stat(roe_vals),
            "avg_turnover_growth_pct": _stat(tov_vals),
            "members": sorted(members, key=lambda r: -(r["total_score"] or -1e9))[:5],  # 섹터 내 상위 5
        })
    return rows


def detect_emerging_sectors(sector_stats):
    """평균 신호 z-score로 부상 섹터 식별."""
    def _zscore(vals, target):
        clean = [v for v in vals if v is not None]
        if len(clean) < 2 or target is None:
            return None
        m = statistics.mean(clean)
        sd = statistics.pstdev(clean) or 1e-9
        return (target - m) / sd

    tp_vals = [r["avg_tp_rev_pct"] for r in sector_stats]
    roe_vals = [r["avg_roe_rev_pct"] for r in sector_stats]
    tov_vals = [r["avg_turnover_growth_pct"] for r in sector_stats]
    total_vals = [r["avg_total_score"] for r in sector_stats]

    for r in sector_stats:
        r["z_tp_rev"] = _zscore(tp_vals, r["avg_tp_rev_pct"])
        r["z_roe_rev"] = _zscore(roe_vals, r["avg_roe_rev_pct"])
        r["z_turnover"] = _zscore(tov_vals, r["avg_turnover_growth_pct"])
        r["z_total"] = _zscore(total_vals, r["avg_total_score"])
        zs = [r["z_tp_rev"], r["z_roe_rev"], r["z_turnover"], r["z_total"]]
        zs = [z for z in zs if z is not None]
        r["composite_z"] = sum(zs) / len(zs) if zs else None
        r["is_emerging"] = (r["composite_z"] is not None and r["composite_z"] >= THRESHOLD_Z)
    return sorted(sector_stats, key=lambda r: -(r["composite_z"] or -1e9))


def main():
    if not os.path.exists(UNIVERSE_PATH):
        print(f"❌ {UNIVERSE_PATH} 없음. screen_full.py 먼저 실행.")
        sys.exit(1)
    universe = load_universe()
    print(f"[1/3] universe 로드: {len(universe)}종목")

    stats = aggregate_by_sector(universe)
    print(f"[2/3] 섹터 집계: {len(stats)}섹터 (≥{MIN_SECTOR_SIZE}종목)")

    ranked = detect_emerging_sectors(stats)
    emerging = [r for r in ranked if r["is_emerging"]]
    print(f"[3/3] 부상 섹터(composite_z ≥ {THRESHOLD_Z}): {len(emerging)}개\n")

    print(f"{'섹터':14s} {'N':>3s}  {'tp↑':>7s} {'roe↑':>7s} {'tov↑':>7s} {'total':>6s} {'z':>6s}  Top 3 종목")
    print("-" * 100)
    for r in ranked:
        flag = "⭐" if r["is_emerging"] else "  "
        members_short = ", ".join(f"{m['ticker']} {m['name'][:8]}" for m in r["members"][:3])
        def fmt(v, p=1):
            return "-" if v is None else f"{v:+.{p}f}"
        print(f"{flag} {r['sector']:13s} {r['n_members']:>3d}  "
              f"{fmt(r['avg_tp_rev_pct']):>7s} {fmt(r['avg_roe_rev_pct']):>7s} "
              f"{fmt(r['avg_turnover_growth_pct']):>7s} {fmt(r['avg_total_score'],3):>6s} "
              f"{fmt(r['composite_z'],2):>6s}  {members_short}")

    # ── 2차안: DART 공시 빈도 변화 ───────────────────────────
    print("\n[2차안] DART 공시 빈도 변화 추적 (~2분, base 60d 페이지네이션)…")
    try:
        dart = datasource.DartDisclosure()
        dart_growth = dart.disclosure_growth(recent_days=30, base_days=60)
        print(f"  전체 공시: 최근30d={dart_growth['n_total_recent']} / 60d={dart_growth['n_total_base']}")
        for cat, v in dart_growth["categories"].items():
            g = v["growth_pct"]
            gs = "+∞" if g == float("inf") else ("—" if g is None else f"{g:+.1f}%")
            samples = ", ".join(v.get("sample_companies", [])[:3]) or "-"
            print(f"  {cat:14s} 30d={v['n_recent']:>4d} 60d={v['n_base']:>4d} growth={gs:>8s}  ex: {samples}")
    except Exception as e:
        print(f"  ⚠️ DART 공시 수집 실패: {type(e).__name__}: {e}")
        dart_growth = {"error": str(e)}

    # universe 종목별 DART 공시 매핑 (회사별 공시 → universe 섹터 집계)
    print("\n[2차안+] universe 종목별 DART 공시 매핑…")
    try:
        per_corp = dart.disclosures_by_corp(recent_days=30, base_days=60)
        ticker_set = {x["ticker"] for x in universe}
        ticker_to_sector = {x["ticker"]: x.get("sector", "미분류") for x in universe}
        sector_dart = defaultdict(lambda: defaultdict(lambda: {"recent": 0, "base": 0}))
        n_universe_with_filings = 0
        for stock_code, info in per_corp.items():
            if stock_code not in ticker_set:
                continue
            n_universe_with_filings += 1
            sec = ticker_to_sector.get(stock_code, "미분류")
            for cat, cnt in info["categories"].items():
                sector_dart[sec][cat]["recent"] += cnt["recent"]
                sector_dart[sec][cat]["base"] += cnt["base"]

        sector_dart_summary = {}
        for sec, cats in sector_dart.items():
            sector_dart_summary[sec] = {}
            for cat, cnt in cats.items():
                rate_r = cnt["recent"] / 30.0
                rate_b = cnt["base"] / 60.0 if cnt["base"] else 0
                growth = ((rate_r/rate_b - 1.0)*100.0) if rate_b > 0 else (None if cnt["recent"] == 0 else float("inf"))
                sector_dart_summary[sec][cat] = {
                    "recent": cnt["recent"], "base": cnt["base"], "growth_pct": growth
                }
        print(f"  universe 100종목 중 {n_universe_with_filings}종목이 60일 내 공시 보유")
        # 부상 섹터에서 contract/treasury 신호 출력
        for r in emerging:
            sec = r["sector"]
            sd = sector_dart_summary.get(sec, {})
            if sd:
                top = sorted(sd.items(), key=lambda kv: -(kv[1].get("growth_pct") or -1e9))[:3]
                top_str = ", ".join(f'{k}={v["recent"]}/{v["base"]}' for k, v in top if v["recent"] > 0)
                if top_str:
                    print(f"  {sec}: {top_str}")
    except Exception as e:
        print(f"  ⚠️ universe 공시 매핑 실패: {type(e).__name__}: {e}")
        sector_dart_summary = {}

    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "n_universe": len(universe),
        "n_sectors_analyzed": len(stats),
        "n_emerging": len(emerging),
        "threshold_composite_z": THRESHOLD_Z,
        "min_sector_size": MIN_SECTOR_SIZE,
        "method": {
            "stage1_sector_momentum": "3단계 universe.json(5팩터)의 종목별 metrics를 섹터별 평균집계 → "
                                       "composite z-score(목표가↑·ROE↑·거래대금↑·종합점수) 상위 = 부상 섹터.",
            "stage2_dart_disclosures": "DART /list.json (pblntf_detail_ty=I001 거래소수시공시)의 보고서명 키워드 분류로 "
                                        "수주(단일판매·공급계약)·자사주 취득/처분/소각·기업가치 제고·M&A "
                                        "공시 빈도 변화(30d vs 60d) 추적. 한국 시장 catalyst-driven 테마 자동 발굴.",
        },
        "next_step_for_llm": "emerging_sectors + dart_disclosure_growth 종합 → (1) 한국 시장 맥락의 테마명 부여 "
                              "(2) 매크로 무관 회사 자체 catalyst인지 vs 매크로 사이클인지 판별 "
                              "(3) 기존 글로벌 테마와 중복되는지 확인 후 ideas.json에 통합",
        "emerging_sectors_stage1": emerging,
        "all_sectors_ranked_stage1": ranked,
        "dart_disclosure_growth_stage2": dart_growth,
        "universe_sector_dart_signals": sector_dart_summary,
    }
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
