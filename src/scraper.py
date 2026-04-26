"""スクレイパー v6 - 億単位価格対応

修正: 「1億3,000万円」を正しく13,000万円としてパース。
旧バグ: 正規表現が「3,000万円」だけ拾い、1億の部分を無視していた。
"""
import json,os,re,time,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from config import JOTO_WARDS,PROPERTY_CATEGORIES,BUDGET,kenbiya_urls,suumo_rent_urls,DATA_DIR,RENT_DATA_BY_CATEGORY,MIN_SIZE_SQM

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)
JST=timezone(timedelta(hours=9));TODAY=datetime.now(JST).strftime("%Y-%m-%d")
HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept":"text/html,application/xhtml+xml",
    "Accept-Language":"ja,en;q=0.9",
}
DELAY=1.0

def url_matches_category(url, expected_category):
    expected_path = PROPERTY_CATEGORIES[expected_category]["kenbiya_path"]
    return f"/{expected_path}/" in url

# ═══════════════════════════════════════
# ★ 価格パーサー（億対応）
# ═══════════════════════════════════════
def parse_price(text):
    """
    テキストから価格（万円単位）を抽出。
    対応パターン:
      "1億3,000万円" → 13000
      "2億万円"      → 20000
      "2億円"        → 20000
      "3,000万円"    → 3000
      "980万円"      → 980
    返値: int（万円）または None
    """
    # パターン1: X億Y万円
    m = re.search(r"(\d+)\s*億\s*([\d,]+)\s*万円", text)
    if m:
        oku = int(m.group(1))
        man = int(m.group(2).replace(",", ""))
        return oku * 10000 + man

    # パターン2: X億万円 or X億円（万の部分がない）
    m = re.search(r"(\d+)\s*億\s*(?:万\s*)?円", text)
    if m:
        return int(m.group(1)) * 10000

    # パターン3: Y万円（億なし）
    m = re.search(r"([\d,]+)\s*万円", text)
    if m:
        return int(m.group(1).replace(",", ""))

    return None

def find_price_container(link_tag):
    el = link_tag
    for _ in range(10):
        parent = el.parent
        if parent is None or parent.name in ('body','html','[document]'):
            break
        text = parent.get_text()
        has_price = "万円" in text or "億" in text
        re_links = [a for a in parent.select("a[href*='/re_']") if "/re_" in a.get("href","")]
        if has_price and len(re_links) == 1:
            return parent
        if has_price and len(re_links) > 1:
            el_text = el.get_text()
            return el if ("万円" in el_text or "億" in el_text) else parent
        el = parent
    el = link_tag
    for _ in range(10):
        parent = el.parent
        if parent is None or parent.name in ('body','html','[document]'):
            return el
        pt = parent.get_text()
        if "万円" in pt or "億" in pt:
            return parent
        el = parent
    return link_tag.parent

def scrape_list_page(url,category,ward):
    props=[]
    try:
        resp=requests.get(url,headers=HEADERS,timeout=15)
        if resp.status_code!=200:
            log.warning(f"  LIST {resp.status_code}: {url}");return props
        soup=BeautifulSoup(resp.text,"lxml")
    except Exception as e:
        log.error(f"  FAIL: {e}");return props
    seen_urls=set();skipped_cat=0
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href:continue
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full_url in seen_urls:continue
        seen_urls.add(full_url)
        if not url_matches_category(full_url, category):
            skipped_cat+=1;continue
        container=find_price_container(link)
        if container is None:continue
        text=container.get_text(separator=" ",strip=True)
        if "万円" not in text and "億" not in text:continue
        prop=parse_property(text,full_url,category,ward)
        if prop:
            # 面積フィルタ: 面積データがあり MIN_SIZE_SQM 未満なら除外
            if prop.get("size") and prop["size"] < MIN_SIZE_SQM:
                continue
            props.append(prop)
    if skipped_cat>0:
        log.info(f"  {ward} {category}: {skipped_cat} skipped (wrong path)")
    log.info(f"  {ward} {category}: {len(seen_urls)} links → {len(props)} parsed")
    return props

def parse_property(text,url,category,ward):
    price = parse_price(text)
    if price is None:return None

    ym=re.search(r"([\d.]+)\s*[％%]",text);yield_pct=float(ym.group(1)) if ym else None
    szm=re.search(r"([\d.]+)\s*m[²㎡]",text);size=float(szm.group(1)) if szm else None
    lsm=re.search(r"土地[：:]?\s*([\d.]+)\s*m[²㎡]",text);land_size=float(lsm.group(1)) if lsm else None
    bm=re.search(r"(\d{4})年(\d{1,2})月",text);built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None
    sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text);station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None;walk_min=int(sm.group(2)) if sm else None
    fm=re.search(r"(\d+)階[／/](\d+)階建",text);floor=f"{fm.group(1)}階／{fm.group(2)}階建" if fm else None
    stm=re.search(r"(RC造|SRC造|S造|木造|軽量鉄骨造|鉄骨造|W造)",text);structure=stm.group(1) if stm else ""
    um=re.search(r"(\d+)\s*戸",text);total_units=int(um.group(1)) if um else None
    am=re.search(r"東京都([\w]+区[\w\d丁目\-]*)",text);address=f"東京都{am.group(1)}" if am else f"東京都{ward}"

    # タイトル（億単位も正しく表示）
    if price >= 10000:
        oku = price // 10000
        man = price % 10000
        price_str = f"{oku}億{man:,}万円" if man > 0 else f"{oku}億円"
    else:
        price_str = f"{price:,}万円"
    title = f"{ward} {price_str}"
    if station: title += f" {station}"
    if yield_pct: title += f" {yield_pct}%"

    geo=geocode(address,ward)
    return {"url":url,"source":"健美家／HOMES","category":category,"ward":ward,
            "title":title,"price":price,"yield_pct":yield_pct,"size":size,
            "land_size":land_size,"built":built,"station":station,"walk_min":walk_min,
            "floor":floor,"structure":structure,"total_units":total_units,"address":address,
            "lat":geo["lat"],"lng":geo["lng"],"geo_source":geo["source"],"scraped_at":TODAY}

