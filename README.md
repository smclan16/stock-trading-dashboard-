# 📊 투자 의사결정 자동화 대시보드

8단계 Top-Down 파이프라인 — 매크로 → 유니버스 → 아이디어 → 매칭 → 리서치 → 포트폴리오 → 시그널 → HITL

## 🚀 Streamlit Cloud 배포

이 저장소는 [Streamlit Cloud](https://share.streamlit.io)에 자동 배포됩니다.

**메인 앱 경로:** `_workspace/09_app/app.py`

## 🔐 API 키 등록

Streamlit Cloud의 **Settings → Secrets** 에서 `.streamlit/secrets.toml.template` 양식대로 등록.

필요 키:
- KRX_API_KEY (한국거래소 Open API)
- DART_API_KEY (Open DART)
- FNSPACE_API_KEY (FnSpace, 종목당 과금)
- FRED_API_KEY (FRED, 무료)
- NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (네이버 검색)
- GITHUB_TOKEN (GitHub Search)

## 💻 로컬 실행

```bash
pip install -r requirements.txt
cd _workspace/09_app
streamlit run app.py
```

API 키는 `_workspace/.env` 에 등록 (Streamlit Cloud는 secrets.toml 자동 사용).

## 📂 구조

```
_workspace/
├── 01_profile ~ 08_signals/    # 8단계 파이프라인
├── lib/datasource.py            # KRX·FRED·FnSpace·DART 어댑터
├── validation/                  # 백테스트·정합성 검증
└── 09_app/                      # Streamlit 대시보드
    ├── app.py
    ├── pages/                   # 5개 페이지
    ├── lib/                     # loader/db/perf/costs
    └── data/portfolio.db        # SQLite (gitignore)
```

## 📋 페이지

1. **🏠 홈** — 8단계 KPI + 시그널 요약
2. **📝 포트폴리오 입력** — 체결·배당 등록 (키움 비용 자동)
3. **📈 수익률 관리** — 누적·KOSPI 대비·테마별 기여도
4. **🧭 파이프라인 상세** — 8단계 로직·조건·산출물
5. **⚙️ 운영 가이드** — 매일/주간 실행
6. **🤖 자동매매 시뮬레이션** — 라이브 + 백테스트 (12M·24M)

## ⚠ 면책

본 자료는 정보 제공 목적의 분석 결과이며 투자 권유가 아닙니다. 모든 매매 결정의 책임은 투자자 본인에게 있습니다.
