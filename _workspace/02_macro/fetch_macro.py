#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""매크로 분석 — FRED LIVE 데이터로 자산배분 자동화.

데이터(전부 FRED, LIVE):
  · VIXCLS, DGS10                                      [핵심 — 3x3 매트릭스 입력]
  · FEDFUNDS, UNRATE, CPIAUCSL(YoY), PCEPI(YoY)        [모니터링]

레짐 판별(3x3 매트릭스):
  VIX × US10Y → W_macro ∈ {0.0, 0.2, 0.3, 0.5, 0.7, 0.8, 1.0}

자산배분:
  equity_pct = equity_pct_min + (equity_pct_max - equity_pct_min) × W_macro
  cash_pct   = 100 - equity_pct   (음수 = 신용매수/차입)

산출:
  · _workspace/02_macro/allocation.json
  · _workspace/02_macro/macro_dashboard.md
"""
import sys, os, json, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
CONSTRAINTS = os.path.join(WS, "01_profile", "constraints.json")

# 3x3 매트릭스: (VIX_bucket, US10Y_bucket) → (W_macro, 레짐 라벨)
# VIX:   Low<20  | Mod 20~30 | High>=30
# US10Y: Low<3.5 | Mod 3.5~4.5 | High>=4.5
MATRIX = {
    ("Low",  "Low"):  (1.0, "초위험선호"),
    ("Low",  "Mod"):  (0.8, "위험선호"),
    ("Low",  "High"): (0.5, "중립"),
    ("Mod",  "Low"):  (0.7, "다소 위험선호"),
    ("Mod",  "Mod"):  (0.5, "중립"),
    ("Mod",  "High"): (0.3, "다소 위험회피"),
    ("High", "Low"):  (0.3, "다소 위험회피"),
    ("High", "Mod"):  (0.2, "위험회피"),
    ("High", "High"): (0.0, "초위험회피"),
}


def bucket_vix(v):
    return "Low" if v < 20 else ("Mod" if v < 30 else "High")


def bucket_us10y(v):
    return "Low" if v < 3.5 else ("Mod" if v < 4.5 else "High")


def status(val, warn_cond=None):
    """대시보드 상태 라벨. warn_cond(callable) 호출 시 WARN, 값 없으면 FAIL."""
    if val is None:
        return "FAIL"
    if warn_cond and warn_cond(val):
        return "WARN"
    return "PASS"


def main():
    print("[1/4] FRED 매크로 지표 수집 중…")
    snap = datasource.MacroData().snapshot()
    errs = {k: v["error"] for k, v in snap.items() if "error" in v}
    if errs:
        print(f"  ⚠️ 일부 시리즈 실패: {errs}")
    for k, v in snap.items():
        if "value" in v:
            unit = "%" if k in ("us10y", "fed_funds", "unemployment", "cpi", "pce") else ""
            print(f"  {k:14s} {v['series_id']:9s} {v['date']}  {v['value']:>7.2f}{unit}  {v['label']}")

    vix = snap.get("vix", {}).get("value")
    us10y = snap.get("us10y", {}).get("value")
    if vix is None or us10y is None:
        print("❌ 핵심 지표(VIX/US10Y) 수집 실패. 중단.")
        sys.exit(1)

    # 레짐 판별
    bv, bu = bucket_vix(vix), bucket_us10y(us10y)
    w_macro, regime = MATRIX[(bv, bu)]
    print(f"\n[2/4] 레짐: VIX={vix:.2f}({bv}) × US10Y={us10y:.2f}%({bu}) → W_macro={w_macro} ({regime})")

    # 투자성향 제약 로드
    print(f"\n[3/4] 투자성향 제약 로드: {CONSTRAINTS}")
    with open(CONSTRAINTS, encoding="utf-8") as f:
        c = json.load(f)
    emin, emax = c["equity_pct_min"], c["equity_pct_max"]
    equity_pct = emin + (emax - emin) * w_macro
    cash_pct = 100 - equity_pct
    print(f"  투자자유형={c['investor_type']} 허용범위 {emin}~{emax}% "
          f"→ equity_pct = {emin} + ({emax}-{emin})×{w_macro} = {equity_pct:.1f}%, cash_pct = {cash_pct:.1f}%")

    # ── allocation.json ────────────────────────────────────────
    now_iso = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    alloc = {
        "equity_pct": round(equity_pct, 2),
        "cash_pct": round(cash_pct, 2),
        "regime": regime,
        "w_macro": w_macro,
        "updated_at": now_iso,
        "basis": {
            "vix": vix,
            "us10y": us10y,
            "fed_funds_rate": snap.get("fed_funds", {}).get("value"),
            "unemployment": snap.get("unemployment", {}).get("value"),
            "cpi_yoy": snap.get("cpi", {}).get("value"),
            "pce_yoy": snap.get("pce", {}).get("value"),
            "equity_pct_min": emin,
            "equity_pct_max": emax,
            "allocation_rule": f"{regime} (VIX {bv}, US10Y {bu}): W_macro={w_macro} → {emin} + ({emax}-{emin})×{w_macro} = {equity_pct:.1f}",
            "data_source": "FRED (St. Louis Fed) — VIXCLS, DGS10, FEDFUNDS, UNRATE, CPIAUCSL, PCEPI",
        },
    }
    json.dump(alloc, open(os.path.join(HERE, "allocation.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # ── macro_dashboard.md ──────────────────────────────────────
    print("\n[4/4] 산출물 생성 중…")
    L = [f"## 매크로 대시보드 {today}\n"]
    L.append("| 지표 | 값 | 갱신일 | 상태 | 출처 |")
    L.append("|------|------|--------|------|------|")
    def row(label, key, fmt="{:.2f}%", warn=None):
        rec = snap.get(key, {})
        if "error" in rec:
            return f"| {label} | — | — | FAIL | FRED({rec['series_id']}) — {rec['error']} |"
        v = rec.get("value"); d = rec.get("date")
        st = status(v, warn)
        vs = fmt.format(v) if v is not None else "—"
        return f"| {label} | {vs} | {d} | {st} | FRED / {rec.get('series_id')} |"

    L.append(row("미국채 10년물 금리 (US10Y)", "us10y"))
    L.append(row("VIX 지수", "vix", "{:.2f}"))
    L.append(row("(모니터링) 미국 기준금리 (Fed Funds)", "fed_funds"))
    L.append(row("(모니터링) 실업률", "unemployment", warn=lambda v: v >= 5.0))
    L.append(row("(모니터링) CPI (YoY%)", "cpi", warn=lambda v: v >= 3.5))
    L.append(row("(모니터링) PCE (YoY%)", "pce", warn=lambda v: v >= 3.0))

    L.append("\n---\n")
    L.append(f"## 레짐 판별 결과\n")
    L.append(f"**레짐: {regime}**\n")
    L.append("### 판별 로직 적용 (VIX × 미국채 10년물 금리, 3x3)\n")
    L.append("| 조건 | 기준 | 실제값 | 등급 |")
    L.append("|------|------|--------|------|")
    def vbucket_desc(b):
        return {"Low": "저변동성 (<20)", "Mod": "중변동성 (20~30)", "High": "고변동성 (≥30)"}[b]
    def ubucket_desc(b):
        return {"Low": "저금리 (<3.5%)", "Mod": "중금리 (3.5~4.5%)", "High": "고금리 (≥4.5%)"}[b]
    L.append(f"| VIX | {vbucket_desc(bv)} | {vix:.2f} | {bv} |")
    L.append(f"| US10Y | {ubucket_desc(bu)} | {us10y:.2f}% | {bu} |")
    L.append(f"\n### 매크로 가중치 ($W_{{macro}}$) 산출")
    L.append(f"- VIX={bv} × US10Y={bu} → **$W_{{macro}} = {w_macro}$** 적용 ({regime}).\n")

    L.append("### 근거 요약")
    L.append(f"1. **VIX {vix:.2f}** — {vbucket_desc(bv)} 구간")
    L.append(f"2. **미국채 10년물 금리 {us10y:.2f}%** — {ubucket_desc(bu)} 구간")
    L.append(f"3. **종합 결론** — 3x3 매트릭스에 따라 {regime}(W_macro={w_macro})로 자산을 배분합니다.\n")

    L.append("---\n")
    L.append("## 자산배분 결정\n")
    L.append(f"- **투자자 유형:** {c['investor_type']} (score: {c.get('_meta',{}).get('score','-')})")
    L.append(f"- **허용 범위:** equity_pct {emin}% ~ {emax}% (범위: {emax-emin}%p)")
    L.append(f"- **자산배분 적용:** {emin}% + {emax-emin}%p × {w_macro} = **{equity_pct:.1f}%**\n")
    L.append("| 자산 | 비중 | 설명 |")
    L.append("|------|------|------|")
    L.append(f"| 주식 (Equity) | **{equity_pct:.1f}%** | 성향에 맞는 종목 구성 |")
    cash_desc = "현금/단기채 비중" if cash_pct >= 0 else "신용매수 및 차입(레버리지) 활용 비중"
    L.append(f"| 현금/채권 (Cash) | **{cash_pct:.1f}%** | {cash_desc} |")
    L.append(f"\n*갱신일: {today} (데이터: FRED LIVE)*")

    open(os.path.join(HERE, "macro_dashboard.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    print(f"\n완료 ✅  주식 {equity_pct:.1f}% / 현금 {cash_pct:.1f}% (레짐: {regime})")
    print("산출: allocation.json, macro_dashboard.md")


if __name__ == "__main__":
    main()
