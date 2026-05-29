"""체결 내역 수동 입력 + 거래 이력 관리"""
import streamlit as st
import pandas as pd
import datetime
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import loader, db, costs, auth

st.set_page_config(page_title='포트폴리오 입력', page_icon='📝', layout='wide')
auth.require_login()
auth.logout_button()
st.title('📝 포트폴리오 입력 — 체결·배당 등록')
st.caption(costs.explain_cost_model())

# 종목 자동완성용 (최종 포트 종목 우선) — Phase 2: 사용자 유형별
ptype = db.get_profile_type()
tp = loader.load_typed('theme_portfolio', ptype)
final_tickers = {h['ticker']: h for h in (tp or {}).get('holdings', [])}
pf = loader.load_typed('portfolio', ptype)
parent_tickers = {h['ticker']: h for h in (pf or {}).get('holdings', [])}
ticker_options = list({**parent_tickers, **final_tickers}.keys())

col1, col2 = st.columns([2, 3])

with col1:
    st.subheader('➕ 신규 체결 입력')
    with st.form('trade_form', clear_on_submit=True):
        trade_date = st.date_input('체결일자', value=datetime.date.today())
        action = st.selectbox('매매 종류', ['BUY', 'SELL', 'DIVIDEND'])
        ticker_input = st.selectbox('티커 선택', options=[''] + ticker_options + ['직접 입력'])
        if ticker_input == '직접 입력':
            ticker = st.text_input('티커 (6자리)', max_chars=6)
            name = st.text_input('종목명')
            theme_id = st.text_input('테마 ID (옵션, 예: #1, #3, default)')
        elif ticker_input:
            ticker = ticker_input
            ref = final_tickers.get(ticker) or parent_tickers.get(ticker) or {}
            name_default = ref.get('name', '')
            theme_default = ''
            if ticker in final_tickers:
                tid = final_tickers[ticker].get('idea_id') or final_tickers[ticker].get('matched_ideas', [None])[0]
                theme_default = f'#{tid}' if tid and tid != 'default' else (tid or '')
            name = st.text_input('종목명', value=name_default)
            theme_id = st.text_input('테마 ID', value=theme_default)
        else:
            ticker, name, theme_id = '', '', ''

        col_a, col_b = st.columns(2)
        with col_a:
            shares = st.number_input(
                '수량 (주)' if action != 'DIVIDEND' else '보유 주식 수',
                min_value=1, value=1, step=1,
            )
        with col_b:
            price_label = '체결 단가 (원)' if action != 'DIVIDEND' else '주당 배당금 (원)'
            price = st.number_input(price_label, min_value=0.0, value=0.0, step=10.0)

        # 키움 매매비용 자동 계산 (사용자 수정 가능)
        if action in ('BUY', 'SELL') and price > 0 and shares > 0:
            auto_cost = costs.calc_trade_cost(price, int(shares), action)
            st.caption(
                f'💡 키움 자동 계산 — 수수료 {auto_cost["fee"]:,}원 · '
                f'슬리피지 {auto_cost["slippage"]:,}원 · '
                f'거래세 {auto_cost["tax"]:,}원 (매도만) · '
                f'합 **{auto_cost["total"]:,}원**'
            )
            col_x, col_y = st.columns(2)
            with col_x:
                fee = st.number_input('수수료+슬리피지 (원, 수정 가능)', min_value=0.0,
                                      value=float(auto_cost['fee'] + auto_cost['slippage']), step=1.0)
            with col_y:
                tax = st.number_input('거래세 (원, 매도만)', min_value=0.0,
                                      value=float(auto_cost['tax']), step=1.0)
        else:
            fee, tax = 0.0, 0.0

        note = st.text_input('메모 (옵션)')

        submitted = st.form_submit_button('💾 저장', use_container_width=True, type='primary')

        if submitted:
            if not ticker or not name or shares <= 0 or price <= 0:
                st.error('티커·종목명·수량·단가를 모두 입력해주세요.')
            else:
                tid = db.add_trade(
                    trade_date=trade_date.strftime('%Y%m%d'),
                    ticker=ticker, name=name, action=action,
                    shares=int(shares), price=float(price),
                    fee=float(fee), tax=float(tax),
                    theme_id=theme_id or None, note=note or None,
                )
                amount = shares * price
                if action == 'BUY':
                    amount += fee
                elif action == 'SELL':
                    amount -= (fee + tax)
                # DIVIDEND: amount = 배당 입금액 (그대로)
                st.success(f"✅ 등록 (id={tid}) | {action} {ticker} {shares}주 × {price:,.0f}원 = {amount:,.0f}원")
                st.rerun()

with col2:
    st.subheader('📋 거래 이력')
    trades = db.list_trades()
    if not trades:
        st.info('등록된 거래가 없습니다.')
    else:
        tdf = pd.DataFrame(trades)
        tdf['금액'] = (tdf['shares'] * tdf['price']).astype(int)
        display_cols = ['id', 'trade_date', 'ticker', 'name', 'action', 'shares', 'price', '금액', 'theme_id', 'note']
        st.dataframe(tdf[display_cols], hide_index=True, use_container_width=True,
                     column_config={
                         'id': st.column_config.NumberColumn('ID', width='small'),
                         'trade_date': '일자',
                         'ticker': '티커',
                         'name': '종목명',
                         'action': st.column_config.TextColumn('매매', width='small'),
                         'shares': st.column_config.NumberColumn('수량', format='%d'),
                         'price': st.column_config.NumberColumn('단가', format='%d원'),
                         '금액': st.column_config.NumberColumn('금액', format='%d원'),
                         'theme_id': '테마',
                         'note': '메모',
                     })
        # 삭제
        del_id = st.number_input('삭제할 거래 ID', min_value=0, value=0, step=1)
        if st.button('🗑 삭제', type='secondary'):
            if del_id > 0:
                db.delete_trade(int(del_id))
                st.success(f'ID {del_id} 삭제 완료')
                st.rerun()

st.divider()
st.subheader('💼 현재 보유 포지션 (체결 누적 기반)')
positions = db.get_positions()
if not positions:
    st.info('보유 종목 없음')
else:
    pdf = pd.DataFrame([{
        '티커': p['ticker'], '종목명': p['name'],
        '보유 수량': p['shares'], '평균 단가': round(p['avg_price'], 0),
        '매수 금액': round(p['avg_price'] * p['shares'], 0),
        '실현 손익': round(p.get('realized_pnl', 0), 0),
        '테마': p.get('theme_id', '-'),
        '최초 매수일': p.get('first_buy_date', '-'),
        '최근 거래': p.get('last_trade_date', '-'),
    } for p in positions.values()])
    st.dataframe(pdf, hide_index=True, use_container_width=True,
                 column_config={
                     '평균 단가': st.column_config.NumberColumn(format='%d원'),
                     '매수 금액': st.column_config.NumberColumn(format='%d원'),
                     '실현 손익': st.column_config.NumberColumn(format='%d원'),
                 })
    # 합계
    total_cost = sum(p['avg_price'] * p['shares'] for p in positions.values())
    total_realized = sum(p.get('realized_pnl', 0) for p in positions.values())
    col1, col2, col3 = st.columns(3)
    col1.metric('총 보유 종목 수', len(positions))
    col2.metric('총 매수 금액', f'{total_cost:,.0f}원')
    col3.metric('총 실현 손익', f'{total_realized:,.0f}원')

st.divider()
st.caption('💡 체결 후 입력 → 매일 종가 후 "수익률 관리" 페이지에서 평가손익 자동 갱신')
