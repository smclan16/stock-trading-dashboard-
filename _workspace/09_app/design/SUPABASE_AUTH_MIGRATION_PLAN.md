# Supabase 멀티유저 마이그레이션 플랜 (확정본)

> 목표: **(1) Supabase에 데이터 저장 · (2) 여러 사용자 Google 로그인 · (3) 사용자 간 데이터 덮어쓰기 문제 해결**
> 작성: 2026-05-29 · 상태: 확정(구현 진행)

---

## 1. 문제 원인 (코드 확인 완료)

`lib/db.py`는 단일 SQLite 파일(`data/portfolio.db`)을 사용하며 **사용자 개념이 전혀 없다.**
모든 사용자가 동일한 `trades` / `simulations` / `simulated_trades` / `portfolio_meta` 테이블을 공유한다.

```python
def list_trades(...):
    rows = conn.execute("SELECT * FROM trades ...")   # 누구의 거래든 전부 반환
```

→ B가 입력하면 A와 같은 테이블에 섞인다. + Streamlit Cloud 재배포 시 파일 휘발.
**두 문제 모두 Supabase + 사용자 스코프로 해결.**

---

## 2. 확정 아키텍처

```
[브라우저] --Google 로그인--> [Streamlit 네이티브 st.login (서버사이드 OAuth)]
                                      │  st.user (sub, email)
                                      ▼
                          [lib/auth.py] user_id = uuid5(google_sub)
                                      │
                          [lib/db.py] 모든 쿼리를 user_id로 스코프
                                      ▼
                          [Supabase Postgres] (service_role, 서버사이드 전용)
                                      RLS 활성화(방어선)
```

### 왜 Streamlit 네이티브 로그인인가 (중요 결정)
처음 구상은 "Supabase가 로그인까지 처리"였으나, Supabase Auth의 OAuth(PKCE)는
Streamlit이 매 상호작용마다 스크립트를 재실행하고 외부 리디렉트 후 세션을 새로 시작하는 구조와 충돌해
**`code_verifier`가 유실**되는 고질적 문제가 있다.

→ Streamlit 1.42+ **네이티브 `st.login("google")`** 이 OAuth 왕복을 서버사이드 쿠키로 안정 처리한다.
   (현재 설치 버전 1.50 → 사용 가능 확인 완료)
→ **로그인 = Streamlit + Google**, **데이터 저장/격리 = Supabase**. 역할 분리.

### 데이터 격리 방식
- 모든 사용자 테이블에 `user_id uuid` 컬럼.
- 접근은 **`lib/db.py` 단일 계층**에서만 일어나며, 항상 현재 로그인 사용자의 `user_id`로 스코프.
- Supabase 연결은 서버사이드(`service_role` 키, 브라우저 노출 없음)이며 RLS는 **방어선(defense-in-depth)** 으로 활성화.
- (선택 업그레이드) 추후 프로젝트 JWT로 사용자 토큰을 발급하면 DB가 직접 RLS를 강제하도록 강화 가능. 현 단계는 단일 데이터 계층 스코프로 충분.

---

## 3. 데이터 분류 — 무엇을 사용자별로 나눌지

| 대상 | 처리 | 이유 |
|------|------|------|
| `trades`, `simulations`, `simulated_trades`, `portfolio_meta` | **사용자별** (`user_id` + 스코프) | 개인 거래/시뮬 데이터 — 덮어쓰기 원인 |
| `price_cache` | **공유** (user_id 없음) | 시세는 만인 공통, 캐시 효율 |
| 파이프라인 산출물 JSON (`loader.py`: universe/ideas/signals/portfolio …) | **공유** (파일 유지) | 분석 결과는 모두에게 동일 |
| 투자성향 진단 결과(`01_profile/constraints.json`) | **현 단계 공유** (Phase 2에서 사용자별 전환) | 개인화이나 파이프라인 재생성과 엮여 별도 단계 |

> ⚠️ **알려진 한계(Phase 2):** 투자성향 진단·모델 포트 생성은 현재 전역 1세트다.
> 이번 작업은 **사용자가 직접 입력하는 거래/시뮬 데이터**의 격리에 집중(요청하신 덮어쓰기 문제의 핵심).

---

## 4. 스키마 (Postgres)

`supabase_schema.sql` 참조. 요약:

