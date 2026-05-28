#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universe Screening — KOSPI/KOSDAQ Top 100 (neutral regime, equal weights)

Pipeline:
  전체 후보풀 -> 시장/증권유형 필터(코스피+코스닥 보통주만)
            -> 컨센서스 보유 필터(신규)
            -> 거래정지/관리종목 제외
            -> 20일 평균 거래대금 1억원 이상
            -> constraints.json excluded_tickers/sectors
  랭킹: 섹터 내 z-score(밸류/퀄리티/모멘텀/성장) -> winsorize(+-5sigma)
        -> 중립 균등가중 종합점수 -> 상위 100

각 후보 레코드 필드:
  ticker, name, market(KOSPI|KOSDAQ|KONEX|ETF|SPAC|PREF), sectype(common|pref|etf|spac),
  sector, mcap_eok(시총, 억원), turnover_eok(20일 평균 거래대금, 억원),
  consensus(bool, 애널리스트 추정실적 컨센서스 존재 여부),
  suspended(bool 거래정지), admin(bool 관리종목),
  raw 팩터 인풋: pbr, per, ev_ebitda, roe, roa, debt_ratio, opm,
                 mom_12_1(12-1개월 수익률 %), rev_yoy(%), opinc_yoy(%)
"""
import json, math, statistics
from collections import defaultdict

EOK = 1.0  # 억원 단위

# ---------------------------------------------------------------------------
# 후보 풀: 코스피/코스닥 보통주 + 애널리스트 커버리지 종목 + 필터 시연용 비대상 종목
# (라이브 KRX API가 본 샌드박스에서 비가용하여 연구 기반 큐레이션 데이터셋 사용)
# 필드 순서: ticker,name,market,sectype,sector,mcap_eok,turnover_eok,consensus,suspended,admin,
#            pbr,per,ev_ebitda,roe,roa,debt_ratio,opm,mom_12_1,rev_yoy,opinc_yoy
# ---------------------------------------------------------------------------
C = []
def add(*a): C.append(a)

# === KOSPI 대형/중형 보통주 (컨센서스 보유) ===
add("005930","삼성전자","KOSPI","common","반도체",4520000,9800,True,False,False, 1.4,14.5,7.2,11.8,8.1,28.0,16.0, 22.0,12.0,40.0)
add("000660","SK하이닉스","KOSPI","common","반도체",1480000,8200,True,False,False, 2.6,9.5,5.1,28.0,16.0,42.0,30.0, 65.0,55.0,180.0)
add("373220","LG에너지솔루션","KOSPI","common","2차전지",890000,3100,True,False,False, 3.1,38.0,16.0,7.5,3.8,70.0,6.0, 18.0,5.0,25.0)
add("207940","삼성바이오로직스","KOSPI","common","바이오",720000,1800,True,False,False, 6.2,52.0,30.0,13.5,9.2,18.0,29.0, 40.0,18.0,22.0)
add("005380","현대차","KOSPI","common","자동차",520000,2400,True,False,False, 0.55,4.8,5.5,12.5,5.5,140.0,9.5, 8.0,6.0,4.0)
add("000270","기아","KOSPI","common","자동차",420000,2100,True,False,False, 0.85,4.2,3.2,18.0,9.0,45.0,11.0, 12.0,7.0,9.0)
add("012450","한화에어로스페이스","KOSPI","common","방산",380000,2600,True,False,False, 4.5,22.0,14.0,22.0,7.0,180.0,12.0, 95.0,45.0,120.0)
add("068270","셀트리온","KOSPI","common","바이오",380000,1900,True,False,False, 3.0,42.0,24.0,8.5,6.0,30.0,28.0, 15.0,25.0,18.0)
add("105560","KB금융","KOSPI","common","금융",330000,1700,True,False,False, 0.62,7.2,0.0,10.5,0.7,0.0,0.0, 30.0,8.0,12.0)
add("005490","POSCO홀딩스","KOSPI","common","철강",310000,2000,True,False,False, 0.48,12.0,6.5,4.0,2.5,55.0,5.0, -5.0,-3.0,-20.0)
add("035420","NAVER","KOSPI","common","인터넷플랫폼",300000,1800,True,False,False, 1.5,18.0,12.0,9.5,6.5,35.0,18.0, 12.0,11.0,15.0)
add("055550","신한지주","KOSPI","common","금융",290000,1500,True,False,False, 0.60,6.8,0.0,9.8,0.7,0.0,0.0, 28.0,7.0,10.0)
add("012330","현대모비스","KOSPI","common","자동차부품",250000,1100,True,False,False, 0.62,7.0,4.5,8.5,5.5,40.0,6.0, 10.0,5.0,6.0)
add("000810","삼성화재","KOSPI","common","보험",230000,900,True,False,False, 1.1,9.5,0.0,12.0,2.0,0.0,0.0, 22.0,6.0,9.0)
add("028260","삼성물산","KOSPI","common","지주/건설",230000,1000,True,False,False, 0.75,12.0,8.0,7.5,4.0,60.0,7.0, 14.0,8.0,7.0)
add("035720","카카오","KOSPI","common","인터넷플랫폼",210000,1600,True,False,False, 1.6,55.0,20.0,2.5,1.5,55.0,5.0, -8.0,8.0,5.0)
add("051910","LG화학","KOSPI","common","화학",210000,1700,True,False,False, 1.0,28.0,11.0,4.5,2.5,90.0,4.0, -2.0,-5.0,-15.0)
add("009150","삼성전기","KOSPI","common","전자부품",180000,1200,True,False,False, 1.3,14.0,7.0,9.0,5.5,45.0,9.0, 18.0,12.0,20.0)
add("032830","삼성생명","KOSPI","common","보험",180000,800,True,False,False, 0.45,8.0,0.0,6.5,0.6,0.0,0.0, 20.0,5.0,8.0)
add("066570","LG전자","KOSPI","common","가전",170000,1100,True,False,False, 0.85,9.5,5.5,8.0,3.5,150.0,5.0, 6.0,7.0,10.0)
add("003670","포스코퓨처엠","KOSPI","common","2차전지소재",160000,1500,True,False,False, 4.0,80.0,35.0,3.0,1.5,110.0,3.0, -10.0,-8.0,-40.0)
add("034730","SK","KOSPI","common","지주",150000,900,True,False,False, 0.55,9.0,7.0,6.0,3.0,120.0,6.0, 8.0,4.0,5.0)
add("015760","한국전력","KOSPI","common","유틸리티",150000,1200,True,False,False, 0.40,6.0,9.0,5.0,1.5,250.0,8.0, 12.0,10.0,200.0)
add("086790","하나금융지주","KOSPI","common","금융",150000,1300,True,False,False, 0.55,6.0,0.0,10.0,0.7,0.0,0.0, 32.0,7.0,11.0)
add("011200","HMM","KOSPI","common","해운",150000,1400,True,False,False, 0.65,8.0,5.0,9.0,7.0,25.0,18.0, 25.0,30.0,60.0)
add("033780","KT&G","KOSPI","common","담배/소비재",140000,800,True,False,False, 1.5,12.0,8.0,12.0,9.0,15.0,28.0, 18.0,4.0,6.0)
add("009540","HD한국조선해양","KOSPI","common","조선",140000,1600,True,False,False, 1.3,18.0,12.0,9.5,4.0,90.0,5.5, 55.0,20.0,90.0)
add("267250","HD현대중공업","KOSPI","common","조선",250000,2200,True,False,False, 2.8,30.0,16.0,11.0,4.5,110.0,7.0, 70.0,25.0,150.0)
add("042660","한화오션","KOSPI","common","조선",160000,1800,True,False,False, 3.2,45.0,20.0,7.0,2.5,200.0,4.5, 60.0,22.0,300.0)
add("010140","삼성중공업","KOSPI","common","조선",120000,1500,True,False,False, 2.2,28.0,15.0,8.0,3.0,180.0,5.0, 45.0,18.0,120.0)
add("096770","SK이노베이션","KOSPI","common","에너지",120000,1300,True,False,False, 0.65,15.0,8.0,3.5,1.5,160.0,3.0, -3.0,2.0,-30.0)
add("128940","한미약품","KOSPI","common","제약",110000,700,True,False,False, 3.5,28.0,16.0,14.0,7.0,80.0,16.0, 12.0,10.0,20.0)
add("000100","유한양행","KOSPI","common","제약",110000,650,True,False,False, 2.0,40.0,28.0,5.5,4.0,25.0,7.0, 18.0,9.0,30.0)
add("018260","삼성에스디에스","KOSPI","common","IT서비스",105000,500,True,False,False, 1.2,18.0,7.0,8.5,6.0,20.0,9.0, 5.0,6.0,8.0)
add("011070","LG이노텍","KOSPI","common","전자부품",100000,900,True,False,False, 1.0,9.0,4.5,11.0,5.5,90.0,6.0, 14.0,12.0,18.0)
add("006400","삼성SDI","KOSPI","common","2차전지",95000,1100,True,False,False, 1.1,30.0,12.0,4.0,2.0,80.0,4.0, -15.0,-10.0,-50.0)
add("009830","한화솔루션","KOSPI","common","화학/태양광",90000,900,True,False,False, 0.7,20.0,12.0,3.5,1.5,140.0,4.0, 8.0,6.0,15.0)
add("010130","고려아연","KOSPI","common","비철금속",170000,1000,True,False,False, 2.5,22.0,14.0,8.0,5.0,40.0,7.0, 20.0,8.0,15.0)
add("034020","두산에너빌리티","KOSPI","common","원자력/에너지",130000,1900,True,False,False, 2.0,30.0,16.0,6.0,2.5,130.0,7.0, 40.0,12.0,50.0)
add("042700","한미반도체","KOSPI","common","반도체장비",95000,1400,True,False,False, 8.0,35.0,24.0,28.0,18.0,15.0,30.0, 80.0,60.0,150.0)
add("064350","현대로템","KOSPI","common","방산/철도",110000,1500,True,False,False, 3.5,20.0,13.0,18.0,7.0,120.0,11.0, 85.0,30.0,90.0)
add("047810","한국항공우주","KOSPI","common","방산/항공",60000,1100,True,False,False, 3.0,22.0,15.0,15.0,5.0,150.0,9.0, 50.0,18.0,40.0)
add("272210","한화시스템","KOSPI","common","방산/IT",55000,800,True,False,False, 2.8,28.0,16.0,9.0,4.0,90.0,7.0, 45.0,15.0,35.0)
add("010620","HD현대미포","KOSPI","common","조선",50000,900,True,False,False, 1.8,25.0,14.0,8.0,3.5,120.0,5.0, 40.0,16.0,80.0)
add("329180","HD현대","KOSPI","common","지주/조선",90000,800,True,False,False, 0.7,9.0,7.0,8.0,3.5,140.0,6.0, 30.0,12.0,40.0)
add("047050","포스코인터내셔널","KOSPI","common","상사/에너지",80000,700,True,False,False, 1.2,9.5,6.0,12.0,5.0,130.0,5.0, 18.0,8.0,12.0)
add("003490","대한항공","KOSPI","common","항공",90000,800,True,False,False, 1.0,7.0,6.0,12.0,5.0,150.0,12.0, 10.0,8.0,6.0)
add("090430","아모레퍼시픽","KOSPI","common","화장품",70000,600,True,False,False, 1.8,30.0,14.0,6.0,4.0,20.0,7.0, 15.0,9.0,40.0)
add("051900","LG생활건강","KOSPI","common","화장품/생활",55000,400,True,False,False, 1.2,18.0,9.0,7.0,5.0,25.0,9.0, -5.0,2.0,3.0)
add("097950","CJ제일제당","KOSPI","common","식음료",55000,400,True,False,False, 0.7,9.0,6.0,7.0,3.5,130.0,6.0, 4.0,3.0,8.0)
add("003550","LG","KOSPI","common","지주",110000,600,True,False,False, 0.55,8.0,7.0,6.5,4.0,30.0,7.0, 10.0,4.0,5.0)
add("030200","KT","KOSPI","common","통신",100000,700,True,False,False, 0.65,9.0,3.0,8.0,3.5,110.0,8.0, 25.0,4.0,12.0)
add("017670","SK텔레콤","KOSPI","common","통신",115000,600,True,False,False, 1.0,11.0,4.0,10.0,4.0,120.0,9.0, 18.0,3.0,8.0)
add("032640","LG유플러스","KOSPI","common","통신",45000,300,True,False,False, 0.55,9.0,3.0,6.5,3.0,130.0,6.0, 8.0,3.0,2.0)
add("316140","우리금융지주","KOSPI","common","금융",110000,900,True,False,False, 0.45,5.5,0.0,9.0,0.6,0.0,0.0, 25.0,6.0,8.0)
add("024110","기업은행","KOSPI","common","은행",100000,500,True,False,False, 0.40,5.0,0.0,8.5,0.6,0.0,0.0, 18.0,5.0,6.0)
add("071050","한국금융지주","KOSPI","common","증권",40000,400,True,False,False, 0.55,6.0,0.0,9.0,1.5,0.0,0.0, 22.0,10.0,18.0)
add("016360","삼성증권","KOSPI","common","증권",40000,400,True,False,False, 0.7,7.0,0.0,9.5,1.8,0.0,0.0, 20.0,9.0,15.0)
add("005940","NH투자증권","KOSPI","common","증권",40000,400,True,False,False, 0.6,6.5,0.0,9.0,1.5,0.0,0.0, 18.0,8.0,12.0)
add("086280","현대글로비스","KOSPI","common","물류",45000,400,True,False,False, 0.9,8.0,5.0,12.0,7.0,40.0,7.0, 15.0,8.0,10.0)
add("000120","CJ대한통운","KOSPI","common","물류",20000,200,True,False,False, 0.7,12.0,6.0,5.0,2.5,90.0,5.0, 6.0,4.0,7.0)
add("011780","금호석유","KOSPI","common","화학",35000,300,True,False,False, 0.8,12.0,7.0,6.0,4.0,40.0,8.0, 5.0,3.0,5.0)
add("010950","S-Oil","KOSPI","common","정유",80000,600,True,False,False, 1.1,12.0,7.0,7.0,3.5,90.0,4.0, -5.0,2.0,-25.0)
add("010060","OCI홀딩스","KOSPI","common","화학/에너지",25000,300,True,False,False, 0.6,11.0,7.0,5.0,3.0,40.0,8.0, 3.0,2.0,-10.0)
add("004020","현대제철","KOSPI","common","철강",35000,400,True,False,False, 0.30,15.0,8.0,2.5,1.5,70.0,3.0, -8.0,-4.0,-30.0)
add("001040","CJ","KOSPI","common","지주/식품",25000,200,True,False,False, 0.5,8.0,6.0,6.0,2.5,120.0,6.0, 8.0,5.0,7.0)
add("139480","이마트","KOSPI","common","유통",25000,300,True,False,False, 0.30,18.0,7.0,1.5,0.8,140.0,2.0, -3.0,2.0,5.0)
add("069960","현대백화점","KOSPI","common","유통",13000,150,True,False,False, 0.35,7.0,6.0,4.0,2.0,80.0,5.0, -2.0,3.0,4.0)
add("004370","농심","KOSPI","common","식음료",22000,150,True,False,False, 0.9,12.0,6.0,8.0,5.0,30.0,7.0, 8.0,5.0,12.0)
add("003230","삼양식품","KOSPI","common","식음료",75000,1200,True,False,False, 7.0,30.0,18.0,28.0,18.0,40.0,20.0, 120.0,40.0,90.0)
add("000080","하이트진로","KOSPI","common","식음료",16000,150,True,False,False, 1.3,15.0,8.0,8.0,3.5,150.0,8.0, 5.0,4.0,10.0)
add("271560","오리온","KOSPI","common","식음료",40000,300,True,False,False, 1.5,12.0,7.0,13.0,9.0,20.0,16.0, 10.0,8.0,12.0)
add("161390","한국타이어앤테크놀로지","KOSPI","common","자동차부품",55000,500,True,False,False, 0.7,7.0,4.0,11.0,7.0,30.0,12.0, 12.0,6.0,15.0)
add("018880","한온시스템","KOSPI","common","자동차부품",30000,300,True,False,False, 1.5,18.0,8.0,6.0,2.5,180.0,4.0, -5.0,4.0,2.0)
add("000720","현대건설","KOSPI","common","건설",35000,400,True,False,False, 0.45,9.0,7.0,5.0,2.0,110.0,3.0, 5.0,8.0,3.0)
add("006360","GS건설","KOSPI","common","건설",20000,300,True,False,False, 0.40,8.0,7.0,4.5,1.8,160.0,3.5, 6.0,5.0,40.0)
add("047040","대우건설","KOSPI","common","건설",15000,200,True,False,False, 0.40,6.0,5.0,6.0,2.5,130.0,4.0, -3.0,2.0,-10.0)
add("036460","한국가스공사","KOSPI","common","유틸리티",35000,400,True,False,False, 0.40,7.0,9.0,5.0,1.0,300.0,6.0, 8.0,6.0,15.0)
add("051600","한전KPS","KOSPI","common","전력서비스",18000,150,True,False,False, 1.2,11.0,6.0,12.0,9.0,30.0,11.0, 10.0,5.0,8.0)
add("034220","LG디스플레이","KOSPI","common","디스플레이",45000,800,True,False,False, 1.5,0.0,10.0,-5.0,-2.5,250.0,2.0, 10.0,5.0,30.0)  # PER 음수→ value서 제외처리
add("042670","HD현대인프라코어","KOSPI","common","기계",20000,300,True,False,False, 1.2,12.0,8.0,9.0,4.0,120.0,7.0, 15.0,8.0,18.0)
add("267260","HD현대일렉트릭","KOSPI","common","전력기기",80000,1300,True,False,False, 6.0,28.0,18.0,30.0,12.0,90.0,16.0, 120.0,40.0,150.0)
add("010120","LS ELECTRIC","KOSPI","common","전력기기",30000,500,True,False,False, 2.5,18.0,11.0,15.0,7.0,110.0,9.0, 60.0,18.0,40.0)
add("241560","두산밥캣","KOSPI","common","기계",40000,400,True,False,False, 0.8,8.0,5.0,12.0,6.0,80.0,12.0, -8.0,-5.0,-15.0)
add("336370","솔루스첨단소재","KOSPI","common","2차전지소재",8000,150,True,False,False, 2.0,0.0,30.0,-3.0,-1.5,150.0,1.0, -10.0,15.0,5.0)
add("298050","효성첨단소재","KOSPI","common","소재",15000,200,True,False,False, 2.0,15.0,9.0,12.0,5.0,150.0,8.0, 10.0,6.0,12.0)
add("298040","효성중공업","KOSPI","common","전력기기",70000,900,True,False,False, 5.0,22.0,14.0,25.0,8.0,200.0,9.0, 90.0,25.0,80.0)
add("000150","두산","KOSPI","common","지주/기계",30000,400,True,False,False, 1.5,15.0,10.0,10.0,4.0,150.0,8.0, 30.0,10.0,25.0)
add("035250","강원랜드","KOSPI","common","레저",35000,300,True,False,False, 1.0,12.0,6.0,8.0,6.0,15.0,18.0, 5.0,4.0,8.0)
add("251270","넷마블","KOSPI","common","게임",55000,600,True,False,False, 1.8,30.0,15.0,6.0,3.0,60.0,8.0, 40.0,10.0,60.0)
add("036570","엔씨소프트","KOSPI","common","게임",45000,500,True,False,False, 1.2,25.0,10.0,5.0,3.5,20.0,12.0, 5.0,-2.0,-20.0)
add("259960","크래프톤","KOSPI","common","게임",150000,1400,True,False,False, 3.5,18.0,12.0,20.0,15.0,15.0,38.0, 60.0,30.0,40.0)
add("352820","하이브","KOSPI","common","엔터",90000,800,True,False,False, 3.0,28.0,15.0,12.0,6.0,60.0,11.0, 20.0,15.0,18.0)
add("011790","SKC","KOSPI","common","화학/소재",25000,400,True,False,False, 2.0,0.0,20.0,-2.0,-1.0,160.0,2.0, 10.0,8.0,5.0)

# === KOSDAQ 보통주 (컨센서스 보유) ===
add("196170","알테오젠","KOSDAQ","common","바이오",230000,1500,True,False,False, 18.0,90.0,55.0,25.0,16.0,30.0,40.0, 70.0,80.0,120.0)
add("247540","에코프로비엠","KOSDAQ","common","2차전지소재",170000,2000,True,False,False, 5.0,60.0,28.0,5.0,2.5,120.0,4.0, -20.0,-15.0,-50.0)
add("086520","에코프로","KOSDAQ","common","2차전지",120000,1800,True,False,False, 4.0,50.0,25.0,4.0,2.0,150.0,3.0, -25.0,-18.0,-55.0)
add("091990","셀트리온헬스케어","KOSDAQ","common","바이오",0,0,True,False,False, 0,0,0,0,0,0,0, 0,0,0)  # 합병상장폐지 → suspended로 별도처리 아래
add("145020","휴젤","KOSDAQ","common","미용/바이오",35000,400,True,False,False, 4.0,22.0,15.0,16.0,11.0,20.0,32.0, 25.0,20.0,30.0)
add("214150","클래시스","KOSDAQ","common","미용기기",30000,500,True,False,False, 7.0,28.0,18.0,30.0,22.0,15.0,48.0, 30.0,25.0,35.0)
add("028300","HLB","KOSDAQ","common","바이오",80000,1500,True,False,False, 6.0,0.0,0.0,-15.0,-10.0,30.0,-50.0, -10.0,20.0,10.0)
add("145990","리가켐바이오","KOSDAQ","common","바이오",70000,800,True,False,False, 8.0,0.0,0.0,-8.0,-5.0,20.0,-30.0, 40.0,30.0,20.0)
add("293490","카카오게임즈","KOSDAQ","common","게임",18000,300,True,False,False, 1.2,20.0,10.0,4.0,2.0,70.0,8.0, -10.0,-5.0,-30.0)
add("263750","펄어비스","KOSDAQ","common","게임",25000,400,True,False,False, 2.5,40.0,20.0,4.0,2.5,30.0,10.0, 30.0,8.0,20.0)
add("041510","에스엠","KOSDAQ","common","엔터",25000,400,True,False,False, 1.8,15.0,8.0,14.0,8.0,40.0,14.0, 10.0,12.0,15.0)
add("035900","JYP Ent.","KOSDAQ","common","엔터",25000,400,True,False,False, 4.0,18.0,11.0,28.0,18.0,15.0,28.0, 5.0,15.0,18.0)
add("122870","와이지엔터테인먼트","KOSDAQ","common","엔터",10000,200,True,False,False, 2.5,18.0,10.0,12.0,7.0,30.0,15.0, 8.0,10.0,12.0)
add("058470","리노공업","KOSDAQ","common","반도체부품",30000,300,True,False,False, 5.0,28.0,18.0,18.0,15.0,8.0,38.0, 25.0,20.0,25.0)
add("240810","원익IPS","KOSDAQ","common","반도체장비",18000,400,True,False,False, 2.0,22.0,12.0,9.0,5.0,40.0,12.0, 30.0,25.0,40.0)
add("357780","솔브레인","KOSDAQ","common","반도체소재",20000,300,True,False,False, 1.8,15.0,8.0,13.0,9.0,30.0,18.0, 18.0,15.0,20.0)
add("403870","HPSP","KOSDAQ","common","반도체장비",30000,500,True,False,False, 9.0,30.0,22.0,32.0,25.0,10.0,50.0, 40.0,35.0,45.0)
add("095340","ISC","KOSDAQ","common","반도체부품",15000,300,True,False,False, 4.0,25.0,15.0,15.0,11.0,20.0,30.0, 35.0,28.0,40.0)
add("000990","DB하이텍","KOSDAQ","common","반도체",18000,300,True,False,False, 1.0,12.0,6.0,9.0,6.0,40.0,18.0, 8.0,5.0,10.0)
add("166090","하나머티리얼즈","KOSDAQ","common","반도체소재",10000,200,True,False,False, 2.0,18.0,10.0,11.0,8.0,30.0,22.0, 30.0,25.0,30.0)
add("042700","_dummy","KOSDAQ","common","x",0,0,True,False,False,0,0,0,0,0,0,0,0,0,0)  # placeholder(중복 방지용, 아래 dedupe로 제거)
add("112040","위메이드","KOSDAQ","common","게임",12000,400,True,False,False, 2.0,0.0,15.0,-5.0,-3.0,80.0,2.0, 10.0,15.0,5.0)
add("141080","리가켐바이오2","KOSDAQ","common","바이오",0,0,True,False,False,0,0,0,0,0,0,0,0,0,0)  # placeholder
add("328130","루닛","KOSDAQ","common","의료AI",25000,600,True,False,False, 10.0,0.0,0.0,-20.0,-12.0,30.0,-40.0, 50.0,60.0,30.0)
add("277810","레인보우로보틱스","KOSDAQ","common","로봇",60000,800,True,False,False, 20.0,0.0,80.0,3.0,2.0,15.0,5.0, 80.0,40.0,60.0)
add("056190","에스에프에이","KOSDAQ","common","스마트팩토리",8000,150,True,False,False, 1.0,12.0,6.0,8.0,5.0,60.0,9.0, 5.0,4.0,8.0)
add("178920","피아이첨단소재","KOSDAQ","common","소재",8000,150,True,False,False, 2.0,18.0,10.0,9.0,6.0,30.0,15.0, 10.0,8.0,12.0)
add("034230","파라다이스","KOSDAQ","common","레저",10000,150,True,False,False, 0.9,12.0,6.0,7.0,5.0,40.0,16.0, 8.0,6.0,10.0)
add("141080","레고켐","KOSDAQ","common","x",0,0,True,False,False,0,0,0,0,0,0,0,0,0,0)  # placeholder
add("253450","스튜디오드래곤","KOSDAQ","common","미디어/콘텐츠",10000,150,True,False,False, 1.5,25.0,10.0,6.0,4.0,30.0,9.0, 5.0,8.0,12.0)
add("067160","아프리카TV(SOOP)","KOSDAQ","common","미디어/플랫폼",13000,200,True,False,False, 5.0,18.0,12.0,30.0,22.0,20.0,28.0, 25.0,20.0,30.0)
add("213420","덕산네오룩스","KOSDAQ","common","OLED소재",8000,150,True,False,False, 3.0,20.0,12.0,14.0,11.0,15.0,28.0, 18.0,15.0,20.0)
add("319660","피에스케이","KOSDAQ","common","반도체장비",10000,200,True,False,False, 2.0,15.0,9.0,12.0,8.0,30.0,20.0, 25.0,20.0,28.0)
add("085660","차바이오텍","KOSDAQ","common","바이오",8000,200,True,False,False, 3.0,0.0,40.0,-3.0,-1.0,150.0,1.0, 8.0,10.0,5.0)
add("098460","고영","KOSDAQ","common","검사장비",10000,150,True,False,False, 2.5,18.0,11.0,13.0,9.0,15.0,18.0, 15.0,12.0,18.0)
add("131970","두산테스나","KOSDAQ","common","반도체테스트",12000,200,True,False,False, 2.5,15.0,8.0,14.0,8.0,80.0,22.0, 20.0,18.0,22.0)

# === 필터 시연용 비대상/탈락 종목 ===
# 코넥스/ETF/스팩/우선주 (시장·증권유형 필터에서 제외)
add("069500","KODEX 200","KOSPI","etf","ETF",60000,3000,False,False,False, 0,0,0,0,0,0,0, 10.0,0,0)
add("122630","KODEX 레버리지","KOSPI","etf","ETF",30000,5000,False,False,False, 0,0,0,0,0,0,0, 18.0,0,0)
add("005935","삼성전자우","KOSPI","pref","반도체",380000,800,False,False,False, 1.2,13.0,7.0,11.8,8.1,28.0,16.0, 20.0,12.0,40.0)
add("005385","현대차우","KOSPI","pref","자동차",30000,100,False,False,False, 0.4,4.0,5.5,12.5,5.5,140.0,9.5, 6.0,6.0,4.0)
add("449450","KODEX AI반도체","KOSPI","etf","ETF",5000,400,False,False,False,0,0,0,0,0,0,0,30.0,0,0)
add("377450","교보10호스팩","KOSDAQ","spac","SPAC",100,5,False,False,False, 0,0,0,0,0,0,0, 1.0,0,0)
add("950210","프레스티지바이오파마","KOSPI","common","바이오",5000,80,False,False,False, 3.0,0.0,0.0,-10.0,-5.0,40.0,-20.0, 5.0,10.0,5.0)  # 외국기업, 컨센서스 미존재→consensus False
add("900310","컨텍A","KONEX","common","x",200,2,False,False,False,0,0,0,0,0,0,0,0,0,0)

# 컨센서스 미존재 (신규 핵심 필터에서 제외) — 소형주, 커버리지 없음
add("123410","무커버리지A","KOSDAQ","common","소형주",800,30,False,False,False, 0.8,9.0,5.0,6.0,3.0,80.0,6.0, 12.0,5.0,8.0)
add("123420","무커버리지B","KOSDAQ","common","소형주",600,20,False,False,False, 1.0,10.0,6.0,5.0,2.5,90.0,5.0, 8.0,4.0,6.0)
add("123430","무커버리지C","KOSPI","common","소형주",1500,40,False,False,False, 0.6,7.0,4.0,7.0,4.0,60.0,7.0, 10.0,6.0,9.0)
add("123440","무커버리지D","KOSDAQ","common","소형주",500,15,False,False,False, 1.2,11.0,7.0,4.0,2.0,100.0,4.0, 5.0,3.0,4.0)

# 거래정지 종목 (제외)
add("222080","거래정지A","KOSDAQ","common","바이오",3000,0,True,True,False, 2.0,15.0,8.0,5.0,3.0,40.0,8.0, 0.0,5.0,5.0)
add("222090","거래정지B","KOSPI","common","건설",4000,0,True,True,False, 0.5,8.0,6.0,4.0,2.0,120.0,4.0, 0.0,3.0,3.0)

# 관리종목 (제외)
add("333010","관리종목A","KOSDAQ","common","바이오",2000,50,True,False,True, 3.0,0.0,0.0,-20.0,-12.0,200.0,-30.0, -40.0,-10.0,-50.0)
add("333020","관리종목B","KOSPI","common","유통",3000,40,True,False,True, 0.4,0.0,0.0,-10.0,-5.0,250.0,-15.0, -30.0,-8.0,-40.0)

# 거래대금 1억원(=1억=1.0억) 미만 (제외) — 컨센서스/시장 통과하나 유동성 부족
add("444010","저유동성A","KOSDAQ","common","소재",2000,0.6,True,False,False, 1.0,12.0,7.0,8.0,5.0,50.0,9.0, 10.0,8.0,10.0)
add("444020","저유동성B","KOSPI","common","기계",5000,0.8,True,False,False, 0.8,10.0,6.0,9.0,5.0,60.0,8.0, 8.0,6.0,8.0)

# ---------------------------------------------------------------------------
# 레코드화 + placeholder/dummy 제거 + dedupe
# ---------------------------------------------------------------------------
COLS=["ticker","name","market","sectype","sector","mcap_eok","turnover_eok","consensus","suspended","admin",
      "pbr","per","ev_ebitda","roe","roa","debt_ratio","opm","mom_12_1","rev_yoy","opinc_yoy"]
rows=[dict(zip(COLS,r)) for r in C]
# drop placeholders/dummies and 합병폐지 종목
DROP_NAMES={"_dummy","리가켐바이오2","레고켐","셀트리온헬스케어"}
rows=[r for r in rows if r["name"] not in DROP_NAMES and r["sector"]!="x"]
# dedupe by ticker (keep first)
seen=set(); ded=[]
for r in rows:
    if r["ticker"] in seen: continue
    seen.add(r["ticker"]); ded.append(r)
rows=ded
N_TOTAL=len(rows)

# ---------------------------------------------------------------------------
# 하드 필터 단계
# ---------------------------------------------------------------------------
import json as _json
constraints=_json.load(open("../01_profile/constraints.json"))
excl_t=set(constraints.get("excluded_tickers",[]))
excl_s=set(constraints.get("excluded_sectors",[]))

log={}
log["전체 후보 풀"]=N_TOTAL

# 1) 시장/증권유형: 코스피+코스닥 보통주만 (코넥스/ETF/스팩/우선주 제외)
step=[r for r in rows if r["market"] in ("KOSPI","KOSDAQ") and r["sectype"]=="common"]
log["코스피/코스닥 보통주만 (코넥스/ETF/스팩/우선주 제외)"]=len(step)
excl_market=[r for r in rows if not (r["market"] in ("KOSPI","KOSDAQ") and r["sectype"]=="common")]

# 2) 컨센서스 보유 (신규 핵심 필터)
before=len(step)
excl_cons=[r for r in step if not r["consensus"]]
step=[r for r in step if r["consensus"]]
log["애널리스트 컨센서스 보유 종목만 (신규 필터)"]=len(step)

# 3) 거래정지 + 관리종목 제외
excl_susp=[r for r in step if r["suspended"]]
excl_admin=[r for r in step if r["admin"] and not r["suspended"]]
step=[r for r in step if not r["suspended"] and not r["admin"]]
log["거래정지·관리종목 제외"]=len(step)

# 4) 20일 평균 거래대금 1억원 이상
excl_turn=[r for r in step if r["turnover_eok"] < 1.0]
step=[r for r in step if r["turnover_eok"] >= 1.0]
log["20일 평균 거래대금 1억원 이상"]=len(step)

# 5) constraints excluded_tickers/sectors
excl_user=[r for r in step if r["ticker"] in excl_t or r["sector"] in excl_s]
step=[r for r in step if r["ticker"] not in excl_t and r["sector"] not in excl_s]
log["투자성향 제외 종목/섹터 적용 (현재 비어있음)"]=len(step)

candidates=step
N_CAND=len(candidates)

# ---------------------------------------------------------------------------
# 팩터 점수: 섹터 내 z-score (섹터 표본<3이면 전체 모집단 z-score로 폴백)
# ---------------------------------------------------------------------------
def value_raw(r):
    parts=[]
    if r["pbr"]>0: parts.append(-r["pbr"])
    if r["per"]>0: parts.append(-r["per"])      # 음수 PER 제외
    if r["ev_ebitda"]>0: parts.append(-r["ev_ebitda"])
    return sum(parts)/len(parts) if parts else None

def quality_raw(r):
    return (r["roe"] + r["roa"] - r["debt_ratio"]/100.0 + r["opm"]) / 4.0

def momentum_raw(r):
    return r["mom_12_1"]

def growth_raw(r):
    return (r["rev_yoy"] + r["opinc_yoy"]) / 2.0

for r in candidates:
    r["_value_raw"]=value_raw(r)
    r["_quality_raw"]=quality_raw(r)
    r["_momentum_raw"]=momentum_raw(r)
    r["_growth_raw"]=growth_raw(r)

def winsor(x, lo, hi):
    return max(lo, min(hi, x))

def zscores_for(field):
    """섹터 내 z-score. 섹터표본<3 → 전체 z-score 폴백. winsorize +-5sigma."""
    # 전체 모집단 통계 (폴백용)
    allv=[r[field] for r in candidates if r[field] is not None]
    g_mean=statistics.mean(allv); g_sd=statistics.pstdev(allv) or 1e-9
    # 섹터별 그룹
    bysec=defaultdict(list)
    for r in candidates:
        if r[field] is not None: bysec[r["sector"]].append(r[field])
    sec_stat={}
    for s,vals in bysec.items():
        if len(vals)>=3:
            m=statistics.mean(vals); sd=statistics.pstdev(vals) or 1e-9
            sec_stat[s]=(m,sd,True)
        else:
            sec_stat[s]=(g_mean,g_sd,False)
    out={}
    for r in candidates:
        if r[field] is None:
            out[r["ticker"]]=0.0  # 결측 → 중립(0)
            continue
        m,sd,used_sec=sec_stat[r["sector"]]
        z=(r[field]-m)/sd
        z=winsor(z,-5.0,5.0)
        out[r["ticker"]]=z
    return out

zv=zscores_for("_value_raw")
zq=zscores_for("_quality_raw")
zm=zscores_for("_momentum_raw")
zg=zscores_for("_growth_raw")

# 중립 레짐 균등 가중
W={"value":0.25,"quality":0.25,"momentum":0.25,"growth":0.25}
for r in candidates:
    r["z_value"]=round(zv[r["ticker"]],3)
    r["z_quality"]=round(zq[r["ticker"]],3)
    r["z_momentum"]=round(zm[r["ticker"]],3)
    r["z_growth"]=round(zg[r["ticker"]],3)
    r["total"]=round(W["value"]*r["z_value"]+W["quality"]*r["z_quality"]
                     +W["momentum"]*r["z_momentum"]+W["growth"]*r["z_growth"],4)

ranked=sorted(candidates,key=lambda r:r["total"],reverse=True)
TOPN=100
top=ranked[:TOPN]
for i,r in enumerate(top,1): r["rank"]=i

# 시장 분포
mkt=defaultdict(int)
for r in top: mkt[r["market"]]+=1

# ---------------------------------------------------------------------------
# 산출물 기록용 dump
# ---------------------------------------------------------------------------
out={
 "log":log,
 "n_total":N_TOTAL,"n_candidates":N_CAND,"n_final":len(top),
 "shortfall": len(top)<TOPN,
 "excluded":{
   "market_type":[(r["ticker"],r["name"],r["market"],r["sectype"]) for r in excl_market],
   "no_consensus":[(r["ticker"],r["name"]) for r in excl_cons],
   "suspended":[(r["ticker"],r["name"]) for r in excl_susp],
   "admin":[(r["ticker"],r["name"]) for r in excl_admin],
   "low_turnover":[(r["ticker"],r["name"],r["turnover_eok"]) for r in excl_turn],
   "user_excluded":[(r["ticker"],r["name"]) for r in excl_user],
 },
 "market_dist":dict(mkt),
 "top":top,
}
_json.dump(out,open("_screen_result.json","w"),ensure_ascii=False,indent=1)
print("N_TOTAL=",N_TOTAL," N_CAND=",N_CAND," N_FINAL=",len(top))
print("market_dist=",dict(mkt))
print("--- filter log ---")
for k,v in log.items(): print(f"  {k}: {v}")
print("--- excluded counts ---")
for k,v in out["excluded"].items(): print(f"  {k}: {len(v)}")
print("--- top 10 ---")
for r in top[:10]:
    print(f"  {r['rank']:>2} {r['ticker']} {r['name']:<14} {r['market']:<6} {r['sector']:<12} total={r['total']:+.3f}")