#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""유니버스 스크리닝 — 하이브리드 LIVE 버전 (실재무 + 컨센서스, 부분 연동).

데이터(LIVE):
  · FnSpace account(재무)  : ROE(지배)/ROA/영업이익률/부채비율/매출·영익 증가율(YoY)/EV-EBITDA 실적치
  · FnSpace consensus-*    : 컨센서스 존재, Fwd ROE/EV-EBITDA, 목표주가괴리율/리비전/투자의견

시드 풀: 기존 큐레이션 코스피/코스닥 실제 코드 (stock_list 권한 확보 전 임시 모집단).

LIVE 적용:
  · 애널리스트 컨센서스 존재 필터 (핵심 신규 필터)
  · 4팩터 섹터 z-score → 레짐 균등가중 → Top 100
      - 밸류  : z(−EV/EBITDA 실적) + z(목표주가괴리율)            [실재무+컨센서스]
      - 퀄리티: z(ROE + ROA − 부채비율/100 + 영업이익률) 실적     [실재무, 기존 스킬 공식]
      - 모멘텀: z(목표주가 상향−하향 3개월)                        [컨센서스 리비전 — 가격모멘텀 대체]
      - 성장  : z(매출·영업이익 증가율 YoY 실적)                   [실재무]

DEFERRED (권한/차단으로 보류 — stock_price/stock_list 권한 확보 시 활성화):
  · 거래정지·관리종목·20일평균거래대금 하드필터
  · 가격 기반 모멘텀(12-1M), PER/PBR, 전체 종목마스터
