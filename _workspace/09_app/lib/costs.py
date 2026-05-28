"""매매비용·신용이자 모델 (키움증권 기준)

[수수료]
- 키움증권 매수·매도 위탁수수료: 0.015% (모바일·HTS 기본)
- 매도 시 증권거래세: 0.18% (KOSPI/KOSDAQ 동일, 농특세 포함)
- 슬리피지 (지정가 가정): 0.05% (보수적)

[신용이자]
- 키움증권 신용이자 평균 연 6.0% (5~7% 범위)
- 적용: 자기자본 대비 equity_pct > 100인 부분 (예: 125% → 25% 신용)
- 일별 누적

[배당금]
- 수동 입력 (trades 테이블에 'DIVIDEND' 액션)
- 또는 외부 데이터 (DART 배당공시) 별도 등록 (수동)
"""

# 키움증권 기본값
KIWOOM_FEE_PCT = 0.015       # 매수·매도 위탁수수료
TAX_PCT = 0.18                # 거래세 (매도 시)
SLIPPAGE_PCT = 0.05           # 지정가 슬리피지 가정
CREDIT_INTEREST_PCT_ANNUAL = 6.0  # 신용 이자 연 평균


def calc_trade_cost(price: float, shares: int, action: str,
                    fee_pct: float = None, tax_pct: float = None,
                    slippage_pct: float = None) -> dict:
    """단일 거래의 비용 계산 (원 단위).

    Args:
        price: 체결 단가 (원)
        shares: 주식 수
        action: 'BUY' 또는 'SELL'
        fee_pct, tax_pct, slippage_pct: 사용자 override (None이면 기본값)

    Returns:
        {'fee': 위탁수수료, 'tax': 거래세(매도만), 'slippage': 슬리피지, 'total': 합계}
    """
    fee_pct = fee_pct if fee_pct is not None else KIWOOM_FEE_PCT
    tax_pct = tax_pct if tax_pct is not None else TAX_PCT
    slip_pct = slippage_pct if slippage_pct is not None else SLIPPAGE_PCT

    amount = price * shares
    if amount <= 0:
        return {'fee': 0, 'tax': 0, 'slippage': 0, 'total': 0}

    fee = round(amount * fee_pct / 100)
    slippage = round(amount * slip_pct / 100)
    if action == 'SELL':
        tax = round(amount * tax_pct / 100)
    else:
        tax = 0
    return {'fee': fee, 'tax': tax, 'slippage': slippage,
            'total': fee + tax + slippage}


def daily_credit_interest(margin_amount: float, annual_pct: float = None) -> float:
    """일별 신용 이자 (원).

    Args:
        margin_amount: 신용 사용 금액 (원, equity_pct > 100 부분)
        annual_pct: 연 이자율 (기본 6.0%)
    """
    annual = annual_pct if annual_pct is not None else CREDIT_INTEREST_PCT_ANNUAL
    if margin_amount <= 0:
        return 0
    return margin_amount * annual / 100 / 365


def cumulative_credit_interest(margin_amount: float, days: int, annual_pct: float = None) -> float:
    """N일간 누적 신용 이자."""
    return daily_credit_interest(margin_amount, annual_pct) * days


def estimate_margin_used(capital_won: float, equity_pct: float) -> float:
    """자기자본 대비 신용 사용 금액 추정.

    적극투자형 equity 125%면 자본 1억 × 0.25 = 0.25억이 신용.
    """
    if equity_pct <= 100:
        return 0
    return capital_won * (equity_pct - 100) / 100


def explain_cost_model() -> str:
    """대시보드 표시용 비용 모델 설명"""
    return (
        f"**키움증권 기준 매매비용:**\n"
        f"- 매수: 수수료 {KIWOOM_FEE_PCT}% + 슬리피지 {SLIPPAGE_PCT}%\n"
        f"- 매도: 수수료 {KIWOOM_FEE_PCT}% + 거래세 {TAX_PCT}% + 슬리피지 {SLIPPAGE_PCT}%\n"
        f"- **왕복 합 약 {(KIWOOM_FEE_PCT*2 + TAX_PCT + SLIPPAGE_PCT*2):.3f}%**\n\n"
        f"**신용이자:** 연 {CREDIT_INTEREST_PCT_ANNUAL}% (자본 대비 equity_pct > 100 부분만 일별 누적)"
    )
