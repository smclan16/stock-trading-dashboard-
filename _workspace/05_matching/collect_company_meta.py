#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5단계 idea-stock-matching — 100종목 회사 메타 일괄 수집 헬퍼.

DART company.json을 일괄 호출하여 종목별 회사 기본 정보 캐시:
  · 회사명·영문명, 대표자, 설립일자, 본사주소, 홈페이지, IR URL, 전화번호
  · induty_code (KSIC 업종코드) → 광의 섹터 (datasource.DartSector.ksic_to_sector)

입력: 3단계 universe.json 또는 universe_full.json (100종목)
출력: _workspace/05_matching/company_meta.json
캐시: 1회 수집 후 변경 드물어 재사용 (강제 갱신 시 --refresh)

사업 상세(사업의 내용·종속회사 등)는 본 스크립트로 수집하지 않음 — LLM이
매칭 시점에 도메인 지식 + 네이버 금융 WebFetch + 필요 시 DART 사업보고서 ZIP로 보강.
"""
import sys, os, json, time, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource
import config
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
OUT_PATH = os.path.join(HERE, "company_meta.json")

DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"


def load_universe():
    """3단계 산출물에서 종목 리스트 로드. universe.json 우선, 없으면 universe_full.json."""
    for fn in ["universe.json", "universe_full.json"]:
        p = os.path.join(WS, "03_universe", fn)
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        # universe.json은 list, universe_full.json은 {meta, universe} dict
        if isinstance(d, list):
            return [{"ticker": x["ticker"], "name": x.get("name"), "sector": x.get("sector")} for x in d], fn
        if isinstance(d, dict) and "universe" in d:
            return [{"ticker": x["ticker"], "name": x.get("name"), "sector": x.get("sector")} for x in d["universe"]], fn
    return [], None


def fetch_company(api_key, corp_code, timeout=20):
    """DART /company.json 단일 호출. 반환: dict 또는 None(에러)."""
    r = requests.get(DART_COMPANY_URL,
                     params={"crtfc_key": api_key, "corp_code": corp_code},
                     timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "000":
        return None
    return j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="기존 캐시 무시하고 전체 재수집")
    args = ap.parse_args()

    universe, src = load_universe()
    if not universe:
        print("❌ 3단계 universe.json 또는 universe_full.json 없음. screen_full.py 먼저 실행 필요.")
        sys.exit(1)
    print(f"[1/3] 유니버스 로드: {len(universe)}종목 (출처: {src})")

    # 기존 캐시
    cache = {}
    if os.path.exists(OUT_PATH) and not args.refresh:
        prev = json.load(open(OUT_PATH, encoding="utf-8"))
        cache = {x["ticker"]: x for x in prev.get("companies", [])}
        print(f"  기존 캐시 {len(cache)}건 재사용 (--refresh로 강제 갱신 가능)")

    # DART 키 + corp_code 매핑
    api_key = config.require("DART_API_KEY")
    ds = datasource.DartSector()
    corp_map = ds.corp_map()
    print(f"[2/3] DART corp_map 로드: {len(corp_map)}건")

    rows = []
    new_count = 0
    err_count = 0
    for i, u in enumerate(universe, 1):
        t = u["ticker"]
        if t in cache:
            rows.append(cache[t])
            continue
        cc = corp_map.get(t)
        if not cc:
            print(f"  [{i:>3}/{len(universe)}] {t} {u.get('name','')[:14]:<14}  corp_code 없음 (보통주 아님 가능)")
            rows.append({"ticker": t, "name": u.get("name"), "sector": u.get("sector"),
                          "corp_code": None, "error": "corp_code not found"})
            err_count += 1
            continue
        try:
            j = fetch_company(api_key, cc)
            if not j:
                print(f"  [{i:>3}/{len(universe)}] {t} {u.get('name','')[:14]:<14}  DART status≠000")
                rows.append({"ticker": t, "name": u.get("name"), "sector": u.get("sector"),
                              "corp_code": cc, "error": "DART status≠000"})
                err_count += 1
            else:
                ksic = j.get("induty_code")
                rec = {
                    "ticker": t,
                    "name": j.get("corp_name") or u.get("name"),
                    "name_eng": j.get("corp_name_eng"),
                    "sector": ds.ksic_to_sector(ksic) if ksic else u.get("sector"),
                    "corp_code": cc,
                    "stock_code": j.get("stock_code"),
                    "ceo": j.get("ceo_nm"),
                    "corp_cls": j.get("corp_cls"),  # Y=KOSPI, K=KOSDAQ, N=KONEX, E=기타
                    "establishment_dt": j.get("est_dt"),
                    "address": j.get("adres"),
                    "homepage": j.get("hm_url"),
                    "ir_url": j.get("ir_url"),
                    "phone": j.get("phn_no"),
                    "induty_code": ksic,
                }
                rows.append(rec)
                new_count += 1
                if new_count <= 5 or new_count % 20 == 0:
                    print(f"  [{i:>3}/{len(universe)}] {t} {rec['name'][:14]:<14}  업종={ksic} 섹터={rec['sector']}")
        except Exception as e:
            print(f"  [{i:>3}/{len(universe)}] {t} {u.get('name','')[:14]:<14}  EXC {type(e).__name__}: {str(e)[:60]}")
            rows.append({"ticker": t, "name": u.get("name"), "sector": u.get("sector"),
                          "corp_code": cc, "error": f"{type(e).__name__}: {str(e)[:80]}"})
            err_count += 1
        time.sleep(0.3)  # DART rate 매너

    import datetime
    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "DART /company.json",
        "n_universe": len(universe),
        "n_new_fetched": new_count,
        "n_from_cache": len(cache) - sum(1 for r in rows if r.get("ticker") in cache and r.get("error")),
        "n_errors": err_count,
        "companies": rows,
    }
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n[3/3] 저장: {OUT_PATH}")
    print(f"  신규 수집 {new_count} / 캐시 활용 {len(cache)} / 오류 {err_count}")


if __name__ == "__main__":
    main()