| 테이블 | 변경점 |
|--------|--------|
| `trades` | `id bigint identity`, **`user_id uuid not null`**, `created_at timestamptz default now()` |
| `simulations` | 동일 + `user_id` |
| `simulated_trades` | 동일 + `user_id`, `sim_id` FK |
| `portfolio_meta` | PK = **(`user_id`, `key`)** |
| `price_cache` | 공유. PK = (ticker, trade_date). user_id 없음 |

- 모든 테이블 **RLS 활성화**.
- 사용자 테이블 정책: `(auth.jwt() ->> 'sub') = user_id::text` 형태(추후 JWT 강화 시)지만, 현 단계는 service_role 연결이라 앱 계층이 enforcement. RLS는 anon/authenticated 키 노출 대비 최소 권한.
- 인덱스: `(user_id, trade_date)`, `(user_id, ticker)`, `simulated_trades(user_id, sim_id)`.

---

## 5. 코드 변경

| 파일 | 작업 |
|------|------|
| `lib/supa.py` (신규) | Supabase 클라이언트 팩토리 (`@st.cache_resource`, service_role) |
| `lib/auth.py` (신규) | `require_login()`, `current_user_id()`, `logout_button()` |
| `lib/db.py` (교체) | sqlite3 → supabase-py. 함수 시그니처 유지, 모든 쿼리에 user_id 스코프. positions 계산 로직 그대로 |
| `app.py` + `pages/*.py` (7개) | import에 `auth` 추가 + `set_page_config` 직후 `auth.require_login()` / 사이드바 `auth.logout_button()` |
| `requirements.txt` | `supabase`, `Authlib>=1.3.2` 추가 |
| `.streamlit/secrets.toml` | Google OAuth + Supabase 키 (template 제공) |
| `migrate_sqlite_to_supabase.py` (신규) | 기존 `portfolio.db` → 소유자 계정으로 이관 |

---

## 6. 사용자(님)가 직접 해야 할 사전 준비 ⚠️ (구현 검증 전 필수)

코드는 키를 런타임에 읽으므로 미리 작성 가능하나, **실제 구동·검증은 아래가 끝나야** 가능합니다.

### A. Supabase 프로젝트
1. https://supabase.com → New Project 생성 (Region: Northeast Asia(Seoul) 권장)
2. Settings → API 에서 다음 복사:
   - **Project URL** (`https://xxxx.supabase.co`)
   - **service_role** 키 (secret — 절대 외부 노출 금지)
3. SQL Editor → `supabase_schema.sql` 붙여넣고 실행

### B. Google OAuth (Streamlit 로그인용)
1. https://console.cloud.google.com → APIs & Services → Credentials
2. **OAuth client ID** 생성 (Application type: Web application)
3. Authorized redirect URIs 에 추가:
   - 로컬: `http://localhost:8501/oauth2callback`
   - 배포: `https://<your-app>/oauth2callback`
4. **Client ID** / **Client secret** 복사

### C. secrets 작성
`.streamlit/secrets.toml.example` 를 `.streamlit/secrets.toml` 로 복사 후 위 값 채우기.
(Streamlit Cloud 배포 시엔 앱 설정의 Secrets 란에 동일 내용 입력)

---

## 7. 작업 순서 (구현)

1. [코드] `lib/supa.py`, `lib/auth.py`, `lib/db.py`, secrets template, requirements ← **즉시 가능**
2. [코드] `app.py` + 7개 페이지 게이트 추가 ← **즉시 가능**
3. [사용자] §6 사전 준비 (Supabase 프로젝트 + Google OAuth + secrets)
4. [코드] `pip install -r requirements.txt`
5. [사용자/코드] `python migrate_sqlite_to_supabase.py` — 기존 데이터 이관
6. [검증] 서로 다른 2개 Google 계정으로 로그인 → 각자 데이터만 보이는지 확인

---

## 8. 검증 체크리스트

- [ ] 계정 A 로그인 → 거래 입력 → 계정 B 로그인 시 **안 보임**
- [ ] 계정 B 입력이 A 데이터를 **덮어쓰지 않음**
- [ ] 로그아웃 → 미로그인 상태에서 모든 페이지 차단
- [ ] 기존 portfolio.db 데이터가 소유자 계정에 정상 표시
- [ ] 시세 캐시(price_cache)는 공유되어 중복 수집 없음
