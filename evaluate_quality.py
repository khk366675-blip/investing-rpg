import os
import sys
import json

# Force UTF-8 encoding for standard output on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

BENCHMARKS_PATH = os.path.join("cache", "sector_benchmarks.json")
CACHE_DIR = "cache"

INDICATORS = [
    'debtToEquity', 'currentRatio', 'returnOnEquity',
    'operatingMargins', 'profitMargins', 'revenueGrowth'
]

SANITY_BOUNDS = {
    'debtToEquity': (0.0, 20.0),      # normalized (raw / 100)
    'currentRatio': (0.0, 30.0),
    'returnOnEquity': (-5.0, 5.0),
    'operatingMargins': (-2.0, 2.0),
    'profitMargins': (-2.0, 2.0),
    'revenueGrowth': (-1.0, 5.0)
}

def load_benchmarks() -> dict:
    """Load sector benchmarks from cache."""
    if not os.path.exists(BENCHMARKS_PATH):
        raise FileNotFoundError(f"Benchmarks file not found at {BENCHMARKS_PATH}. Please run build_benchmarks.py first.")
    with open(BENCHMARKS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def calculate_quality_score(ticker: str, field: str, val, sector: str, benchmarks_db: dict) -> float:
    """
    Calculate 0~1 quality score using piecewise-linear interpolation.
    - val is None -> returns None (점수 없음)
    - Unknown sector -> raises ValueError (no silent defaults)
    - debtToEquity -> reversed (lower is better: 1.0 - score)
    """
    if not sector or sector.strip() == "":
        raise ValueError(f"FLAG: Ticker '{ticker}' has empty or missing sector. Cannot evaluate quality score.")
        
    benchmarks = benchmarks_db.get("benchmarks", {})
    if sector not in benchmarks:
        raise ValueError(f"FLAG: Sector '{sector}' for ticker '{ticker}' is not found in sector benchmarks.")
        
    if val is None:
        return None
        
    # Get benchmark percentiles
    ind_bench = benchmarks[sector]["indicators"].get(field)
    if not ind_bench:
        return None
        
    p10 = ind_bench["p10"]
    p50 = ind_bench["p50"]
    p90 = ind_bench["p90"]
    
    # Get sanity bounds
    lower_bound, upper_bound = SANITY_BOUNDS[field]
    
    # Cast val to float
    val_f = float(val)
    
    # Piecewise-linear interpolation
    if val_f < p10:
        if p10 > lower_bound:
            score = 0.0 + (val_f - lower_bound) / (p10 - lower_bound) * 0.1
        else:
            score = 0.1
    elif val_f < p50:
        if p50 > p10:
            score = 0.1 + (val_f - p10) / (p50 - p10) * 0.4
        else:
            score = 0.5
    elif val_f < p90:
        if p90 > p50:
            score = 0.5 + (val_f - p50) / (p90 - p50) * 0.4
        else:
            score = 0.9
    else:
        if upper_bound > p90:
            score = 0.9 + (val_f - p90) / (upper_bound - p90) * 0.1
        else:
            score = 1.0
            
    # Clamp to [0.0, 1.0]
    score = max(0.0, min(1.0, score))
    
    # Reverse for debtToEquity (lower is better)
    if field == 'debtToEquity':
        score = 1.0 - score
        
    return score

def load_ticker_data(ticker: str) -> dict:
    """Load cached raw data for a ticker."""
    cache_path = os.path.join(CACHE_DIR, f"{ticker}.json")
    if not os.path.exists(cache_path):
        # Fallback to sp500 cache if main cache doesn't exist
        fallback_path = os.path.join(CACHE_DIR, "sp500", f"{ticker}.json")
        if os.path.exists(fallback_path):
            cache_path = fallback_path
        else:
            print(f"Warning: Cached data for {ticker} not found. Please run Step 1 harness first.")
            return None
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        return data.get("info") if "info" in data else data

def main():
    benchmarks_db = load_benchmarks()
    
    # Probe tickers: AAPL, JPM, KO, RIVN
    probe_tickers = ["AAPL", "JPM", "KO", "RIVN"]
    
    print("\n### 5. 방향 sanity 프로브 결과 (AAPL, JPM, KO, RIVN)")
    
    for ticker in probe_tickers:
        info = load_ticker_data(ticker)
        if not info:
            continue
            
        sector = info.get("sector")
        print(f"\n#### Ticker: {ticker} | Sector: {sector}")
        print("| Indicator | Raw Value | Normalized Value | Quality Score (0~1) |")
        print("| --- | --- | --- | --- |")
        
        for ind in INDICATORS:
            raw_val = info.get(ind)
            norm_val = raw_val
            
            # Normalize debtToEquity
            if ind == 'debtToEquity' and raw_val is not None:
                norm_val = raw_val / 100.0
                
            try:
                score = calculate_quality_score(ticker, ind, norm_val, sector, benchmarks_db)
                score_str = f"{score:.4f}" if score is not None else "점수 없음"
            except Exception as e:
                score_str = f"ERROR: {str(e)}"
                
            raw_str = f"{raw_val:.4f}" if raw_val is not None else "None"
            norm_str = f"{norm_val:.4f}" if norm_val is not None else "None"
            
            print(f"| {ind} | {raw_str} | {norm_str} | {score_str} |")
            
    # Dummy sector test
    print("\n### 6. 미지섹터 처리 확인 (의도적 더미 테스트)")
    
    # 6a: Missing sector string
    print("\nCase A: Sector is empty string (sector='')")
    try:
        calculate_quality_score("DUMMY", "currentRatio", 1.5, "", benchmarks_db)
        print("FAIL: Expected exception was not raised.")
    except Exception as e:
        print(f"SUCCESS: Raised expected exception: {e}")
        
    # 6b: Unknown sector
    print("\nCase B: Sector is unknown (sector='Superb Tech')")
    try:
        calculate_quality_score("DUMMY", "currentRatio", 1.5, "Superb Tech", benchmarks_db)
        print("FAIL: Expected exception was not raised.")
    except Exception as e:
        print(f"SUCCESS: Raised expected exception: {e}")

if __name__ == "__main__":
    main()