WARD_CENTER={"台東区":(35.7126,139.7800),"墨田区":(35.7107,139.8015),"江東区":(35.6727,139.8171),
             "荒川区":(35.7360,139.7834),"足立区":(35.7751,139.8046),"葛飾区":(35.7436,139.8471),
             "江戸川区":(35.7068,139.8680)}

def geocode(address,ward):
    try:
        r=requests.get("https://msearch.gsi.go.jp/address-search/AddressSearch",params={"q":address},timeout=5)
        if r.status_code==200:
            d=r.json()
            if d and len(d)>0:
                c=d[0].get("geometry",{}).get("coordinates",[])
                if len(c)==2:return {"lat":c[1],"lng":c[0],"source":"GSI"}
    except:pass
    c=WARD_CENTER.get(ward,(35.69,139.81))
    return {"lat":c[0],"lng":c[1],"source":"fallback"}

def scrape_goo():
    props=[]
    try:
        r=requests.get("https://house.goo.ne.jp/toushi/office/area_tokyo.html",headers=HEADERS,timeout=15)
        if r.status_code!=200:return props
        soup=BeautifulSoup(r.text,"lxml")
    except:return props
    seen=set()
    for link in soup.select("a[href*='/toushi/detail/']"):
        href=link.get("href","")
        if href in seen:continue
        seen.add(href)
        full=f"https://house.goo.ne.jp{href}" if href.startswith("/") else href
        container=find_price_container(link)
        if container is None:continue
        text=container.get_text(separator=" ",strip=True)
        if "万円" not in text and "億" not in text:continue
        ward=next((w for w in JOTO_WARDS.values() if w in text),None)
        if not ward:continue
        prop=parse_property(text,full,"store",ward)
        if prop:
            prop["source"]="goo不動産"
            if prop.get("size") and prop["size"] < MIN_SIZE_SQM:
                continue
            props.append(prop)
    log.info(f"  goo: {len(props)} props")
    return props

def dedup(props):
    seen_urls=set();s1=[]
    for p in props:
        if p["url"] not in seen_urls:seen_urls.add(p["url"]);s1.append(p)
    groups={}
    for p in s1:
        key=f"{p['category']}_{p['ward']}_{p['price']}_{p.get('size','')}_{p.get('built','')}"
        if key not in groups:groups[key]=[]
        groups[key].append(p)
    s2=[]
    for key,g in groups.items():
        first=g[0]
        if len(g)>1:first["title"]=f"{first['title']}（他{len(g)-1}件同条件あり）"
        s2.append(first)
    log.info(f"  dedup: {len(props)}→{len(s1)}→{len(s2)}")
    return s2

def get_fallback_rent(ward):
    condo=RENT_DATA_BY_CATEGORY["condo"]["data"].get(ward,{})
    store=RENT_DATA_BY_CATEGORY["store"]["data"].get(ward,{})
    house=RENT_DATA_BY_CATEGORY["house"]["data"].get(ward,{})
    merged={**condo};merged["store_tsubo"]=store.get("坪単価",1.5);merged["house_3LDK"]=house.get("3LDK",15.0)
    return merged

def scrape_rent():
    rent={}
    for item in suumo_rent_urls():
        w=item["ward"]
        try:
            r=requests.get(item["url"],headers=HEADERS,timeout=15)
            if r.status_code==200:
                text=r.text;data=get_fallback_rent(w)
                for ly,pat in {"1R":r"1R[^0-9]*([\d.]+)万","1K":r"1K[^0-9]*([\d.]+)万",
                    "1LDK":r"1LDK[^0-9]*([\d.]+)万","2LDK":r"2LDK[^0-9]*([\d.]+)万",
                    "3LDK":r"3LDK[^0-9]*([\d.]+)万"}.items():
                    m=re.search(pat,text)
                    if m:data[ly]=float(m.group(1))
                rent[w]=data
            else:rent[w]=get_fallback_rent(w)
        except:rent[w]=get_fallback_rent(w)
        time.sleep(DELAY)
    return rent

def main():
    Path(DATA_DIR).mkdir(exist_ok=True);all_props=[]
    log.info("=== 健美家 ===")
    for t in kenbiya_urls():
        all_props.extend(scrape_list_page(t["url"],t["category"],t["ward"]))
        time.sleep(DELAY)
    log.info("=== goo不動産 ===")
    all_props.extend(scrape_goo())
    log.info("=== 重複排除 ===")
    uniq=dedup(all_props)
    log.info("=== 賃料相場 ===")
    rent=scrape_rent()
    wc={w:{c:sum(1 for p in uniq if p["ward"]==w and p["category"]==c) for c in PROPERTY_CATEGORIES} for w in JOTO_WARDS.values()}
    out={"scraped_at":TODAY,"total_properties":len(uniq),"properties":uniq,"rent_data":rent,
         "ward_counts":wc,"rent_by_category":RENT_DATA_BY_CATEGORY}
    path=os.path.join(DATA_DIR,"properties.json")
    with open(path,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了: {len(uniq)}件 ===")
    for ck,c in PROPERTY_CATEGORIES.items():
        n=sum(1 for p in uniq if p["category"]==ck)
        log.info(f"  {c['label']}: {n}件")

if __name__=="__main__":
    main()
