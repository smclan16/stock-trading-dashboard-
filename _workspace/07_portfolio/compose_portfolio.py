#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""7단계 포트폴리오 구성 — 4·5·6단계 결합 + 1단계 투자성향 제약.

산식:
  For each idea i:
      idea_alloc = ideas[i].allocation_pct
      matched = matching_matrix[i].matched_tickers
      weighted_score(t) = research[t].total_score × intensity_factor[t.intensity]
      ticker_w_in_idea(t) = weighted_score(t) / Σ × idea_alloc

  종목 총비중 = Σ (모든 매칭 아이디어에서의 비중) + default_picks.weight_pct

투자성향 제약 (constraints.json):
  · excluded_tickers / excluded_sectors    → 제거 후 재정규화
  · max_single_stock_pct                   → 단일 상한, 초과분 비례 재분배
  · max_sector_pct                         → 섹터 상한
  · max_annual_volatility                  → 포트폴리오 변동성 사후 검증

자본 대비:
  capital_weight = ticker_weight × allocation.equity_pct / 100
"""
import sys, os, json, math, datetime, argparse
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
CONSTRAINTS = os.path.join(WS, "01_profile", "constraints.json")
ALLOCATION = os.path.join(WS, "02_macro", "allocation.json")
IDEAS = os.path.join(WS, "04_ideas", "ideas.json")
MATRIX = os.path.join(WS, "05_matching", "matching_matrix.json")
SCORES = os.path.join(WS, "06_research", "research_scores.json")
META = os.path.join(WS, "05_matching", "company_meta.json")
OUT_JSON = os.path.join(HERE, "portfolio.json")
OUT_MD = os.path.join(HERE, "model_portfolio.md")

INTENSITY_FACTOR = {"direct": 1.0, "indirect": 0.7, "value_chain": 0.4, "perception": 0.3}

# 시총별 차등 단일 종목 비중 상한 (단위: 억원)
SIZE_CAP_TIERS = [
    (10000, 15),  # 시총 ≥1조 → 15%
    (1000, 10),   # 1천억~1조 → 10%
    (0, 5),       # ~1천억 → 5%
]


def size_cap_for(mcap_eok, default_cap):
    """시총 규모별 단일 종목 비중 상한. mcap 정보 없으면 default_cap."""
    if mcap_eok is None or mcap_eok <= 0:
        return default_cap
    for threshold, cap in SIZE_CAP_TIERS:
        if mcap_eok >= threshold:
            return min(cap, default_cap)
    return min(SIZE_CAP_TIERS[-1][1], default_cap)


def compose_idea_weights(matching, scores_by_ticker, ideas_alloc, excluded_tickers, excluded_sectors,
                          ticker_sector, surviving=None, weighting='score', mcap_by_ticker=None,
                          mcap_blend=0.5):
    """아이디어별 매칭 종목에 비중 분배. 멀티 매칭 종목은 자동 합산.

    weighting: 'score' (기존, 매력도×intensity)
              | 'mcap' (시총가중)
              | 'hybrid' (mcap_blend: mcap^α × (score×intensity)^(1-α) 기하평균, α=mcap_blend)

    surviving (set or None): None이면 모든 종목, set이면 해당 집합 내 종목만 분배(사후 재분배용)
    반환: dict[ticker] = {weight, sources: [{idea_id, contribution}]}
    """
    weights = defaultdict(lambda: {"weight": 0.0, "sources": []})
    mcap_by_ticker = mcap_by_ticker or {}

    for idea in matching["matrix"]:
        idea_id = idea["idea_id"]
        idea_alloc = ideas_alloc.get(str(idea_id), 0)
        if idea_alloc <= 0:
            continue
        matched = idea.get("matched_tickers", [])
        # 제외·surviving 필터
        filtered = []
        for mt in matched:
            t = mt["ticker"]
            if surviving is not None and t not in surviving:
                continue
            if t in excluded_tickers:
                continue
            if ticker_sector.get(t) in excluded_sectors:
                continue
            score = scores_by_ticker.get(t, {}).get("total_score", 0)
            inten = mt.get("intensity", "indirect")
            f = INTENSITY_FACTOR.get(inten, 0.3)
            mcap = mcap_by_ticker.get(t, 0) or 0
            score_x = score * f  # 기존 매력도×intensity

            if weighting == 'mcap':
                # 순수 시총 가중 (intensity는 cutoff용 — 0이면 매칭 약함이라 제외)
                ws = mcap if f > 0 else 0
            elif weighting == 'hybrid':
                # 기하평균: mcap^α × score_x^(1-α)
                if mcap > 0 and score_x > 0:
                    ws = (mcap ** mcap_blend) * (score_x ** (1 - mcap_blend))
                else:
                    ws = score_x  # mcap 데이터 없으면 score만
            else:  # 'score' (기본 기존 방식)
                ws = score_x

            if ws > 0:
                filtered.append({"ticker": t, "intensity": inten, "weighted_score": ws})
        if not filtered:
            continue
        score_sum = sum(x["weighted_score"] for x in filtered)
        if score_sum <= 0:
            continue
        for x in filtered:
            contribution = (x["weighted_score"] / score_sum) * idea_alloc
            weights[x["ticker"]]["weight"] += contribution
            weights[x["ticker"]]["sources"].append({
                "idea_id": idea_id,
                "idea_theme": idea["idea_theme"],
                "intensity": x["intensity"],
                "contribution_pct": round(contribution, 3),
            })
    return weights


def add_default_picks(weights, ideas, excluded_tickers, ticker_sector, excluded_sectors, surviving=None):
    """4단계 default_picks.picks를 종목 비중에 추가.
    surviving 지정 시 살아남은 picks만 추가 + 풀 총비중(allocation_pct)을 비례 재분배."""
    dp = ideas.get("default_picks", {})
    picks = dp.get("picks", []) or []
    total_alloc = dp.get("allocation_pct", 0)

    # 활성 picks 필터
    active = []
    for p in picks:
        t = p["ticker"]
        if surviving is not None and t not in surviving:
            continue
        if t in excluded_tickers or ticker_sector.get(t) in excluded_sectors:
            continue
        active.append(p)
    if not active:
        return weights

    # 활성 picks의 원래 weight 합 vs total_alloc → scale (살아남은 picks끼리 비례 재분배)
    active_sum = sum(p.get("weight_pct", 0) for p in active)
    scale = (total_alloc / active_sum) if active_sum > 0 else 0

    for p in active:
        adjusted_w = p.get("weight_pct", 0) * scale
        weights[p["ticker"]]["weight"] += adjusted_w
        weights[p["ticker"]]["sources"].append({
            "source": "default_picks",
            "macro_beta": p.get("macro_beta"),
            "catalyst": p.get("catalyst"),
            "contribution_pct": round(adjusted_w, 3),
        })
    return weights


def apply_single_stock_cap(weights, default_cap, mcap_by_ticker=None):
    """단일 종목 한도 강제. 시총별 차등 cap 적용 (mcap_by_ticker 제공 시).
    초과분을 cap 여유 있는 종목에 매력도 비례 재분배."""
    if not default_cap or default_cap <= 0:
        return weights
    mcap_by_ticker = mcap_by_ticker or {}
    # 종목별 cap 계산 (시총 기반)
    caps = {t: size_cap_for(mcap_by_ticker.get(t), default_cap) for t in weights}

    for _ in range(50):
        over = {t: d for t, d in weights.items() if d["weight"] > caps[t]}
        if not over:
            break
        # 초과 종목을 cap으로 자름
        excess = 0
        for t, d in over.items():
            excess += d["weight"] - caps[t]
            d["weight"] = caps[t]
        # 나머지 종목 (cap 미달 + 0 초과)에 매력도(현재 weight) 비례 재분배
        recipients = {t: d for t, d in weights.items()
                       if d["weight"] > 0 and d["weight"] < caps[t] and t not in over}
        if not recipients:
            break
        rec_sum = sum(d["weight"] for d in recipients.values())
        if rec_sum <= 0:
            break
        for t, d in recipients.items():
            add = (d["weight"] / rec_sum) * excess
            # cap 초과하지 않도록 limit
            max_addable = caps[t] - d["weight"]
            d["weight"] += min(add, max_addable)
    return weights


def apply_sector_cap(weights, ticker_sector, cap):
    """섹터 한도 강제."""
    if not cap or cap <= 0:
        return weights
    for _ in range(50):
        # 섹터별 합산
        by_sec = defaultdict(list)
        for t, d in weights.items():
            if d["weight"] > 0:
                by_sec[ticker_sector.get(t, "기타")].append(t)
        over_secs = {}
        for sec, tickers in by_sec.items():
            sec_sum = sum(weights[t]["weight"] for t in tickers)
            if sec_sum > cap:
                over_secs[sec] = (sec_sum, tickers)
        if not over_secs:
            break
        for sec, (sec_sum, tickers) in over_secs.items():
            scale = cap / sec_sum
            excess = 0
            for t in tickers:
                old = weights[t]["weight"]
                new = old * scale
                excess += old - new
                weights[t]["weight"] = new
            # 다른 섹터 종목에 비례 재분배
            other_tickers = [t for t, d in weights.items() if ticker_sector.get(t) != sec and d["weight"] > 0]
            if not other_tickers:
                continue
            other_sum = sum(weights[t]["weight"] for t in other_tickers)
            if other_sum <= 0:
                continue
            for t in other_tickers:
                add = (weights[t]["weight"] / other_sum) * excess
                weights[t]["weight"] += add
    return weights


def estimate_portfolio_volatility(weights, betas, kospi_annual_vol=0.18):
    """단순 포트폴리오 변동성 추정: β 가중 + idiosyncratic 무시.
    σ_p ≈ |β_p| × σ_KOSPI (β_p = Σ w_i × β_i / Σ w_i)
    """
    used = {t: d["weight"] for t, d in weights.items() if d["weight"] > 0 and t in betas}
    if not used:
        return None
    total_w = sum(used.values())
    if total_w <= 0:
        return None
    beta_p = sum(used[t] * betas[t] for t in used) / total_w
    return abs(beta_p) * kospi_annual_vol * 100  # %


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-weight", type=float, default=0.5, help="최소 비중(%) 미만 종목 제외 후 재정규화")
    ap.add_argument("--top-n", type=int, default=None, help="매력도 상위 N종목만 사용 (기본 전체)")
    ap.add_argument("--weighting", choices=["score", "mcap", "hybrid"], default="hybrid",
                    help="비중 산식: score(매력도)·mcap(시총)·hybrid(혼합, 기본)")
    ap.add_argument("--mcap-blend", type=float, default=0.5,
                    help="hybrid 모드 시 시총 가중치 α (0~1, 기본 0.5). w = mcap^α × score^(1-α)")
    args = ap.parse_args()

    print("[1/5] 입력 로드…")
    constraints = json.load(open(CONSTRAINTS, encoding="utf-8"))
    allocation = json.load(open(ALLOCATION, encoding="utf-8"))
    ideas = json.load(open(IDEAS, encoding="utf-8"))
    matching = json.load(open(MATRIX, encoding="utf-8"))
    scores_doc = json.load(open(SCORES, encoding="utf-8"))
    company_meta = {x["ticker"]: x for x in json.load(open(META, encoding="utf-8"))["companies"]}

    investor_type = constraints.get("investor_type", "-")
    equity_pct = allocation.get("equity_pct", 100)
    excluded_tickers = set(constraints.get("excluded_tickers", []) or [])
    excluded_sectors = set(constraints.get("excluded_sectors", []) or [])
    max_single = constraints.get("max_single_stock_pct", 15)
    max_sector = constraints.get("max_sector_pct", 30)
    max_vol = constraints.get("max_annual_volatility", 30)

    scores_by_ticker = {r["ticker"]: r for r in scores_doc.get("rows", [])}
    ticker_sector = {r["ticker"]: r.get("sector") for r in scores_doc.get("rows", [])}
    betas = {r["ticker"]: r.get("macro_beta") for r in scores_doc.get("rows", []) if r.get("macro_beta") is not None}
    ideas_alloc = ideas.get("allocation_weights", {}).get("by_idea", {})

    print(f"  투자자={investor_type} equity={equity_pct}% max_single={max_single}% max_sector={max_sector}%")
    print(f"  제외 종목: {len(excluded_tickers)} / 제외 섹터: {len(excluded_sectors)}")
    print(f"  매크로 베타 수집 종목: {len(betas)}")

    # Top-N 제한 (선택)
    if args.top_n:
        top_set = set(r["ticker"] for r in sorted(scores_doc["rows"], key=lambda x: -x["total_score"])[:args.top_n])
        # matching에서 top_set 외 제거
        for idea in matching["matrix"]:
            idea["matched_tickers"] = [m for m in idea["matched_tickers"] if m["ticker"] in top_set]
        print(f"  Top-{args.top_n} 제한 적용")

    # 시총 정보 사전 수집 (1차·재분배에서 사용)
    mcap_by_ticker = {}
    univ_doc = json.load(open(os.path.join(WS, "03_universe", "universe.json"), encoding="utf-8"))
    univ_list = univ_doc.get("universe", univ_doc) if isinstance(univ_doc, dict) else univ_doc
    for x in univ_list:
        m = x.get("metrics") or {}
        if m.get("mcap_eok"):
            mcap_by_ticker[x["ticker"]] = m["mcap_eok"]
    print(f"  weighting={args.weighting}" + (f" (mcap_blend={args.mcap_blend})" if args.weighting == "hybrid" else ""))

    print("\n[2/5] 1차 아이디어별 종목 비중 분배…")
    weights_1 = compose_idea_weights(matching, scores_by_ticker, ideas_alloc,
                                       excluded_tickers, excluded_sectors, ticker_sector,
                                       weighting=args.weighting,
                                       mcap_by_ticker=mcap_by_ticker,
                                       mcap_blend=args.mcap_blend)
    add_default_picks(weights_1, ideas, excluded_tickers, ticker_sector, excluded_sectors)
    print(f"  1차 분배: {len(weights_1)}종목")

    # min_weight 필터 → 살아남은 종목 집합
    surviving = {t for t, d in weights_1.items() if d["weight"] >= args.min_weight}
    n_dropped = len(weights_1) - len(surviving)
    print(f"  min_weight={args.min_weight}% 필터: {len(surviving)}종목 유지 / {n_dropped}종목 제외")

    print("\n[3/5] 사후 재분배 (살아남은 종목으로 idea_alloc 정확 보존)…")
    weights = compose_idea_weights(matching, scores_by_ticker, ideas_alloc,
                                    excluded_tickers, excluded_sectors, ticker_sector,
                                    surviving=surviving,
                                    weighting=args.weighting,
                                    mcap_by_ticker=mcap_by_ticker,
                                    mcap_blend=args.mcap_blend)
    add_default_picks(weights, ideas, excluded_tickers, ticker_sector, excluded_sectors,
                       surviving=surviving)
    total_after = sum(d["weight"] for d in weights.values())
    print(f"  재분배 후: {len(weights)}종목 / 합 {total_after:.2f}% (의도된 100%)")

    print("\n[4/5] 투자성향 제약 적용 (단일·섹터 한도 + 시총별 차등)…")
    n_small = sum(1 for t in weights if mcap_by_ticker.get(t, 0) < 1000)
    n_mid = sum(1 for t in weights if 1000 <= mcap_by_ticker.get(t, 0) < 10000)
    n_large = sum(1 for t in weights if mcap_by_ticker.get(t, 0) >= 10000)
    print(f"  시총 분포: 대형(≥1조) {n_large} / 중형(1천억~1조) {n_mid} / 소형(~1천억) {n_small}")
    weights = apply_single_stock_cap(weights, max_single, mcap_by_ticker)
    weights = apply_sector_cap(weights, ticker_sector, max_sector)
    # 한 번 더 검증
    final_total = sum(d["weight"] for d in weights.values())
    if final_total > 0 and abs(final_total - 100) > 0.5:
        # 미세 재정규화
        for d in weights.values():
            d["weight"] = d["weight"] / final_total * 100

    # 종목명 보강
    name_map = {x["ticker"]: x.get("name") for x in scores_doc.get("rows", [])}
    name_map.update({t: m.get("name") for t, m in company_meta.items()})

    holdings = []
    for t, d in sorted(weights.items(), key=lambda kv: -kv[1]["weight"]):
        sc = scores_by_ticker.get(t, {})
        # ideas 매핑 정리
        idea_ids = sorted({s["idea_id"] for s in d["sources"] if s.get("idea_id") is not None})
        is_default = any(s.get("source") == "default_picks" for s in d["sources"])
        mc = mcap_by_ticker.get(t)
        size_tier = ("large" if mc and mc >= 10000 else ("mid" if mc and mc >= 1000 else "small"))
        cap_applied = size_cap_for(mc, max_single)
        holdings.append({
            "ticker": t,
            "name": name_map.get(t),
            "sector": ticker_sector.get(t),
            "weight_pct": round(d["weight"], 3),
            "capital_weight_pct": round(d["weight"] * equity_pct / 100, 3),
            "attractiveness": sc.get("total_score"),
            "macro_beta": sc.get("macro_beta"),
            "matched_ideas": idea_ids,
            "is_default_pick": is_default,
            "mcap_eok": mc,
            "size_tier": size_tier,
            "size_cap_pct": cap_applied,
            "sources": d["sources"],
        })

    print(f"\n[5/5] 사후 검증 (변동성·집중도)…")
    # 섹터·집중도
    sec_dist = defaultdict(float)
    for h in holdings:
        sec_dist[h["sector"] or "기타"] += h["weight_pct"]
    sec_max = max(sec_dist.values()) if sec_dist else 0
    hhi = sum((h["weight_pct"]) ** 2 for h in holdings)

    # 변동성 추정 (β × KOSPI σ 단순화)
    vol_est = estimate_portfolio_volatility({h["ticker"]: {"weight": h["weight_pct"]} for h in holdings}, betas)

    # 시총별 cap 위반 검증
    size_cap_violations = [{"ticker": h["ticker"], "name": h["name"], "weight": h["weight_pct"],
                              "cap": h["size_cap_pct"], "tier": h["size_tier"], "mcap_eok": h["mcap_eok"]}
                             for h in holdings if h["weight_pct"] > h["size_cap_pct"] + 0.01]
    constraint_checks = {
        "max_single_stock": {"limit": max_single,
                              "actual_max": round(max((h["weight_pct"] for h in holdings), default=0), 2),
                              "passed": all(h["weight_pct"] <= max_single + 0.01 for h in holdings)},
        "size_based_cap": {"tiers": "대형(≥1조)=15% / 중형(1천억~1조)=10% / 소형(~1천억)=5%",
                            "violations": size_cap_violations, "passed": len(size_cap_violations) == 0},
        "max_sector": {"limit": max_sector,
                        "actual_max_pct": round(sec_max, 2),
                        "actual_max_sector": max(sec_dist, key=sec_dist.get) if sec_dist else None,
                        "by_sector_pct": {k: round(v, 2) for k, v in sec_dist.items()},
                        "passed": sec_max <= max_sector + 0.01},
        "max_annual_volatility": {"limit": max_vol,
                                   "estimated_pct": round(vol_est, 2) if vol_est is not None else None,
                                   "method": "β-weighted KOSPI σ (단순화, idiosyncratic 무시)",
                                   "passed": (vol_est is None) or (vol_est <= max_vol)},
        "excluded_tickers_applied": list(excluded_tickers),
        "excluded_sectors_applied": list(excluded_sectors),
    }

    diversification = {
        "n_holdings": len(holdings),
        "hhi": round(hhi, 1),
        "sector_distribution_pct": {k: round(v, 2) for k, v in sec_dist.items()},
    }

    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "investor_type": investor_type,
        "investor_score": (constraints.get("_meta", {}) or {}).get("score"),
        "equity_pct": equity_pct,
        "cash_pct": allocation.get("cash_pct", 100 - equity_pct),
        "regime": allocation.get("regime"),
        "method": "weighted-allocation (4단계 비중 × 5단계 매칭 × 6단계 매력도) + 1단계 투자성향 제약",
        "n_holdings": len(holdings),
        "holdings": holdings,
        "constraint_checks": constraint_checks,
        "diversification": diversification,
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # Markdown
    L = [f"# 모델 포트폴리오 ({datetime.datetime.now().strftime('%Y-%m-%d')})\n",
         f"**투자자 유형:** {investor_type} (score {out['investor_score']}) | **주식 비중:** {equity_pct}% | **레짐:** {allocation.get('regime')}\n",
         f"**방법론:** 4단계 비중 × 5단계 매칭 × 6단계 매력도 + 투자성향 제약\n",
         f"## 보유 종목 ({len(holdings)}개)\n",
         "| 순위 | 티커 | 종목명 | 섹터 | 비중 | 자본대비 | 매칭 | 매력도 | β | uncov |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for i, h in enumerate(holdings, 1):
        ideas_str = "+".join(f"#{i}" for i in h["matched_ideas"]) if h["matched_ideas"] else "default"
        b = f"{h['macro_beta']:.2f}" if h["macro_beta"] is not None else "-"
        u = "✓" if h["is_default_pick"] else ""
        L.append(f"| {i} | {h['ticker']} | {(h['name'] or '')[:14]} | {(h['sector'] or '-')[:6]} | "
                  f"{h['weight_pct']:.2f}% | {h['capital_weight_pct']:.2f}% | {ideas_str[:20]} | "
                  f"{(h['attractiveness'] or 0):.1f} | {b} | {u} |")

    L.append("\n## 제약 검증\n")
    cs = constraint_checks
    pass_str = lambda b: "✅" if b else "❌"
    L.append(f"- **단일 종목 한도** ({cs['max_single_stock']['limit']}%): 최대 {cs['max_single_stock']['actual_max']}% {pass_str(cs['max_single_stock']['passed'])}")
    L.append(f"- **섹터 한도** ({cs['max_sector']['limit']}%): 최대 {cs['max_sector']['actual_max_pct']}% ({cs['max_sector']['actual_max_sector']}) {pass_str(cs['max_sector']['passed'])}")
    if cs['max_annual_volatility']['estimated_pct'] is not None:
        L.append(f"- **포트폴리오 변동성 한도** ({cs['max_annual_volatility']['limit']}%): 추정 {cs['max_annual_volatility']['estimated_pct']}% {pass_str(cs['max_annual_volatility']['passed'])} (β 단순화)")
    L.append(f"- **제외 종목 적용**: {len(cs['excluded_tickers_applied'])}건 / **제외 섹터**: {len(cs['excluded_sectors_applied'])}건")

    L.append("\n## 분산 지표\n")
    L.append(f"- N종목: {diversification['n_holdings']}")
    L.append(f"- HHI: {diversification['hhi']} (낮을수록 분산도 높음)")
    L.append(f"- 섹터 분포 (Top 6):")
    for sec, pct in sorted(diversification["sector_distribution_pct"].items(), key=lambda kv: -kv[1])[:6]:
        L.append(f"    - {sec}: {pct:.2f}%")
    L.append("\n*면책: 정보 제공 목적이며 투자 권유가 아닙니다.*")

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"  저장: {OUT_JSON}, {OUT_MD}")

    print(f"\n=== 포트폴리오 상위 10 ===")
    for h in holdings[:10]:
        ideas_str = "+".join(f"#{i}" for i in h["matched_ideas"]) if h["matched_ideas"] else "default"
        b = f"β={h['macro_beta']:.2f}" if h["macro_beta"] is not None else "β=-"
        print(f"  {h['ticker']} {(h['name'] or '')[:14]:<14} {(h['sector'] or '-')[:6]:<6} 비중={h['weight_pct']:>5.2f}% 자본대비={h['capital_weight_pct']:>5.2f}% 매칭={ideas_str[:20]:<22} 매력={(h['attractiveness'] or 0):>5.1f} {b}")

    print(f"\n=== 제약 검증 ===")
    print(f"  단일 최대: {cs['max_single_stock']['actual_max']}% (한도 {max_single}%) {pass_str(cs['max_single_stock']['passed'])}")
    print(f"  시총별 cap (15/10/5%): 위반 {len(size_cap_violations)}건 {pass_str(cs['size_based_cap']['passed'])}")
    for v in size_cap_violations[:3]:
        print(f"    ⚠️ {v['ticker']} {v['name'][:12]} ({v['tier']}, 시총 {v['mcap_eok']:.0f}억) → {v['weight']:.2f}% > cap {v['cap']}%")
    print(f"  섹터 최대: {cs['max_sector']['actual_max_pct']}% ({cs['max_sector']['actual_max_sector']}, 한도 {max_sector}%) {pass_str(cs['max_sector']['passed'])}")
    if cs['max_annual_volatility']['estimated_pct'] is not None:
        print(f"  변동성 추정: {cs['max_annual_volatility']['estimated_pct']}% (한도 {max_vol}%) {pass_str(cs['max_annual_volatility']['passed'])}")
    print(f"  N종목: {len(holdings)} / HHI: {diversification['hhi']}")


if __name__ == "__main__":
    main()
