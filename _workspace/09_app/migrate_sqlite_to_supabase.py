"""기존 SQLite(portfolio.db) → Supabase 데이터 이관 (1회용).

사용법:
  1) 앱에 한 번 로그인 → 사이드바 "내 사용자 ID" 복사
  2) python migrate_sqlite_to_supabase.py --user-id <복사한-UUID>

옵션:
  --db    portfolio.db 경로 (기본: data/portfolio.db)
  --dry   실제 쓰기 없이 건수만 출력

trades / portfolio_meta / simulations / simulated_trades 를 해당 user_id 로 이관.
price_cache 는 공유 테이블이므로 user_id 없이 upsert.
"""
import argparse
import os
import sqlite3
import sys
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.9/3.10 fallback (pip install tomli)

HERE = os.path.dirname(os.path.abspath(__file__))


def load_supabase():
    secrets_path = os.path.join(HERE, ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_path):
        # 루트의 .streamlit 도 확인
        secrets_path = os.path.join(os.path.dirname(os.path.dirname(HERE)), ".streamlit", "secrets.toml")
    with open(secrets_path, "rb") as f:
        cfg = tomllib.load(f)["supabase"]
    from supabase import create_client
    return create_client(cfg["url"], cfg["service_key"])


def rows(conn, table):
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
    except sqlite3.OperationalError:
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", required=True, help="이관 대상 사용자 UUID (앱 사이드바에서 확인)")
    ap.add_argument("--db", default=os.path.join(HERE, "data", "portfolio.db"))
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"❌ SQLite 파일 없음: {args.db}")

    uid = args.user_id
    conn = sqlite3.connect(args.db)
    sb = None if args.dry else load_supabase()

    def insert(table, payload, returning=False):
        if args.dry:
            return None
        res = sb.table(table).insert(payload).execute()
        return res.data

    def upsert(table, payload):
        if args.dry:
            return
        sb.table(table).upsert(payload).execute()

    # 1) trades
    trades = rows(conn, "trades")
    for t in trades:
        insert("trades", {
            "user_id": uid, "trade_date": t["trade_date"], "ticker": t["ticker"],
            "name": t.get("name"), "action": t["action"], "shares": t["shares"],
            "price": t["price"], "fee": t.get("fee") or 0, "tax": t.get("tax") or 0,
            "theme_id": t.get("theme_id"), "note": t.get("note"),
        })
    print(f"trades            : {len(trades)}건")

    # 2) portfolio_meta
    metas = rows(conn, "portfolio_meta")
    for m in metas:
        upsert("portfolio_meta", {"user_id": uid, "key": m["key"], "value": m.get("value")})
    print(f"portfolio_meta    : {len(metas)}건")

    # 3) simulations (+ id 재매핑) → simulated_trades
    sims = rows(conn, "simulations")
    id_map = {}
    for s in sims:
        data = insert("simulations", {
            "user_id": uid, "name": s.get("name"), "start_date": s["start_date"],
            "start_capital": s["start_capital"], "status": s.get("status") or "active",
            "last_synced": s.get("last_synced"), "notes": s.get("notes"),
        })
        if data:
            id_map[s["id"]] = data[0]["id"]
    print(f"simulations       : {len(sims)}건")

    sim_trades = rows(conn, "simulated_trades")
    migrated_st = 0
    for st_row in sim_trades:
        new_sim_id = id_map.get(st_row["sim_id"])
        if new_sim_id is None and not args.dry:
            continue  # 매핑 실패한 고아 행 스킵
        insert("simulated_trades", {
            "user_id": uid, "sim_id": new_sim_id, "trade_date": st_row["trade_date"],
            "ticker": st_row["ticker"], "name": st_row.get("name"), "action": st_row["action"],
            "shares": st_row["shares"], "price": st_row["price"],
            "fee": st_row.get("fee") or 0, "tax": st_row.get("tax") or 0,
            "theme_id": st_row.get("theme_id"), "signal_type": st_row.get("signal_type"),
            "note": st_row.get("note"),
        })
        migrated_st += 1
    print(f"simulated_trades  : {migrated_st}/{len(sim_trades)}건")

    # 4) price_cache (공유)
    pc = rows(conn, "price_cache")
    if pc and not args.dry:
        sb.table("price_cache").upsert(
            [{"ticker": r["ticker"], "trade_date": r["trade_date"], "close": r["close"]} for r in pc]
        ).execute()
    print(f"price_cache(공유) : {len(pc)}건")

    conn.close()
    print("\n✅ DRY-RUN 완료 (실제 쓰기 없음)" if args.dry else f"\n✅ 이관 완료 → user_id={uid}")


if __name__ == "__main__":
    main()
