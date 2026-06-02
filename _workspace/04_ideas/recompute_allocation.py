#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4단계 아이디어별 편입 비중 자동 재계산.

산식:
  가중점수 = idea.total_score × category_multiplier
  if matched_count(idea) == 0:
      allocation_pct = 0%
  else:
      allocation_pct = 가중점수 / Σ(가중점수) × (100 − UNCOVERED_PCT)

  default_picks(uncovered pool) allocation_pct = UNCOVERED_PCT (고정)
  합계 = 100%

입력:
  · _workspace/04_ideas/ideas.json          (5점 평가·4분류 결과)
  · _workspace/05_matching/matching_matrix.json (매칭 종목 수)

출력 (ideas.json 갱신):
  · 각 idea.allocation_pct
  · allocation_weights 메타 섹션
  · default_picks.allocation_pct

CLI 옵션으로 카테고리 가중치와 uncovered 비중 조정 가능.
"""
import os, sys, json, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
IDEAS_PATH = os.path.join(HERE, "ideas.json")
MATRIX_PATH = os.path.join(WS, "05_matching", "matching_matrix.json")

# 기본 설정
DEFAULTS = {
    "uncovered_pct": 5.0,
    "core_weight": 1.5,
    "watch_weight": 1.0,
    "unverified_weight": 0.5,
    "weak_weight": 0.0,
}


def main():
    ap = argparse.ArgumentParser(description="4단계 아이디어 편입 비중 자동 재계산")
    ap.add_argument("--uncovered-pct", type=float, default=DEFAULTS["uncovered_pct"],
                    help="매크로 무관 개별 종목 풀(default_picks) 비중 %% (기본 %(default)s)")
    ap.add_argument("--core-weight", type=float, default=DEFAULTS["core_weight"],
                    help="핵심 장기 테마 후보 가중치 (기본 %(default)s)")
    ap.add_argument("--watch-weight", type=float, default=DEFAULTS["watch_weight"],
                    help="관찰 리스트 가중치 (기본 %(default)s)")
    ap.add_argument("--unverified-weight", type=float, default=DEFAULTS["unverified_weight"],
                    help="검증 부족 가중치 (기본 %(default)s)")
    ap.add_argument("--weak-weight", type=float, default=DEFAULTS["weak_weight"],
                    help="아직 약한 아이디어 가중치 (기본 %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="ideas.json 저장 없이 계산만 출력")

    args = ap.parse_args()

    cat_weights = {
        "핵심 장기 테마 후보": args.core_weight,
        "관찰 리스트": args.watch_weight,
        "검증 부족": args.unverified_weight,
        "아직 약한 아이디어": args.weak_weight,
    }
    uncovered_pct = args.uncovered_pct
    allocatable = 100.0 - uncovered_pct

    if not os.path.exists(IDEAS_PATH):
        print(f"❌ {IDEAS_PATH} 없음"); sys.exit(1)
    if not os.path.exists(MATRIX_PATH):
        print(f"❌ {MATRIX_PATH} 없음. 5단계 idea-stock-matching 먼저 실행."); sys.exit(1)

    ideas = json.load(open(IDEAS_PATH, encoding="utf-8"))
    mat = json.load(open(MATRIX_PATH, encoding="utf-8"))

    matched = {x["idea_id"]: len(x.get("matched_tickers", [])) for x in mat["matrix"]}

    # idea_id → category 매핑
    id_to_cat = {}
    for cat, ids in ideas["categories"].items():
        for i in ids:
            id_to_cat[i] = cat

    rows = []
    for i in ideas["ideas"]:
        n = matched.get(i["idea_id"], 0)
        cat = id_to_cat.get(i["idea_id"], "관찰 리스트")
        w = cat_weights.get(cat, 1.0)
        weighted = i["scores"]["total"] * w if n > 0 else 0
        rows.append({
            "id": i["idea_id"], "theme": i["theme"], "cat": cat,
            "total": i["scores"]["total"], "cat_weight": w,
            "weighted_score": weighted, "n_matched": n,
        })

    wsum = sum(r["weighted_score"] for r in rows)
    print(f"[설정] uncovered={uncovered_pct}% / 가용={allocatable}% / 가중치={cat_weights}")
    print(f"[가중점수 합] {wsum}\n")

    cat_short = {"핵심 장기 테마 후보": "핵심", "관찰 리스트": "관찰",
                  "검증 부족": "검증부족", "아직 약한 아이디어": "약함"}
    print(f"{'ID':>3s} {'테마':30s} {'분류':<6s} {'매칭':>4s} {'점수':>4s} {'가중':>5s} {'비중%':>7s}")
    print("-" * 85)
    # 90.0 is the baseline theme weight. Remainder of reduced uncovered_pct goes to the main theme.
    baseline_allocatable = 90.0
    for r in rows:
        r["allocation_pct"] = (r["weighted_score"] / wsum) * baseline_allocatable if wsum > 0 else 0

    if rows:
        main_theme_row = max(rows, key=lambda x: x["weighted_score"])
        remainder = max(0.0, 10.0 - uncovered_pct)
        main_theme_row["allocation_pct"] += remainder

    total_pct = 0
    for r in sorted(rows, key=lambda x: -x["weighted_score"]):
        pct = r["allocation_pct"]
        r["allocation_pct"] = round(pct, 2)
        total_pct += r["allocation_pct"]
        flag = "" if r["n_matched"] > 0 else "  ★매칭0"
        cs = cat_short.get(r["cat"], "?")
        print(f"{r['id']:>3d} {r['theme'][:28]:<30s} {cs:<6s} {r['n_matched']:>4d} {r['total']:>4d} ×{r['cat_weight']:<4.1f} {r['allocation_pct']:>6.2f}%{flag}")
    print(f"\n   default_picks (uncovered pool)            —     —      —    {uncovered_pct:>5.2f}%  [고정]")
    print(f"\n합계: {round(total_pct + uncovered_pct, 2)}%")

    if args.dry_run:
        print("\n--dry-run: ideas.json 저장 안 함")
        return

    # ideas.json 갱신
    ideas["allocation_weights"] = {
        "computed_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": "카테고리 차등 가중치 적용. 매칭 0건은 0%. uncovered_pool 고정 비중.",
        "category_multipliers": cat_weights,
        "allocatable_total_pct": allocatable,
        "uncovered_fixed_pct": uncovered_pct,
        "weighted_score_sum": round(wsum, 2),
        "formula": f"allocation_pct = (idea.total_score × category_multiplier) / Σ(weighted_scores) × {allocatable}",
        "by_idea": {str(r["id"]): r["allocation_pct"] for r in rows},
        "uncovered_pool_pct": uncovered_pct,
        "sanity_check_sum": round(total_pct + uncovered_pct, 2),
    }
    for i in ideas["ideas"]:
        row = next(r for r in rows if r["id"] == i["idea_id"])
        i["allocation_pct"] = row["allocation_pct"]

    # default_picks 구조 (단일 종목 X → 복수 종목 풀)
    legacy_single = ideas.pop("default_single_pick", None)
    existing_picks = ideas.get("default_picks", {})
    ideas["default_picks"] = {
        "status": existing_picks.get("status", "PENDING_STAGE_6"),
        "purpose": "매크로 사이클·테마 점수·레짐 가중치와 독립적인 개별 종목 풀(복수). "
                    "5단계 matching에서 어떤 아이디어에도 매칭되지 않은 종목 풀(uncovered_tickers)에서 "
                    "6단계 company-research가 N종목 선정·비중 배분.",
        "n_picks_target": existing_picks.get("n_picks_target", "6단계 결정 (예: 3~5종목, 매력도·매크로베타 기준 균등 또는 가중)"),
        "selection_criteria": [
            "4단계 어느 아이디어에도 매칭되지 않음 (5단계 uncovered_tickers 풀에서)",
            "6단계 company-research 매력도 점수 상위 30% 이내",
            "매크로 베타 < 0.7 (시장 변동성 의존도 낮음)",
            "회사 고유 catalyst 명시 가능 (임상·M&A·턴어라운드·자사주 소각·신제품 등)",
        ],
        "filled_by": "06_research/company-research",
        "allocation_pct": uncovered_pct,
        "picks": existing_picks.get("picks", []),
    }
    if legacy_single:
        ideas["default_picks"]["_migration_note"] = "default_single_pick (단일 1종목, 20%) → default_picks (복수 풀, 10%)로 재정의"

    json.dump(ideas, open(IDEAS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {IDEAS_PATH}")


if __name__ == "__main__":
    main()
