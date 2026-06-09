import sys
import os
import json
import math

# Force UTF-8 encoding for standard output on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

from synthesis import get_ticker_info, synthesize_portfolio_stats, load_benchmarks

# 1. GICS Sector to RPG Race Mapping
SECTOR_RACE_MAP = {
    "Financial Services": "황금기사단",
    "Technology": "기계종",
    "Healthcare": "생명교단",
    "Consumer Cyclical": "유랑상단",
    "Consumer Defensive": "곡물수호자",
    "Industrials": "강철공방",
    "Energy": "화염거인",
    "Basic Materials": "광맥족",
    "Real Estate": "성채수호족",
    "Utilities": "전류정령",
    "Communication Services": "파동전령"
}

# 1.1. GICS Sector to Korean Sector Label Mapping
SECTOR_LABEL_MAP = {
    "Financial Services": "금융",
    "Technology": "테크",
    "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재",
    "Consumer Defensive": "필수소비재",
    "Industrials": "산업재",
    "Energy": "에너지",
    "Basic Materials": "소재",
    "Real Estate": "부동산",
    "Utilities": "유틸리티",
    "Communication Services": "커뮤니케이션"
}

# 2. GICS Industry to Subfaction Mapping Table
SUBFACTION_MAP = {
    # Technology
    ("Technology", "Software - Application"): "소프트웨어",
    ("Technology", "Software - Infrastructure"): "소프트웨어",
    ("Technology", "Semiconductors"): "반도체",
    ("Technology", "Semiconductor Equipment & Materials"): "반도체장비",
    ("Technology", "Computer Hardware"): "하드웨어",
    ("Technology", "Information Technology Services"): "IT서비스",
    ("Technology", "Electronic Components"): "전자부품",
    
    # Financial Services
    ("Financial Services", "Banks - Diversified"): "은행",
    ("Financial Services", "Banks - Regional"): "은행",
    ("Financial Services", "Asset Management"): "자산운용",
    ("Financial Services", "Credit Services"): "신용결제",
    ("Financial Services", "Capital Markets"): "투자은행",
    ("Financial Services", "Insurance - Property & Casualty"): "보험",
    ("Financial Services", "Insurance - Life"): "보험",
    ("Financial Services", "Financial Data & Stock Exchanges"): "금융정보",
    
    # Healthcare
    ("Healthcare", "Diagnostics & Research"): "진단연구",
    ("Healthcare", "Medical Devices"): "의료기기",
    ("Healthcare", "Drug Manufacturers - General"): "제약",
    ("Healthcare", "Drug Manufacturers - Specialty & Generic"): "제약",
    ("Healthcare", "Medical Instruments & Supplies"): "의료장비",
    ("Healthcare", "Healthcare Plans"): "의료서비스",
    ("Healthcare", "Biotechnology"): "바이오",
    
    # Consumer Cyclical
    ("Consumer Cyclical", "Travel Services"): "여행레저",
    ("Consumer Cyclical", "Restaurants"): "외식",
    ("Consumer Cyclical", "Auto Manufacturers"): "자동차",
    ("Consumer Cyclical", "Specialty Retail"): "전문유통",
    ("Consumer Cyclical", "Internet Retail"): "이커머스",
    ("Consumer Cyclical", "Packaging & Containers"): "용기포장",
    
    # Consumer Defensive
    ("Consumer Defensive", "Packaged Foods"): "가공식품",
    ("Consumer Defensive", "Household & Personal Products"): "생활용품",
    ("Consumer Defensive", "Beverages - Non-Alcoholic"): "식음료",
    ("Consumer Defensive", "Beverages - Brewers"): "식음료",
    ("Consumer Defensive", "Discount Stores"): "할인점",
    ("Consumer Defensive", "Tobacco"): "기호품",
    
    # Industrials
    ("Industrials", "Specialty Industrial Machinery"): "산업기계",
    ("Industrials", "Aerospace & Defense"): "방산우주",
    ("Industrials", "Integrated Freight & Logistics"): "물류운송",
    ("Industrials", "Railroads"): "물류운송",
    ("Industrials", "Building Products & Equipment"): "건축자재",
    
    # Energy
    ("Energy", "Oil & Gas E&P"): "에너지 E&P",
    ("Energy", "Oil & Gas Integrated"): "에너지 E&P",
    ("Energy", "Oil & Gas Midstream"): "에너지유통",
    ("Energy", "Oil & Gas Equipment & Services"): "에너지유통",
    
    # Basic Materials
    ("Basic Materials", "Specialty Chemicals"): "화학소재",
    ("Basic Materials", "Steel"): "금속소재",
    ("Basic Materials", "Gold"): "광물자원",
    ("Basic Materials", "Copper"): "광물자원",
    
    # Real Estate
    ("Real Estate", "REIT - Specialty"): "특수리츠",
    ("Real Estate", "REIT - Residential"): "주거리츠",
    ("Real Estate", "REIT - Retail"): "상업리츠",
    
    # Utilities
    ("Utilities", "Utilities - Regulated Electric"): "전력유틸리티",
    ("Utilities", "Utilities - Independent Power Producers"): "발전유틸리티",
    
    # Communication Services
    ("Communication Services", "Entertainment"): "엔터테인먼트",
    ("Communication Services", "Telecom Services"): "통신서비스",
    ("Communication Services", "Internet Content & Information"): "인터넷서비스"
}

