#!/usr/bin/env python3
"""Search stock exchange disclosure filings (SSE + HKEX).

Usage:
    python tools/search_stock_disclosure.py --query "百利天恒"
    python tools/search_stock_disclosure.py --query "信达生物" --exchange hkex
    python tools/search_stock_disclosure.py --query "ADC" --exchange sse
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.http_client import fetch_json, fetch_text
from utils.parsers import parse_html, extract_text, safe_json_output
from utils.cache import cache_key, get as cache_get, put as cache_put


def simplify_to_traditional(text: str) -> str:
    """Simple Chinese character conversion for HKEX search.

    Converts common simplified characters to traditional for better HKEX search results.
    """
    # Common simplified -> traditional mappings for biotech company names
    mappings = {
        "万": "萬", "与": "萬", "药": "藥", "业": "業", "东": "東", "为": "為",
        "个": "個", "后": "後", "来": "來", "国": "國", "达": "達", "达": "達",
        "进": "進", "远": "遠", "迁": "遷", "过": "過", "迈": "邁", "适": "適",
        "选": "選", "连": "連", "递": "遞", "逻": "邏", "遗": "遺", "遭": "遭",
        "部": "部", "都": "都", "里": "裏", "重": "重", "针": "針", "钧": "鈞",
        "铁": "鐵", "银": "銀", "铜": "銅", "铝": "鋁", "长": "長", "门": "門",
        "问": "問", "间": "間", "阅": "閱", "阎": "閻", "阐": "闡", "队": "隊",
        "阶": "階", "除": "除", "险": "險", "陪": "陪", "陵": "陵", "陶": "陶",
        "集": "集", "难": "難", "雨": "雨", "雪": "雪", "云": "雲", "雷": "雷",
        "零": "零", "雾": "霧", "电": "電", "需": "需", "露": "露", "霜": "霜",
        "霞": "霞", "雾": "霧", "霸": "霸", "露": "露", "青": "青", "静": "靜",
        "非": "非", "面": "面", "靥": "靨", "项": "項", "顺": "順", "须": "須",
        "页": "頁", "风": "風", "飞": "飛", "食": "食", "餐": "餐", "饮": "飲",
        "饭": "飯", "饲": "飼", "饱": "飽", "饰": "飾", "首": "首", "香": "香",
        "马": "馬", "驱": "驅", "驾": "駕", "骑": "騎", "验": "驗", "腾": "騰",
        "骄": "驕", "骥": "驥", "骨": "骨", "骰": "骰", "髓": "髓", "体": "體",
        "魂": "魂", "魅": "魅", "鱼": "魚", "鲜": "鮮", "鲁": "魯", "鲍": "鮑",
        "鲨": "鯊", "鲸": "鯨", "鸟": "鳥", "鸡": "雞", "鸭": "鴨", "鹅": "鵝",
        "鸣": "鳴", "鸥": "鷗", "鹰": "鷹", "鹿": "鹿", "麦": "麥", "麻": "麻",
        "黄": "黃", "黑": "黑", "默": "默", "齐": "齊", "齿": "齒", "龈": "齦",
        "龙": "龍", "龚": "龔", "龟": "龜",
        # More common in company names
        "华": "華", "医": "醫", "药": "藥", "讯": "訊", "达": "達", "诺": "諾",
        "诚": "誠", "健": "健", "康": "康", "方": "方", "生": "生", "物": "物",
        "荣": "榮", "昌": "昌", "君": "君", "实": "實", "信": "信", "达": "達",
        "百": "百", "济": "濟", "神": "神", "州": "州", "亚": "亞", "盛": "盛",
        "歌": "歌", "礼": "禮", "衰": "衰", "科": "科", "伦": "倫", "博": "博",
        "泰": "泰", "复": "復", "宏": "宏", "汉": "漢", "霖": "霖", "石": "石",
        "基": "基", "业": "業", "迈": "邁", "博": "博", "爱": "愛", "德": "德",
        "琪": "琪", "云": "雲", "顶": "頂", "新": "新", "耀": "耀", "欧": "歐",
        "康": "康", "维": "維", "视": "視", "开": "開", "拓": "拓", "永": "永",
        "嘉": "嘉", "和": "和", "药": "藥", "明": "明", "鉅": "巨", "德": "德",
        "铂": "鉑", "加": "加", "科": "科", "思": "思", "心": "心", "通": "通",
        "贝": "貝", "康": "康", "兆": "兆", "眼": "眼", "科": "科", "归": "歸",
        "创": "創", "桥": "橋", "康": "康", "诺": "諾", "亚": "亞", "腾": "騰",
        "盛": "盛", "博": "博", "药": "藥", "先": "先", "瑞": "瑞", "达": "達",
        "堃": "堃", "博": "博", "胜": "勝", "集": "集", "团": "團", "和": "和",
        "誉": "譽", "微": "微", "泰": "泰", "医": "醫", "疗": "療", "器": "器",
        "人": "人", "圣": "聖", "诺": "諾", "医": "醫", "药": "藥", "百": "百",
        "奥": "奧", "赛": "賽", "图": "圖", "健": "健", "世": "世", "科": "科",
        "技": "技", "沣": "灃", "博": "博", "安": "安", "绿": "綠", "竹": "竹",
        "笛": "笛", "来": "來", "凯": "凱", "药": "藥", "宜": "宜", "明": "明",
        "昂": "昂", "友": "友", "芝": "芝", "友": "友", "君": "君", "圣": "聖",
        "泰": "泰", "荃": "荃", "映": "映", "恩": "恩", "觅": "覓", "瑞": "瑞",
        "恒": "恆", "银": "銀", "智": "智", "能": "能", "硅": "硅", "昀": "昀",
    }

    result = []
    for char in text:
        result.append(mappings.get(char, char))
    return "".join(result)

RATE_KEY = "stock_disclosure"

# ---------------------------------------------------------------------------
# SSE  (Shanghai Stock Exchange)
# ---------------------------------------------------------------------------

SSE_SEARCH_API = "https://query.sse.com.cn/search/getSearchResult.do"

# East Money stock search API (working alternative)
EASTMONEY_SEARCH_API = "http://searchapi.eastmoney.com/api/suggest/get"
EASTMONEY_STOCK_INFO_API = "http://push2.eastmoney.com/api/qt/stock/get"

async def _search_sse(query: str, max_results: int) -> list[dict]:
    """Search SSE via East Money APIs (SSE's own API is currently broken).

    Strategy:
    1. Use East Money stock search to find stock codes for queried companies
    2. Use East Money stock info API to get basic company data
    3. Return working URLs to East Money quote page and CNINFO search
    """
    items: list[dict] = []
    try:
        import httpx

        # Step 1: Search for stock code using East Money
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # East Money stock search API
            search_resp = await client.get(
                EASTMONEY_SEARCH_API,
                params={
                    "input": query,
                    "type": "14",  # stock type
                    "cb": "jsonpCallback",
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "http://quote.eastmoney.com/",
                },
            )

            stock_code = None
            stock_name = None

            if search_resp.status_code == 200:
                # Parse JSONP response
                text = search_resp.text
                if "jsonpCallback(" in text:
                    try:
                        # Extract JSON from JSONP
                        json_str = text[text.index("(") + 1 : text.rindex(")")].strip()
                        import json
                        data = json.loads(json_str)
                        if data.get("QuotationCodeTable", {}).get("Data"):
                            stock_info = data["QuotationCodeTable"]["Data"][0]
                            stock_code = stock_info.get("Code", "")
                            stock_name = stock_info.get("Name", "")
                    except Exception:
                        pass

            # Step 2: If we found a stock code, get more info and create result
            if stock_code and stock_name:
                # Get additional stock info
                info_resp = await client.get(
                    EASTMONEY_STOCK_INFO_API,
                    params={
                        "cb": "jsonpCallback",
                        "secid": f"1.{stock_code}",  # SSE stocks use market code 1
                        "fields": "f57,f58,f43,f44,f45,f162",
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    },
                )

                items.append({
                    "title": f"{stock_name} ({stock_code})",
                    "url": f"http://quote.eastmoney.com/sh{stock_code}.html",
                    "content": f"SSE科创板上市公司: {stock_name} (股票代码: {stock_code})",
                    "published_at": "",
                    "metadata": {
                        "exchange": "SSE",
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "source": "East Money",
                    },
                })

                # Add CNINFO disclosure link
                items.append({
                    "title": f"{stock_name} 公告查询 (巨潮资讯)",
                    "url": f"http://www.cninfo.com.cn/new/fulltextSearch/fulltextSearch.do?searchkey={quote(stock_name)}&column=sse",
                    "content": f"巨潮资讯网公告查询链接 - {stock_name}",
                    "published_at": "",
                    "metadata": {
                        "exchange": "SSE",
                        "stock_code": stock_code,
                        "source": "CNINFO",
                    },
                })

    except Exception:
        pass

    if not items:
        # Fallback to manual search URL
        search_url = f"http://www.cninfo.com.cn/new/fulltextSearch/index?searchkey={quote(query)}&type=sh"
        items.append({
            "title": f"SSE disclosure search for: {query}",
            "url": search_url,
            "content": "SSE API unavailable. Use CNINFO (巨潮资讯) for SSE stock disclosures.",
            "published_at": "",
            "metadata": {"exchange": "SSE", "fallback": True},
        })
    return items


# ---------------------------------------------------------------------------
# HKEX (Hong Kong Exchange - HKEXnews)
# ---------------------------------------------------------------------------

# Known 18A biotech stock codes (for direct lookup)
HKEX_18A_CODES = {
    "01801": "信达生物", "06160": "百济神州", "01877": "君实生物", "09995": "荣昌生物",
    "09926": "康方生物", "09969": "诺诚健华", "06855": "亚盛医药-B", "01672": "歌礼制药-B",
    "01558": "东阳光药", "02696": "复宏汉霖", "09688": "再鼎医药", "01167": "加科思-B",
    "02142": "和铂医药-B", "06996": "德琪医药-B", "01952": "云顶新耀-B", "06998": "嘉和生物-B",
    "02126": "药明巨诺-B", "09939": "开拓药业-B", "01477": "欧康维视生物-B", "06978": "永泰生物-B",
    "09996": "沛嘉医疗-B", "02160": "心通医疗-B", "02170": "贝康医疗-B", "06606": "诺辉健康",
    "06622": "兆科眼科-B", "02171": "科济药业-B", "02190": "归创通桥-B", "02162": "康诺亚-B",
    "02137": "腾盛博药-B", "06609": "心玮医疗-B", "06669": "先瑞达医疗-B", "02216": "堃博医疗-B",
    "06628": "创胜集团-B", "02256": "和誉-B", "02235": "微泰医疗-B", "02252": "微创机器人-B",
    "02257": "圣诺医药-B", "02315": "百奥赛图-B", "09877": "健世科技-B", "06922": "康沣生物-B",
    "06955": "博安生物-B", "02480": "绿竹生物-B", "02487": "科笛-B", "02105": "来凯医药-B",
    "06990": "科伦博泰生物-B", "01541": "宜明昂科-B", "02496": "友芝友生物-B",
    "02511": "君圣泰医药-B", "02509": "荃信生物-B", "01244": "3D Medicines-B",
}


async def _search_hkex(query: str, max_results: int) -> list[dict]:
    """Search HKEXnews for company announcements.

    For biotech companies, we can directly lookup by stock code.
    Note: HKEX uses Traditional Chinese encoding.
    """
    items: list[dict] = []

    # Check if query matches a known 18A stock code
    code_match = re.match(r'^(0?\d{4,5})$', query.strip())
    company_name = None
    stock_code = None
    search_query = query

    if code_match:
        code = code_match.group(1).zfill(5)  # Normalize to 5 digits
        if code in HKEX_18A_CODES:
            company_name = HKEX_18A_CODES[code]
            stock_code = code
            # Use both code and name for search
            search_query = code
    else:
        # Reverse lookup: search by company name in known 18A codes
        trad_query = simplify_to_traditional(query)
        for code, name in HKEX_18A_CODES.items():
            if query in name or trad_query in name or name in query or name in trad_query:
                company_name = name
                stock_code = code
                search_query = code
                break

    # Convert simplified Chinese to traditional for name searches
    if not code_match and not stock_code:
        search_query = simplify_to_traditional(query)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(
                "https://www1.hkexnews.hk/search/titlesearch.xhtml",
                params={
                    "lang": "ZH",
                    "market": "SEHK",
                    "searchType": "0",
                    "t1code": "40000",
                    "t2Gcode": "-2",
                    "t2code": "-2",
                    "query": search_query,
                    "from": "20200101",
                    "to": "",
                    "rowRange": f"1-{max_results}",
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                },
            )
            resp.raise_for_status()
            html = resp.text

        # Try multiple selectors for HKEX results
        soup = parse_html(html)
        selectors = [
            "table.table tr",
            "div.result-item",
            "tr.Row0",
            "tr.Row1",
            "div#resultList table tr",
            "table.result-table tr",
        ]

        for selector in selectors:
            rows = soup.select(selector)
            if rows:
                break

        for row in rows[:max_results]:
            cells = row.select("td")
            if len(cells) >= 3:
                date_str = extract_text(cells[0])
                stock_code = extract_text(cells[1])
                link = cells[-1].find("a") or cells[2].find("a")
                title = extract_text(link) if link else extract_text(cells[2])
                href = ""
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"https://www1.hkexnews.hk{href}"
                items.append({
                    "title": title,
                    "url": href,
                    "content": f"Stock: {stock_code}",
                    "published_at": date_str,
                    "metadata": {
                        "exchange": "HKEX",
                        "stock_code": stock_code,
                    },
                })
    except Exception as e:
        pass

    if not items:
        # Return known company info if available
        content_msg = "Parsing returned no results."
        if company_name:
            content_msg = f"Known 18A company: {company_name}. Use fetch_page.py on the URL above for manual inspection."

        items.append({
            "title": f"HKEX search for: {query}",
            "url": f"https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=ZH&query={quote(search_query)}",
            "content": content_msg,
            "published_at": "",
            "metadata": {"exchange": "HKEX", "fallback": True},
        })

        # Add company lookup result if we have it
        if company_name and code_match:
            items.append({
                "title": f"{company_name} (Stock Code: {code_match.group(1)})",
                "url": f"https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities/Equities-Quote?sym={code_match.group(1)}",
                "content": f"Known HKEX 18A biotech company: {company_name}",
                "published_at": "",
                "metadata": {"exchange": "HKEX", "stock_code": code_match.group(1), "company_name": company_name},
            })

    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def search(args: argparse.Namespace) -> dict:
    ck = cache_key("stock", {"q": args.query, "e": args.exchange, "n": args.max_results})
    cached = cache_get(ck)
    if cached is not None:
        return cached

    tasks = []
    ex = args.exchange.lower()
    if ex in ("sse", "both"):
        tasks.append(_search_sse(args.query, args.max_results))
    if ex in ("hkex", "both"):
        tasks.append(_search_hkex(args.query, args.max_results))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_items: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
        elif isinstance(r, Exception):
            all_items.append({
                "title": f"[Error: {r}]",
                "url": "",
                "content": str(r),
                "published_at": "",
                "metadata": {"error": True},
            })

    output = safe_json_output("StockDisclosure", args.query, all_items)
    cache_put(ck, output, ttl_seconds=3600)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Search stock exchange disclosures")
    parser.add_argument("--query", "-q", required=True, help="Company name or keyword")
    parser.add_argument(
        "--exchange",
        default="both",
        choices=["sse", "hkex", "both"],
        help="Which exchange (default: both)",
    )
    parser.add_argument("--max-results", type=int, default=10, help="Max results per exchange")
    args = parser.parse_args()

    result = asyncio.run(search(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
