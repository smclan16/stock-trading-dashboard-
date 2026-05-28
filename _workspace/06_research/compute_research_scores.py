#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6단계 종목별 6축 매력도 점수 자동 산출.

산식 (총 100점):
  펀더멘털 35 = 밸류 12 + 퀄리티 13 + 성장 10
  모멘텀·리비전 25 = 목표가↑ 9 + ROE↑ 8 + 거래대금↑ 8
  테마 적합도 15 = intensity × multi_boost × alloc_boost (정규화)
  이벤트·Catalyst 15 = 네이버 뉴스↑ 8 + DART 긍정 4 + 수주 3
  리스크역수 10 = (100 − risk_score) / 10

데이터 재활용 (추가 API 호출 최소화):
  · universe.json — 5팩터 z-score (밸류·퀄리티·목표가↑·ROE↑·거래대금↑)
  · matching_matrix.json — 매칭 + intensity + uncovered
  · ideas.json — 아이디어별 allocation_pct
  · DartDisclosure 캐시 — 긍정·부정 공시 빈도

신규 호출:
  · WeakSignals.naver_news_growth(종목명) — 종목별 (소량, ~5분)
  · KRXMarket.macro_beta() — uncovered 종목만 (default_picks 필터)

산출:
  · ranking.json — Top-N 우선순위
  · ranking.md — 사람 읽기용
  · research_scores.json — 전 종목 6축 raw
  · ideas.json 갱신 — default_picks.picks 리스트
