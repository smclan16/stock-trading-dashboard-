#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""투자 파이프라인 외부 데이터 어댑터.

현재 활성(LIVE) 소스:
  - FnSpace 컨센서스(consensus-*): 구독 권한 확인됨
      · consensus-earning-fiscal : 컨센서스 존재 여부 + 추정 성장(매출/영익 YoY), ROE(지배)
      · consensus-forward        : Fwd.12M ROE, Fwd.12M EV/EBITDA
      · consensus-price          : 목표주가괴리율, 목표주가 상향-하향(3개월) 리비전, 투자의견
  - FnSpace account(재무): ROE(지배)/ROA/영업이익률/부채비율/매출·영익 증가율(YoY)/EV-EBITDA 실적치
      (요청당 최대 10종목, 연도 단위)

비활성(DEFERRED) 소스 — 권한/차단으로 현재 사용 불가, 권한 확보 시 활성화:
  - FnSpace stock_price  : 시총/20일평균거래대금/거래정지구분/주가(모멘텀)/PER/PBR → "사용권한 없음"
  - FnSpace stock_list   : 코스피/코스닥 종목 마스터 → "사용권한 없음"
  - KRX(pykrx)           : 비한국 IP anti-bot 차단
  - Finnhub(한국주식)     : 무료 티어 미지원(403). 미국주식만 동작.

