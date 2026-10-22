import os
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer

# Ensure vader lexicon is present
try:
    nltk.data.find('sentiment/vader_lexicon.zip')
except LookupError:
    nltk.download('vader_lexicon', quiet=True)

sia = SentimentIntensityAnalyzer()

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_API_SECRET")

# Sector Bull / Bear Pairs (Long <-> Inverse Short)
LONG_SHORT_PAIRS = {
    # Tech & AI
    "SOXL": {"bear": "SOXS", "sector": "Semiconductors & AI", "proxy": "NVDA,AMD,INTC,TSM,SOXL"},
    "TQQQ": {"bear": "SQQQ", "sector": "Tech & Nasdaq 100", "proxy": "AAPL,MSFT,AMZN,GOOGL,QQQ"},
    "NVDL": {"bear": "NVDD", "sector": "Nvidia Ecosystem", "proxy": "NVDA"},
    "TSLL": {"bear": "TSLS", "sector": "Tesla & EV", "proxy": "TSLA"},
    
    # Broad Market & Crypto
    "UPRO": {"bear": "SPXU", "sector": "S&P 500 Broad Market", "proxy": "SPY,UPRO"},
    "CONL": {"bear": "CONI", "sector": "Coinbase & Crypto", "proxy": "COIN,BTC"},
    "MSTX": {"bear": "MSTZ", "sector": "MicroStrategy / Bitcoin", "proxy": "MSTR,BTC"},
    
    # Commodities, Energy & Sectorals
    "GUSH": {"bear": "DRIP", "sector": "Oil & Gas Exploration", "proxy": "XOP,CVX,XOM"},
    "UCO":  {"bear": "SCO",  "sector": "Crude Oil", "proxy": "USO,CL"},
    "LABU": {"bear": "LABD", "sector": "Biotech & Genomics", "proxy": "XBI,IBB"},
    "FAS":  {"bear": "FAZ",  "sector": "Banks & Financials", "proxy": "XLF,JPM,BAC"},
}

# Reverse lookup for bears
BEAR_TO_BULL = {v["bear"]: k for k, v in LONG_SHORT_PAIRS.items()}

CACHE_FILE = os.path.join(os.path.dirname(__file__), "news_sentiment.json")


def get_news_client():
    if not API_KEY or not API_SECRET:
        return None
    try:
        return NewsClient(api_key=API_KEY, secret_key=API_SECRET)
    except Exception:
        return None


def analyze_headline_sentiment(text: str) -> dict:
    """Analyze sentiment using NLTK VADER lexicon."""
    scores = sia.polarity_scores(text)
    compound = scores["compound"]
    if compound >= 0.15:
        label = "BULLISH"
        signal = "BUY LONG"
    elif compound <= -0.15:
        label = "BEARISH"
        signal = "BUY SHORT"
    else:
        label = "NEUTRAL"
        signal = "HOLD / NEUTRAL"
    return {
        "compound": round(compound, 3),
        "pos": round(scores["pos"], 3),
        "neg": round(scores["neg"], 3),
        "neu": round(scores["neu"], 3),
        "label": label,
        "signal": signal,
    }


def fetch_and_analyze_news(limit: int = 15) -> list:
    """
    Fetch breaking news from Alpaca News API for key tickers and sectors,
    compute sentiment polarity, and return structured feeds.
    """
    client = get_news_client()
    if not client:
        return []

    # Target symbols: Broad market + top high-beta movers
    symbols_to_query = "SPY,QQQ,NVDA,TSLA,SOXL,COIN,MSTR,AAPL,MSFT"
    try:
        req = NewsRequest(symbols=symbols_to_query, limit=limit)
        response = client.get_news(req)
        raw_news = response.data.get("news", [])
    except Exception as e:
        print(f"[NewsEngine] Error fetching news: {e}")
        return load_cached_sentiment()

    analyzed_items = []
    for item in raw_news:
        headline = item.headline or ""
        summary = item.summary or ""
        full_text = f"{headline}. {summary}".strip()
        analysis = analyze_headline_sentiment(full_text)
        
        # Check if news relates to any of our Long/Short sectors
        matched_pair = None
        for bull_sym, pair_info in LONG_SHORT_PAIRS.items():
            proxies = [p.strip().upper() for p in pair_info["proxy"].split(",")]
            if any(sym in item.symbols for sym in proxies) or bull_sym in item.symbols:
                matched_pair = {
                    "bull": bull_sym,
                    "bear": pair_info["bear"],
                    "sector": pair_info["sector"]
                }
                break

        analyzed_items.append({
            "id": getattr(item, "id", None) or str(hash(headline)),
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if item.created_at else "",
            "headline": headline,
            "summary": summary[:220] + "..." if len(summary) > 220 else summary,
            "symbols": item.symbols,
            "source": getattr(item, "source", "Alpaca Feed"),
            "url": getattr(item, "url", ""),
            "sentiment": analysis,
            "pair_recommendation": matched_pair,
        })

    # Cache results
    save_cached_sentiment(analyzed_items)
    return analyzed_items


def get_sector_sentiment_summary() -> dict:
    """Calculate aggregated sentiment score per traded pair."""
    news_items = load_cached_sentiment()
    if not news_items:
        news_items = fetch_and_analyze_news(limit=20)

    summary = {}
    for bull_sym, pair_info in LONG_SHORT_PAIRS.items():
        proxies = [p.strip().upper() for p in pair_info["proxy"].split(",")]
        relevant_scores = []
        for item in news_items:
            if any(s in item.get("symbols", []) for s in proxies) or bull_sym in item.get("symbols", []):
                relevant_scores.append(item["sentiment"]["compound"])
        
        if relevant_scores:
            avg_score = round(sum(relevant_scores) / len(relevant_scores), 3)
        else:
            avg_score = 0.0

        if avg_score >= 0.12:
            stance = "BULLISH"
            target_etf = bull_sym
        elif avg_score <= -0.12:
            stance = "BEARISH"
            target_etf = pair_info["bear"]
        else:
            stance = "NEUTRAL"
            target_etf = bull_sym

        summary[bull_sym] = {
            "sector": pair_info["sector"],
            "bull_etf": bull_sym,
            "bear_etf": pair_info["bear"],
            "sentiment_score": avg_score,
            "stance": stance,
            "target_etf": target_etf,
            "article_count": len(relevant_scores),
        }
    return summary


def save_cached_sentiment(items: list):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "news": items
            }, f, indent=2)
    except Exception as e:
        print(f"[NewsEngine] Error saving cache: {e}")


def load_cached_sentiment() -> list:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
                return data.get("news", [])
        except Exception:
            return []
    return []


if __name__ == "__main__":
    print("Testing News Sentiment Engine...")
    news = fetch_and_analyze_news(10)
    print(f"Fetched & Analyzed {len(news)} breaking news articles.")
    summary = get_sector_sentiment_summary()
    for sym, data in summary.items():
        if data["article_count"] > 0:
            print(f" -> {sym} [{data['sector']}]: {data['stance']} (Score: {data['sentiment_score']}) -> Target: {data['target_etf']}")
