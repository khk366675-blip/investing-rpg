import os
import sys
import json
import math
import argparse
import datetime

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

def get_ticker_info(ticker: str) -> dict:
    """Find and load raw cached ticker data from cache/ or cache/sp500/."""
    paths = [
        os.path.join(CACHE_DIR, f"{ticker}.json"),
        os.path.join(CACHE_DIR, "sp500", f"{ticker}.json")
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("info") if "info" in data else data
            except Exception:
                pass
    return None

def get_clean_dividend_yield(info: dict) -> float:
    """Standardize dividend yield to a decimal format (e.g. 0.0193 for 1.93%)."""
    dy = info.get("dividendYield") or info.get("yield")
    if dy is not None:
        dy = float(dy)
        tay = info.get("trailingAnnualDividendYield")
        if tay is not None:
            tay = float(tay)
            # If dy is expressed as percentage (e.g. 1.93) and tay as decimal (e.g. 0.018),
            # dy will be closer to tay * 100 than tay.
            if abs(dy - tay * 100) < abs(dy - tay):
                return dy / 100.0
            else:
                return dy
        else:
            # Heuristic fallback if trailingAnnualDividendYield is missing
            if dy > 0.05:
                return dy / 100.0
            return dy
    return 0.0

def calculate_quality_score(ticker: str, field: str, val, sector: str, benchmarks_db: dict) -> tuple:
    """
    Calculate 0~1 quality score using piecewise-linear interpolation and linear extrapolation.
    Returns: (score, estimated, note)
    """
    if not sector or sector.strip() == "":
        raise ValueError(f"FLAG: Ticker '{ticker}' has empty or missing sector. Cannot evaluate quality score.")
        
    benchmarks = benchmarks_db.get("benchmarks", {})
    if sector not in benchmarks:
        raise ValueError(f"FLAG: Sector '{sector}' for ticker '{ticker}' is not found in benchmarks.")
        
    if val is None:
        return None, False, "Value is missing"
        
    ind_bench = benchmarks[sector]["indicators"].get(field)
    if not ind_bench:
        return None, False, f"Benchmark indicator '{field}' not found"
        
    p10 = ind_bench["p10"]
    p50 = ind_bench["p50"]
    p90 = ind_bench["p90"]
    
    val_f = float(val)
    
    # Piecewise-linear interpolation with linear extrapolation
    if val_f < p10:
        if p50 > p10:
            slope = 0.4 / (p50 - p10)
            score = 0.1 + (val_f - p10) * slope
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
        if p90 > p50:
            slope = 0.4 / (p90 - p50)
            score = 0.9 + (val_f - p90) * slope
        else:
            score = 0.9
            
    # Clamp to [0.0, 1.0]
    score = max(0.0, min(1.0, score))
    
    # Direction inversion for debtToEquity (lower is better)
    if field == 'debtToEquity':
        score = 1.0 - score
        
    return score, False, ""

def classify_asset_type(info: dict) -> str:
    """Determine asset type: EQUITY, ETF, REIT, ADR."""
    quote_type = info.get("quoteType", "").upper()
    industry = info.get("industry", "")
    long_name = info.get("longName", "")
    country = info.get("country", "")
    
    if quote_type == "ETF":
        return "ETF"
    elif quote_type == "REIT" or (industry and "REIT" in industry):
        return "REIT"
    elif quote_type == "EQUITY":
        if country and country != "United States":
            return "ADR"
        if long_name and ("ADR" in long_name or "American Depositary Shares" in long_name or "Depositary Receipt" in long_name):
            return "ADR"
        return "EQUITY"
    return quote_type if quote_type else "EQUITY"

def synthesize_portfolio_stats(portfolio: list, benchmarks_db: dict) -> dict:
    """
    Synthesize stats (HP, ATK, DEF, SPD, CRIT, REGEN, 숙련) and provenance for a portfolio.
    portfolio: list of tuples like [('AAPL', 10), ('JPM', 5)]
    """
    results = {}
    positions = []
    
    # 1. Aggregate duplicate positions (Step 2c)
    aggregated = {}
    for ticker, shares in portfolio:
        aggregated[ticker] = aggregated.get(ticker, 0) + shares
    portfolio_agg = list(aggregated.items())
    
    # Pre-load ticker info and prices
    for ticker, shares in portfolio_agg:
        info = get_ticker_info(ticker)
        if not info:
            print(f"Warning: Could not find cache info for {ticker}.")
            continue
            
        # Try currentPrice, fallback to regularMarketPrice
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        if price is None:
            print(f"Warning: Could not determine price for {ticker}.")
            continue
            
        positions.append({
            "ticker": ticker,
            "shares": shares,
            "price": float(price),
            "info": info
        })
        
    if not positions:
        return {}
        
    # 1. Personalization Axis & Level Multipliers (requires full portfolio values) (Step 2c)
    hp_map = calculate_portfolio_hp(positions)
    lvl_mult_map = calculate_portfolio_lvl_mults(positions)
    
    # 2. Ticker Axis: Calculate stats for each ticker
    for pos in positions:
        ticker = pos["ticker"]
        shares = pos["shares"]
        price = pos["price"]
        info = pos["info"]
        
        asset_type = classify_asset_type(info)
        sector = info.get("sector")
        
        # Level multiplier (Step 2c)
        lvl_mult, lvl_norm, lvl_note = lvl_mult_map[ticker]
        
        ticker_stats = {}
        provenance = {}
        
        # Pull HP from personalization map
        hp_val, hp_pct, hp_note = hp_map[ticker]
        ticker_stats["HP"] = hp_val
        provenance["HP"] = {
            "stat": "HP",
            "value": hp_val,
            "axis": "개인화",
            "source_metric": "shares * price",
            "raw_value": shares * price,
            "sector": sector or "N/A",
            "sector_percentile": round(hp_pct, 4),
            "estimated": False,
            "note": hp_note
        }
        
        if asset_type == "ETF":
            # --- ETF / Index branch ---
            # ATK: price momentum only (growth score = 0.5)
            high = info.get("fiftyTwoWeekHigh")
            low = info.get("fiftyTwoWeekLow")
            if high is not None and low is not None and high > low:
                momentum = (price - low) / (high - low)
                momentum = max(0.0, min(1.0, momentum))
                mom_note = f"52-week momentum: {momentum:.4f}"
                est_atk = True
            else:
                momentum = 0.5
                mom_note = "52-week pricing missing, used 0.5"
                est_atk = True
            
            atk_score = 0.7 * 0.5 + 0.3 * momentum
            atk_val = int(round(10 + atk_score * 990))
            ticker_stats["ATK"] = atk_val
            provenance["ATK"] = {
                "stat": "ATK",
                "value": atk_val,
                "axis": "종목",
                "source_metric": "0.7 * G_score (0.5) + 0.3 * Mom",
                "raw_value": momentum,
                "sector": "N/A",
                "sector_percentile": 0.5,
                "estimated": est_atk,
                "note": f"ETF recipe. Growth missing (used 0.5). {mom_note}"
            }
            
            # DEF: estimated median 505
            ticker_stats["DEF"] = 505
            provenance["DEF"] = {
                "stat": "DEF",
                "value": 505,
                "axis": "종목",
                "source_metric": "N/A",
                "raw_value": None,
                "sector": "N/A",
                "sector_percentile": 0.5,
                "estimated": True,
                "note": "ETF recipe. Corporate health missing, used 0.5 (505)"
            }
            
            # SPD: beta if present, else 1.0 (SPD = 300)
            beta = info.get("beta")
            if beta is not None:
                beta_val = float(beta)
                est_spd = False
                spd_note = f"Beta: {beta_val:.4f}"
            else:
                beta_val = 1.0
                est_spd = True
                spd_note = "Beta missing, used 1.0 (300)"
                
            spd_val = scale_beta_to_spd(beta_val)
            ticker_stats["SPD"] = spd_val
            provenance["SPD"] = {
                "stat": "SPD",
                "value": spd_val,
                "axis": "종목",
                "source_metric": "beta",
                "raw_value": beta_val,
                "sector": "N/A",
                "sector_percentile": 0.5,
                "estimated": est_spd,
                "note": spd_note
            }
            
            # CRIT: 52-week spread
            if high is not None and low is not None and low > 0:
                spread = (high - low) / low
                spread_score = math.log(1 + spread) / math.log(3.0)
                crit_val = int(round(10 + min(1.0, spread_score) * 990))
                crit_note = f"Spread: {spread:.4f}"
                est_crit = False
            else:
                crit_val = 300
                crit_note = "52-week pricing missing, used 300"
                est_crit = True
                
            ticker_stats["CRIT"] = crit_val
            provenance["CRIT"] = {
                "stat": "CRIT",
                "value": crit_val,
                "axis": "종목",
                "source_metric": "spread",
                "raw_value": spread if (high is not None and low is not None and low > 0) else None,
                "sector": "N/A",
                "sector_percentile": 0.5,
                "estimated": est_crit,
                "note": crit_note
            }
            
            # REGEN: dividendYield
            dy = get_clean_dividend_yield(info)
            dy_score = min(1.0, dy / 0.08)
            regen_val = int(round(10 + dy_score * 990))
            ticker_stats["REGEN"] = regen_val
            provenance["REGEN"] = {
                "stat": "REGEN",
                "value": regen_val,
                "axis": "종목",
                "source_metric": "dividendYield",
                "raw_value": dy,
                "sector": "N/A",
                "sector_percentile": round(dy_score, 4),
                "estimated": False,
                "note": f"Dividend yield: {dy:.2%}" if dy > 0 else "No dividend yield"
            }
            
            # 숙련: estimated median 505
            ticker_stats["숙련"] = 505
            provenance["숙련"] = {
                "stat": "숙련",
                "value": 505,
                "axis": "종목",
                "source_metric": "N/A",
                "raw_value": None,
                "sector": "N/A",
                "sector_percentile": 0.5,
                "estimated": True,
                "note": "ETF recipe. Profitability missing, used 0.5 (505)"
            }
            
        else:
            # --- Equity / REIT branch ---
            # ATK: Growth (70%) + Momentum (30%)
            growth = info.get("revenueGrowth")
            growth_score, est_growth, growth_note = calculate_quality_score(ticker, 'revenueGrowth', growth, sector, benchmarks_db)
            if growth_score is None:
                growth_score = 0.5
                est_growth = True
                growth_note = "Growth missing, used 0.5"
                
            high = info.get("fiftyTwoWeekHigh")
            low = info.get("fiftyTwoWeekLow")
            if high is not None and low is not None and high > low:
                momentum = (price - low) / (high - low)
                momentum = max(0.0, min(1.0, momentum))
                est_mom = False
                mom_note = f"52-week momentum: {momentum:.4f}"
            else:
                momentum = 0.5
                est_mom = True
                mom_note = "52-week pricing missing, used 0.5"
                
            atk_score = 0.7 * growth_score + 0.3 * momentum
            atk_val = int(round(10 + atk_score * 990))
            ticker_stats["ATK"] = atk_val
            provenance["ATK"] = {
                "stat": "ATK",
                "value": atk_val,
                "axis": "종목",
                "source_metric": "0.7 * Growth_score + 0.3 * Momentum",
                "raw_value": {"growth": growth, "momentum": momentum},
                "sector": sector,
                "sector_percentile": round(atk_score, 4),
                "estimated": est_growth or est_mom,
                "note": f"Growth score: {growth_score:.4f} ({'est' if est_growth else 'raw'}), {mom_note}"
            }
            
            # DEF: Health (debtToEquity + currentRatio)
            de_raw = info.get("debtToEquity")
            de_norm = de_raw / 100.0 if de_raw is not None else None
            cr_raw = info.get("currentRatio")
            
            de_score, de_est, de_note = calculate_quality_score(ticker, 'debtToEquity', de_norm, sector, benchmarks_db)
            cr_score, cr_est, cr_note = calculate_quality_score(ticker, 'currentRatio', cr_raw, sector, benchmarks_db)
            
            if de_score is not None and cr_score is not None:
                def_score = (de_score + cr_score) / 2.0
                est_def = False
                def_note = f"Averaged: D/E score = {de_score:.4f}, CurrentRatio score = {cr_score:.4f}"
            elif de_score is not None or cr_score is not None:
                # One missing
                def_score = de_score if de_score is not None else cr_score
                est_def = True
                def_note = f"One health metric missing. Used: D/E score = {de_score}, CR score = {cr_score}"
            else:
                # Both missing
                if sector == "Financial Services":
                    # Bank fallback: use ROE quality score
                    roe = info.get("returnOnEquity")
                    roe_score, roe_est, roe_note = calculate_quality_score(ticker, 'returnOnEquity', roe, sector, benchmarks_db)
                    if roe_score is not None:
                        def_score = roe_score
                        est_def = True
                        def_note = f"Financial services fallback. D/E and CurrentRatio missing. Used ROE quality score ({roe_score:.4f}) as proxy."
                    else:
                        def_score = 0.5
                        est_def = True
                        def_note = "Financial services fallback. D/E, CurrentRatio, and ROE missing. Used 0.5."
                else:
                    def_score = 0.5
                    est_def = True
                    def_note = "All health metrics missing. Used 0.5."
                    
            def_val = int(round(10 + def_score * 990))
            ticker_stats["DEF"] = def_val
            provenance["DEF"] = {
                "stat": "DEF",
                "value": def_val,
                "axis": "종목",
                "source_metric": "debtToEquity & currentRatio",
                "raw_value": {"debtToEquity_raw": de_raw, "currentRatio_raw": cr_raw},
                "sector": sector,
                "sector_percentile": round(def_score, 4),
                "estimated": est_def,
                "note": def_note
            }
            
            # SPD: beta
            beta = info.get("beta")
            if beta is not None:
                beta_val = float(beta)
                est_spd = False
                spd_note = f"Beta: {beta_val:.4f}"
            else:
                beta_val = 1.0
                est_spd = True
                spd_note = "Beta missing, used 1.0 (300)"
                
            spd_val = scale_beta_to_spd(beta_val)
            ticker_stats["SPD"] = spd_val
            provenance["SPD"] = {
                "stat": "SPD",
                "value": spd_val,
                "axis": "종목",
                "source_metric": "beta",
                "raw_value": beta,
                "sector": sector,
                "sector_percentile": 0.5,
                "estimated": est_spd,
                "note": spd_note
            }
            
            # CRIT: 52-week spread
            if high is not None and low is not None and low > 0:
                spread = (high - low) / low
                spread_score = math.log(1 + spread) / math.log(3.0)
                crit_val = int(round(10 + min(1.0, spread_score) * 990))
                crit_note = f"Spread: {spread:.4f}"
                est_crit = False
            else:
                crit_val = 300
                crit_note = "52-week pricing missing, used 300"
                est_crit = True
                
            ticker_stats["CRIT"] = crit_val
            provenance["CRIT"] = {
                "stat": "CRIT",
                "value": crit_val,
                "axis": "종목",
                "source_metric": "spread",
                "raw_value": spread if (high is not None and low is not None and low > 0) else None,
                "sector": sector,
                "sector_percentile": 0.5,
                "estimated": est_crit,
                "note": crit_note
            }
            
            # REGEN: dividendYield
            dy = get_clean_dividend_yield(info)
            dy_score = min(1.0, dy / 0.08)
            regen_val = int(round(10 + dy_score * 990))
            ticker_stats["REGEN"] = regen_val
            provenance["REGEN"] = {
                "stat": "REGEN",
                "value": regen_val,
                "axis": "종목",
                "source_metric": "dividendYield",
                "raw_value": dy,
                "sector": sector,
                "sector_percentile": round(dy_score, 4),
                "estimated": False,
                "note": f"Dividend yield: {dy:.2%}" if dy > 0 else "No dividend yield"
            }
            
            # 숙련: Average of profitability metrics (ROE, operatingMargins, profitMargins)
            roe_raw = info.get("returnOnEquity")
            op_raw = info.get("operatingMargins")
            pr_raw = info.get("profitMargins")
            
            roe_score, _, _ = calculate_quality_score(ticker, 'returnOnEquity', roe_raw, sector, benchmarks_db)
            op_score, _, _ = calculate_quality_score(ticker, 'operatingMargins', op_raw, sector, benchmarks_db)
            pr_score, _, _ = calculate_quality_score(ticker, 'profitMargins', pr_raw, sector, benchmarks_db)
            
            valid_scores = [s for s in [roe_score, op_score, pr_score] if s is not None]
            if valid_scores:
                sk_score = sum(valid_scores) / len(valid_scores)
                est_sk = len(valid_scores) < 3
                sk_note = f"Averaged: ROE_score={roe_score}, OpMargin_score={op_score}, ProfitMargin_score={pr_score}"
            else:
                sk_score = 0.5
                est_sk = True
                sk_note = "All profitability metrics missing, used 0.5"
                
            sk_val = int(round(10 + sk_score * 990))
            ticker_stats["숙련"] = sk_val
            provenance["숙련"] = {
                "stat": "숙련",
                "value": sk_val,
                "axis": "종목",
                "source_metric": "returnOnEquity, operatingMargins, profitMargins",
                "raw_value": {"returnOnEquity": roe_raw, "operatingMargins": op_raw, "profitMargins": pr_raw},
                "sector": sector,
                "sector_percentile": round(sk_score, 4),
                "estimated": est_sk,
                "note": sk_note
            }
        # Apply Level Multiplier to stock-axis stats (Step 2c)
        for stat_name in ["HP", "ATK", "DEF", "SPD", "CRIT", "REGEN", "숙련"]:
            prov = provenance[stat_name]
            base_val = prov["value"]
            if stat_name == "HP":
                prov["base_value"] = base_val
                prov["lvl_mult"] = 1.0
                prov["final_value"] = base_val
                prov["value"] = base_val
            else:
                final_val = max(10, min(1000, int(round(base_val * lvl_mult))))
                prov["base_value"] = base_val
                prov["lvl_mult"] = round(lvl_mult, 4)
                prov["final_value"] = final_val
                prov["value"] = final_val
                ticker_stats[stat_name] = final_val
            
        results[ticker] = {
            "asset_type": asset_type,
            "shares": shares,
            "value": price * shares,
            "stats": ticker_stats,
            "provenance": provenance
        }
        
    return results

def calculate_portfolio_hp(positions: list) -> dict:
    """Calculate portfolio-relative HP for each position based on position values."""
    values = {p["ticker"]: p["price"] * p["shares"] for p in positions}
    
    if len(positions) == 1:
        ticker = positions[0]["ticker"]
        return {ticker: (300, 0.5, "Single position fallback")}
        
    min_val = min(values.values())
    max_val = max(values.values())
    
    results = {}
    for ticker, val in values.items():
        val_clamped = max(0.01, val)
        min_clamped = max(0.01, min_val)
        max_clamped = max(0.01, max_val)
        
        if min_clamped == max_clamped:
            hp_norm = 0.5
            hp_note = "All positions have identical values"
        else:
            hp_norm = (math.log(val_clamped) - math.log(min_clamped)) / (math.log(max_clamped) - math.log(min_clamped))
            hp_note = f"Log-scaled value: {math.log(val_clamped):.4f} (Portfolio Range: {math.log(min_clamped):.4f} - {math.log(max_clamped):.4f})"
            
        hp = int(round(10 + hp_norm * 990))
        results[ticker] = (hp, hp_norm, hp_note)
        
    return results

def calculate_portfolio_lvl_mults(positions: list) -> dict:
    """Calculate portfolio-relative level multipliers based on position values."""
    values = {p["ticker"]: p["price"] * p["shares"] for p in positions}
    
    if len(positions) == 1:
        ticker = positions[0]["ticker"]
        # Fallback for single position (or min == max): lvl_norm = 0.5
        # LVL_MULT = 0.4 + lvl_norm * 0.6 = 0.4 + 0.5 * 0.6 = 0.70
        return {ticker: (0.70, 0.5, "Single position fallback (LVL_MULT=0.7000)")}
        
    min_val = min(values.values())
    max_val = max(values.values())
    
    results = {}
    for ticker, val in values.items():
        val_clamped = max(0.01, val)
        min_clamped = max(0.01, min_val)
        max_clamped = max(0.01, max_val)
        
        if min_clamped == max_clamped:
            lvl_norm = 0.5
            note = "All positions have identical values (LVL_MULT=0.7000)"
        else:
            lvl_norm = (math.log(val_clamped) - math.log(min_clamped)) / (math.log(max_clamped) - math.log(min_clamped))
            note = f"Log-scaled level norm: {lvl_norm:.4f} (Portfolio Range: {math.log(min_clamped):.4f} - {math.log(max_clamped):.4f})"
            
        lvl_mult = 0.4 + lvl_norm * 0.6
        results[ticker] = (lvl_mult, lvl_norm, note)
        
    return results

def scale_beta_to_spd(beta: float) -> int:
    """Scale absolute beta to SPD [10, 1000]."""
    if beta <= 0:
        return 10
    elif beta <= 1.0:
        return int(round(10 + beta * 290))
    elif beta <= 2.5:
        return int(round(300 + (beta - 1.0) / 1.5 * 700))
    else:
        return 1000

def print_synthesis_report(results: dict):
    """Print synthesized stats and provenance for portfolio."""
    print("\n### 1. 샘플 포트폴리오 스탯 및 Provenance 내역")
    for ticker, data in results.items():
        print(f"\n#### Ticker: {ticker} (Type: {data['asset_type']}, Shares: {data['shares']}, Value: ${data['value']:,.2f})")
        print("\n**[ 7스탯 요약 ]**")
        stats = data["stats"]
        print(f"| HP | ATK | DEF | SPD | CRIT | REGEN | 숙련 |")
        print(f"| --- | --- | --- | --- | --- | --- | --- |")
        print(f"| {stats['HP']} | {stats['ATK']} | {stats['DEF']} | {stats['SPD']} | {stats['CRIT']} | {stats['REGEN']} | {stats['숙련']} |")
        
        print("\n**[ Provenance Trail (원천 데이터 검증) ]**")
        print("| Stat | Value | Base Value | Lvl Mult | Axis | Source Metric | Raw Value | Sector Percentile | Estimated | Note |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for stat_name in ["HP", "ATK", "DEF", "SPD", "CRIT", "REGEN", "숙련"]:
            prov = data["provenance"][stat_name]
            raw_val_str = str(prov["raw_value"])
            if len(raw_val_str) > 30:
                raw_val_str = raw_val_str[:27] + "..."
            pct_str = f"{prov['sector_percentile']:.4f}" if isinstance(prov['sector_percentile'], float) else str(prov['sector_percentile'])
            print(f"| {stat_name} | {prov['value']} | {prov.get('base_value', prov['value'])} | {prov.get('lvl_mult', 1.0):.4f} | {prov['axis']} | {prov['source_metric']} | {raw_val_str} | {pct_str} | {prov['estimated']} | {prov['note']} |")

def main():
    benchmarks_db = load_benchmarks()
    
    # Portfolio basket: AAPL (10), JPM (5), RIVN (8), SPY (3), SEG (20)
    # Note: SEG is a small cap stock (Seaport Entertainment Group Inc) from the Pershing Square 13F.
    portfolio = [
        ("AAPL", 10),
        ("JPM", 5),
        ("RIVN", 8),
        ("SPY", 3),
        ("SEG", 20)
    ]
    
    results = synthesize_portfolio_stats(portfolio, benchmarks_db)
    print_synthesis_report(results)
    
    # Determinism test
    print("\n### 2. 결정론 검증 (Determinism Test)")
    results_run2 = synthesize_portfolio_stats(portfolio, benchmarks_db)
    det_match = True
    for ticker in results:
        for stat in ["HP", "ATK", "DEF", "SPD", "CRIT", "REGEN", "숙련"]:
            if results[ticker]["stats"][stat] != results_run2[ticker]["stats"][stat]:
                det_match = False
                break
    print(f"Result: {'SUCCESS (100% 동일)' if det_match else 'FAIL (불일치 발생)'}")
    
    # 1. 밸런스 검증 (고가주 1주 vs 저가주 N주)
    print("\n### 3. 밸런스 검증 (AAPL 1주 vs SEG 12주)")
    # We include anchors so that the range is not extremely narrow, proving that their LVL_MULTs are close.
    portfolio_bal = [
        ("AAPL", 1),   # Value ~ $301
        ("SEG", 12),   # Value ~ $285
        ("JPM", 10),   # Max anchor ~ $3,111
        ("RIVN", 2)    # Min anchor ~ $33
    ]
    res_bal = synthesize_portfolio_stats(portfolio_bal, benchmarks_db)
    aapl_mult = res_bal["AAPL"]["provenance"]["ATK"]["lvl_mult"]
    seg_mult = res_bal["SEG"]["provenance"]["ATK"]["lvl_mult"]
    print(f"AAPL (1 shares)  Value: ${res_bal['AAPL']['value']:.2f} -> LVL_MULT: {aapl_mult:.4f}")
    print(f"SEG (12 shares) Value: ${res_bal['SEG']['value']:.2f} -> LVL_MULT: {seg_mult:.4f}")
    print(f"Difference: {abs(aapl_mult - seg_mult):.4f}")
    
    # 2. 합산 검증
    print("\n### 4. 합산 검증 ([(AAPL, 1), (AAPL, 2), (JPM, 5)])")
    portfolio_dup = [
        ("AAPL", 1),
        ("AAPL", 2),
        ("JPM", 5)
    ]
    res_dup = synthesize_portfolio_stats(portfolio_dup, benchmarks_db)
    print(f"Aggregated Keys: {list(res_dup.keys())}")
    for ticker in res_dup:
        print(f"Ticker: {ticker} -> Aggregated Shares: {res_dup[ticker]['shares']}")
        
    # 3. 동일종목 다른수량 검증 (AAPL 1주 vs 100주)
    print("\n### 5. 동일종목 다른수량 검증 (AAPL 1주 vs 100주)")
    # We set JPM to 20 shares so AAPL(1) is not the maximum, and AAPL(100) becomes the maximum.
    portfolio_base = [
        ("AAPL", 1),
        ("JPM", 20),
        ("RIVN", 8),
        ("SPY", 3),
        ("SEG", 20)
    ]
    portfolio_alt = [
        ("AAPL", 100),
        ("JPM", 20),
        ("RIVN", 8),
        ("SPY", 3),
        ("SEG", 20)
    ]
    res_base = synthesize_portfolio_stats(portfolio_base, benchmarks_db)
    res_alt = synthesize_portfolio_stats(portfolio_alt, benchmarks_db)
    
    print("\n**AAPL 1주 vs 100주 스탯 비교**")
    print("| Shares | HP | ATK | DEF | SPD | CRIT | REGEN | 숙련 | LVL_MULT |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    s1 = res_base["AAPL"]["stats"]
    s100 = res_alt["AAPL"]["stats"]
    m1 = res_base["AAPL"]["provenance"]["ATK"]["lvl_mult"]
    m100 = res_alt["AAPL"]["provenance"]["ATK"]["lvl_mult"]
    print(f"| 1 | {s1['HP']} | {s1['ATK']} | {s1['DEF']} | {s1['SPD']} | {s1['CRIT']} | {s1['REGEN']} | {s1['숙련']} | {m1:.4f} |")
    print(f"| 100 | {s100['HP']} | {s100['ATK']} | {s100['DEF']} | {s100['SPD']} | {s100['CRIT']} | {s100['REGEN']} | {s100['숙련']} | {m100:.4f} |")
    
    # List all estimated stats and their reasons
    print("\n### 6. Estimated = True 스탯 목록 및 사유")
    print("| Ticker | Stat | Value | Fallback Reason |")
    print("| --- | --- | --- | --- |")
    for ticker, data in results.items():
        for stat, prov in data["provenance"].items():
            if prov["estimated"]:
                print(f"| {ticker} | {stat} | {prov['value']} | {prov['note']} |")

if __name__ == "__main__":
    main()
