#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""유니버스 스크리닝 — 전체(FULL) LIVE 버전 · 5팩터 컨센서스리비전 모델.

데이터(모두 LIVE):
  · KRX Open API : 코스피/코스닥 전종목 마스터·시총·거래대금·시장구분·소속부(관리종목)·가격
  · FnSpace      : 컨센서스 존재(필터), account(재무), consensus-price(목표주가괴리율),
                   consensus-forward(Fwd 12M ROE — EPS Fwd.12M 직접 항목이 null이라 ROE로 대체)
  · Open DART    : 종목별 업종 → 광의 섹터 (섹터중립 z-score)

요청 스펙:
  하드필터 ① 거래정지 제외  ② 관리종목 제외  ③ 20일평균거래대금 1억원 미만 제외
          ④ 애널리스트 컨센서스 존재  (코스피/코스닥 보통주)
  5팩터 섹터중립 z-score(±5σ) + 레짐 가중치 → Top 100
      - 밸류           : 기존 그대로 (−PER, −PBR, −EV/EBITDA 평균)
      - 퀄리티         : 기존 그대로 (ROE + ROA − 부채/100 + 영익률)
      - 목표주가↑(1M)  : 1M 전 대비 컨센서스 목표주가 변화율(%)
                         · 종가 × (1 + 목표주가괴리율/100) 으로 목표주가 유도
      - Fwd ROE↑(1M)   : Fwd 12M ROE 1M 변화율(%)  *(EPS Fwd.12M 대체 — FnSpace null)*
      - 거래대금↑      : 최근 1M 평균거래대금 / 3M前 1M 평균거래대금 − 1 (%)

