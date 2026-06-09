import os
import sys
import json
import time
import argparse
import xml.etree.ElementTree as ET
import pandas as pd
import requests
import yfinance as yf

# Force UTF-8 encoding for standard output on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

CACHE_DIR = "cache"
SEC_USER_AGENT = "InvestingRPG/1.0 (contact@investingrpg.com)"

COLLECTED_FIELDS = [
    'ticker', 'longName', 'sector', 'industry', 'quoteType', 'currency',
    'currentPrice', 'regularMarketPrice', 'previousClose', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow', 'marketCap',
    'trailingPE', 'forwardPE', 'priceToBook',
    'debtToEquity', 'currentRatio',
    'returnOnEquity', 'operatingMargins', 'profitMargins', 'revenueGrowth',
    'beta', 'dividendYield'
]

def normalize_ticker(symbol: str) -> str:
    """
    Standardize ticker symbol to Yahoo Finance format.
    Converts dots/slashes to dashes (e.g. BRK.B -> BRK-B).
    """
    if not symbol:
        return ""
    normalized = symbol.strip().upper()
    normalized = normalized.replace('.', '-')
    normalized = normalized.replace('/', '-')
    return normalized

def load_cached_data(ticker: str) -> dict:
    """Load cached data from file if it exists."""
    cache_path = os.path.join(CACHE_DIR, f"{ticker}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_cached_data(ticker: str, data: dict):
    """Save ticker data to local cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{ticker}.json")
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_ticker_data(ticker: str) -> dict:
    """Fetch ticker data using yfinance."""
    print(f"Fetching raw data for {ticker} from yfinance...")
    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info
        
        # Determine if ticker was not found.
        if not info or not isinstance(info, dict) or 'symbol' not in info or (info.get('longName') is None and info.get('regularMarketPrice') is None and info.get('currentPrice') is None):
            return {"status": "NOT_FOUND"}
            
        try:
            news = yf_ticker.news
        except Exception:
            news = []
            
        return {
            "status": "OK",
            "info": info,
            "news": news
        }
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return {"status": "ERROR", "error": str(e)}

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

def validate_field(ticker: str, field: str, val, asset_type: str, info: dict) -> tuple:
    """
    Validate a single cell and return (status, reason).
    Status values: OK, N/A, 의도된 결측, FLAG
    """
    # 1. Null check and type-applicability logic
    if val is None:
        if field in ['ticker', 'longName', 'quoteType', 'currency', 'currentPrice', 'regularMarketPrice', 'previousClose', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow']:
            if field in ['currentPrice', 'regularMarketPrice']:
                other_field = 'regularMarketPrice' if field == 'currentPrice' else 'currentPrice'
                if info.get(other_field) is None:
                    return "FLAG", "Both currentPrice and regularMarketPrice are None"
                else:
                    return "N/A", "Field is None but alternative price field is populated"
            return "FLAG", f"Essential field '{field}' is None"
            
        if field in ['sector', 'industry', 'marketCap', 'forwardPE', 'priceToBook', 'returnOnEquity', 'operatingMargins', 'profitMargins', 'revenueGrowth']:
            if asset_type == 'ETF':
                return "N/A", f"Field '{field}' is not applicable for ETFs"
            else:
                return "FLAG", f"Required field '{field}' is None"
                
        if field == 'trailingPE':
            if asset_type == 'ETF':
                return "N/A", "trailingPE is N/A for ETFs"
            eps = info.get('trailingEps')
            margins = info.get('profitMargins')
            if (eps is not None and eps < 0) or (margins is not None and margins < 0):
                return "의도된 결측", "trailingPE is None because the company is unprofitable (negative earnings)"
            else:
                return "FLAG", "trailingPE is None but the company appears profitable or EPS is unavailable"
                
        if field in ['debtToEquity', 'currentRatio']:
            if asset_type == 'ETF':
                return "N/A", f"Field '{field}' is N/A for ETFs"
            elif asset_type == 'ADR':
                return "의도된 결측", f"Field '{field}' is often missing for foreign ADRs in yfinance"
            elif asset_type == 'EQUITY':
                sector = info.get('sector', '')
                if sector == 'Financial Services':
                    return "의도된 결측", f"Field '{field}' is often missing for Financial Services sector"
                return "FLAG", f"Field '{field}' is None for normal equity"
            else:
                return "FLAG", f"Field '{field}' is None"
                
        if field == 'beta':
            if asset_type == 'ETF':
                return "의도된 결측", "beta is often None for ETFs in yfinance"
            return "FLAG", "beta is None"
            
        if field == 'dividendYield':
            if asset_type == 'REIT':
                return "FLAG", "dividendYield is None but REITs are required to pay dividends"
            return "의도된 결측", "dividendYield is None (company may not pay dividends)"
            
        return "FLAG", f"Field '{field}' is None"

    # 2. Value validation rules (when field has a value)
    if field == 'trailingPE':
        try:
            val_f = float(val)
            if val_f > 1000:
                return "FLAG", f"trailingPE is extremely high ({val_f:.2f} > 1000)"
            if val_f < 0:
                return "FLAG", f"trailingPE is negative ({val_f:.2f} < 0)"
        except ValueError:
            return "FLAG", f"trailingPE is not a float: {val}"
            
    elif field == 'debtToEquity':
        try:
            val_f = float(val)
            normalized_val = val_f / 100.0  # Normalize percentage to ratio
            if normalized_val > 5:
                return "FLAG", f"debtToEquity (normalized) is too high ({normalized_val:.2f} > 5.0, raw: {val_f:.2f})"
            if normalized_val < 0:
                return "FLAG", f"debtToEquity (normalized) is negative ({normalized_val:.2f} < 0, raw: {val_f:.2f})"
        except ValueError:
            return "FLAG", f"debtToEquity is not a float: {val}"
            
    elif field == 'beta':
        try:
            val_f = float(val)
            if val_f < -1 or val_f > 3:
                return "FLAG", f"beta is outside expected range -1 to 3 ({val_f:.2f})"
        except ValueError:
            return "FLAG", f"beta is not a float: {val}"
            
    elif field in ['returnOnEquity', 'operatingMargins', 'profitMargins', 'revenueGrowth']:
        try:
            val_f = float(val)
            if abs(val_f) > 5:
                return "FLAG", f"Field '{field}' has absolute value > 5 ({val_f:.2f}). Unit error (>500%) suspected."
        except ValueError:
            return "FLAG", f"Field '{field}' is not a float: {val}"
            
    elif field in ['sector', 'industry']:
        if not str(val).strip():
            if asset_type == 'EQUITY':
                return "FLAG", f"Field '{field}' is empty for EQUITY"
            else:
                return "N/A", f"Field '{field}' is empty for {asset_type}"

    return "OK", ""

def process_tickers(ticker_list: list, refresh: bool) -> tuple:
    """Process a list of tickers, validate them, and build the validation matrix."""
    results = {}
    flagged_cells = []
    raw_samples = {}
    
    for raw_ticker in ticker_list:
        ticker = normalize_ticker(raw_ticker)
        if not ticker:
            continue
            
        data = None
        if not refresh:
            data = load_cached_data(ticker)
            if data:
                print(f"Loaded cached data for {ticker}")
                
        if not data:
            data = fetch_ticker_data(ticker)
            save_cached_data(ticker, data)
            time.sleep(0.5)
            
        if data.get("status") == "NOT_FOUND":
            results[ticker] = {"status": "NOT_FOUND"}
            flagged_cells.append({
                "ticker": ticker,
                "field": "ALL",
                "status": "NOT_FOUND",
                "reason": "Ticker not found or failed to load from Yahoo Finance"
            })
            continue
        elif data.get("status") == "ERROR":
            results[ticker] = {"status": "ERROR", "error": data.get("error")}
            flagged_cells.append({
                "ticker": ticker,
                "field": "ALL",
                "status": "ERROR",
                "reason": f"API Error: {data.get('error')}"
            })
            continue
            
        info = data.get("info", {})
        news = data.get("news", [])
        asset_type = classify_asset_type(info)
        
        raw_samples[ticker] = {
            "asset_type": asset_type,
            "raw_info": info
        }
        
        ticker_matrix = {}
        for field in COLLECTED_FIELDS:
            if field == 'ticker':
                ticker_matrix[field] = "OK"
            elif field == 'news':
                status, reason = validate_field(ticker, field, news if news else None, asset_type, info)
                ticker_matrix[field] = status
                if status == "FLAG":
                    flagged_cells.append({"ticker": ticker, "field": field, "status": status, "reason": reason})
            else:
                val = info.get(field)
                status, reason = validate_field(ticker, field, val, asset_type, info)
                ticker_matrix[field] = status
                if status == "FLAG":
                    flagged_cells.append({"ticker": ticker, "field": field, "status": status, "reason": reason})
                    
        results[ticker] = {
            "status": "OK",
            "asset_type": asset_type,
            "matrix": ticker_matrix
        }
        
    return results, flagged_cells, raw_samples

def map_cusips_openfigi(cusips: list) -> dict:
    """Map a list of CUSIPs/CINS to stock tickers using OpenFIGI v3 batch mapping, chunked to 10 jobs per request."""
    url = "https://api.openfigi.com/v3/mapping"
    headers = {"Content-Type": "application/json"}
    US_EXCHANGE_CODES = {"US", "UN", "UW", "UQ", "UR", "UT", "UA", "UB", "UF", "UC", "UD", "UZ"}
    US_MIC_CODES = {"XNYS", "XNAS", "ARCX", "BATS", "XASE", "EDGA", "EDGX", "IEXG"}
    mapping = {}
    
    # OpenFIGI v3 free tier allows a maximum of 10 jobs per batch request
    chunk_size = 10
    chunks = [cusips[i:i + chunk_size] for i in range(0, len(cusips), chunk_size)]
    
    for chunk in chunks:
        # Determine ID type dynamically: CINS (e.g. H1467J104 Swiss CINS) starts with a letter, standard CUSIP is numeric
        body = []
        for c in chunk:
            id_type = "ID_CINS" if c[0].isalpha() else "ID_CUSIP"
            body.append({"idType": id_type, "idValue": c})
            
        try:
            response = requests.post(url, headers=headers, json=body, timeout=15)
            if response.status_code != 200:
                print(f"OpenFIGI API error: {response.status_code} - {response.text}")
                for c in chunk:
                    mapping[c] = None
                continue
                
            res_data = response.json()
            for idx, item in enumerate(res_data):
                cusip = chunk[idx]
                data_list = item.get("data", [])
                
                if not data_list or "error" in item or item.get("warning") == "No identifier found":
                    mapping[cusip] = None
                    continue
                    
                # Filter matches for US exchanges or US MIC codes to prevent foreign listing leakage
                valid_matches = []
                for match in data_list:
                    exch = match.get("exchCode", "")
                    mic = match.get("micCode", "")
                    if exch in US_EXCHANGE_CODES or mic in US_MIC_CODES:
                        valid_matches.append(match)
                        
                if not valid_matches:
                    mapping[cusip] = None
                    continue
                    
                # Sort matches to choose the best US composite stock match
                def match_score(m):
                    score = 0
                    # Prefer Equity / Common Stock
                    market_sector = m.get("marketSecDes") or m.get("marketSector") or ""
                    sec_type = m.get("securityType") or m.get("securityType2") or ""
                    if market_sector == "Equity":
                        score += 10
                    if sec_type == "Common Stock":
                        score += 10
                    # Prefer compositeFIGI
                    if m.get("compositeFIGI"):
                        score += 5
                    # Prefer exact exchange code US (US composite)
                    if m.get("exchCode") == "US":
                        score += 2
                    return score
                    
                best_match = sorted(valid_matches, key=match_score, reverse=True)[0]
                mapping[cusip] = best_match.get("ticker")
                
            # Add a small delay between requests to be gentle to the API
            time.sleep(0.5)
        except Exception as e:
            print(f"Error calling OpenFIGI for chunk {chunk}: {e}")
            for c in chunk:
                mapping[c] = None
                
    return mapping

def get_latest_13f_xml_url(cik: str) -> tuple:
    """Fetch recent submissions for CIK and return the XML URL of the latest 13F-HR."""
    headers = {"User-Agent": SEC_USER_AGENT}
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    
    response = requests.get(submissions_url, headers=headers)
    if response.status_code != 200:
        return None, None
        
    data = response.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    
    latest_13f_idx = -1
    for idx, form in enumerate(forms):
        if form == "13F-HR":
            latest_13f_idx = idx
            break
            
    if latest_13f_idx == -1:
        return None, None
        
    accession_number = recent.get("accessionNumber", [])[latest_13f_idx]
    filing_date = recent.get("filingDate", [])[latest_13f_idx]
    
    acc_no_dashes = accession_number.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/index.json"
    
    index_response = requests.get(index_url, headers=headers)
    if index_response.status_code != 200:
        return None, None
        
    index_data = index_response.json()
    items = index_data.get("directory", {}).get("item", [])
    
    xml_filename = None
    for item in items:
        name = item.get("name", "")
        if name.lower().endswith(".xml") and ("infotable" in name.lower() or "table" in name.lower() or "13f" in name.lower()):
            xml_filename = name
            break
            
    if not xml_filename:
        for item in items:
            name = item.get("name", "")
            if name.lower().endswith(".xml") and not name.lower().startswith("primary_doc"):
                xml_filename = name
                break
                
    if not xml_filename:
        return None, None
        
    xml_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{xml_filename}"
    return xml_url, filing_date

def parse_13f_xml(xml_url: str) -> tuple:
    """Parse holdings XML, separate options, and aggregate stock holdings by CUSIP."""
    headers = {"User-Agent": SEC_USER_AGENT}
    response = requests.get(xml_url, headers=headers)
    if response.status_code != 200:
        return {}, 0, 0
        
    root = ET.fromstring(response.content)
    info_tables = root.findall('.//{*}infoTable')
    
    stock_holdings = {}
    option_count = 0
    total_val = 0.0
    
    for it in info_tables:
        issuer_el = it.find('.//{*}nameOfIssuer')
        cusip_el = it.find('.//{*}cusip')
        val_el = it.find('.//{*}value')
        put_call_el = it.find('.//{*}putCall')
        title_class_el = it.find('.//{*}titleOfClass')
        
        if issuer_el is not None and cusip_el is not None and val_el is not None:
            name = issuer_el.text.strip()
            cusip = cusip_el.text.strip()
            val = float(val_el.text.strip())
            
            put_call = put_call_el.text.strip().upper() if put_call_el is not None and put_call_el.text else ""
            title_class = title_class_el.text.strip().upper() if title_class_el is not None and title_class_el.text else ""
            
            is_option = False
            if put_call in ["PUT", "CALL"]:
                is_option = True
            elif "PUT" in title_class or "CALL" in title_class or "OPTION" in title_class:
                is_option = True
                
            if is_option:
                option_count += 1
                continue
                
            total_val += val
            if cusip in stock_holdings:
                stock_holdings[cusip]["value"] += val
            else:
                stock_holdings[cusip] = {
                    "name": name,
                    "cusip": cusip,
                    "value": val
                }
                
    return stock_holdings, option_count, total_val

def print_matrix_markdown(results: dict):
    """Print the validation matrix as a clean markdown table."""
    headers = ["Ticker", "Type"] + COLLECTED_FIELDS[1:]
    print("\n### 1. 종목 × 필드 결측/이상 매트릭스 표\n")
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    
    for ticker, data in results.items():
        if data.get("status") == "NOT_FOUND":
            row = [ticker, "NOT_FOUND"] + ["NOT_FOUND"] * (len(COLLECTED_FIELDS) - 1)
            print("| " + " | ".join(row) + " |")
        elif data.get("status") == "ERROR":
            row = [ticker, "ERROR"] + ["API_ERROR"] * (len(COLLECTED_FIELDS) - 1)
            print("| " + " | ".join(row) + " |")
        else:
            row = [ticker, data["asset_type"]]
            matrix = data["matrix"]
            for field in COLLECTED_FIELDS[1:]:
                row.append(matrix.get(field, "N/A"))
            print("| " + " | ".join(row) + " |")

def main():
    parser = argparse.ArgumentParser(description="investing-rpg Data Validation Harness")
    parser.add_argument("--tickers", type=str, default="AAPL,JPM,SPY,O,RIVN,TSM,BRK-B,ZZZZ",
                        help="Comma-separated list of tickers to validate")
    parser.add_argument("--refresh", action="store_true", help="Force refetching of yfinance data (ignore cache)")
    args = parser.parse_args()
    
    ticker_list = [t.strip() for t in args.tickers.split(",") if t.strip()]
    print(f"Validation Basket: {ticker_list}")
    
    # Run processing
    results, flagged, raw_samples = process_tickers(ticker_list, args.refresh)
    
    # Output Gate Deliverable 1: Matrix Table
    print_matrix_markdown(results)
    
    # Output Gate Deliverable 2: Flagged/Not Found Cells List
    print("\n### 2. FLAG·NOT_FOUND 셀 목록 + 각 원인 분류\n")
    if not flagged:
        print("정상 (이상 데이터 없음)")
    else:
        print("| Ticker | Field | Status | Reason / Classification |")
        print("| --- | --- | --- | --- |")
        for cell in flagged:
            print(f"| {cell['ticker']} | {cell['field']} | {cell['status']} | {cell['reason']} |")
            
    # Output Gate Deliverable 3: Raw Samples (1 normal equity + 1 branching case)
    print("\n### 3. 종목별 raw 값 샘플\n")
    normal_ticker = "AAPL"
    branch_ticker = "SPY"
    
    for t in [normal_ticker, branch_ticker]:
        if t in raw_samples:
            sample = raw_samples[t]
            print(f"#### Ticker: {t} (Asset Type: {sample['asset_type']})")
            subset = {}
            test_keys = ['symbol', 'longName', 'quoteType', 'industry', 'sector', 'currentPrice', 'regularMarketPrice', 'marketCap', 'trailingPE', 'debtToEquity', 'beta']
            for k in test_keys:
                if k in sample['raw_info']:
                    subset[k] = sample['raw_info'][k]
            print("```json")
            print(json.dumps(subset, indent=2, ensure_ascii=False))
            print("```")
            
    # Verification of debtToEquity normalization
    print("\n### 3.1 D/E Normalization Verification\n")
    for t in ticker_list:
        norm_t = normalize_ticker(t)
        if norm_t in raw_samples:
            info = raw_samples[norm_t]['raw_info']
            de = info.get('debtToEquity')
            if de is not None:
                norm_de = de / 100.0
                status = "OK" if 0 <= norm_de <= 5 else "FLAG"
                print(f"- **{norm_t}**: raw debtToEquity = {de:.2f}, normalized = {norm_de:.2f}x -> {status}")
                
    # Output Gate Deliverable 4: Dual SEC 13F-HR Smoke Test
    print("\n### 4. 버크셔 & 퍼싱 스퀘어 13F 상위 10개 및 CUSIP 매핑 결과\n")
    filers = [
        {"name": "Berkshire Hathaway", "cik": "0001067983"},
        {"name": "Pershing Square", "cik": "0001336528"}
    ]
    
    all_mapped_results = {}
    cusips_to_map = set()
    filer_holdings = {}
    
    for f in filers:
        xml_url, filing_date = get_latest_13f_xml_url(f["cik"])
        if not xml_url:
            print(f"Failed to locate 13F XML for {f['name']}")
            continue
            
        holdings, option_count, total_val = parse_13f_xml(xml_url)
        sorted_holdings = sorted(holdings.values(), key=lambda x: x["value"], reverse=True)[:10]
        
        filer_holdings[f["name"]] = {
            "top_10": sorted_holdings,
            "total_val": total_val,
            "option_count": option_count,
            "filing_date": filing_date
        }
        
        for h in sorted_holdings:
            cusips_to_map.add(h["cusip"])
            
    # Batch map CUSIPs
    mapping = map_cusips_openfigi(list(cusips_to_map))
    
    success_count = 0
    failure_count = 0
    total_options = 0
    
    for f_name, data in filer_holdings.items():
        print(f"#### Filer: {f_name} (Filing Date: {data['filing_date']})")
        print(f"- Option (Derivative) Rows Count: {data['option_count']}")
        print(f"- Total Stock Portfolio Value: {data['total_val']:,.2f}")
        print("\n| Rank | Issuer | CUSIP | Value | Weight | Mapped Ticker | Status |")
        print("| --- | --- | --- | --- | --- | --- | --- |")
        
        total_options += data['option_count']
        
        for rank, h in enumerate(data["top_10"]):
            cusip = h["cusip"]
            val = h["value"]
            name = h["name"]
            weight = val / data["total_val"] if data["total_val"] > 0 else 0
            
            ticker = mapping.get(cusip)
            status = "OK"
            if ticker:
                success_count += 1
            else:
                ticker = "매핑실패"
                status = "매핑실패"
                failure_count += 1
                
            print(f"| {rank+1} | {name} | {cusip} | {val:,.2f} | {weight:.2%} | {ticker} | {status} |")
        print()
        
    print(f"#### Cumulative Summary")
    print(f"- Total Successful Stock Mappings: {success_count}")
    print(f"- Total Failed Stock Mappings: {failure_count}")
    print(f"- Total Options/Derivatives Rows: {total_options}")

if __name__ == "__main__":
    main()