# 3. RPG Accent Symbols for Dominant Stats
STAT_ACCENT_MAP = {
    "HP": "심장",
    "ATK": "검",
    "DEF": "방패",
    "SPD": "날개",
    "CRIT": "안대",
    "REGEN": "성수",
    "숙련": "지팡이"
}

# 4. Color Palette Mapping (Placeholders for 3a)
PALETTE_MAP = {
    "황금기사단": {"primary": "Gold", "secondary": "Bronze", "accent": "Ruby"},
    "기계종": {"primary": "Cyber_Blue", "secondary": "Neon_Purple", "accent": "Cyan"},
    "생명교단": {"primary": "Forest_Green", "secondary": "Silver", "accent": "Emerald"},
    "유랑상단": {"primary": "Orange", "secondary": "Brown", "accent": "Amber"},
    "곡물수호자": {"primary": "Brown", "secondary": "Tan", "accent": "Wheat"},
    "강철공방": {"primary": "Steel_Gray", "secondary": "Iron", "accent": "Rust"},
    "화염거인": {"primary": "Crimson", "secondary": "Charcoal", "accent": "Fire"},
    "광맥족": {"primary": "Earth_Brown", "secondary": "Stone", "accent": "Topaz"},
    "성채수호족": {"primary": "Granite", "secondary": "Slate", "accent": "Quartz"},
    "전류정령": {"primary": "Sky_Blue", "secondary": "White", "accent": "Gold"},
    "파동전령": {"primary": "Pink", "secondary": "Lavender", "accent": "Rose"},
    "지수형": {"primary": "Neutral_Gray", "secondary": "Silver", "accent": "Platinum"}
}

# Market Cap Thresholds for Rarity Frame
MC_LEGENDARY = 200_000_000_000  # 200B
MC_EPIC = 10_000_000_000       # 10B
MC_RARE = 2_000_000_000         # 2B

def get_level_tier(lvl_mult: float) -> int:
    """Divide lvl_mult [0.4, 1.0] into 5 equal tiers."""
    if lvl_mult < 0.52:
        return 1
    elif lvl_mult < 0.64:
        return 2
    elif lvl_mult < 0.76:
        return 3
    elif lvl_mult < 0.88:
        return 4
    else:
        return 5

def get_rarity_frame(market_cap: float) -> str:
    """Categorize market cap into rarity frames."""
    if market_cap is None:
        return "common"
    if market_cap >= MC_LEGENDARY:
        return "legendary"
    elif market_cap >= MC_EPIC:
        return "epic"
    elif market_cap >= MC_RARE:
        return "rare"
    else:
        return "common"

def get_dominant_stat(stats: dict) -> str:
    """Return highest stat using deterministic tie-breaker order."""
    order = ["HP", "ATK", "DEF", "SPD", "CRIT", "REGEN", "숙련"]
    # Sort by value descending, and if tied, by fixed priority order
    sorted_stats = sorted(stats.items(), key=lambda x: (x[1], -order.index(x[0])), reverse=True)
    return sorted_stats[0][0]