비용 관리: FnSpace 종목당 과금 → KRX 하드필터(무료) 통과 후 시총 상위 MAX_FNSPACE만 조회
"""
import sys, os, json, statistics, datetime
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
TOPN = 100
MAX_FNSPACE = 1000     # 컨센서스/재무 조회 상한(시총 상위) — 비용/시간 경계
MIN_TURNOVER_EOK = 30.0 # 20일 평균 거래대금 30억원 (스몰캡 노이즈 제거)

# v5 — 대형주 누락 보정
LARGE_CAP_AUTO_INCLUDE = 20     # 시총 상위 N종 자동 포함 (5팩터 점수 무관, 컨센서스 보유 필수)
LARGECAP_SCORE_BONUS_TIERS = [   # (시총_조원, 점수 보너스)
    (30, 0.50),  # 시총 ≥ 30조 (삼성전자, SK하이닉스, LG에너지솔루션 등)
    (10, 0.30),  # 시총 ≥ 10조
    (3, 0.15),   # 시총 ≥ 3조
]

def largecap_bonus(mcap_eok):
    """시총 기반 점수 보너스 (조원 단위 환산)"""
    if not mcap_eok:
        return 0.0
    mcap_trillion = mcap_eok / 10000.0  # 억원 → 조원
    for threshold, bonus in LARGECAP_SCORE_BONUS_TIERS:
        if mcap_trillion >= threshold:
            return bonus
    return 0.0

# 5팩터 레짐 가중치: 위험선호=리비전·거래대금 강화 / 위험회피=밸류·퀄리티 강화
REGIME_WEIGHTS = {
    "위험선호": {"value": .10, "quality": .15, "tp_rev": .25, "roe_rev": .25, "turnover": .25},
    "중립":     {"value": .20, "quality": .20, "tp_rev": .20, "roe_rev": .20, "turnover": .20},
    "위험회피": {"value": .30, "quality": .30, "tp_rev": .15, "roe_rev": .15, "turnover": .10},
}
FACTORS = ("value", "quality", "tp_rev", "roe_rev", "turnover")


def load_regime():
    p = os.path.join(WS, "02_macro", "allocation.json")
    regime = "중립"
    if os.path.exists(p):
        regime = json.load(open(p, encoding="utf-8")).get("regime", "중립")
    return regime, REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["중립"])


def load_sector_map():
    """기존 큐레이션에서 알려진 업종 매핑(부분). 없으면 '미분류'."""
    m = {}
    for fn in ["universe_200_prev.json", "universe_curated_100_prev.json", "universe.json"]:
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            try:
                for x in json.load(open(p, encoding="utf-8")):
                    if isinstance(x, dict) and x.get("ticker") and x.get("sector"):
                        m.setdefault(str(x["ticker"]), x["sector"])
            except Exception:
                pass
    return m


def gz(vals):
    v = [x for x in vals if x is not None]
    return (statistics.mean(v), (statistics.pstdev(v) or 1e-9)) if v else (0.0, 1e-9)


def winsor(x, lo=-5.0, hi=5.0):
    return max(lo, min(hi, x))


def zscore_global(rows, field):
    m, sd = gz([r.get(field) for r in rows])
    return {r["ticker"]: (0.0 if r.get(field) is None else winsor((r[field] - m) / sd)) for r in rows}


def zscore_sector(rows, field, min_n=5):
    """섹터 내 z-score. 섹터 표본<min_n 또는 미분류/기타 → 전역 폴백. ±5σ winsor."""
    gm, gsd = gz([r.get(field) for r in rows])
    bysec = defaultdict(list)
    for r in rows:
        if r.get(field) is not None:
            bysec[r.get("sector", "미분류")].append(r[field])
    stat = {}
    for s, vs in bysec.items():
        stat[s] = (statistics.mean(vs), statistics.pstdev(vs) or 1e-9) if (len(vs) >= min_n and s not in ("미분류", "기타")) else (gm, gsd)
    out = {}
    for r in rows:
        if r.get(field) is None:
            out[r["ticker"]] = 0.0
        else:
            m, sd = stat.get(r.get("sector", "미분류"), (gm, gsd))
            out[r["ticker"]] = winsor((r[field] - m) / sd)
    return out


def main():
    regime, W = load_regime()
    secmap = load_sector_map()
    krx = datasource.KRXMarket()

    asof = krx.latest_trading_date()
    print(f"KRX 기준일: {asof} | 레짐={regime} 가중치={W}")
    daily = krx.daily(asof)
    info = krx.base_info(asof)
    n_all = len(daily)
    log = {"전체(코스피+코스닥)": n_all}

    # ── 하드필터 (KRX, 무료) ─────────────────────────────
    def is_common(t):
        i = info.get(t, {}); nm = daily[t]["name"]
        return (i.get("secugrp") == "주권" and i.get("kind") == "보통주"
                and "스팩" not in nm and "기업인수목적" not in nm)
    def sect_of(t):
        return (daily[t].get("sect") or "") + "|" + (info.get(t, {}).get("sect_tp") or "")

    step = [t for t in daily if is_common(t)]
    log["보통주만(우선주/리츠/스팩/예탁/외국주권 제외)"] = len(step)
    step = [t for t in step if "관리" not in sect_of(t) and "SPAC" not in sect_of(t)]
    log["관리종목 제외"] = len(step)
    step = [t for t in step if daily[t]["volume"] not in (0, None)]
    log["거래정지 제외(거래량 0)"] = len(step)

    # 20일 평균 거래대금
    avg_to, td_dates = krx.avg_turnover_20d(asof, 20)
    step = [t for t in step if (avg_to.get(t) is not None and avg_to[t] >= MIN_TURNOVER_EOK)]
    log[f"20일평균거래대금 ≥ {MIN_TURNOVER_EOK:.0f}억"] = len(step)
    print(f"거래대금 산출 영업일 {len(td_dates)}일 | 유동성 통과 풀: {len(step)}")

    # 시총 상위로 fnspace 조회 범위 경계
    step.sort(key=lambda t: (daily[t]["mcap_eok"] or 0), reverse=True)
    capped = step[:MAX_FNSPACE]
    log[f"컨센서스 조회 대상(시총 상위 {MAX_FNSPACE})"] = len(capped)

    # 회전율 3M→1M 증가율 (KRX, 무료) — 시총 정규화로 스몰캡 노이즈 보정
    tov_growth, rec_dates, prior_dates = krx.turnover_ratio_growth_3m1m(asof, recent=20, gap=40)
    print(f"회전율(거래대금/시총) 비교: 최근1M({rec_dates[-1]}~{rec_dates[0]}) vs 3M전1M({prior_dates[-1]}~{prior_dates[0]})")

    # 1M 전 종가 (목표주가 1M 변화율 유도용) — turnover_growth_3m1m이 이미 회수한 영업일 재활용
    ref_1m = rec_dates[-1] if rec_dates else None
    price_1m_ago = {t: d["close"] for t, d in krx.daily(ref_1m).items()} if ref_1m else {}
    price_now = {t: d["close"] for t, d in daily.items()}

    # ── 컨센서스 존재 필터 (fnspace) ─────────────────────
    import pandas as pd
    con = datasource.ConsensusData(chunk=10)
    cov = {}
    for grp in datasource._chunks(capped, 10):
        df = con._get(category="consensus-earning-fiscal", code=grp, item=["E121500"],
                      consolgb="M", annualgb="A",
                      from_year=str(datetime.datetime.now().year), to_year=str(datetime.datetime.now().year),
                      kor_item_name=True)
        if df is not None and len(df) and "영업이익" in df.columns:
            df = df.copy(); df["CODE"] = df["CODE"].map(datasource._strip_a)
            for c, g in df.groupby("CODE"):
                v = pd.to_numeric(g["영업이익"], errors="coerce").dropna()
                if len(v):
                    cov[c] = True
    covered = [t for t in capped if cov.get(t)]
    log["애널리스트 컨센서스 존재"] = len(covered)
    print(f"컨센서스 보유: {len(covered)}")

    # ── 컨센서스 1M 리비전 (목표주가, Fwd 12M ROE) ────────
    rev = con.revision_1m(covered, prices_now=price_now, prices_1m_ago=price_1m_ago, lookback_days=45)
    n_tp = sum(1 for c in covered if rev.get(c, {}).get("tp_rev_1m") is not None)
    n_roe = sum(1 for c in covered if rev.get(c, {}).get("roe_fwd_rev_1m") is not None)
    print(f"리비전 수집: 목표주가 {n_tp}/{len(covered)} · Fwd ROE {n_roe}/{len(covered)}")

    # ── 재무(account) 수집 (fnspace) ─────────────────────
    fin = datasource.FinancialsData(chunk=10)
    # 확장 항목: EPS/BPS 추가(PER/PBR 산출용)
    items = dict(fin.ITEMS); items.update({"M312000": "EPS(지배,Adj.)", "M314000": "BPS(지배,Adj.)"})
    findata = {}
    yr = str(datetime.datetime.now().year - 1)
    for grp in datasource._chunks(covered, 10):
        try:
            df = fin.fs.get_data(category="account", code=grp, item=list(items),
                                 consolgb="M", annualgb="A", from_year=yr, to_year=yr, kor_item_name=True)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        df = df.copy(); df["CODE"] = df["CODE"].map(datasource._strip_a)
        km = {"ROE(지배)": "roe", "ROA": "roa", "영업이익률": "opm", "부채비율": "debt",
              "매출액증가율(YoY)": "rev_yoy", "영업이익증가율(YoY)": "opinc_yoy",
              "EV/EBITDA2": "ev_ebitda", "EPS(지배, Adj.)": "eps", "BPS(지배, Adj.)": "bps"}
        for code, g in df.groupby("CODE"):
            rec = {}
            for kor, short in km.items():
                if kor in g.columns:
                    v = pd.to_numeric(g[kor], errors="coerce").dropna()
                    rec[short] = float(v.iloc[-1]) if len(v) else None
            findata[code] = rec
    print(f"재무 수집: {len(findata)}/{len(covered)}")

    # ── 업종(섹터) 분류: Open DART induty_code → 광의 섹터 (없으면 큐레이션/미분류) ──
    import config as _cfg
    sectors = {}
    use_sector_z = False
    if _cfg.get("DART_API_KEY"):
        try:
            ind = datasource.DartSector().industry(covered)
            sectors = {t: ind.get(t, {}).get("sector", "미분류") for t in covered}
            use_sector_z = True
            from collections import Counter as _C
            print(f"DART 업종 매핑: {sum(1 for v in sectors.values() if v not in ('미분류','기타'))}/{len(covered)} "
                  f"| 분포 상위 {_C(sectors.values()).most_common(6)}")
        except Exception as e:
            print(f"DART 업종 조회 실패({type(e).__name__}) → 전역 z-score 폴백")
    if not sectors:
        sectors = {t: secmap.get(t, "미분류") for t in covered}

    # ── 팩터 raw ─────────────────────────────────────────
    rows = []
    for t in covered:
        f = findata.get(t, {})
        close = daily[t]["close"]
        per = (close / f["eps"]) if (f.get("eps") and f["eps"] > 0 and close) else None
        pbr = (close / f["bps"]) if (f.get("bps") and f["bps"] > 0 and close) else None
        ev = f.get("ev_ebitda") if (f.get("ev_ebitda") and f["ev_ebitda"] > 0) else None
        rv = rev.get(t, {})
        rows.append({
            "ticker": t, "name": daily[t]["name"], "market": daily[t]["market"],
            "sector": sectors.get(t, "미분류"),
            "mcap_eok": daily[t]["mcap_eok"], "turnover20_eok": avg_to.get(t),
            "per": per, "pbr": pbr, "ev_ebitda": ev,
            "roe": f.get("roe"), "roa": f.get("roa"), "opm": f.get("opm"), "debt": f.get("debt"),
            "tp_rev_1m": rv.get("tp_rev_1m"),
            "roe_fwd_rev_1m": rv.get("roe_fwd_rev_1m"),
            "turnover_growth_pct": tov_growth.get(t),
        })

    # 밸류 서브지표 전역표준화 후 평균 (기존 로직 유지)
    pm, psd = gz([r["per"] for r in rows])
    bm, bsd = gz([r["pbr"] for r in rows])
    em, esd = gz([r["ev_ebitda"] for r in rows])
    for r in rows:
        parts = []
        if r["per"] is not None: parts.append(-(r["per"] - pm) / psd)
        if r["pbr"] is not None: parts.append(-(r["pbr"] - bm) / bsd)
        if r["ev_ebitda"] is not None: parts.append(-(r["ev_ebitda"] - em) / esd)
        r["_value_raw"] = sum(parts) / len(parts) if parts else None
        if all(r.get(k) is not None for k in ("roe", "roa", "debt", "opm")):
            r["_quality_raw"] = r["roe"] + r["roa"] - r["debt"] / 100.0 + r["opm"]
        else:
            r["_quality_raw"] = None
        r["_tp_rev_raw"] = r["tp_rev_1m"]
        r["_roe_rev_raw"] = r["roe_fwd_rev_1m"]
        r["_turnover_raw"] = r["turnover_growth_pct"]

    zf = zscore_sector if use_sector_z else zscore_global
    zv  = zf(rows, "_value_raw");    zq  = zf(rows, "_quality_raw")
    ztp = zf(rows, "_tp_rev_raw");   zrr = zf(rows, "_roe_rev_raw")
    zto = zf(rows, "_turnover_raw")
    for r in rows:
        r["z_value"]    = round(zv[r["ticker"]], 3)
        r["z_quality"]  = round(zq[r["ticker"]], 3)
        r["z_tp_rev"]   = round(ztp[r["ticker"]], 3)
        r["z_roe_rev"]  = round(zrr[r["ticker"]], 3)
        r["z_turnover"] = round(zto[r["ticker"]], 3)
        base = (W["value"]*r["z_value"] + W["quality"]*r["z_quality"]
                + W["tp_rev"]*r["z_tp_rev"] + W["roe_rev"]*r["z_roe_rev"]
                + W["turnover"]*r["z_turnover"])
        r["largecap_bonus"] = round(largecap_bonus(r["mcap_eok"]), 3)
        r["total"] = round(base + r["largecap_bonus"], 4)

    # v5: Top 100 = 5팩터 상위 80 + 시총 상위 20 자동 포함
    ranked = sorted(rows, key=lambda r: r["total"], reverse=True)
    n_score_slot = TOPN - LARGE_CAP_AUTO_INCLUDE  # 5팩터 점수 슬롯 (80)
    # 1) 시총 상위 N종 (컨센서스 보유 = rows 안에 있는 종목)
    mcap_sorted = sorted(rows, key=lambda r: r.get("mcap_eok") or 0, reverse=True)
    mcap_top = mcap_sorted[:LARGE_CAP_AUTO_INCLUDE]
    mcap_top_tickers = set(r["ticker"] for r in mcap_top)
    # 2) 점수 상위 (시총 자동 포함분 제외하고 n_score_slot개)
    score_picks = []
    for r in ranked:
        if r["ticker"] in mcap_top_tickers:
            continue
        score_picks.append(r)
        if len(score_picks) >= n_score_slot:
            break
    # 3) 합치고 점수 순 정렬
    top = sorted(mcap_top + score_picks, key=lambda r: r["total"], reverse=True)
    # 자동 포함 표시
    for r in top:
        r["auto_included_by_mcap"] = (r["ticker"] in mcap_top_tickers)
    for i, r in enumerate(top, 1):
        r["rank"] = i
    shortfall = len(top) < TOPN
    mkt_dist = Counter(r["market"] for r in top)

    # ── 산출물 ───────────────────────────────────────────
    def f(x, p=1):
        return "-" if x is None else f"{x:.{p}f}"

    out_universe = [{
        "rank": r["rank"], "ticker": r["ticker"], "name": r["name"], "market": r["market"],
        "sector": r["sector"],
        "auto_included_by_mcap": r.get("auto_included_by_mcap", False),
        "largecap_bonus": r.get("largecap_bonus", 0.0),
        "scores": {"value": r["z_value"], "quality": r["z_quality"],
                   "tp_rev": r["z_tp_rev"], "roe_rev": r["z_roe_rev"],
                   "turnover": r["z_turnover"], "total": r["total"]},
        "metrics": {"mcap_eok": r["mcap_eok"], "turnover20_eok": r["turnover20_eok"],
                    "per": r["per"], "pbr": r["pbr"], "ev_ebitda": r["ev_ebitda"],
                    "roe": r["roe"], "roa": r["roa"], "opm": r["opm"], "debt_ratio": r["debt"],
                    "tp_rev_1m_pct": r["tp_rev_1m"],
                    "roe_fwd_rev_1m_pct": r["roe_fwd_rev_1m"],
                    "turnover_growth_pct": r["turnover_growth_pct"]},
    } for r in top]
    meta = {
        "as_of": asof, "regime": regime, "weights": W, "n_final": len(top),
        "data_source": "KRX Open API + FnSpace account/consensus(price·forward) — ALL LIVE",
        "universe_total": n_all, "filter_log": log, "market_dist": dict(mkt_dist),
        "turnover_window_days": len(td_dates),
        "turnover_growth_window": {"recent": [rec_dates[-1], rec_dates[0]] if rec_dates else None,
                                    "prior": [prior_dates[-1], prior_dates[0]] if prior_dates else None},
        "revision_lookback_days": 45,
        "fnspace_scope_cap": MAX_FNSPACE,
        "zscore_basis": "섹터중립(DART 업종)" if use_sector_z else "전역(global)",
        "factor_model": {
            "factors": list(FACTORS),
            "note": "5팩터 섹터중립 z-score(±5σ) · 레짐 가중치 합산",
        },
        "notes": ["거래정지=거래량0 근사",
                  ("z-score 섹터중립(DART induty_code 기반, 표본<5 섹터는 전역 폴백)" if use_sector_z
                   else "z-score 전역(업종 분류 미보유)"),
                  f"컨센서스/재무 조회는 시총 상위 {MAX_FNSPACE}로 경계(애널리스트 커버리지 집중구간)",
                  "목표주가 1M 변화율 = (종가×(1+괴리율/100))의 최근/1M전 비율",
                  "Fwd 12M ROE(E211570) 1M 변화율로 EPS Fwd 12M 대체 (FnSpace EPS 항목 null 응답); 음수 ROE 종목은 None"],
    }
    json.dump({"meta": meta, "universe": out_universe},
              open(os.path.join(HERE, "universe_full.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # canonical universe.json 교체는 검증 후 promote_universe.py 로 별도 수행(자동 교체 안 함).

    # markdown
    L = []
    L.append("# 투자유니버스 (FULL LIVE: KRX + FnSpace) — Top 100\n")
    L.append(f"- **기준일**: {asof} (KRX 최신 영업일)")
    L.append("- **데이터**: KRX Open API(시장) + FnSpace account/consensus-price/consensus-forward — 전부 실시간")
    L.append(f"- **레짐**: {regime} → 밸류 {W['value']:.0%} / 퀄리티 {W['quality']:.0%} / 목표가↑ {W['tp_rev']:.0%} / ROE↑ {W['roe_rev']:.0%} / 거래대금↑ {W['turnover']:.0%}")
    L.append(f"- **시장분포(Top100)**: " + ", ".join(f"{k} {v}" for k, v in mkt_dist.items()) + "\n")
    L.append("## 하드필터 단계별 종목 수")
    L.append("| 단계 | 통과 |")
    L.append("|------|------|")
    for kk, vv in log.items():
        L.append(f"| {kk} | {vv} |")
    L.append(f"| **최종 Top {TOPN}** | {len(top)} |\n")
    if shortfall:
        L.append(f"> ⚠️ 최종 {len(top)}종목으로 목표 {TOPN} 미만.\n")
    L.append("## 팩터 정의 (5팩터 컨센서스리비전 모델)")
    L.append("- **밸류** = z(−PER=종가/EPS) + z(−PBR=종가/BPS) + z(−EV/EBITDA) 평균 *(기존)*")
    L.append("- **퀄리티** = z(ROE + ROA − 부채비율/100 + 영업이익률) *(기존)*")
    L.append("- **목표가↑(1M)** = z((TP_now / TP_1M − 1) ×100), TP = 종가×(1+목표주가괴리율/100)")
    L.append("- **ROE↑(1M)** = z((ROE_Fwd12M_now / ROE_Fwd12M_1M − 1) ×100)  *(EPS Fwd.12M 대체 — FnSpace EPS 항목 null)*")
    L.append("- **거래대금↑** = z((최근 1M 평균거래대금 / 3M전 1M 평균거래대금 − 1) ×100)")
    L.append(f"- *z-score 기준: {'섹터중립(DART 업종, 표본<5 전역 폴백)' if use_sector_z else '전역(업종 미보유)'}. ±5σ winsorize.*\n")
    L.append("## 유니버스 (Top 100)")
    L.append("| 순위 | 티커 | 종목명 | 시장 | 섹터 | 시총(억) | 20일거래대금(억) | PER | ROE | 목표가↑% | ROE↑% | 거래대금↑% | 밸류 | 퀄리티 | 목표가↑ | ROE↑ | 거래대금↑ | 종합 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in top:
        L.append(f"| {r['rank']} | {r['ticker']} | {r['name']} | {r['market']} | {r['sector']} | "
                 f"{f(r['mcap_eok'],0)} | {f(r['turnover20_eok'],0)} | {f(r['per'])} | {f(r['roe'])} | "
                 f"{f(r['tp_rev_1m'])} | {f(r['roe_fwd_rev_1m'])} | {f(r['turnover_growth_pct'])} | "
                 f"{r['z_value']:+.2f} | {r['z_quality']:+.2f} | {r['z_tp_rev']:+.2f} | "
                 f"{r['z_roe_rev']:+.2f} | {r['z_turnover']:+.2f} | {r['total']:+.3f} |")
    open(os.path.join(HERE, "universe_full.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    print(f"\n최종 {len(top)}종목 (목표 {TOPN}{'  ⚠️부족' if shortfall else ''}) | 시장분포 {dict(mkt_dist)}")
    print("상위 10:")
    for r in top[:10]:
        print(f"  {r['rank']:>2} {r['ticker']} {r['name'][:12]:<12} {r['market']:<6} "
              f"시총={f(r['mcap_eok'],0)}억 PER={f(r['per'])} ROE={f(r['roe'])} "
              f"목표가↑={f(r['tp_rev_1m'])}% ROE↑={f(r['roe_fwd_rev_1m'])}% "
              f"거래대금↑={f(r['turnover_growth_pct'])}% 종합={r['total']:+.3f}")
    print("\n산출: universe_full.md / universe_full.json (검증 후 universe.json 으로 promote)")


if __name__ == "__main__":
    main()