설계 원칙: 활성 소스만으로 동작하되, DEFERRED 소스가 열리면 동일 인터페이스에
필드를 채워넣도록 확장점(check_capabilities / FnSpaceMarket 스텁)을 둔다.
"""
import sys, os, json, datetime
from collections import defaultdict
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

FNSPACE_KEY = lambda: config.get("FNSPACE_API_KEY")
FINNHUB_KEY = lambda: config.get("FINNHUB_API_KEY")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _strip_a(code):
    return code[1:] if isinstance(code, str) and code.startswith("A") else code


def _latest_per_code(df, value_cols):
    """일자(DT) 시계열에서 코드별 가장 최근의 (모든 value_cols가 채워진) 행을 반환."""
    if df is None or len(df) == 0:
        return {}
    df = df.copy()
    df["CODE"] = df["CODE"].map(_strip_a)
    if "DT" in df.columns:
        df = df.sort_values("DT")
    out = {}
    for code, g in df.groupby("CODE"):
        rec = {}
        for c in value_cols:
            if c in g.columns:
                s = pd.to_numeric(g[c], errors="coerce").dropna()
                if len(s):
                    rec[c] = float(s.iloc[-1])
        if rec:
            out[code] = rec
    return out


class ConsensusData:
    """FnSpace 컨센서스 기반 데이터 (현재 유일한 LIVE 한국주식 소스)."""

    # 항목코드 ↔ 한글 컬럼명
    F_GROWTH = {"E231000": "매출액증가율(YoY)", "E231400": "영업이익증가율(YoY)", "E211500": "ROE(지배)"}
    FWD = {"E211570": "ROE(Fwd.12M)", "E331060": "EV/EBITDA(Fwd.12M)"}
    PRICE = {"E610360": "목표주가괴리율", "E611150": "목표주가(상향-하향)/전체(3개월)", "E610100": "투자의견"}
    # Fwd 12M ROE (LIVE 검증). FnSpace에서 EPS Fwd.12M 직접 항목은 값이 null로 응답되어
    # ROE Fwd.12M의 1M 변화율로 EPS 리비전을 근사한다(자본 변동이 작을 때 강한 양의 상관).
    FWD_ROE_ITEM = "E211570"
    FWD_ROE_COL = "ROE(Fwd.12M)"

    def __init__(self, chunk=10):  # FnSpace 요청당 최대 10종목
        from fnspace import FnSpace
        self.fs = FnSpace(FNSPACE_KEY())
        self.chunk = min(chunk, 10)

    def _get(self, **kw):
        return self.fs.get_data(**kw)

    def fetch(self, codes, fiscal_year=None):
        """주어진 종목코드 리스트에 대해 컨센서스 팩터 인풋을 일괄 수집.

        반환: dict[code] = {
           consensus: bool,          # 추정실적 컨센서스 존재
           rev_yoy, opinc_yoy,       # 성장(추정 YoY %)
           est_roe,                  # ROE(지배) 추정 (당해연도)
           fwd_roe, fwd_ev_ebitda,   # Fwd.12M
           target_upside,            # 목표주가괴리율(%)
           rev_momentum,             # 목표주가 상향-하향(3개월) %
           opinion,                  # 투자의견(1~5, 높을수록 매수)
        }
        """
        year = fiscal_year or datetime.datetime.now().year
        today = datetime.datetime.now().strftime("%Y%m%d")
        frm = (datetime.datetime.now() - datetime.timedelta(days=25)).strftime("%Y%m%d")
        codes = [str(c) for c in codes]
        acc = {c: {} for c in codes}

        for grp in _chunks(codes, self.chunk):
            # 1) 추정실적(Fiscal) — 존재 여부 + 성장 + ROE
            df = self._get(category="consensus-earning-fiscal", code=grp,
                           item=list(self.F_GROWTH), consolgb="M", annualgb="A",
                           from_year=str(year), to_year=str(year), kor_item_name=True)
            if df is not None and len(df):
                df = df.copy(); df["CODE"] = df["CODE"].map(_strip_a)
                for code, g in df.groupby("CODE"):
                    r = g.iloc[-1]
                    acc.setdefault(code, {})
                    acc[code]["consensus"] = True
                    acc[code]["rev_yoy"] = pd.to_numeric(g.get("매출액증가율(YoY)"), errors="coerce").dropna().iloc[-1] if "매출액증가율(YoY)" in g and pd.to_numeric(g["매출액증가율(YoY)"], errors="coerce").notna().any() else None
                    acc[code]["opinc_yoy"] = pd.to_numeric(g.get("영업이익증가율(YoY)"), errors="coerce").dropna().iloc[-1] if "영업이익증가율(YoY)" in g and pd.to_numeric(g["영업이익증가율(YoY)"], errors="coerce").notna().any() else None
                    acc[code]["est_roe"] = pd.to_numeric(g.get("ROE(지배)"), errors="coerce").dropna().iloc[-1] if "ROE(지배)" in g and pd.to_numeric(g["ROE(지배)"], errors="coerce").notna().any() else None

            # 2) Forward 지표
            df = self._get(category="consensus-forward", code=grp, item=list(self.FWD),
                           from_date=frm, to_date=today, kor_item_name=True)
            for code, rec in _latest_per_code(df, list(self.FWD.values())).items():
                acc.setdefault(code, {})
                acc[code]["fwd_roe"] = rec.get("ROE(Fwd.12M)")
                acc[code]["fwd_ev_ebitda"] = rec.get("EV/EBITDA(Fwd.12M)")

            # 3) 투자의견/목표주가
            df = self._get(category="consensus-price", code=grp, item=list(self.PRICE),
                           from_date=frm, to_date=today, kor_item_name=True)
            for code, rec in _latest_per_code(df, list(self.PRICE.values())).items():
                acc.setdefault(code, {})
                acc[code]["target_upside"] = rec.get("목표주가괴리율")
                acc[code]["rev_momentum"] = rec.get("목표주가(상향-하향)/전체(3개월)")
                acc[code]["opinion"] = rec.get("투자의견")

        for c in acc:
            acc[c].setdefault("consensus", False)
        return acc

    def revision_1m(self, codes, prices_now=None, prices_1m_ago=None, lookback_days=45):
        """1개월 전 대비 목표주가·Fwd 12M ROE 상향조정폭(%).

        - 목표주가 1M 변화율: E610360(목표주가괴리율) 시계열 + KRX 종가로 목표주가 유도
              target = price × (1 + upside/100); rev% = (target_now/target_1m_ago - 1) × 100
        - Fwd ROE 1M 변화율: E211570(ROE Fwd.12M) 시계열의 최근/1M전 비교
              FnSpace에서 EPS Fwd.12M 직접 항목이 null로 응답되어 ROE 변화율로 대체.
              자본 변동이 작을 때 EPS 변화율과 강한 양의 상관.
              ROE 음수(적자 추정) 또는 분모 ≤ 0 인 경우 None

        Args:
            codes: 종목코드 리스트
            prices_now: dict[code]=현재 종가 (KRX daily(asof)[t]["close"])
            prices_1m_ago: dict[code]=1M 전 종가 (KRX daily(ref_1m)[t]["close"])
            lookback_days: 컨센서스 시계열 회수 일수 (>=35 권장)

        Returns: dict[code] = {tp_rev_1m, roe_fwd_rev_1m}  (값 없음→None)
        """
        today_dt = datetime.datetime.now()
        today = today_dt.strftime("%Y%m%d")
        frm = (today_dt - datetime.timedelta(days=lookback_days)).strftime("%Y%m%d")
        cutoff = today_dt - datetime.timedelta(days=30)  # ≈1M ago
        codes = [str(c) for c in codes]
        pn_all = prices_now or {}; po_all = prices_1m_ago or {}
        out = {c: {"tp_rev_1m": None, "roe_fwd_rev_1m": None} for c in codes}

        def _pick(g, col):
            """col이 채워진 행만 정렬 후 (latest, ~1M전) 행 반환. 부족 시 (None, None)."""
            g2 = g.dropna(subset=["DT_dt"]).sort_values("DT_dt")
            v = pd.to_numeric(g2.get(col), errors="coerce")
            g2 = g2.assign(_v=v).dropna(subset=["_v"])
            if len(g2) < 2:
                return None, None
            latest = g2.iloc[-1]
            old_rows = g2[g2["DT_dt"] <= cutoff]
            old = old_rows.iloc[-1] if len(old_rows) else g2.iloc[0]
            return latest, old

        for grp in _chunks(codes, self.chunk):
            # 1) 목표주가괴리율 시계열 → 목표주가 유도
            df = self._get(category="consensus-price", code=grp, item=["E610360"],
                           from_date=frm, to_date=today, kor_item_name=True)
            if df is not None and len(df):
                df = df.copy(); df["CODE"] = df["CODE"].map(_strip_a)
                df["DT_dt"] = pd.to_datetime(df["DT"], errors="coerce")  # FnSpace는 ISO YYYY-MM-DD
                col = "목표주가괴리율" if "목표주가괴리율" in df.columns else "E610360"
                for code, g in df.groupby("CODE"):
                    latest, old = _pick(g, col)
                    if latest is None:
                        continue
                    pn = pn_all.get(code); po = po_all.get(code)
                    if not (pn and po and pn > 0 and po > 0):
                        continue
                    tp_n = pn * (1.0 + float(latest["_v"]) / 100.0)
                    tp_o = po * (1.0 + float(old["_v"]) / 100.0)
                    if tp_o > 0:
                        out.setdefault(code, {"tp_rev_1m": None, "roe_fwd_rev_1m": None})
                        out[code]["tp_rev_1m"] = (tp_n / tp_o - 1.0) * 100.0

            # 2) Fwd 12M ROE 시계열 (EPS Fwd.12M 대체 — FnSpace EPS 항목 null 응답)
            try:
                df = self._get(category="consensus-forward", code=grp, item=[self.FWD_ROE_ITEM],
                               from_date=frm, to_date=today, kor_item_name=True)
            except Exception:
                df = None
            if df is not None and len(df):
                df = df.copy(); df["CODE"] = df["CODE"].map(_strip_a)
                df["DT_dt"] = pd.to_datetime(df["DT"], errors="coerce")
                roe_col = self.FWD_ROE_COL if self.FWD_ROE_COL in df.columns else self.FWD_ROE_ITEM
                if roe_col in df.columns:
                    for code, g in df.groupby("CODE"):
                        latest, old = _pick(g, roe_col)
                        if latest is None:
                            continue
                        rn, ro = float(latest["_v"]), float(old["_v"])
                        if ro > 0 and rn > 0:  # 양수 ROE만 의미있는 % 변화
                            out.setdefault(code, {"tp_rev_1m": None, "roe_fwd_rev_1m": None})
                            out[code]["roe_fwd_rev_1m"] = (rn / ro - 1.0) * 100.0
        return out


class FinancialsData:
    """FnSpace account(재무) 기반 실적 데이터 (LIVE)."""

    ITEMS = {
        "M211500": "ROE(지배)", "M211600": "ROA", "M211000": "영업이익률",
        "M221100": "부채비율", "M231000": "매출액증가율(YoY)",
        "M231400": "영업이익증가율(YoY)", "M331030": "EV/EBITDA2",
    }

    def __init__(self, chunk=10):
        from fnspace import FnSpace
        self.fs = FnSpace(FNSPACE_KEY())
        self.chunk = min(chunk, 10)

    def fetch(self, codes, year=None, consolgb="M"):
        """반환: dict[code] = {roe, roa, opm, debt, rev_yoy, opinc_yoy, ev_ebitda}.
        최신 연간(직전연도) 기준. 일부 종목 누락 시 빈 dict."""
        year = year or (datetime.datetime.now().year - 1)
        codes = [str(c) for c in codes]
        out = {}
        keymap = {"ROE(지배)": "roe", "ROA": "roa", "영업이익률": "opm", "부채비율": "debt",
                  "매출액증가율(YoY)": "rev_yoy", "영업이익증가율(YoY)": "opinc_yoy",
                  "EV/EBITDA2": "ev_ebitda"}
        for grp in _chunks(codes, self.chunk):
            try:
                df = self.fs.get_data(category="account", code=grp, item=list(self.ITEMS),
                                      consolgb=consolgb, annualgb="A",
                                      from_year=str(year), to_year=str(year), kor_item_name=True)
            except Exception:
                df = None
            if df is None or len(df) == 0:
                continue
            df = df.copy(); df["CODE"] = df["CODE"].map(_strip_a)
            for code, g in df.groupby("CODE"):
                r = g.iloc[-1]
                rec = {}
                for kor, short in keymap.items():
                    if kor in g.columns:
                        v = pd.to_numeric(g[kor], errors="coerce").dropna()
                        rec[short] = float(v.iloc[-1]) if len(v) else None
                out[code] = rec
        return out


class KRXMarket:
    """KRX(한국거래소) Open API — 종목마스터/시총/거래대금/시장구분/가격(모멘텀).

    Base: https://data-dbg.krx.co.kr/svc/apis  | 인증: 쿼리파라미터 AUTH_KEY | GET
    엔드포인트(basDd=YYYYMMDD):
      sto/stk_bydd_trd      코스피 일별매매정보
      sto/ksq_bydd_trd      코스닥 일별매매정보
      sto/stk_isu_base_info 코스피 종목기본정보 (소속부=관리종목 등)
      sto/ksq_isu_base_info 코스닥 종목기본정보
    응답: {"OutBlock_1": [ {...}, ... ]}
    """
    # 인증: HTTP 헤더 AUTH_KEY (KRX 표준). 키 인식됨="Unauthorized API Call"(서비스 미승인),
    #       키 미인식="Unauthorized Key". 각 서비스는 포털에서 '활용신청'/승인 필요.
    BASE = "https://data-dbg.krx.co.kr/svc/apis"
    TRD = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}
    INFO = {"KOSPI": "sto/stk_isu_base_info", "KOSDAQ": "sto/ksq_isu_base_info"}

    def __init__(self, timeout=30):
        self.key = config.require("KRX_API_KEY")
        self.timeout = timeout
        self._cache = {}  # (endpoint, basDd) -> list

    def _get(self, endpoint, basDd):
        """KRX API GET with 429 retry + persistent disk cache."""
        import requests, json as _json, time, os
        ck = (endpoint, basDd)
        # 1) 메모리 캐시
        if ck in self._cache:
            return self._cache[ck]
        # 2) 디스크 캐시 (GitHub Actions 재사용)
        cache_dir = os.path.join(os.path.dirname(__file__), '.krx_cache')
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{endpoint.replace('/', '_')}_{basDd}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, encoding='utf-8') as f:
                    rows = _json.load(f)
                self._cache[ck] = rows
                return rows
            except Exception:
                pass
        # 3) API 호출 (429 retry)
        url = f"{self.BASE}/{endpoint}"
        last_err = None
        for attempt, sleep_sec in enumerate([0, 5, 15, 45], 1):
            if sleep_sec:
                time.sleep(sleep_sec)
            try:
                r = requests.get(url, params={"basDd": basDd},
                                 headers={"AUTH_KEY": self.key}, timeout=self.timeout)
                if r.status_code == 429:
                    last_err = requests.exceptions.HTTPError(f"429 (try {attempt}/4)")
                    continue  # backoff retry
                r.raise_for_status()
                try:
                    data = r.json()
                except Exception:
                    self._cache[ck] = []
                    return []
                rows = data.get("OutBlock_1") or data.get("output") or []
                self._cache[ck] = rows
                # 디스크 캐시 저장 (성공 시만)
                try:
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        _json.dump(rows, f, ensure_ascii=False)
                except Exception:
                    pass
                return rows
            except requests.exceptions.HTTPError as e:
                last_err = e
                if e.response is None or e.response.status_code != 429:
                    raise
        # 4) 4번 retry 모두 429 → 실패
        if last_err:
            raise last_err
        return []

    @staticmethod
    def _num(x):
        try:
            return float(str(x).replace(",", "").strip())
        except Exception:
            return None

    @staticmethod
    def _field(row, *names):
        for n in names:
            if n in row and row[n] not in ("", None):
                return row[n]
        return None

    def latest_trading_date(self, start=None, lookback=14):
        """start(기본 오늘)부터 과거로 거슬러 데이터가 존재하는 첫 영업일(YYYYMMDD) 반환.
        환경 시계가 실제 KRX 가용일보다 앞설 수 있어 역탐색 필요."""
        d = start or datetime.datetime.now()
        if isinstance(d, str):
            d = datetime.datetime.strptime(d, "%Y%m%d")
        for _ in range(lookback):
            ds = d.strftime("%Y%m%d")
            if d.weekday() < 5 and self._get(self.TRD["KOSPI"], ds):
                return ds
            d -= datetime.timedelta(days=1)
        return None

    def trading_dates(self, end, n):
        """end(YYYYMMDD)부터 과거로 영업일 n개 리스트(최신순).
        v15: KRX 호출 없이 평일 + 한국 공휴일 근사로 빠른 산출.
        """
        d = datetime.datetime.strptime(end, "%Y%m%d")
        out = []
        scans = 0
        while len(out) < n and scans < n * 3 + 20:
            ds = d.strftime("%Y%m%d")
            if d.weekday() < 5:  # 평일만 (공휴일 일부 포함 가능 — yfinance가 빈 값 반환)
                out.append(ds)
            d -= datetime.timedelta(days=1); scans += 1
        return out

    def latest_trading_date_no_api(self, end=None):
        """KRX 호출 없이 최근 평일 반환 (오늘 기준 또는 end YYYYMMDD)."""
        d = datetime.datetime.strptime(end, "%Y%m%d") if end else datetime.datetime.now()
        for _ in range(10):
            if d.weekday() < 5:
                return d.strftime("%Y%m%d")
            d -= datetime.timedelta(days=1)
        return d.strftime("%Y%m%d")

    def _daily_yfinance(self, basDd, tickers=None):
        """v15: KRX 429 시 yfinance fallback.
        tickers 지정 시 그 종목만, None이면 빈 dict (full universe X)."""
        try:
            import yfinance as yf
        except ImportError:
            return {}
        if not tickers:
            return {}
        d = datetime.datetime.strptime(basDd, "%Y%m%d")
        start = (d - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        end = (d + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        out = {}
        for tkr in tickers:
            for suffix in ['.KS', '.KQ']:
                try:
                    df = yf.Ticker(f"{tkr}{suffix}").history(start=start, end=end, auto_adjust=False)
                    if df.empty:
                        continue
                    # 가장 최근 row (basDd 또는 그 이전)
                    target_ts = pd.Timestamp(d.strftime("%Y-%m-%d"))
                    df_valid = df[df.index.tz_localize(None) <= target_ts] if df.index.tz else df[df.index <= target_ts]
                    if df_valid.empty:
                        continue
                    row = df_valid.iloc[-1]
                    out[tkr] = {
                        "name": "", "market": "KOSPI" if suffix == '.KS' else "KOSDAQ",
                        "close": float(row['Close']),
                        "volume": int(row['Volume']) if pd.notna(row['Volume']) else 0,
                        "turnover_eok": None, "mcap_eok": None,
                        "list_shrs": None, "sect": "",
                    }
                    break  # 성공하면 다음 종목
                except Exception:
                    continue
        return out

    def daily(self, basDd, fallback_tickers=None):
        """해당 일자 전체 코스피+코스닥 종목 매매정보.
        v15: KRX 429 시 fallback_tickers 종목만 yfinance로 보조.
        """
        out = {}
        try:
            for mkt, ep in self.TRD.items():
                for row in self._get(ep, basDd):
                    t = self._field(row, "ISU_SRT_CD", "ISU_CD")
                    if not t:
                        continue
                    t = str(t).strip()[-6:]
                    turnover = self._num(self._field(row, "ACC_TRDVAL"))
                    mcap = self._num(self._field(row, "MKTCAP"))
                    out[t] = {
                        "name": (self._field(row, "ISU_NM", "ISU_ABBRV") or "").strip(),
                        "market": mkt,
                        "close": self._num(self._field(row, "TDD_CLSPRC")),
                        "volume": self._num(self._field(row, "ACC_TRDVOL")),
                        "turnover_eok": (turnover / 1e8) if turnover is not None else None,
                        "mcap_eok": (mcap / 1e8) if mcap is not None else None,
                        "list_shrs": self._num(self._field(row, "LIST_SHRS")),
                        "sect": (self._field(row, "SECT_TP_NM") or "").strip(),
                    }
        except Exception as e:
            print(f"  ⚠ KRX daily 호출 실패 ({e}), yfinance fallback 시도")
            if fallback_tickers:
                yf_out = self._daily_yfinance(basDd, fallback_tickers)
                if yf_out:
                    out.update(yf_out)
                    print(f"  ✅ yfinance fallback {len(yf_out)}종 수신")
        return out

    def base_info(self, basDd):
        """종목기본정보 — 소속부(SECT_TP_NM: 관리종목/투자주의환기 등) 등 상태 필드."""
        out = {}
        for mkt, ep in self.INFO.items():
            for row in self._get(ep, basDd):
                t = self._field(row, "ISU_SRT_CD", "ISU_CD")
                if not t:
                    continue
                t = str(t).strip()[-6:]
                out[t] = {
                    "sect_tp": (self._field(row, "SECT_TP_NM") or "").strip(),
                    "secugrp": (self._field(row, "SECUGRP_NM") or "").strip(),  # 주권/우선주 등
                    "kind": (self._field(row, "KIND_STKCERT_TP_NM") or "").strip(),
                }
        return out

    def avg_turnover_20d(self, end_date, days=20):
        """종목별 최근 days 영업일 평균 거래대금(억원)."""
        dates = self.trading_dates(end_date, days)
        acc = defaultdict(list)
        for ds in dates:
            for t, d in self.daily(ds).items():
                if d["turnover_eok"] is not None:
                    acc[t].append(d["turnover_eok"])
        return {t: (sum(v) / len(v)) for t, v in acc.items() if v}, dates

    def turnover_ratio_growth_3m1m(self, end_date, recent=20, gap=40, lo=-95.0, hi=2000.0):
        """3M前 1M 회전율 대비 최근 1M 회전율 증가율(%).
        회전율 = 거래대금 / 시총 (스몰캡 거래대금 급증 노이즈 보정).
        반환: (dict[ticker]=growth_pct, recent_dates, prior_dates)
        """
        total = recent + gap + recent
        dates = self.trading_dates(end_date, total)
        if len(dates) < total:
            total = len(dates)
        recent_dates = dates[:recent]
        prior_dates = dates[total - recent:total]
        acc_r = defaultdict(list); acc_p = defaultdict(list)
        for ds in recent_dates:
            for t, d in self.daily(ds).items():
                if d.get("turnover_eok") and d.get("mcap_eok") and d["mcap_eok"] > 0:
                    acc_r[t].append(d["turnover_eok"] / d["mcap_eok"])  # 일별 회전율(억/억)
        for ds in prior_dates:
            for t, d in self.daily(ds).items():
                if d.get("turnover_eok") and d.get("mcap_eok") and d["mcap_eok"] > 0:
                    acc_p[t].append(d["turnover_eok"] / d["mcap_eok"])
        out = {}
        for t in set(acc_r) & set(acc_p):
            r = sum(acc_r[t]) / len(acc_r[t])
            p = sum(acc_p[t]) / len(acc_p[t])
            if p <= 0:
                continue
            g = (r / p - 1.0) * 100.0
            out[t] = max(lo, min(hi, g))
        return out, recent_dates, prior_dates

    def turnover_growth_3m1m(self, end_date, recent=20, gap=40, lo=-95.0, hi=2000.0):
        """3개월 전 대비 최근 1개월 거래대금 증가율(%).
        recent=20영업일(최근 1M), gap=40영업일(약 2M 갭) → 합계 60영업일 회수 후
        앞 20일(prior 1M, ~3M전)과 최근 20일(recent 1M) 평균을 비교한다.
        반환: (dict[ticker]=growth_pct, recent_dates, prior_dates)
        """
        total = recent + gap + recent
        dates = self.trading_dates(end_date, total)  # 최신순
        if len(dates) < total:
            total = len(dates)
        recent_dates = dates[:recent]
        prior_dates = dates[total - recent:total]
        acc_r = defaultdict(list); acc_p = defaultdict(list)
        for ds in recent_dates:
            for t, d in self.daily(ds).items():
                if d["turnover_eok"] is not None:
                    acc_r[t].append(d["turnover_eok"])
        for ds in prior_dates:
            for t, d in self.daily(ds).items():
                if d["turnover_eok"] is not None:
                    acc_p[t].append(d["turnover_eok"])
        out = {}
        for t in set(acc_r) & set(acc_p):
            r = sum(acc_r[t]) / len(acc_r[t])
            p = sum(acc_p[t]) / len(acc_p[t])
            if p <= 0:
                continue
            g = (r / p - 1.0) * 100.0
            out[t] = max(lo, min(hi, g))
        return out, recent_dates, prior_dates

    def kospi_index_history(self, end_date, weeks=26):
        """KOSPI 종합지수 weekly 종가 시계열. KRX idx/kospi_dd_trd 엔드포인트.
        반환: list of {date, close}, 가장 오래된 → 최신 순.
        """
        import requests
        dates = self.trading_dates(end_date, weeks * 5)  # ≈ 5 trading days/week
        dates.sort()  # 오래된→최신
        # weekly로 sampling: 매 5번째 (월요일 효과 무시, 단순 샘플링)
        wk_dates = dates[::5][-weeks:] if len(dates) >= weeks else dates[::5]

        out = []
        for ds in wk_dates:
            cache_key = ("idx/kospi_dd_trd", ds)
            if cache_key in self._cache:
                rows = self._cache[cache_key]
            else:
                r = requests.get(f"{self.BASE}/idx/kospi_dd_trd",
                                 params={"basDd": ds}, headers={"AUTH_KEY": self.key},
                                 timeout=self.timeout)
                try:
                    rows = r.json().get("OutBlock_1") or []
                except Exception:
                    rows = []
                self._cache[cache_key] = rows
            for row in rows:
                if (row.get("IDX_NM") or "").strip() == "코스피":
                    p = (row.get("CLSPRC_IDX") or "").replace(",", "").strip()
                    if p:
                        try:
                            out.append({"date": ds, "close": float(p)})
                        except ValueError:
                            pass
                    break
        return out

    def macro_beta(self, tickers, end_date, weeks=26, min_obs=10):
        """종목별 52주(기본 26주 weekly) 매크로 베타 = Cov(r_stock, r_kospi) / Var(r_kospi).
        반환: dict[ticker] = beta. 데이터 부족 종목은 누락.
        """
        import math
        kospi = self.kospi_index_history(end_date, weeks)
        if len(kospi) < min_obs:
            return {}

        kospi_dates = [k["date"] for k in kospi]
        # 각 ticker의 같은 날짜 종가 수집 (KRXMarket.daily가 캐시 활용)
        stock_closes = {t: [] for t in tickers}
        for ds in kospi_dates:
            daily = self.daily(ds)
            for t in tickers:
                rec = daily.get(t)
                if rec and rec.get("close"):
                    stock_closes[t].append((ds, rec["close"]))

        def log_returns(values):
            rets = []
            for i in range(1, len(values)):
                a, b = values[i-1], values[i]
                if a and b and a > 0 and b > 0:
                    rets.append(math.log(b / a))
            return rets

        kospi_ret = log_returns([k["close"] for k in kospi])
        if len(kospi_ret) < min_obs:
            return {}
        mean_k = sum(kospi_ret) / len(kospi_ret)
        var_k = sum((k - mean_k) ** 2 for k in kospi_ret) / len(kospi_ret)
        if var_k <= 0:
            return {}

        out = {}
        for t, prices in stock_closes.items():
            if len(prices) < min_obs:
                continue
            # ticker별 날짜와 KOSPI 날짜 정렬 — 동일 길이 보장
            t_dates = {p[0]: p[1] for p in prices}
            common = [(d, kospi[i]["close"], t_dates[d]) for i, d in enumerate(kospi_dates) if d in t_dates]
            if len(common) < min_obs:
                continue
            t_ret = log_returns([c[2] for c in common])
            k_ret = log_returns([c[1] for c in common])
            n = min(len(t_ret), len(k_ret))
            if n < min_obs:
                continue
            t_ret, k_ret = t_ret[-n:], k_ret[-n:]
            mean_t = sum(t_ret) / n
            mean_k_local = sum(k_ret) / n
            var_k_local = sum((k - mean_k_local) ** 2 for k in k_ret) / n
            if var_k_local <= 0:
                continue
            cov = sum((t_ret[i] - mean_t) * (k_ret[i] - mean_k_local) for i in range(n)) / n
            out[t] = cov / var_k_local
        return out

    def momentum_12_1(self, end_date, lo=-95.0, hi=1500.0):
        """12-1개월 수익률(%): close(약 1개월 전) / close(약 12개월 전) - 1.
        KRX 종가는 수정주가가 아니므로 상장주식수 변동으로 액면분할/병합/증자를 보정한다:
          adj_old = close_12m × (shares_12m / shares_1m)
        잔여 이상치(증자/합병 등)는 [lo, hi]% 로 winsorize."""
        d_recent = self.trading_dates(end_date, 22)
        ref_1m = d_recent[-1] if len(d_recent) >= 21 else (d_recent[-1] if d_recent else end_date)
        d12 = (datetime.datetime.strptime(end_date, "%Y%m%d") - datetime.timedelta(days=365))
        ref_12m = self.latest_trading_date(start=d12, lookback=10)
        p1 = self.daily(ref_1m); p12 = self.daily(ref_12m) if ref_12m else {}
        out = {}
        for t in p1:
            a = p1[t]; b = p12.get(t)
            if not b:
                continue
            c1, c12 = a["close"], b["close"]
            if not (c1 and c12 and c12 > 0):
                continue
            s1, s12 = a.get("list_shrs"), b.get("list_shrs")
            c12_adj = c12 * (s12 / s1) if (s1 and s12 and s1 > 0) else c12
            if c12_adj <= 0:
                continue
            r = (c1 / c12_adj - 1.0) * 100.0
            out[t] = max(lo, min(hi, r))
        return out, ref_1m, ref_12m


class MacroData:
    """FRED(St. Louis Fed) API — 매크로 지표 단일 소스.

    Base: https://api.stlouisfed.org/fred/series/observations?series_id=...&api_key=...&file_type=json
    무료 키: https://fred.stlouisfed.org/docs/api/api_key.html
    VIX도 FRED가 CBOE 공식 데이터(VIXCLS)로 일별 제공 → yfinance 의존성 불필요.

    매크로 단계에서 사용하는 시리즈:
      · VIXCLS   CBOE Volatility Index (VIX, 일별 종가)        [핵심]
      · DGS10    10-Year Treasury Constant Maturity Rate (%)    [핵심]
      · FEDFUNDS Federal Funds Effective Rate (월별 %)          [모니터링]
      · UNRATE   미국 실업률 (월별 %)                           [모니터링]
      · CPIAUCSL CPI All Urban Consumers (지수 → YoY 계산)      [모니터링]
      · PCEPI    PCE Price Index (지수 → YoY 계산)              [모니터링]
    """
    BASE = "https://api.stlouisfed.org/fred/series/observations"
    SERIES = {
        "vix":           ("VIXCLS",   "CBOE Volatility Index (VIX)"),
        "us10y":         ("DGS10",    "미국채 10년물 금리"),
        "fed_funds":     ("FEDFUNDS", "미국 기준금리"),
        "unemployment":  ("UNRATE",   "미국 실업률"),
        "cpi":           ("CPIAUCSL", "CPI (All Urban)"),
        "pce":           ("PCEPI",    "PCE Price Index"),
    }
    # YoY% 변환 대상(지수형 시리즈)
    YOY_SERIES = {"cpi", "pce"}

    def __init__(self, timeout=20):
        self.key = config.require("FRED_API_KEY")
        self.timeout = timeout

    def _series(self, series_id, limit=400):
        """최근 limit개 관측치(최신순) 반환: list[{date, value}]."""
        import requests
        r = requests.get(self.BASE, params={
            "series_id": series_id, "api_key": self.key, "file_type": "json",
            "limit": limit, "sort_order": "desc",
        }, timeout=self.timeout)
        r.raise_for_status()
        obs = r.json().get("observations", []) or []
        out = []
        for o in obs:
            v = o.get("value")
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue  # '.' (휴장/결측) 스킵
            out.append({"date": o.get("date"), "value": fv})
        return out

    def latest(self, series_id):
        """단일 시리즈 최신 유효값. 반환: {date, value} or None."""
        obs = self._series(series_id, limit=10)
        return obs[0] if obs else None

    def yoy(self, series_id):
        """월별 지수 시리즈의 YoY% 산출 (최신 vs 12개월 전).
        반환: {date, value(%), index_now, index_12m}."""
        obs = self._series(series_id, limit=18)
        if len(obs) < 13:
            return None
        now = obs[0]; old = obs[12]
        if not (old["value"] and old["value"] != 0):
            return None
        return {"date": now["date"],
                "value": (now["value"] / old["value"] - 1.0) * 100.0,
                "index_now": now["value"], "index_12m": old["value"]}

    def snapshot(self):
        """매크로 단계가 필요한 모든 지표 일괄 수집. dict[key] = {date, value, series_id, label}."""
        out = {}
        for k, (sid, label) in self.SERIES.items():
            try:
                rec = self.yoy(sid) if k in self.YOY_SERIES else self.latest(sid)
                if rec:
                    out[k] = {"date": rec["date"], "value": rec["value"],
                              "series_id": sid, "label": label}
            except Exception as e:
                out[k] = {"error": f"{type(e).__name__}: {str(e)[:80]}",
                          "series_id": sid, "label": label}
        return out


class DartDisclosure:
    """DART /list.json — 한국 상장사 공시 빈도 추적 (한국 테마 자동 발굴용).

    DART의 pblntf_detail_ty=I001(거래소수시공시)이 단일판매·공급계약·자사주취득·자사주소각·
    유상감자 등을 모두 포함하므로, 보고서명(report_nm) 키워드로 카테고리 분류한다.

    카테고리:
      · contract       — 수주 (단일판매·공급계약): K-방산·K-조선·전력기기 자동 발굴
      · treasury_acq   — 자기주식 취득결정: 주주환원·밸류업 신호
      · treasury_burn  — 자기주식 소각: 가장 강한 거버넌스/밸류업 신호
      · value_up       — 기업가치 제고 계획 공시: 정부 밸류업 정책 호응
      · m_and_a        — M&A·분할·합병: 거버넌스 변화 신호
    """
    BASE = "https://opendart.fss.or.kr/api/list.json"

    KEYWORD_PATTERNS = {
        # 긍정 카테고리
        "contract": ["단일판매", "공급계약"],
        "treasury_acq": ["자기주식 취득결정", "자기주식취득결정", "자사주취득"],
        "treasury_disp": ["자기주식 처분결정", "자기주식처분결정"],
        "treasury_burn": ["자기주식소각", "자기주식 소각", "주식 소각"],
        "value_up": ["기업가치 제고", "기업가치제고", "주주환원"],
        "m_and_a": ["분할", "합병", "주식매수청구"],
        # 부정 카테고리 (리스크 점수)
        "lawsuit": ["소송", "분쟁", "고소", "고발"],
        "embezzlement": ["횡령", "배임"],
        "capital_reduction": ["감자결정", "감자"],
        "convertible_recall": ["만기전 사채 취득", "사채취득", "만기전취득"],
        "default_risk": ["부도", "회생", "법정관리", "워크아웃", "기업회생"],
        "delisting_risk": ["상장폐지", "관리종목", "투자환기"],
    }

    # 카테고리 분류: 긍정/부정/중립
    POSITIVE_CATS = ("contract", "treasury_acq", "treasury_burn", "value_up")
    NEGATIVE_CATS = ("lawsuit", "embezzlement", "capital_reduction",
                     "convertible_recall", "default_risk", "delisting_risk")
    # m_and_a, treasury_disp는 사안에 따라 달라 중립으로 두고 LLM 해석

    def __init__(self, timeout=20):
        self.key = config.require("DART_API_KEY")
        self.timeout = timeout

    def fetch_filings(self, bgn_de, end_de, pblntf_detail_ty="I001", max_pages=120):
        """기간 내 공시 list 페이지네이션 수집. DART는 검색 기간 3개월 한도."""
        import requests
        out = []
        page = 1
        while page <= max_pages:
            r = requests.get(self.BASE, params={
                "crtfc_key": self.key, "bgn_de": bgn_de, "end_de": end_de,
                "pblntf_detail_ty": pblntf_detail_ty,
                "page_no": page, "page_count": 100,
            }, timeout=self.timeout)
            r.raise_for_status()
            j = r.json()
            if j.get("status") != "000":
                break
            items = j.get("list") or []
            out.extend(items)
            total_page = int(j.get("total_page", 1) or 1)
            if page >= total_page:
                break
            page += 1
        return out

    @classmethod
    def categorize(cls, items):
        """report_nm 키워드로 카테고리 분류."""
        out = {k: [] for k in cls.KEYWORD_PATTERNS}
        out["other"] = []
        for it in items:
            nm = (it.get("report_nm") or "")
            matched = False
            for cat, patterns in cls.KEYWORD_PATTERNS.items():
                if any(p in nm for p in patterns):
                    out[cat].append(it)
                    matched = True
                    break
            if not matched:
                out["other"].append(it)
        return out

    def disclosure_growth(self, recent_days=30, base_days=60):
        """최근 N일 vs 베이스 M일 카테고리별 공시 빈도 증가율(%)."""
        import datetime as _dt
        end_date = _dt.date.today()
        base_start = end_date - _dt.timedelta(days=base_days)
        recent_start = end_date - _dt.timedelta(days=recent_days)
        recent_cutoff_str = recent_start.strftime("%Y%m%d")

        # 1번 fetch로 base 전체 받고 클라이언트에서 윈도우 분류
        all_items = self.fetch_filings(base_start.strftime("%Y%m%d"),
                                        end_date.strftime("%Y%m%d"))
        recent_items = [it for it in all_items if (it.get("rcept_dt") or "") >= recent_cutoff_str]

        base_cat = self.categorize(all_items)
        rec_cat = self.categorize(recent_items)

        out = {"recent_days": recent_days, "base_days": base_days,
               "n_total_recent": len(recent_items), "n_total_base": len(all_items),
               "categories": {}}
        for cat in list(self.KEYWORD_PATTERNS) + ["other"]:
            n_r = len(rec_cat[cat])
            n_b = len(base_cat[cat])
            rate_r = n_r / recent_days
            rate_b = n_b / base_days if base_days else 0
            growth = ((rate_r / rate_b - 1.0) * 100.0) if rate_b > 0 else (None if n_r == 0 else float("inf"))
            out["categories"][cat] = {
                "n_recent": n_r, "n_base": n_b, "growth_pct": growth,
                "sample_companies": list({(it.get("corp_name") or "")[:20]
                                          for it in rec_cat[cat][:8]})[:5],
            }
        return out

    def disclosures_by_corp(self, recent_days=30, base_days=60):
        """회사별 카테고리별 공시 dict 반환 (섹터별 집계 입력용).
        반환: {corp_code(또는 stock_code): {cat: count_recent, base_count, ...}}"""
        import datetime as _dt
        end_date = _dt.date.today()
        base_start = end_date - _dt.timedelta(days=base_days)
        recent_cutoff = (end_date - _dt.timedelta(days=recent_days)).strftime("%Y%m%d")

        all_items = self.fetch_filings(base_start.strftime("%Y%m%d"),
                                        end_date.strftime("%Y%m%d"))
        out = {}
        for it in all_items:
            cc = it.get("corp_code")
            sc = it.get("stock_code") or ""
            if not cc:
                continue
            nm = it.get("report_nm") or ""
            dt = it.get("rcept_dt") or ""
            cat = "other"
            for k, patterns in self.KEYWORD_PATTERNS.items():
                if any(p in nm for p in patterns):
                    cat = k; break
            key = sc if (sc and sc.isdigit() and len(sc) == 6) else cc
            out.setdefault(key, {"corp_code": cc, "stock_code": sc, "categories": {}})
            slot = out[key]["categories"].setdefault(cat, {"recent": 0, "base": 0})
            slot["base"] += 1
            if dt >= recent_cutoff:
                slot["recent"] += 1
        return out


class WeakSignals:
    """투자 아이디어 발굴 단계의 약한 신호 자동 수집기.

    무료 공개 API만 사용 (대부분 키 불필요):
      [연구·기술 차원]
        · arXiv API               — 키워드별 논문 수 증가율 (30d vs 180d)
        · Google Patents (xhr)    — 글로벌 특허 출원 수 증가율 (6M vs 24M, R&D 실투자 증거)
      [개발자·얼리어답터 차원]
        · GitHub Search API       — 키워드 신규 repo 증가율 (30d vs 180d)
        · HackerNews Algolia      — 게시물 증가율 (30d vs 180d, VC/테크 트래픽)
      [대중 차원]
        · Wikipedia Pageviews     — 페이지 조회수 (4w vs 12w)
        · Google Trends (옵션)    — pytrends 설치 시 검색량 (4w vs 12w, best-effort)
      [사업화·정책 차원]
        · SEC EDGAR full-text     — 미국 상장사 공시 키워드 mention (3M vs 12M, Verifiability)
        · federalregister.gov     — 미국 연방 정책/규정 mention (90d vs 365d, Policy 자동화)

    각 지표는 (recent_rate / base_rate - 1) × 100 = 일별 정규화 증가율(%)로 표준화.
    완전 자동화의 한계: VC 자금, ETF Flow는 LLM 리서치(WebFetch)로 보완.
    """
    ARXIV_BASE = "http://export.arxiv.org/api/query"
    WIKI_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
    GITHUB_BASE = "https://api.github.com/search/repositories"
    HN_BASE = "https://hn.algolia.com/api/v1/search"
    SEC_EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index"
    GPATENTS_BASE = "https://patents.google.com/xhr/query"
    FED_REG_BASE = "https://www.federalregister.gov/api/v1/documents.json"
    NAVER_NEWS_BASE = "https://openapi.naver.com/v1/search/news.json"
    # 국회 의안정보 — 의안목록 (공공데이터포털 인증키 또는 open.assembly.go.kr 발급키)
    ASSEMBLY_BILLS_BASE = "https://open.assembly.go.kr/portal/openapi/nzmimeepazxkubxpm"
    UA = "InvestmentPipeline/1.0 (smclan16@gmail.com)"

    def __init__(self, timeout=60, github_token=None):
        self.timeout = timeout
        self.github_token = github_token or config.get("GITHUB_TOKEN")  # 옵션: 60→5000 req/h
        self.naver_id = config.get("NAVER_CLIENT_ID")
        self.naver_secret = config.get("NAVER_CLIENT_SECRET")
        self.data_go_kr_key = config.get("DATA_GO_KR_KEY")

    @staticmethod
    def _growth(n_recent, n_base, days_recent, days_base):
        """일별 정규화 증가율(%). base가 0이면 None(시계열 없음) 또는 inf(완전 신규)."""
        rate_r = n_recent / days_recent
        rate_b = n_base / days_base if days_base else 0
        if rate_b > 0:
            return (rate_r / rate_b - 1.0) * 100.0
        return None if n_recent == 0 else float("inf")

    def arxiv_paper_growth(self, keyword, recent_days=30, base_days=180, max_results=1000):
        """arXiv 키워드 검색의 최근/베이스라인 논문 수 증가율(%).
        base_days 윈도우 내 논문 수 N_b, recent_days 윈도우 내 논문 수 N_r 비교.
        - growth_pct = (N_r/recent_days) / (N_b/base_days) - 1, ×100
        max_results는 base_days를 모두 커버할 수 있도록 넉넉히(2000) — 인기 키워드는 일별 5건+
        truncated=True 시 베이스 윈도우가 잘렸으니 growth_pct는 보수적으로 해석.
        """
        import requests, urllib.parse, xml.etree.ElementTree as ET
        q = urllib.parse.quote(f'all:"{keyword}"')
        url = f"{self.ARXIV_BASE}?search_query={q}&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
        r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
        r.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(r.text)
        now = datetime.datetime.utcnow()
        cutoff_r = now - datetime.timedelta(days=recent_days)
        cutoff_b = now - datetime.timedelta(days=base_days)
        entries = root.findall("a:entry", ns)
        n_r = n_b = 0
        oldest = None
        for entry in entries:
            ds = entry.findtext("a:published", default="", namespaces=ns)
            if not ds:
                continue
            try:
                d = datetime.datetime.strptime(ds[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
            oldest = d if (oldest is None or d < oldest) else oldest
            if d >= cutoff_b:
                n_b += 1
                if d >= cutoff_r:
                    n_r += 1
        # base 윈도우가 max_results에 잘렸는지 판정: 가장 오래된 entry가 base 컷오프보다 늦으면 잘림
        truncated = (oldest is not None and oldest > cutoff_b and len(entries) >= max_results)
        rate_r = n_r / recent_days
        rate_b = (n_b / base_days) if n_b else 0
        growth = ((rate_r / rate_b - 1.0) * 100.0) if rate_b > 0 else (None if n_r == 0 else float("inf"))
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "days_recent": recent_days, "days_base": base_days,
                "growth_pct": growth, "truncated": truncated}

    def wiki_pageview_growth(self, article, recent_weeks=4, base_weeks=12, project="en.wikipedia"):
        """Wikipedia Pageviews API로 최근 4주 vs 12주 평균 조회수 증가율(%).
        article: 위키 페이지 슬러그(예: 'Artificial_intelligence'). 한글은 'ko.wikipedia' + 한글 슬러그.
        반환: {article, avg_recent, avg_base, growth_pct}
        """
        import requests, urllib.parse
        end = datetime.datetime.utcnow() - datetime.timedelta(days=2)  # API 지연 보정
        start = end - datetime.timedelta(weeks=base_weeks)
        url = (f"{self.WIKI_BASE}/{project}/all-access/all-agents/"
               f"{urllib.parse.quote(article, safe='')}/daily/"
               f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}")
        r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
        if r.status_code == 404:
            return {"article": article, "error": "404 (페이지 없음)", "growth_pct": None}
        r.raise_for_status()
        items = r.json().get("items", []) or []
        if not items:
            return {"article": article, "error": "empty", "growth_pct": None}
        views = [(it.get("timestamp", ""), it.get("views", 0)) for it in items]
        views.sort(key=lambda x: x[0])
        recent_cut = (end - datetime.timedelta(weeks=recent_weeks)).strftime("%Y%m%d")
        recent_vals = [v for ts, v in views if ts[:8] >= recent_cut]
        base_vals = [v for _, v in views]
        avg_r = sum(recent_vals) / len(recent_vals) if recent_vals else 0
        avg_b = sum(base_vals) / len(base_vals) if base_vals else 0
        growth = ((avg_r / avg_b - 1.0) * 100.0) if avg_b > 0 else None
        return {"article": article, "project": project, "avg_recent": avg_r, "avg_base": avg_b,
                "growth_pct": growth, "n_days": len(views)}

    def google_trends_growth(self, keywords, recent_weeks=4, base_weeks=12, geo=""):
        """Google Trends 4w/12w 비교 — pytrends 설치 시에만 동작 (best-effort).
        반환: dict[keyword] = {avg_recent, avg_base, growth_pct} 또는 {error}.
        """
        try:
            from pytrends.request import TrendReq
        except ImportError:
            return {k: {"error": "pytrends not installed (pip install pytrends)"} for k in keywords}
        out = {}
        try:
            tr = TrendReq(hl="en-US", tz=540, timeout=(10, 20))  # KST
            tr.build_payload(keywords[:5], timeframe=f"today 3-m", geo=geo)
            df = tr.interest_over_time()
            if df is None or len(df) == 0:
                return {k: {"error": "empty Trends response"} for k in keywords}
            n = len(df)
            split = max(1, n - recent_weeks)
            for k in keywords[:5]:
                if k not in df.columns:
                    out[k] = {"error": "keyword not in response"}; continue
                recent = float(df[k].iloc[split:].mean())
                base = float(df[k].mean())
                growth = ((recent / base - 1.0) * 100.0) if base > 0 else None
                out[k] = {"avg_recent": recent, "avg_base": base, "growth_pct": growth}
        except Exception as e:
            return {k: {"error": f"{type(e).__name__}: {str(e)[:80]}"} for k in keywords}
        return out

    # ── 신규 5개 신호 메서드 ─────────────────────────────────────

    def github_repo_growth(self, keyword, recent_days=30, base_days=180):
        """GitHub 키워드 신규 repo 수 증가율(%). 개발자/얼리어답터 신호.
        토큰(GITHUB_TOKEN) 있으면 60→5000 req/h.
        반환: {keyword, n_recent, n_base, growth_pct, rate_limit_remaining}
        """
        import requests, urllib.parse
        headers = {"User-Agent": self.UA, "Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        today = datetime.date.today()
        d_r = (today - datetime.timedelta(days=recent_days)).isoformat()
        d_b = (today - datetime.timedelta(days=base_days)).isoformat()

        def _count(since):
            q = f'"{keyword}" created:>{since}'
            url = f"{self.GITHUB_BASE}?q={urllib.parse.quote(q)}&per_page=1"
            r = requests.get(url, headers=headers, timeout=self.timeout)
            if r.status_code == 422:  # 한도 초과(>1000건은 미지원) → 따옴표 제거 재시도
                q = f"{keyword} created:>{since}"
                r = requests.get(f"{self.GITHUB_BASE}?q={urllib.parse.quote(q)}&per_page=1",
                                 headers=headers, timeout=self.timeout)
            r.raise_for_status()
            return int((r.json() or {}).get("total_count", 0)), r.headers.get("X-RateLimit-Remaining")

        n_r, rem = _count(d_r)
        n_b, _   = _count(d_b)
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days),
                "rate_limit_remaining": rem}

    def hackernews_mention_growth(self, keyword, recent_days=30, base_days=180):
        """HackerNews Algolia — 키워드 게시물 증가율(%). VC/창업가/테크 트래픽 신호."""
        import requests, urllib.parse
        now = int(datetime.datetime.utcnow().timestamp())

        def _count(days):
            ts = now - days * 86400
            url = (f"{self.HN_BASE}?query={urllib.parse.quote(keyword)}"
                   f"&tags=story&numericFilters=created_at_i>{ts}&hitsPerPage=0")
            r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
            r.raise_for_status()
            return int((r.json() or {}).get("nbHits", 0))

        n_r = _count(recent_days)
        n_b = _count(base_days)
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days)}

    def sec_edgar_growth(self, keyword, recent_days=90, base_days=365, forms=("10-K",), retries=2):
        """SEC EDGAR full-text — 미국 상장사 공시(10-K 등) 키워드 mention 증가율(%).
        Verifiability·Capital Inflow 보강(기업이 사업화로 인식하기 시작했나).
        forms: 단일 form만 정확히 동작 (콤마 결합은 500 에러). 여러 form 필요 시 합산.
        retries: 500 발생 시 인용구→평문 폴백 후 재시도. (일부 키워드 인용구 처리 이슈 대응)
        """
        import requests, urllib.parse, time
        today = datetime.date.today()

        def _count(days, form):
            start = (today - datetime.timedelta(days=days)).isoformat()
            for attempt in range(retries + 1):
                # 1차: 인용구 검색. 폴백: 평문(단어 결합)
                if attempt == 0:
                    q = f'%22{urllib.parse.quote(keyword)}%22'
                else:
                    q = urllib.parse.quote(keyword)
                url = (f"{self.SEC_EDGAR_BASE}?q={q}"
                       f"&dateRange=custom&startdt={start}&enddt={today.isoformat()}"
                       f"&forms={form}")
                try:
                    r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
                    r.raise_for_status()
                    return int(((r.json() or {}).get("hits") or {}).get("total", {}).get("value", 0))
                except requests.HTTPError:
                    if attempt < retries:
                        time.sleep(1.5); continue
                    raise

        n_r = sum(_count(recent_days, f) for f in forms)
        n_b = sum(_count(base_days, f) for f in forms)
        return {"keyword": keyword, "forms": list(forms),
                "n_recent": n_r, "n_base": n_b,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days)}

    def patent_growth(self, keyword, recent_days=180, base_days=730):
        """Google Patents(xhr) — 글로벌 특허 출원 증가율(%). R&D 실투자 증거(Durability).
        비공식 endpoint이나 동작 안정적. priority date 기준."""
        import requests, urllib.parse
        today = datetime.date.today()

        def _count(days):
            d = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
            inner = f'q={keyword}&after=priority:{d}&before=priority:{today.strftime("%Y%m%d")}&num=1'
            url = f"{self.GPATENTS_BASE}?url={urllib.parse.quote(inner)}"
            r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
            r.raise_for_status()
            j = r.json() or {}
            return int(((j.get("results") or {}).get("total_num_results")) or 0)

        n_r = _count(recent_days)
        n_b = _count(base_days)
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days)}

    def naver_news_growth(self, keyword, recent_days=30, base_days=180, max_pages=10):
        """네이버 뉴스 검색 API — 한국 미디어 키워드 mention 증가율(%).
        한국 측면의 대중·미디어 인지도 (미국 SEC/Wikipedia 영문판의 한국 대응).
        sort=date 최신순으로 max_pages × 100개 회수 → 클라이언트에서 윈도우 카운팅.
        반환: {keyword, n_recent, n_base, growth_pct, truncated}
        """
        if not (self.naver_id and self.naver_secret):
            return {"keyword": keyword, "error": "NAVER_CLIENT_ID/SECRET not set in .env"}
        import requests, urllib.parse, email.utils
        headers = {"X-Naver-Client-Id": self.naver_id,
                   "X-Naver-Client-Secret": self.naver_secret,
                   "User-Agent": self.UA}
        now = datetime.datetime.utcnow()
        cutoff_r = now - datetime.timedelta(days=recent_days)
        cutoff_b = now - datetime.timedelta(days=base_days)
        n_r = n_b = 0
        oldest = None
        total = None
        for page in range(max_pages):
            start = page * 100 + 1
            if start > 1000:  # API 상한
                break
            url = (f"{self.NAVER_NEWS_BASE}?query={urllib.parse.quote(keyword)}"
                   f"&display=100&start={start}&sort=date")
            r = requests.get(url, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            j = r.json()
            total = j.get("total")
            items = j.get("items", []) or []
            if not items:
                break
            for it in items:
                try:
                    d = datetime.datetime(*email.utils.parsedate(it.get("pubDate", ""))[:6])
                except (TypeError, ValueError):
                    continue
                oldest = d if (oldest is None or d < oldest) else oldest
                if d >= cutoff_b:
                    n_b += 1
                    if d >= cutoff_r:
                        n_r += 1
            # 마지막 page에서 가장 오래된 항목이 base 컷오프보다 늦으면 다음 페이지
            if items and oldest and oldest < cutoff_b:
                break
        truncated = (oldest is not None and oldest > cutoff_b and (total or 0) > n_b)
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "naver_total_match": total,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days),
                "truncated": truncated}

    def assembly_bill_growth(self, keyword, recent_days=180, base_days=730, age=22):
        """국회 의안정보 — 의안명에 키워드 포함 의안 증가율(%). 한국 정책/법안 모멘텀.
        age: 국회 대수 (22 = 22대, 2024.05~2028.05). 6개월 vs 24개월 윈도우(법안 주기 고려).
        반환: {keyword, n_recent, n_base, growth_pct}
        """
        if not self.data_go_kr_key:
            return {"keyword": keyword, "error": "DATA_GO_KR_KEY not set in .env"}
        import requests, urllib.parse
        today = datetime.date.today()
        # 의안목록 API: BILL_NAME LIKE 검색. 페이지네이션으로 base_days 윈도우 회수.
        n_r = n_b = 0
        page, page_size = 1, 1000
        cutoff_r = today - datetime.timedelta(days=recent_days)
        cutoff_b = today - datetime.timedelta(days=base_days)
        oldest_in_window = None
        while True:
            url = (f"{self.ASSEMBLY_BILLS_BASE}?KEY={self.data_go_kr_key}&Type=json"
                   f"&pIndex={page}&pSize={page_size}&AGE={age}"
                   f"&BILL_NAME={urllib.parse.quote(keyword)}")
            r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
            r.raise_for_status()
            j = r.json()
            # 응답 구조: {nzmimeepazxkubxpm: [{head:[...]}, {row:[...]}]} 또는 {RESULT:{CODE,MESSAGE}}
            root = j.get("nzmimeepazxkubxpm") or []
            if not isinstance(root, list) or len(root) < 2:
                if isinstance(j.get("RESULT"), dict):
                    msg = j["RESULT"].get("MESSAGE", "")
                    if "INFO-200" in str(j["RESULT"].get("CODE", "")):  # 해당 데이터 없음
                        break
                    return {"keyword": keyword, "error": f"API: {msg}"}
                break
            rows = root[1].get("row", []) or []
            if not rows:
                break
            for r0 in rows:
                ds = r0.get("PROPOSE_DT", "")  # YYYY-MM-DD
                try:
                    d = datetime.datetime.strptime(ds[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if d >= cutoff_b:
                    n_b += 1
                    if d >= cutoff_r:
                        n_r += 1
                    oldest_in_window = d if (oldest_in_window is None or d < oldest_in_window) else oldest_in_window
            if len(rows) < page_size:
                break
            page += 1
            if page > 5:  # 최대 5000건 회수 (안전 한도)
                break
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days)}

    def federal_register_growth(self, keyword, recent_days=90, base_days=365):
        """federalregister.gov — 미국 연방 정책/규정 문서 키워드 mention 증가율(%).
        Policy Momentum 직접 자동화 (LLM 추정 → 측정으로 격상)."""
        import requests, urllib.parse
        today = datetime.date.today()

        def _count(days):
            start = (today - datetime.timedelta(days=days)).isoformat()
            url = (f"{self.FED_REG_BASE}?conditions[term]={urllib.parse.quote(keyword)}"
                   f"&conditions[publication_date][gte]={start}&per_page=1")
            r = requests.get(url, headers={"User-Agent": self.UA}, timeout=self.timeout)
            r.raise_for_status()
            return int((r.json() or {}).get("count", 0))

        n_r = _count(recent_days)
        n_b = _count(base_days)
        return {"keyword": keyword, "n_recent": n_r, "n_base": n_b,
                "growth_pct": self._growth(n_r, n_b, recent_days, base_days)}


class DartSector:
    """Open DART(전자공시) — 종목별 업종(KSIC) 분류. 섹터중립 z-score용.

    corpCode.xml(전 corp_code↔stock_code 매핑, ZIP) 1회 다운로드(캐시) +
    company.json(corp_code별 induty_code) → KSIC 2자리 division → 광의 섹터 라벨.
    """
    BASE = "https://opendart.fss.or.kr/api"
    CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

    # KSIC 2자리(division) → 광의 섹터
    KSIC2 = {
        "01":"농림어업","02":"농림어업","03":"농림어업","05":"광업","06":"광업","07":"광업","08":"광업",
        "10":"음식료","11":"음식료","12":"음식료","13":"섬유의류","14":"섬유의류","15":"섬유의류",
        "16":"목재제지","17":"목재제지","18":"목재제지","19":"정유","20":"화학","21":"제약바이오",
        "22":"고무플라스틱","23":"비금속","24":"철강금속","25":"금속가공","26":"전자반도체","27":"의료정밀",
        "28":"전기장비","29":"기계","30":"자동차","31":"운송장비","32":"기타제조","33":"기타제조",
        "35":"유틸리티","36":"유틸리티","37":"환경","38":"환경","39":"환경",
        "41":"건설","42":"건설","45":"유통","46":"유통","47":"유통","49":"운송물류","50":"운송물류",
        "51":"운송물류","52":"운송물류","55":"숙박음식","56":"숙박음식",
        "58":"미디어콘텐츠","59":"미디어콘텐츠","60":"미디어콘텐츠","61":"통신","62":"IT서비스","63":"IT서비스",
        "64":"금융","65":"보험","66":"금융","68":"부동산","70":"전문서비스","71":"전문서비스","72":"전문서비스",
        "73":"전문서비스","74":"전문서비스","75":"전문서비스",
    }

    def __init__(self, timeout=30):
        self.key = config.require("DART_API_KEY")
        self.timeout = timeout
        os.makedirs(self.CACHE, exist_ok=True)
        self._corpmap = None

    def _cache_path(self, name):
        return os.path.join(self.CACHE, name)

    def corp_map(self, refresh=False):
        """dict[stock_code(6)] = corp_code(8). corpCode.xml ZIP 1회 다운로드 후 캐시."""
        if self._corpmap is not None and not refresh:
            return self._corpmap
        cp = self._cache_path("dart_corpmap.json")
        if os.path.exists(cp) and not refresh:
            self._corpmap = json.load(open(cp, encoding="utf-8"))
            return self._corpmap
        import requests, io, zipfile, xml.etree.ElementTree as ET, json as _json
        r = requests.get(f"{self.BASE}/corpCode.xml", params={"crtfc_key": self.key}, timeout=self.timeout)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml = z.read(z.namelist()[0])
        root = ET.fromstring(xml)
        m = {}
        for it in root.iter("list"):
            sc = (it.findtext("stock_code") or "").strip()
            cc = (it.findtext("corp_code") or "").strip()
            if sc and sc.isdigit() and len(sc) == 6 and cc:
                m[sc] = cc
        self._corpmap = m
        _json.dump(m, open(cp, "w", encoding="utf-8"))
        return m

    def ksic_to_sector(self, code):
        if not code:
            return "미분류"
        d2 = str(code).strip()[:2]
        return self.KSIC2.get(d2, "기타")

    def industry(self, stock_codes, use_cache=True):
        """dict[stock_code] = {'induty_code', 'sector'}. company.json 종목별 1콜(캐시)."""
        import requests, json as _json
        cp = self._cache_path("dart_industry.json")
        cache = {}
        if use_cache and os.path.exists(cp):
            cache = _json.load(open(cp, encoding="utf-8"))
        cm = self.corp_map()
        out = {}
        dirty = False
        for sc in stock_codes:
            sc = str(sc)
            if use_cache and sc in cache:
                out[sc] = cache[sc]; continue
            cc = cm.get(sc)
            if not cc:
                out[sc] = {"induty_code": None, "sector": "미분류"}; continue
            try:
                r = requests.get(f"{self.BASE}/company.json",
                                 params={"crtfc_key": self.key, "corp_code": cc}, timeout=self.timeout)
                j = r.json()
                ind = j.get("induty_code") if j.get("status") == "000" else None
            except Exception:
                ind = None
            rec = {"induty_code": ind, "sector": self.ksic_to_sector(ind)}
            out[sc] = rec; cache[sc] = rec; dirty = True
        if dirty:
            _json.dump(cache, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        return out


def check_capabilities():
    """현재 키로 어떤 데이터 소스가 LIVE/DEFERRED 인지 1종목 기준 진단."""
    caps = {}
    fn = FNSPACE_KEY()
    if fn:
        from fnspace import FnSpace
        fs = FnSpace(fn)
        yr = datetime.datetime.now().year
        probes = {
            "fnspace:consensus-earning-fiscal": dict(category="consensus-earning-fiscal", code=["005930"],
                item=["E231000"], from_year=str(yr), to_year=str(yr)),
            "fnspace:consensus-forward": dict(category="consensus-forward", code=["005930"],
                item=["E211570"], from_date="20260401",
                to_date=datetime.datetime.now().strftime("%Y%m%d")),
            "fnspace:consensus-price": dict(category="consensus-price", code=["005930"],
                item=["E610360"], from_date="20260401",
                to_date=datetime.datetime.now().strftime("%Y%m%d")),
            "fnspace:account(재무)": dict(category="account", code=["005930"], item=["M211500"],
                from_year=str(yr - 1), to_year=str(yr - 1)),
            "fnspace:stock_price(거래대금/거래정지/시총)": dict(category="stock_price", code=["005930"],
                item=["S106410"], from_date="20260501",
                to_date=datetime.datetime.now().strftime("%Y%m%d")),
            "fnspace:stock_list(종목마스터)": dict(category="stock_list", mkttype="4"),
        }
        for name, kw in probes.items():
            try:
                df = fs.get_data(**kw)
                caps[name] = "LIVE" if (df is not None and len(df)) else "DEFERRED(권한없음/빈응답)"
            except Exception as e:
                caps[name] = f"DEFERRED({type(e).__name__})"
    else:
        caps["fnspace"] = "키 미설정"

    fh = FINNHUB_KEY()
    if fh:
        import finnhub
        cli = finnhub.Client(api_key=fh)
        try:
            cli.company_basic_financials("005930.KS", "all")
            caps["finnhub:한국주식"] = "LIVE"
        except Exception:
            caps["finnhub:한국주식"] = "DEFERRED(무료티어 미지원/403)"
        try:
            ok = bool(cli.company_basic_financials("AAPL", "all").get("metric"))
            caps["finnhub:미국주식"] = "LIVE" if ok else "DEFERRED"
        except Exception as e:
            caps["finnhub:미국주식"] = f"DEFERRED({type(e).__name__})"
    else:
        caps["finnhub"] = "키 미설정"

    if config.get("KRX_API_KEY"):
        try:
            k = KRXMarket()
            d = k.latest_trading_date()
            if d:
                u = k.daily(d)
                caps["krx:openapi(일별매매/종목)"] = f"LIVE (기준일 {d}, 종목 {len(u)})"
            else:
                caps["krx:openapi(일별매매/종목)"] = "DEFERRED(가용 영업일 없음/빈응답)"
        except Exception as e:
            caps["krx:openapi(일별매매/종목)"] = f"DEFERRED({type(e).__name__}: {str(e)[:60]})"
    else:
        caps["krx:openapi"] = "키 미설정"

    if config.get("DART_API_KEY"):
        try:
            ds = DartSector()
            ind = ds.industry(["005930"])
            rec = ind.get("005930", {})
            caps["dart:업종(induty)"] = (f"LIVE (005930 induty={rec.get('induty_code')} → {rec.get('sector')})"
                                       if rec.get("induty_code") else "DEFERRED(업종 응답없음)")
        except Exception as e:
            caps["dart:업종(induty)"] = f"DEFERRED({type(e).__name__}: {str(e)[:60]})"
    else:
        caps["dart:업종"] = "키 미설정"

    try:
        from pykrx import stock
        df = stock.get_market_cap_by_ticker("20250902", market="KOSPI")
        caps["krx:pykrx(스크래핑)"] = "LIVE" if (df is not None and len(df)) else "DEFERRED(차단/빈응답)"
    except Exception:
        caps["krx:pykrx(스크래핑)"] = "DEFERRED(차단)"
    return caps


if __name__ == "__main__":
    import json
    print(json.dumps(check_capabilities(), ensure_ascii=False, indent=2))