def generate_appearance_spec(ticker: str, data_2c: dict) -> dict:
    """
    Generate AppearanceSpec JSON for a ticker using GICS metadata and 2c stats.
    """
    info = get_ticker_info(ticker)
    if not info:
        raise ValueError(f"Could not find cache info for ticker {ticker}")
        
    asset_type = data_2c["asset_type"]
    stats = data_2c["stats"]
    provenance = data_2c["provenance"]
    
    sector = info.get("sector")
    industry = info.get("industry")
    market_cap = info.get("marketCap")
    
    # 1. Determine Race, Body Type, & Sector Label (Step 3a-fix)
    if asset_type in ("ETF", "REIT"):
        race = "지수형"
        race_body_type = "중립골격"
        sector_label = "지수형"
    else:
        if sector not in SECTOR_RACE_MAP:
            raise ValueError(f"FLAG: Sector '{sector}' for ticker '{ticker}' is not found in SECTOR_RACE_MAP.")
        race = SECTOR_RACE_MAP[sector]
        race_body_type = f"{race}_체형"
        sector_label = SECTOR_LABEL_MAP[sector]
        
    # 2. Determine Subfaction
    if asset_type in ("ETF", "REIT"):
        subfaction = "지수형"
    else:
        subfaction = SUBFACTION_MAP.get((sector, industry))
        if not subfaction:
            subfaction = "기타"
            # Log the unmapped industry
            print(f"[Mapping Alert] Ticker '{ticker}': GICS industry '{industry}' in sector '{sector}' is not mapped. Defaulted to '기타'.")
            
    # 3. Color Palette
    palette = PALETTE_MAP.get(race, {"primary": "Gray", "secondary": "Dark_Gray", "accent": "White"})
    
    # 4. Size & Aura (HP and Skill normalized [0, 1])
    hp_val = stats["HP"]
    sk_val = stats["숙련"]
    size = round((hp_val - 10) / 990, 4)
    aura = round((sk_val - 10) / 990, 4)
    
    # 5. Level Tier
    # Retrieve level multiplier from ATK (or any other stock-axis stat)
    lvl_mult = provenance["ATK"]["lvl_mult"]
    level_tier = get_level_tier(lvl_mult)
    
    # 6. Rarity Frame
    rarity_frame = get_rarity_frame(market_cap)
    
    # 7. Dominant Stat & Accents
    dominant_stat = get_dominant_stat(stats)
    dominant_accent = STAT_ACCENT_MAP[dominant_stat]
    accents = [f"{subfaction}_휘장", dominant_accent]
    
    # 8. Estimated Badge
    estimated_stats = [s for s, p in provenance.items() if p["estimated"]]
    estimated_badge = {
        "enabled": len(estimated_stats) > 0,
        "stats": estimated_stats
    }
    
    # 9. Normalize Type
    spec_type = asset_type
    if spec_type not in ("EQUITY", "ETF", "REIT"):
        spec_type = "EQUITY"
        
    return {
        "ticker": ticker,
        "race": race,
        "raceBodyType": race_body_type,
        "subfaction": subfaction,
        "sectorLabel": sector_label,
        "palette": palette,
        "size": size,
        "aura": aura,
        "levelTier": level_tier,
        "rarityFrame": rarity_frame,
        "accents": accents,
        "dominantStat": dominant_stat,
        "estimatedBadge": estimated_badge,
        "type": spec_type
    }

