#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""파이프라인 단계 간 논리 정합성 자동 검증.

검증 항목:
  A. 자산배분(2) ↔ 포트폴리오 비중 합(7)
  B. 투자성향 제약(1) ↔ 포트폴리오 실제 비중(7)  ← 강제
  C. 아이디어 비중(4) ↔ 종목 비중 합(7)         ← 핵심 정합성
  D. default_picks 매크로 베타(1·6 ↔ 7)
  E. 매칭 강도(5) ↔ 비중 분배 일관성
  F. 6단계 매력도 ↔ 7단계 비중 상관성
  G. 데이터 신선도 (각 단계 as_of)
  H. 레짐(2) ↔ 팩터 가중치(3·4)

PASS/WARN/FAIL 판정 → logic_validation_report.{md,json}
"""
import os, sys, json, datetime, math
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)

PATH = {
    "constraints": os.path.join(WS, "01_profile", "constraints.json"),
    "allocation":  os.path.join(WS, "02_macro",   "allocation.json"),
    "universe":    os.path.join(WS, "03_universe", "universe.json"),
    "ideas":       os.path.join(WS, "04_ideas",   "ideas.json"),
    "matching":    os.path.join(WS, "05_matching", "matching_matrix.json"),
    "research":    os.path.join(WS, "06_research", "research_scores.json"),
    "portfolio":   os.path.join(WS, "07_portfolio", "portfolio.json"),
}
OUT_JSON = os.path.join(HERE, "logic_validation_report.json")
OUT_MD   = os.path.join(HERE, "logic_validation_report.md")

# 임계값
THRESH_EQUITY_DIFF = 1.0       # 자산배분 vs 포트폴리오 합 (%p)
THRESH_IDEA_DIFF   = 1.5       # 아이디어 비중 vs 실제 (%p, 단일/섹터 한도 영향 고려)
THRESH_BETA        = 0.7       # default_picks 매크로 베타 한도
THRESH_RHO         = 0.3       # 매력도-비중 Spearman 최소
FRESH_DAYS = {"allocation": 7, "universe": 30, "ideas": 90, "matching": 30, "research": 30, "portfolio": 7}


def load(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def parse_date(s, fmt_list=("%Y%m%d", "%Y-%m-%d")):
    if not s:
        return None
    s = str(s)[:10]
    for f in fmt_list:
        try:
            return datetime.datetime.strptime(s.replace("-","")[:8] if f == "%Y%m%d" else s, f).date()
        except ValueError:
            continue
    return None


def spearman_corr(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    def rank(vals):
        s = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0] * len(vals)
        for i, idx in enumerate(s):
            r[idx] = i + 1
        return r
    rx, ry = rank(xs), rank(ys)
    d2 = sum((rx[i] - ry[i])**2 for i in range(n))
    return 1 - (6 * d2) / (n * (n*n - 1))


def main():
    print("[검증 시작] 파이프라인 1·2·4·5·6·7단계 정합성")
    docs = {k: load(p) for k, p in PATH.items()}
    missing = [k for k, v in docs.items() if v is None]
    if missing:
        print(f"❌ 필수 산출물 누락: {missing}")
        sys.exit(1)

    constraints = docs["constraints"]
    allocation  = docs["allocation"]
    ideas       = docs["ideas"]
    matching    = docs["matching"]
    research    = docs["research"]
    portfolio   = docs["portfolio"]

    holdings = portfolio.get("holdings", [])
    n_h = len(holdings)
    print(f"  포트폴리오 {n_h}종목 / 투자자={portfolio.get('investor_type')} / equity={portfolio.get('equity_pct')}%")

    results = []

    # ── A: 자산배분 ↔ 포트폴리오 합
    sum_w = sum(h["weight_pct"] for h in holdings)
    sum_cw = sum(h["capital_weight_pct"] for h in holdings)
    eq = allocation.get("equity_pct", 100)
    diff_total = abs(sum_w - 100)
    diff_capital = abs(sum_cw - eq)
    a_pass = (diff_total <= 0.5) and (diff_capital <= THRESH_EQUITY_DIFF)
    a_warn = (not a_pass) and (diff_total <= 1.5) and (diff_capital <= 2.0)
    results.append({
        "id": "A", "name": "자산배분 ↔ 포트폴리오 합",
        "verdict": "PASS" if a_pass else ("WARN" if a_warn else "FAIL"),
        "detail": f"포트폴리오 합 {sum_w:.2f}% (기준 100%, 오차 {diff_total:.2f}%p) / 자본대비 합 {sum_cw:.2f}% vs equity {eq}% (오차 {diff_capital:.2f}%p)",
    })

    # ── B: 투자성향 제약 ↔ 포트폴리오 실제 비중
    max_single = constraints.get("max_single_stock_pct", 100)
    max_sector = constraints.get("max_sector_pct", 100)
    max_vol = constraints.get("max_annual_volatility", 100)
    excluded_t = set(constraints.get("excluded_tickers", []) or [])
    excluded_s = set(constraints.get("excluded_sectors", []) or [])

    h_max = max((h["weight_pct"] for h in holdings), default=0)
    sec_dist = defaultdict(float)
    for h in holdings:
        sec_dist[h.get("sector") or "기타"] += h["weight_pct"]
    sec_max = max(sec_dist.values()) if sec_dist else 0
    sec_max_name = max(sec_dist, key=sec_dist.get) if sec_dist else None

    excluded_in_holdings = [h["ticker"] for h in holdings if h["ticker"] in excluded_t]
    excluded_sec_in_holdings = [h["ticker"] for h in holdings if h.get("sector") in excluded_s]

    vol_est = portfolio.get("constraint_checks", {}).get("max_annual_volatility", {}).get("estimated_pct")

    results.append({"id": "B-1", "name": "단일 종목 한도",
                    "verdict": "PASS" if h_max <= max_single + 0.01 else "FAIL",
                    "detail": f"최대 {h_max:.2f}% / 한도 {max_single}%"})
    results.append({"id": "B-2", "name": "섹터 한도",
                    "verdict": "PASS" if sec_max <= max_sector + 0.01 else "FAIL",
                    "detail": f"최대 {sec_max:.2f}% ({sec_max_name}) / 한도 {max_sector}%"})
    results.append({"id": "B-3", "name": "제외 종목/섹터",
                    "verdict": "PASS" if not (excluded_in_holdings or excluded_sec_in_holdings) else "FAIL",
                    "detail": f"제외 종목 위반 {len(excluded_in_holdings)} / 제외 섹터 위반 {len(excluded_sec_in_holdings)}"})
    if vol_est is not None:
        vol_pass = vol_est <= max_vol
        results.append({"id": "B-4", "name": "포트폴리오 변동성 한도",
                        "verdict": "PASS" if vol_pass else "WARN",
                        "detail": f"추정 {vol_est:.2f}% / 한도 {max_vol}% (β 단순화 한계)"})

    # ── C: 아이디어 비중 ↔ 종목 비중 합
    ideas_alloc = ideas.get("allocation_weights", {}).get("by_idea", {})
    # holdings의 source contribution을 idea_id별로 집계
    actual_by_idea = defaultdict(float)
    default_actual = 0
    for h in holdings:
        srcs = h.get("sources", [])
        if not srcs:
            continue
        # weight_pct를 source 기여도 비례로 재분배 (단일/섹터 한도 강제로 weight 변화 후 source 비례 유지 가정)
        src_contributions = [(s.get("idea_id"), s.get("contribution_pct", 0)) for s in srcs if s.get("idea_id") is not None]
        default_contrib = sum(s.get("contribution_pct", 0) for s in srcs if s.get("source") == "default_picks")
        contrib_sum = sum(c for _, c in src_contributions) + default_contrib
        if contrib_sum <= 0:
            continue
        for idea_id, contrib in src_contributions:
            share = contrib / contrib_sum
            actual_by_idea[idea_id] += h["weight_pct"] * share
        if default_contrib > 0:
            share = default_contrib / contrib_sum
            default_actual += h["weight_pct"] * share

    idea_diffs = []
    for idea_id_str, expected in ideas_alloc.items():
        if expected <= 0:
            continue
        idea_id = int(idea_id_str)
        actual = actual_by_idea.get(idea_id, 0)
        diff = abs(expected - actual)
        idea_diffs.append({"idea_id": idea_id, "expected": expected, "actual": round(actual, 3), "diff": round(diff, 3)})
    max_idea_diff = max((d["diff"] for d in idea_diffs), default=0)
    c_verdict = "PASS" if max_idea_diff <= THRESH_IDEA_DIFF else ("WARN" if max_idea_diff <= 3.0 else "FAIL")
    over = [d for d in idea_diffs if d["diff"] > THRESH_IDEA_DIFF]
    over_strs = []
    for d in over[:3]:
        over_strs.append("#{0}={1:.2f}→{2:.2f}".format(d["idea_id"], d["expected"], d["actual"]))
    over_detail = (" (" + ", ".join(over_strs) + ")") if over_strs else ""
    results.append({"id": "C", "name": "아이디어 비중 정합", "verdict": c_verdict,
                    "detail": f"최대 차이 {max_idea_diff:.2f}%p / 임계 {THRESH_IDEA_DIFF}%p / 위반 {len(over)}개{over_detail}"})
    # default_picks 합 비교
    expected_default = ideas.get("default_picks", {}).get("allocation_pct", 0)
    diff_default = abs(default_actual - expected_default)
    results.append({"id": "C-default", "name": "default_picks 비중 합",
                    "verdict": "PASS" if diff_default <= 0.5 else "WARN",
                    "detail": f"expected {expected_default}% / actual {default_actual:.2f}% (오차 {diff_default:.2f}%p)"})

    # ── D: default_picks 매크로 베타
    picks = ideas.get("default_picks", {}).get("picks", []) or []
    bad_beta = [p for p in picks if p.get("macro_beta", 99) >= THRESH_BETA]
    results.append({"id": "D", "name": "default_picks 매크로 베타",
                    "verdict": "PASS" if not bad_beta else "FAIL",
                    "detail": (f"{len(picks)}종목 모두 β<{THRESH_BETA}" if not bad_beta
                                else f"β≥{THRESH_BETA} 위반 {len(bad_beta)}종목: " + ", ".join(f"{p['ticker']}({p['macro_beta']})" for p in bad_beta))})

    # ── E: 매칭 강도 ↔ 비중 분배 일관성
    # 같은 아이디어 내에서 direct intensity 종목이 indirect보다 평균 weight ≥ 같아야
    violations = 0
    for idea in matching["matrix"]:
        idea_id = idea["idea_id"]
        match_list = idea.get("matched_tickers", []) or []
        by_int = defaultdict(list)
        for mt in match_list:
            for h in holdings:
                if h["ticker"] == mt["ticker"] and idea_id in h.get("matched_ideas", []):
                    by_int[mt["intensity"]].append(h["weight_pct"])
                    break
        # direct 평균 vs indirect 평균
        def avg(xs):
            return sum(xs)/len(xs) if xs else 0
        if by_int.get("direct") and by_int.get("indirect"):
            if avg(by_int["direct"]) < avg(by_int["indirect"]) - 0.5:
                violations += 1
    results.append({"id": "E", "name": "매칭 강도-비중 일관성",
                    "verdict": "PASS" if violations == 0 else "WARN",
                    "detail": f"direct < indirect 평균 비중 위반: {violations}개 아이디어"})

    # ── F: 매력도-비중 상관 (Spearman)
    pairs = [(h["attractiveness"] or 0, h["weight_pct"]) for h in holdings if h["attractiveness"] is not None]
    if len(pairs) >= 5:
        rho = spearman_corr([p[0] for p in pairs], [p[1] for p in pairs])
        results.append({"id": "F", "name": "매력도-비중 상관",
                        "verdict": "PASS" if rho >= THRESH_RHO else "WARN",
                        "detail": f"Spearman ρ = {rho:.3f} / 임계 {THRESH_RHO}"})

    # ── G: 데이터 신선도
    today = datetime.date.today()
    fresh_issues = []
    for stage, days_limit in FRESH_DAYS.items():
        doc = docs.get(stage)
        if not doc:
            continue
        # as_of 또는 updated_at 추출
        ts = (doc.get("as_of") if isinstance(doc, dict) else None) or (doc.get("meta", {}).get("as_of") if isinstance(doc, dict) and isinstance(doc.get("meta"), dict) else None)
        d = parse_date(ts)
        if d:
            age = (today - d).days
            if age > days_limit:
                fresh_issues.append(f"{stage}={age}d (한도 {days_limit}d)")
    results.append({"id": "G", "name": "데이터 신선도",
                    "verdict": "PASS" if not fresh_issues else "WARN",
                    "detail": "모든 단계 신선" if not fresh_issues else " / ".join(fresh_issues)})

    # ── H: 레짐 ↔ 팩터 가중치
    regime = allocation.get("regime", "")
    # 3단계 universe 5팩터 레짐 가중치 직접 검증
    univ = docs.get("universe") or {}
    factor_w = (univ.get("meta", {}) or {}).get("weights", {}) if isinstance(univ, dict) else {}
    momentum_w = (factor_w.get("tp_rev", 0) + factor_w.get("roe_rev", 0) + factor_w.get("turnover", 0))
    h_pass = True; h_msg = f"레짐={regime} / 모멘텀계열 합={momentum_w:.0%}"
    if regime == "위험회피" and momentum_w > 0.30:
        h_pass = False; h_msg += f" → 위험회피 레짐에서 모멘텀 30% 초과"
    results.append({"id": "H", "name": "레짐 ↔ 팩터 가중치",
                    "verdict": "PASS" if h_pass else "WARN",
                    "detail": h_msg})

    # 종합 판정
    fails = [r for r in results if r["verdict"] == "FAIL"]
    warns = [r for r in results if r["verdict"] == "WARN"]
    overall = "FAIL" if fails else ("WARN" if warns else "PASS")

    print(f"\n{'='*70}")
    print(f"{'#':<8s} {'항목':<28s} {'판정':<6s} 세부")
    print('-'*70)
    for r in results:
        emoji = {"PASS":"✅", "WARN":"⚠️ ", "FAIL":"❌"}[r["verdict"]]
        print(f"{r['id']:<8s} {r['name'][:26]:<28s} {emoji}{r['verdict']:<5s} {r['detail'][:90]}")
    print('='*70)
    print(f"종합 판정: {overall} (FAIL {len(fails)}, WARN {len(warns)})")

    # 저장
    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall": overall,
        "n_fail": len(fails),
        "n_warn": len(warns),
        "results": results,
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    L = [f"# 논리 정합성 검증 리포트 ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})\n",
         f"**종합 판정:** {overall} (FAIL {len(fails)} / WARN {len(warns)})\n",
         "| # | 검증 항목 | 판정 | 세부 |", "|---|---|---|---|"]
    for r in results:
        emoji = {"PASS":"✅ PASS", "WARN":"⚠️ WARN", "FAIL":"❌ FAIL"}[r["verdict"]]
        L.append(f"| {r['id']} | {r['name']} | {emoji} | {r['detail']} |")
    if fails:
        L.append("\n## 조치 필요 (FAIL)\n")
        for r in fails:
            L.append(f"- **{r['id']} {r['name']}**: {r['detail']}")
    if warns:
        L.append("\n## 검토 권고 (WARN)\n")
        for r in warns:
            L.append(f"- {r['id']} {r['name']}: {r['detail']}")
    L.append(f"\n*임계: 자산배분 오차 ±{THRESH_EQUITY_DIFF}%p / 아이디어 비중 ±{THRESH_IDEA_DIFF}%p / default_picks β<{THRESH_BETA}*")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n저장: {OUT_JSON}, {OUT_MD}")


if __name__ == "__main__":
    main()