"""
import sys, os, json, statistics, datetime
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
TOPN = 100

REGIME_WEIGHTS = {
    "위험선호": {"value": .15, "quality": .20, "momentum": .40, "growth": .25},
    "중립":     {"value": .25, "quality": .25, "momentum": .25, "growth": .25},
    "위험회피": {"value": .35, "quality": .35, "momentum": .15, "growth": .15},
}


def load_seed():
    seed = {}
    for fn in ["universe_200_prev.json", "universe.json"]:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            continue
        for x in json.load(open(p, encoding="utf-8")):
            t = str(x.get("ticker", ""))
            if t.isdigit() and len(t) == 6 and t not in seed:
                seed[t] = {"ticker": t, "name": x.get("name", ""), "sector": x.get("sector", "기타")}
    return seed


def load_regime():
    p = os.path.join(WS, "02_macro", "allocation.json")
    regime = "중립"
    if os.path.exists(p):
        regime = json.load(open(p, encoding="utf-8")).get("regime", "중립")
    return regime, REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["중립"])


def gz(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return 0.0, 1e-9
    return statistics.mean(v), (statistics.pstdev(v) or 1e-9)


def winsor(x, lo=-5.0, hi=5.0):
    return max(lo, min(hi, x))


def sector_z(rows, field):
    allv = [r[field] for r in rows if r.get(field) is not None]
    gm, gsd = gz(allv)
    bysec = defaultdict(list)
    for r in rows:
        if r.get(field) is not None:
            bysec[r["sector"]].append(r[field])
    stat = {}
    for s, vs in bysec.items():
        stat[s] = (statistics.mean(vs), statistics.pstdev(vs) or 1e-9) if len(vs) >= 3 else (gm, gsd)
    out = {}
    for r in rows:
        if r.get(field) is None:
            out[r["ticker"]] = 0.0
        else:
            m, sd = stat[r["sector"]]
            out[r["ticker"]] = winsor((r[field] - m) / sd)
    return out


def main():
    seed = load_seed()
    regime, W = load_regime()
    codes = list(seed)
    print(f"시드 풀: {len(codes)}종목 | 레짐={regime} 가중치={W}")

    con = datasource.ConsensusData(chunk=10).fetch(codes)
    fin = datasource.FinancialsData(chunk=10).fetch(codes)  # 직전연도 실적
    n_fin = sum(1 for t in codes if fin.get(t))
    print(f"재무(account) 수집: {n_fin}/{len(codes)}종목 | 컨센서스 수집 완료")

    # --- 필터 1: 컨센서스 존재 ---
    n_total = len(codes)
    cand = [t for t in codes if con.get(t, {}).get("consensus")]
    no_consensus = [t for t in codes if not con.get(t, {}).get("consensus")]
    print(f"컨센서스 보유: {len(cand)} / 미보유 제외: {len(no_consensus)}")

    # --- 밸류 서브지표 전역 표준화 통계 (실적 EV/EBITDA + 목표가괴리율) ---
    ev_m, ev_sd = gz([fin.get(t, {}).get("ev_ebitda") for t in cand])
    up_m, up_sd = gz([con.get(t, {}).get("target_upside") for t in cand])

    rows = []
    for t in cand:
        c = con.get(t, {}); f = fin.get(t, {})
        # 밸류: -EV/EBITDA(실적) 표준화 + 목표주가괴리율 표준화 평균
        parts = []
        if f.get("ev_ebitda") is not None:
            parts.append(-(f["ev_ebitda"] - ev_m) / ev_sd)
        if c.get("target_upside") is not None:
            parts.append((c["target_upside"] - up_m) / up_sd)
        value_raw = sum(parts) / len(parts) if parts else None
        # 퀄리티: 실적 ROE + ROA - 부채비율/100 + 영업이익률 (기존 스킬 공식). 결측 시 Fwd ROE 폴백
        if all(f.get(k) is not None for k in ("roe", "roa", "debt", "opm")):
            quality_raw = f["roe"] + f["roa"] - f["debt"] / 100.0 + f["opm"]
        else:
            quality_raw = c.get("fwd_roe")
        # 모멘텀: 컨센서스 리비전(3개월)
        momentum_raw = c.get("rev_momentum")
        # 성장: 실적 매출/영익 증가율 YoY 평균. 결측 시 컨센서스 추정 폴백
        gp = [x for x in (f.get("rev_yoy"), f.get("opinc_yoy")) if x is not None]
        if not gp:
            gp = [x for x in (c.get("rev_yoy"), c.get("opinc_yoy")) if x is not None]
        growth_raw = sum(gp) / len(gp) if gp else None

        rows.append({
            "ticker": t, "name": seed[t]["name"], "sector": seed[t]["sector"],
            "_value_raw": value_raw, "_quality_raw": quality_raw,
            "_momentum_raw": momentum_raw, "_growth_raw": growth_raw,
            # 정보용 실측 컬럼
            "roe": f.get("roe"), "roa": f.get("roa"), "opm": f.get("opm"), "debt": f.get("debt"),
            "ev_ebitda_act": f.get("ev_ebitda"), "rev_yoy_act": f.get("rev_yoy"), "opinc_yoy_act": f.get("opinc_yoy"),
            "opinion": c.get("opinion"), "target_upside": c.get("target_upside"),
            "fwd_roe": c.get("fwd_roe"), "rev_momentum": c.get("rev_momentum"),
            "fin_ok": bool(f),
        })

    zv = sector_z(rows, "_value_raw"); zq = sector_z(rows, "_quality_raw")
    zm = sector_z(rows, "_momentum_raw"); zg = sector_z(rows, "_growth_raw")
    for r in rows:
        r["z_value"] = round(zv[r["ticker"]], 3); r["z_quality"] = round(zq[r["ticker"]], 3)
        r["z_momentum"] = round(zm[r["ticker"]], 3); r["z_growth"] = round(zg[r["ticker"]], 3)
        r["total"] = round(W["value"]*r["z_value"] + W["quality"]*r["z_quality"]
                           + W["momentum"]*r["z_momentum"] + W["growth"]*r["z_growth"], 4)

    ranked = sorted(rows, key=lambda r: r["total"], reverse=True)
    top = ranked[:TOPN]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    asof = datetime.datetime.now().strftime("%Y-%m-%d")
    shortfall = len(top) < TOPN

    # ---- universe_consensus.json ----
    out_json = []
    for r in top:
        out_json.append({
            "rank": r["rank"], "ticker": r["ticker"], "name": r["name"],
            "market": "KOSPI/KOSDAQ", "sector": r["sector"],
            "scores": {"value": r["z_value"], "quality": r["z_quality"],
                       "momentum": r["z_momentum"], "growth": r["z_growth"], "total": r["total"]},
            "financials_actual": {"roe": r["roe"], "roa": r["roa"], "opm": r["opm"],
                                  "debt_ratio": r["debt"], "ev_ebitda": r["ev_ebitda_act"],
                                  "rev_yoy": r["rev_yoy_act"], "opinc_yoy": r["opinc_yoy_act"]},
            "consensus_inputs": {"opinion": r["opinion"], "target_upside_pct": r["target_upside"],
                                 "fwd_roe": r["fwd_roe"], "revision_3m": r["rev_momentum"]},
        })
    meta = {
        "as_of": asof, "regime": regime, "weights": W, "n_final": len(top),
        "data_source": "FnSpace account(재무, LIVE) + consensus-* (LIVE)",
        "method": "hybrid 4factor (실재무 퀄리티/성장/밸류 + 컨센서스 모멘텀/목표가), sector z-score, ±5σ winsor, regime weights",
        "filters_applied": ["애널리스트 컨센서스 존재 (LIVE)"],
        "filters_deferred": ["거래정지", "관리종목", "20일평균거래대금 1억", "가격모멘텀(12-1M)", "PER/PBR"],
        "seed_note": "종목마스터(stock_list) 권한 확보 전까지 기존 큐레이션 코드로 모집단 시드",
        "financials_coverage": f"{n_fin}/{n_total}",
    }
    json.dump({"meta": meta, "universe": out_json},
              open(os.path.join(HERE, "universe_consensus.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # ---- universe_consensus.md ----
    def f(x, p=1):
        return "-" if x is None else f"{x:.{p}f}"
    L = []
    L.append("# 투자유니버스 (하이브리드 LIVE: 실재무+컨센서스) — Top 100\n")
    L.append(f"- **기준일**: {asof}")
    L.append("- **데이터 소스**: FnSpace `account`(재무, 실시간) + `consensus-*`(컨센서스, 실시간) — **구독 데이터**")
    L.append(f"- **레짐**: {regime} → 밸류 {W['value']:.0%} / 퀄리티 {W['quality']:.0%} / 모멘텀 {W['momentum']:.0%} / 성장 {W['growth']:.0%}")
    L.append(f"- **시드 모집단**: 기존 큐레이션 코스피/코스닥 {n_total}종목 (※ stock_list 권한 확보 시 전체 시장 확장)")
    L.append(f"- **재무 커버리지**: {n_fin}/{n_total}종목 실적 수집\n")
    L.append("## 적용 필터 (LIVE)")
    L.append("| 단계 | 통과 | 비고 |")
    L.append("|------|------|------|")
    L.append(f"| 시드 모집단 | {n_total} | 기존 큐레이션 실제 코드 |")
    L.append(f"| **애널리스트 컨센서스 존재** | {len(cand)} | -{len(no_consensus)} (추정실적 컨센서스 미보유 제외) |")
    L.append(f"| 최종 Top {TOPN} | {len(top)} | 종합점수 상위 |\n")
    L.append("## 보류된 필터/팩터 (DEFERRED — fnspace 주가/리스트 권한 확보 시 활성화)")
    L.append("- 거래정지·관리종목 제외, 20일평균거래대금 1억원 필터 → `stock_price`/`stock_list` 권한 필요")
    L.append("- 가격 기반 모멘텀(12-1M)·PER/PBR → `stock_price` 권한 필요 (현재 모멘텀은 컨센서스 리비전 3개월로 대체)\n")
    L.append("## 팩터 정의")
    L.append("- **밸류** = z(−EV/EBITDA 실적) + z(목표주가괴리율) 평균  *(실재무+컨센서스)*")
    L.append("- **퀄리티** = z(ROE + ROA − 부채비율/100 + 영업이익률) 실적  *(기존 스킬 공식)*")
    L.append("- **모멘텀** = z(목표주가 상향−하향/전체, 3개월)  *(컨센서스 리비전; 가격모멘텀 대체)*")
    L.append("- **성장** = z(매출·영업이익 증가율 YoY 실적)\n")
    if shortfall:
        L.append(f"> ⚠️ 컨센서스 통과 종목 {len(cand)}개로 목표 {TOPN} 미만. 시드 풀 확장 필요.\n")
    L.append("## 유니버스 (Top 100)")
    L.append("| 순위 | 티커 | 종목명 | 섹터 | ROE | 부채% | EV/EBITDA | 목표가괴리% | 투자의견 | 밸류 | 퀄리티 | 모멘텀 | 성장 | 종합 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in top:
        L.append(f"| {r['rank']} | {r['ticker']} | {r['name']} | {r['sector']} | "
                 f"{f(r['roe'])} | {f(r['debt'])} | {f(r['ev_ebitda_act'])} | {f(r['target_upside'])} | {f(r['opinion'],2)} | "
                 f"{r['z_value']:+.2f} | {r['z_quality']:+.2f} | {r['z_momentum']:+.2f} | {r['z_growth']:+.2f} | {r['total']:+.3f} |")
    open(os.path.join(HERE, "universe_consensus.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    print(f"\n최종 {len(top)}종목 (목표 {TOPN}{'  ⚠️부족' if shortfall else ''})")
    print("상위 10:")
    for r in top[:10]:
        print(f"  {r['rank']:>2} {r['ticker']} {r['name'][:12]:<12} ROE={f(r['roe'])} 부채={f(r['debt'])} "
              f"EV/EBITDA={f(r['ev_ebitda_act'])} 괴리={f(r['target_upside'])}% 종합={r['total']:+.3f}")
    print("\n산출: universe_consensus.md / universe_consensus.json")


if __name__ == "__main__":
    main()