"""
import sys, os, json, math, datetime, time, statistics, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
UNIVERSE_PATH = os.path.join(WS, "03_universe", "universe.json")
IDEAS_PATH = os.path.join(WS, "04_ideas", "ideas.json")
MATRIX_PATH = os.path.join(WS, "05_matching", "matching_matrix.json")
META_PATH = os.path.join(WS, "05_matching", "company_meta.json")
OUT_SCORES = os.path.join(HERE, "research_scores.json")
OUT_RANKING_JSON = os.path.join(HERE, "ranking.json")
OUT_RANKING_MD = os.path.join(HERE, "ranking.md")

# 6축 가중치 (총 100)
W_VALUE = 12; W_QUALITY = 13; W_GROWTH = 10           # 펀더 35
W_TP_REV = 9; W_ROE_REV = 8; W_TURNOVER = 8           # 모멘텀 25
W_THEME = 15                                          # 테마 15
W_NEWS = 8; W_DART_POS = 4; W_DART_CONTRACT = 3       # Catalyst 15
W_RISK_INV = 10                                       # 리스크역수 10

INTENSITY_WEIGHT = {"direct": 1.0, "indirect": 0.7, "value_chain": 0.4, "perception": 0.3}

# DART 부정 카테고리별 리스크 가중치 (DartDisclosure.NEGATIVE_CATS와 일치)
RISK_CAT_WEIGHTS = {
    "embezzlement": 30,       # 횡령·배임
    "default_risk": 30,       # 부도·회생·법정관리
    "delisting_risk": 25,     # 상장폐지·관리·투자환기
    "capital_reduction": 20,  # 감자
    "lawsuit": 15,            # 소송·분쟁
    "convertible_recall": 10, # 사채 만기전 취득
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def normalize_z_to_pts(z, max_pts, z_cap=2.0):
    """z-score를 [0, max_pts]로 선형 매핑. z=0 → max_pts/2, z=+z_cap → max_pts."""
    if z is None:
        return max_pts / 2  # 중간값 (정보 부족)
    z = clamp(z, -z_cap, z_cap)
    return (z + z_cap) / (2 * z_cap) * max_pts


def score_fundamental(univ_entry):
    """펀더멘털 35점 = 밸류 12 + 퀄리티 13 + 성장 10."""
    s = univ_entry.get("scores") or {}
    m = univ_entry.get("metrics") or {}
    val = normalize_z_to_pts(s.get("value"), W_VALUE)
    qual = normalize_z_to_pts(s.get("quality"), W_QUALITY)
    # 성장: rev_yoy, opinc_yoy 평균 → z (universe 내 상대)
    rev = m.get("rev_yoy"); opi = m.get("opinc_yoy")
    if rev is not None and opi is not None:
        growth_raw = (rev + opi) / 2.0
    elif rev is not None:
        growth_raw = rev
    elif opi is not None:
        growth_raw = opi
    else:
        growth_raw = None
    return val, qual, growth_raw  # growth_raw는 모든 종목 z-score 후 환산


def score_momentum(univ_entry):
    """모멘텀·리비전 25점."""
    s = univ_entry.get("scores") or {}
    tp = normalize_z_to_pts(s.get("tp_rev"), W_TP_REV)
    roe = normalize_z_to_pts(s.get("roe_rev"), W_ROE_REV)
    tov = normalize_z_to_pts(s.get("turnover"), W_TURNOVER)
    return tp, roe, tov


def score_theme(ticker, matching_matrix, ideas_alloc):
    """테마 적합도 15점. raw_theme = max(intensity_weight) × multi_boost × sqrt(alloc/10)."""
    matches = []
    for idea in matching_matrix["matrix"]:
        for mt in idea.get("matched_tickers", []):
            if mt["ticker"] == ticker:
                matches.append({"idea_id": idea["idea_id"], "intensity": mt.get("intensity", "indirect")})
                break
    if not matches:
        return 0.0, []  # uncovered
    max_int = max(INTENSITY_WEIGHT.get(m["intensity"], 0.3) for m in matches)
    n = len(matches)
    multi_boost = min(1.5, 1 + 0.1 * (n - 1))
    alloc_sum = sum(ideas_alloc.get(str(m["idea_id"]), 0) for m in matches)
    alloc_boost = math.sqrt(max(0.1, alloc_sum / 10.0))
    raw = max_int * multi_boost * alloc_boost
    return raw, matches


def naver_news_growths(tickers_with_names, ws):
    """종목명별 네이버 뉴스 30d vs 180d 증가율(%). 네이버 키 없거나 실패 종목은 None."""
    out = {}
    for t, name in tickers_with_names:
        try:
            r = ws.naver_news_growth(name, recent_days=30, base_days=180, max_pages=2)
            if "error" in r:
                out[t] = None
            else:
                out[t] = r.get("growth_pct")
        except Exception:
            out[t] = None
        time.sleep(0.15)
    return out


def score_catalyst(ticker, name, news_growth_pct, dart_per_corp):
    """Catalyst 15점."""
    # 네이버 뉴스 mention 증가율 → 8점 (clip -100~+300)
    if news_growth_pct is None:
        news_pts = W_NEWS / 2  # 정보 부족
    else:
        g = clamp(news_growth_pct, -100, 300)
        news_pts = (g + 100) / 400 * W_NEWS

    # DART 긍정 카테고리: 자사주 취득/소각, 기업가치 제고, m_and_a
    corp_data = dart_per_corp.get(ticker, {}).get("categories", {})
    pos_cnt = (corp_data.get("treasury_acq", {}).get("recent", 0)
               + corp_data.get("treasury_burn", {}).get("recent", 0)
               + corp_data.get("value_up", {}).get("recent", 0)
               + corp_data.get("m_and_a", {}).get("recent", 0))
    pos_pts = min(W_DART_POS, pos_cnt / 5 * W_DART_POS)

    # 단일판매·공급계약 수주
    contract_cnt = corp_data.get("contract", {}).get("recent", 0)
    contract_pts = min(W_DART_CONTRACT, contract_cnt / 3 * W_DART_CONTRACT)

    return news_pts, pos_pts, contract_pts


def score_risk_inverse(ticker, dart_per_corp):
    """리스크역수 10점. risk_score(0~100) 계산 → 역수로 매핑.
    DartDisclosure.NEGATIVE_CATS 분류 결과 활용 (60일 윈도우 base 카운트)."""
    corp_data = dart_per_corp.get(ticker, {}).get("categories", {})
    rs = 0
    triggered = []
    for cat, weight in RISK_CAT_WEIGHTS.items():
        cnt = corp_data.get(cat, {}).get("base", 0)
        if cnt > 0:
            # 1건 발생 시 weight, 2건째부터 추가 +50% (다중 발생 가중)
            risk_add = weight + (cnt - 1) * weight * 0.5
            rs += risk_add
            triggered.append(f"{cat}×{cnt}({risk_add:.0f})")
    rs = min(100, rs)
    risk_inv_pts = (100 - rs) / 100 * W_RISK_INV
    return risk_inv_pts, rs, triggered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-news", action="store_true", help="네이버 뉴스 catalyst 건너뜀(시간 절약)")
    ap.add_argument("--skip-dart", action="store_true", help="DART 공시 catalyst 건너뜀")
    ap.add_argument("--skip-beta", action="store_true", help="매크로 베타 건너뜀(uncovered 후보 미산출)")
    ap.add_argument("--default-picks-n", type=int, default=4, help="default_picks 선정 종목 수")
    ap.add_argument("--top-n", type=int, default=15, help="ranking.json 상위 N")
    args = ap.parse_args()

    print("[1/6] 입력 로드…")
    univ = json.load(open(UNIVERSE_PATH, encoding="utf-8"))
    universe = univ.get("universe", univ)  # dict 또는 list
    ideas = json.load(open(IDEAS_PATH, encoding="utf-8"))
    matching = json.load(open(MATRIX_PATH, encoding="utf-8"))
    company_meta = {x["ticker"]: x for x in json.load(open(META_PATH, encoding="utf-8"))["companies"]}
    ideas_alloc = ideas.get("allocation_weights", {}).get("by_idea", {})
    print(f"  universe {len(universe)}종목 / ideas {len(ideas['ideas'])}테마 / matching {matching['n_ideas']}테마")

    # uncovered 티커
    uncovered_list = matching.get("uncovered_tickers", {}).get("list_flat", [])
    matched_set = set()
    for idea in matching["matrix"]:
        for mt in idea.get("matched_tickers", []):
            matched_set.add(mt["ticker"])
    print(f"  매칭 {len(matched_set)}종목 / uncovered {len(uncovered_list)}종목")

    # 모든 종목에 대해 6축 산출
    print("\n[2/6] 펀더멘털·모멘텀 점수 산출…")
    rows = []
    for e in universe:
        t = e["ticker"]
        val, qual, growth_raw = score_fundamental(e)
        tp, roe_rev, tov = score_momentum(e)
        theme_raw, theme_matches = score_theme(t, matching, ideas_alloc)
        rows.append({
            "ticker": t, "name": e["name"], "sector": e.get("sector"),
            "val_pts": val, "qual_pts": qual, "growth_raw": growth_raw,
            "tp_pts": tp, "roe_rev_pts": roe_rev, "tov_pts": tov,
            "theme_raw": theme_raw, "theme_matches": theme_matches,
        })

    # 성장 raw → z-score → 점수 환산
    growth_vals = [r["growth_raw"] for r in rows if r["growth_raw"] is not None]
    if growth_vals:
        gm = statistics.mean(growth_vals)
        gsd = statistics.pstdev(growth_vals) or 1e-9
    else:
        gm, gsd = 0, 1
    for r in rows:
        if r["growth_raw"] is not None:
            z = (r["growth_raw"] - gm) / gsd
            r["growth_pts"] = normalize_z_to_pts(z, W_GROWTH)
        else:
            r["growth_pts"] = W_GROWTH / 2

    # 테마 raw → 정규화 (max=15)
    max_theme = max((r["theme_raw"] for r in rows), default=1)
    for r in rows:
        r["theme_pts"] = (r["theme_raw"] / max_theme * W_THEME) if max_theme > 0 else 0

    # Catalyst 데이터 수집
    if not args.skip_dart:
        print("\n[3/6] DART 공시 빈도 수집 (30d vs 60d)…")
        try:
            dart = datasource.DartDisclosure()
            dart_per_corp = dart.disclosures_by_corp(recent_days=30, base_days=60)
            print(f"  {len(dart_per_corp)} 회사 DART 공시 데이터")
        except Exception as e:
            print(f"  ⚠️ DART 수집 실패: {e}"); dart_per_corp = {}
    else:
        print("\n[3/6] DART 공시 (--skip-dart) 건너뜀"); dart_per_corp = {}

    if not args.skip_news:
        print("\n[4/6] 네이버 뉴스 mention 수집 (종목명별)…")
        ws_signals = datasource.WeakSignals()
        if not (ws_signals.naver_id and ws_signals.naver_secret):
            print("  ⚠️ NAVER 키 없음, news_growth_pct=None 처리")
            news_growth = {r["ticker"]: None for r in rows}
        else:
            tickers_names = [(r["ticker"], r["name"]) for r in rows]
            news_growth = naver_news_growths(tickers_names, ws_signals)
            ok = sum(1 for v in news_growth.values() if v is not None)
            print(f"  네이버 뉴스 수집 완료: {ok}/{len(rows)}")
    else:
        print("\n[4/6] 네이버 뉴스 (--skip-news) 건너뜀")
        news_growth = {r["ticker"]: None for r in rows}

    print("\n[5/6] Catalyst·리스크역수 점수 산출…")
    for r in rows:
        news_pts, pos_pts, contract_pts = score_catalyst(r["ticker"], r["name"],
                                                          news_growth.get(r["ticker"]), dart_per_corp)
        risk_inv_pts, rs, triggered = score_risk_inverse(r["ticker"], dart_per_corp)
        r["news_pts"] = news_pts
        r["dart_pos_pts"] = pos_pts
        r["dart_contract_pts"] = contract_pts
        r["risk_inv_pts"] = risk_inv_pts
        r["risk_score"] = rs
        r["risk_triggered"] = triggered
        r["news_growth_pct"] = news_growth.get(r["ticker"])

        # 6축 합산
        r["fundamental"] = round(r["val_pts"] + r["qual_pts"] + r["growth_pts"], 2)
        r["momentum"] = round(r["tp_pts"] + r["roe_rev_pts"] + r["tov_pts"], 2)
        r["theme"] = round(r["theme_pts"], 2)
        r["catalyst"] = round(news_pts + pos_pts + contract_pts, 2)
        r["risk_inv"] = round(risk_inv_pts, 2)
        r["total_score"] = round(r["fundamental"] + r["momentum"] + r["theme"]
                                  + r["catalyst"] + r["risk_inv"], 2)
        r["is_uncovered"] = r["ticker"] in uncovered_list

    # 매크로 베타 (uncovered만)
    if not args.skip_beta and uncovered_list:
        print(f"\n[6/6] 매크로 베타 산출 (uncovered {len(uncovered_list)}종목)…")
        krx = datasource.KRXMarket()
        asof = krx.latest_trading_date()
        betas = krx.macro_beta(uncovered_list, asof, weeks=26)
        print(f"  베타 계산 완료: {len(betas)}/{len(uncovered_list)}")
    else:
        print("\n[6/6] 매크로 베타 (--skip-beta) 건너뜀")
        betas = {}

    for r in rows:
        r["macro_beta"] = round(betas.get(r["ticker"]), 3) if r["ticker"] in betas else None

    # 산출물 저장
    rows.sort(key=lambda x: -x["total_score"])
    print(f"\n[저장] {OUT_SCORES}, {OUT_RANKING_JSON}, {OUT_RANKING_MD}")

    json.dump({"as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "n_scored": len(rows), "rows": rows},
              open(OUT_SCORES, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ranking
    ranking_top = rows[:args.top_n]
    json.dump({"as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "top_n": args.top_n,
               "ranking": [{"rank": i+1, "ticker": r["ticker"], "name": r["name"], "sector": r["sector"],
                            "total_score": r["total_score"],
                            "breakdown": {"fundamental": r["fundamental"], "momentum": r["momentum"],
                                          "theme": r["theme"], "catalyst": r["catalyst"],
                                          "risk_inv": r["risk_inv"]},
                            "is_uncovered": r["is_uncovered"], "macro_beta": r["macro_beta"]}
                           for i, r in enumerate(ranking_top)]},
              open(OUT_RANKING_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # markdown
    L = [f"# 6단계 종목 매력도 랭킹 (Top {args.top_n})\n",
         f"**as_of**: {datetime.datetime.now().strftime('%Y-%m-%d')}\n",
         "산식: 펀더 35 + 모멘텀 25 + 테마 15 + Catalyst 15 + 리스크역수 10 = **100점**\n",
         "| 순위 | 티커 | 종목명 | 섹터 | 펀더 | 모멘 | 테마 | Cat | 리스역 | **총점** | β | uncov |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranking_top, 1):
        beta = f"{r['macro_beta']:.2f}" if r['macro_beta'] is not None else "-"
        uflag = "✓" if r['is_uncovered'] else ""
        L.append(f"| {i} | {r['ticker']} | {r['name'][:12]} | {r['sector'][:6] if r['sector'] else '-'} | "
                  f"{r['fundamental']:.1f} | {r['momentum']:.1f} | {r['theme']:.1f} | {r['catalyst']:.1f} | "
                  f"{r['risk_inv']:.1f} | **{r['total_score']:.1f}** | {beta} | {uflag} |")
    open(OUT_RANKING_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")

    # default_picks 선정 (uncovered + 매크로 베타 < 0.7 + 매력도 상위)
    print(f"\n[default_picks] uncovered 풀에서 매크로 베타 < 0.7 필터 → 상위 {args.default_picks_n}종목 선정…")
    uncov_rows = [r for r in rows if r["is_uncovered"]]
    cands = [r for r in uncov_rows if r["macro_beta"] is not None and r["macro_beta"] < 0.7]
    cands.sort(key=lambda x: -x["total_score"])
    picks = cands[:args.default_picks_n]
    if picks:
        equal_w = ideas["default_picks"]["allocation_pct"] / len(picks)
        picks_data = [{"ticker": p["ticker"], "name": p["name"],
                       "attractiveness": round(p["total_score"], 1),
                       "macro_beta": p["macro_beta"],
                       "catalyst": f"news_growth {p.get('news_growth_pct')}% / DART pos+contract pts {p['dart_pos_pts']+p['dart_contract_pts']:.1f}",
                       "weight_pct": round(equal_w, 2)}
                      for p in picks]
        ideas["default_picks"]["status"] = "FILLED"
        ideas["default_picks"]["picks"] = picks_data
        json.dump(ideas, open(IDEAS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  ideas.json default_picks.picks 갱신: {len(picks)}종목 (각 {equal_w:.1f}%)")
        for p in picks_data:
            print(f"    {p['ticker']} {p['name'][:14]:<14} β={p['macro_beta']:.2f} 매력도={p['attractiveness']:.1f} {p['weight_pct']}%")
    else:
        print("  ⚠️ 후보 종목 없음 (베타 필터 통과 0종목 또는 --skip-beta)")

    print("\n=== Top 10 ===")
    for r in rows[:10]:
        beta = f"β={r['macro_beta']:.2f}" if r['macro_beta'] is not None else "β=-"
        print(f"  {r['ticker']} {r['name'][:14]:<14} 펀더={r['fundamental']:>5.1f} 모멘={r['momentum']:>5.1f} 테마={r['theme']:>5.1f} Cat={r['catalyst']:>4.1f} 리스역={r['risk_inv']:>4.1f} 총={r['total_score']:>5.1f} {beta}")


if __name__ == "__main__":
    main()