def main():
    benchmarks_db = load_benchmarks()
    
    # Portfolio basket including target tickers for verification
    portfolio = [
        ("AAPL", 10),
        ("JPM", 5),
        ("RIVN", 8),
        ("SPY", 3),
        ("SEG", 20),
        ("NVDA", 10),
        ("KO", 15),
        ("XOM", 12)
    ]
    
    # Run 2c Synthesis
    results_2c = synthesize_portfolio_stats(portfolio, benchmarks_db)
    
    # 1. Generate and Print AppearanceSpec JSON for 8 tickers
    print("### 1. 8종목 AppearanceSpec JSON 전체 출력")
    specs = {}
    for ticker, _ in portfolio:
        spec = generate_appearance_spec(ticker, results_2c[ticker])
        specs[ticker] = spec
    print(json.dumps(specs, indent=2, ensure_ascii=False))
    
    print("\n" + "="*80 + "\n")
    
    # 2. Subfaction Mapping & Sector Label Validation Table
    print("### 2. 분파 및 섹터 라벨 매핑 검증")
    print("| Ticker | GICS Sector | Expected Race | Actual Race | Expected Label | Actual Label | Subfaction | Matching |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    checks = [
        ("AAPL", "Technology", "기계종", specs["AAPL"]["race"], "테크", specs["AAPL"]["sectorLabel"], specs["AAPL"]["subfaction"]),
        ("NVDA", "Technology", "기계종", specs["NVDA"]["race"], "테크", specs["NVDA"]["sectorLabel"], specs["NVDA"]["subfaction"]),
        ("JPM", "Financial Services", "황금기사단", specs["JPM"]["race"], "금융", specs["JPM"]["sectorLabel"], specs["JPM"]["subfaction"]),
        ("KO", "Consumer Defensive", "곡물수호자", specs["KO"]["race"], "필수소비재", specs["KO"]["sectorLabel"], specs["KO"]["subfaction"]),
        ("XOM", "Energy", "화염거인", specs["XOM"]["race"], "에너지", specs["XOM"]["sectorLabel"], specs["XOM"]["subfaction"]),
        ("SEG", "Real Estate", "성채수호족", specs["SEG"]["race"], "부동산", specs["SEG"]["sectorLabel"], specs["SEG"]["subfaction"]),
        ("RIVN", "Consumer Cyclical", "유랑상단", specs["RIVN"]["race"], "경기소비재", specs["RIVN"]["sectorLabel"], specs["RIVN"]["subfaction"])
    ]
    for tick, sec, exp_race, act_race, exp_lbl, act_lbl, sub in checks:
        matching = "OK" if (exp_race == act_race and exp_lbl == act_lbl) else "FAIL"
        print(f"| {tick} | {sec} | {exp_race} | {act_race} | {exp_lbl} | {act_lbl} | {sub} | {matching} |")
        
    print("\n" + "="*80 + "\n")
    
    # 3. Fallback check (Unknown industry dummy ticker)
    print("### 3. 기타 안착 검증 (미매핑 Industry 시뮬레이션)")
    # Mock a dummy ticker data
    dummy_ticker = "DUMMY_TECH"
    dummy_data = {
        "asset_type": "EQUITY",
        "stats": {"HP": 500, "ATK": 300, "DEF": 300, "SPD": 300, "CRIT": 300, "REGEN": 300, "숙련": 500},
        "provenance": {
            "HP": {"value": 500, "estimated": False, "lvl_mult": 1.0},
            "ATK": {"value": 300, "estimated": False, "lvl_mult": 0.70},
            "DEF": {"value": 300, "estimated": False, "lvl_mult": 0.70},
            "SPD": {"value": 300, "estimated": False, "lvl_mult": 0.70},
            "CRIT": {"value": 300, "estimated": False, "lvl_mult": 0.70},
            "REGEN": {"value": 300, "estimated": False, "lvl_mult": 0.70},
            "숙련": {"value": 500, "estimated": False, "lvl_mult": 0.70}
        }
    }
    global get_ticker_info
    original_get_ticker_info = get_ticker_info
    def mock_get_ticker_info(ticker):
        if ticker == "DUMMY_TECH":
            return {"sector": "Technology", "industry": "Quantum Magic", "marketCap": 1_000_000_000, "quoteType": "EQUITY"}
        return original_get_ticker_info(ticker)
    
    get_ticker_info = mock_get_ticker_info
    try:
        dummy_spec = generate_appearance_spec(dummy_ticker, dummy_data)
        print(f"Ticker: {dummy_ticker} -> Sector: Technology, Industry: Quantum Magic")
        print(f"Mapped Race: {dummy_spec['race']} (Expected: 기계종)")
        print(f"Mapped Sector Label: {dummy_spec['sectorLabel']} (Expected: 테크)")
        print(f"Mapped Subfaction: {dummy_spec['subfaction']} (Expected: 기타)")
        print(f"Fallback check result: {'SUCCESS (기타 안착)' if dummy_spec['subfaction'] == '기타' else 'FAIL'}")
    finally:
        get_ticker_info = original_get_ticker_info
        
    print("\n" + "="*80 + "\n")
    
    # 4. ETF check
    print("### 4. ETF 분기 검증 (SPY)")
    spy_spec = specs["SPY"]
    print(f"SPY Race: {spy_spec['race']} (Expected: 지수형)")
    print(f"SPY Body Type: {spy_spec['raceBodyType']} (Expected: 중립골격)")
    print(f"SPY Subfaction: {spy_spec['subfaction']} (Expected: 지수형)")
    print(f"ETF check result: {'SUCCESS' if spy_spec['race'] == '지수형' and spy_spec['raceBodyType'] == '중립골격' else 'FAIL'}")
    
    print("\n" + "="*80 + "\n")
    
    # 5. JPM estimated check
    print("### 5. estimatedBadge 검증 (JPM)")
    jpm_spec = specs["JPM"]
    print(f"JPM estimatedBadge: {json.dumps(jpm_spec['estimatedBadge'], ensure_ascii=False)}")
    print(f"JPM check result: {'SUCCESS' if jpm_spec['estimatedBadge']['enabled'] and 'DEF' in jpm_spec['estimatedBadge']['stats'] else 'FAIL'}")
    
    print("\n" + "="*80 + "\n")
    
    # 6. Determinism check
    print("### 6. 결정론 검증 (Determinism Test)")
    spec_run2 = generate_appearance_spec("AAPL", results_2c["AAPL"])
    det_match = specs["AAPL"] == spec_run2
    print(f"AAPL Spec Run 1 vs Run 2 match: {'SUCCESS (100% 동일)' if det_match else 'FAIL'}")

if __name__ == "__main__":
    main()
