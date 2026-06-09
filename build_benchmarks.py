import os
import sys
import json
import time
import datetime
import argparse
from io import StringIO
import pandas as pd
import numpy as np
import requests
import yfinance as yf

# Force UTF-8 encoding for standard output on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

SP500_CACHE_DIR = os.path.join("cache", "sp500")
BENCHMARKS_PATH = os.path.join("cache", "sector_benchmarks.json")

KNOWN_SECTORS = {
    "Technology", "Financial Services", "Healthcare", "Consumer Cyclical",
    "Consumer Defensive", "Industrials", "Energy", "Basic Materials",
    "Real Estate", "Utilities", "Communication Services"
}

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

def fetch_sp500_tickers() -> list:
    """Scrape S&P 500 tickers from Wikipedia and normalize them."""
    print("Scraping S&P 500 tickers from Wikipedia...")
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        tickers = df['Symbol'].tolist()
        # Normalize tickers (convert dots/slashes to dashes for yfinance compatibility)
        normalized = [t.strip().upper().replace('.', '-').replace('/', '-') for t in tickers]
        print(f"Successfully scraped {len(normalized)} tickers.")
        return normalized
    except Exception as e:
        print(f"Error scraping S&P 500 tickers: {e}")
        raise

def get_ticker_info(ticker: str, refresh: bool = False) -> dict:
    """Fetch ticker info from yfinance with local caching."""
    os.makedirs(SP500_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(SP500_CACHE_DIR, f"{ticker}.json")
    
    if not refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
            
    # Fetch from yfinance
    print(f"Fetching yfinance info for {ticker}...")
    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info
        if info and isinstance(info, dict):
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            return info
    except Exception as e:
        print(f"Warning: Failed to fetch {ticker}: {e}")
        
    return None

def build_benchmarks(refresh_all: bool = False):
    """Fetch universe, clean data, compute sector benchmarks, and cache results."""
    tickers = fetch_sp500_tickers()
    
    raw_data = []
    skipped_count = 0
    
    total = len(tickers)
    for idx, ticker in enumerate(tickers):
        info = get_ticker_info(ticker, refresh=refresh_all)
        if not info:
            skipped_count += 1
            continue
            
        # Get sector
        sector = info.get("sector")
        
        # Collect values
        row = {"ticker": ticker, "sector": sector}
        for ind in INDICATORS:
            val = info.get(ind)
            # Normalize debtToEquity
            if ind == 'debtToEquity' and val is not None:
                val = val / 100.0
            row[ind] = val
            
        raw_data.append(row)
        
        # Yield cpu / minimize rate limits if downloading
        if not os.path.exists(os.path.join(SP500_CACHE_DIR, f"{ticker}.json")):
            time.sleep(0.1)
            
        if (idx + 1) % 50 == 0 or (idx + 1) == total:
            print(f"Processed {idx + 1}/{total} tickers...")
            
    df_raw = pd.DataFrame(raw_data)
    print(f"Collected raw info for {len(df_raw)} tickers. Skipped {skipped_count}.")
    
    # Sector validation
    distinct_sectors = df_raw['sector'].dropna().unique().tolist()
    print(f"Distinct sectors observed: {distinct_sectors}")
    
    unmapped_sectors = [s for s in distinct_sectors if s not in KNOWN_SECTORS]
    if unmapped_sectors:
        print(f"FLAG: Observed sectors not in known 11: {unmapped_sectors}")
    
    # Calculate benchmarks per sector
    benchmarks = {}
    sector_counts = df_raw['sector'].value_counts().to_dict()
    
    # Sanity filter stats
    filter_stats = {ind: {"dropped_null": 0, "dropped_sanity": 0} for ind in INDICATORS}
    
    for sector in KNOWN_SECTORS:
        sector_df = df_raw[df_raw['sector'] == sector]
        n_tickers = len(sector_df)
        
        benchmarks[sector] = {
            "N": n_tickers,
            "confidence": "OK" if n_tickers >= 30 else "저신뢰",
            "indicators": {}
        }
        
        for ind in INDICATORS:
            series = sector_df[ind]
            
            # 1. Drop nulls
            dropped_null = series.isna().sum()
            filter_stats[ind]["dropped_null"] += int(dropped_null)
            clean_series = series.dropna()
            
            # 2. Apply sanity bounds
            lower, upper = SANITY_BOUNDS[ind]
            sanity_mask = (clean_series >= lower) & (clean_series <= upper)
            dropped_sanity = (~sanity_mask).sum()
            filter_stats[ind]["dropped_sanity"] += int(dropped_sanity)
            
            filtered_series = clean_series[sanity_mask]
            n_valid = len(filtered_series)
            
            coverage = n_valid / n_tickers if n_tickers > 0 else 0.0
            
            if n_valid > 0:
                p10 = float(np.percentile(filtered_series, 10))
                p50 = float(np.percentile(filtered_series, 50))
                p90 = float(np.percentile(filtered_series, 90))
            else:
                p10, p50, p90 = 0.0, 0.0, 0.0
                
            benchmarks[sector]["indicators"][ind] = {
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "coverage_pct": round(coverage * 100, 2),
                "total_valid": n_valid,
                "status": "OK" if coverage >= 0.5 else "저커버리지"
            }
            
    # Save output
    output_data = {
        "build_date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "filter_stats": filter_stats,
        "benchmarks": benchmarks
    }
    
    os.makedirs(os.path.dirname(BENCHMARKS_PATH), exist_ok=True)
    with open(BENCHMARKS_PATH, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully saved sector benchmarks to {BENCHMARKS_PATH}.")

def main():
    parser = argparse.ArgumentParser(description="investing-rpg Benchmark Generator")
    parser.add_argument("--refresh", action="store_true", help="Force refetching of S&P 500 yfinance data")
    args = parser.parse_args()
    
    build_benchmarks(refresh_all=args.refresh)

if __name__ == "__main__":
    main()
