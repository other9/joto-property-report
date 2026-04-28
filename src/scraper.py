"""スクレイパー - 店舗特化版（実質利回りスクリーニング対応）

追加取得: 新着/値下げフラグ、空室フラグ → 指値率推定に使用
事前計算: 推定指値率、推定実質利回り → 5%未満を除外
"""
import json,os,re,time,logging,math
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from config import (JOTO_WARDS,PROPERTY_CATEGORIES,BUDGET,kenbiya_urls,suumo_rent_urls,
                    DATA_DIR,RENT_DATA_BY_CATEGORY,MIN_SIZE_SQM,MAX_WALK_MIN,
                    TOTAL_EXPENSE_RATIO,ACQUISITION_COST_RATIO,NEGOTIATION_RATES,MIN_NET_YIELD)

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

def parse_price(text):
    m=re.search(r"(\d+)\s*億\s*([\d,]+)\s*万円",text)
    if m:return int(m.group(1))*10000+int(m.group(2).replace(",",""))
    m=re.search(r"(\d+)\s*億\s*(?:万\s*)?円",text)
    if m:return int(m.group(1))*10000
    m=re.search(r"([\d,]+)\s*万円",text)
    if m:return int(m.group(1).replace(",",""))
    return None

# ═══════════════════════════════════════
# 指値率・実質利回り 事前推定
# ═══════════════════════════════════════
def estimate_negotiation_rate(is_new, is_reduced, is_vacant, built_year):
    """物件の状態から推定指値率を算出"""
    age = 2026 - built_year if built_year else 30  # 築年数不明なら30年と仮定

    if is_new:
        return NEGOTIATION_RATES["new_listing"]
    if is_vacant and age >= 35:
        return NEGOTIATION_RATES["vacant_old"]
    if is_reduced and age >= 20:
        return NEGOTIATION_RATES["long_and_reduced"]
    if is_reduced:
        return NEGOTIATION_RATES["price_reduced"]
    if age >= 30:
        return NEGOTIATION_RATES["long_listed"]
    return NEGOTIATION_RATES["normal"]

def estimate_net_yield(price, yield_pct, nego_rate):
    """表面利回りから推定実質利回りを算出"""
    if not yield_pct or not price or price <= 0:
        return None
    annual_rent = price * (yield_pct / 100)  # 万円
    annual_expense = annual_rent * TOTAL_EXPENSE_RATIO
    net_income = annual_rent - annual_expense

    negotiated_price = price * (1 - nego_rate)
    total_cost = negotiated_price * (1 + ACQUISITION_COST_RATIO)

    if total_cost <= 0:
        return None
    return round(net_income / total_cost * 100, 2)

# ═══════════════════════════════════════
# 一覧ページ取得
# ═══════════════════════════════════════
def find_price_container(link_tag):
    el=link_tag
    for _ in range(10):
        parent=el.parent
        if parent is None or parent.name in ('body','html','[document]'):break
        text=parent.get_text()
        has_price="万円" in text or "億" in text
        re_links=[a for a in parent.select("a[href*='/re_']") if "/re_" in a.get("href","")]
        if has_price and len(re_links)==1:return parent
        if has_price and len(re_links)>1:
            et=el.get_text()
            return el if ("万円" in et or "億" in et) else parent
        el=parent
    el=link_tag
    for _ in range(10):
        parent=el.parent
        if parent is None or parent.name in ('body','html','[document]'):return el
        pt=parent.get_text()
        if "万円" in pt or "億" in pt:return parent
        el=parent
    return link_tag.parent

def scrape_list_page(url,category,ward):
    props=[]
    try:
        resp=requests.get(url,headers=HEADERS,timeout=15)
        if resp.status_code!=200:return props
        soup=BeautifulSoup(resp.text,"lxml")
    except Exception as e:
        log.error(f"  FAIL: {e}");return props
    seen_urls=set()
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href:continue
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full_url in seen_urls:continue
        seen_urls.add(full_url)
        if not url_matches_category(full_url,category):continue
        container=find_price_container(link)
        if container is None:continue
        text=container.get_text(separator=" ",strip=True)
        if "万円" not in text and "億" not in text:continue
        prop=parse_property(text,full_url,category,ward,"健美家／HOMES")
        if prop:
            if prop.get("size") and prop["size"]<MIN_SIZE_SQM:continue
            # 駅徒歩フィルタ
            if prop.get("walk_min") and prop["walk_min"]>MAX_WALK_MIN:continue
            # 実質利回りフィルタ
            if prop.get("est_net_yield") is not None and prop["est_net_yield"]<MIN_NET_YIELD:
                continue
            props.append(prop)
    log.info(f"  {ward}: {len(seen_urls)} links → {len(props)} passed (net≥{MIN_NET_YIELD}%)")
    return props

def parse_property(text,url,category,ward,source):
    price=parse_price(text)
    if price is None:return None

    ym=re.search(r"([\d.]+)\s*[％%]",text);yield_pct=float(ym.group(1)) if ym else None
    szm=re.search(r"([\d.]+)\s*m[²㎡]",text);size=float(szm.group(1)) if szm else None
    bm=re.search(r"(\d{4})年(\d{1,2})月",text);built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None
    built_year=int(bm.group(1)) if bm else None
    sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text);station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None;walk_min=int(sm.group(2)) if sm else None

    # 階数（地下対応）
    bfm=re.search(r"地下(\d+)階[／/](\d+)階建",text)
    nfm=re.search(r"(?<!地下)(\d+)階[／/](\d+)階建",text)
    if bfm:floor=f"B{bfm.group(1)}階／{bfm.group(2)}階建"
    elif nfm:floor=f"{nfm.group(1)}階／{nfm.group(2)}階建"
    else:floor=None

    is_first_floor=False
    if floor and floor.startswith("1階"):is_first_floor=True

    stm=re.search(r"(RC造|SRC造|S造|木造|軽量鉄骨造|鉄骨造|W造)",text);structure=stm.group(1) if stm else ""
    am=re.search(r"東京都([\w]+区[\w\d丁目\-]*)",text);address=f"東京都{am.group(1)}" if am else f"東京都{ward}"

    # ★ 新着/値下げ/空室フラグ
    is_new="新着" in text
    is_reduced="値下げ" in text or "価格変更" in text
    is_vacant="空室" in text or "空き" in text

    # ★ 指値率・実質利回り推定
    nego_rate=estimate_negotiation_rate(is_new,is_reduced,is_vacant,built_year)
    est_net_yield=estimate_net_yield(price,yield_pct,nego_rate)

    # タイトル
    if price>=10000:
        oku=price//10000;man=price%10000
        ps=f"{oku}億{man:,}万円" if man>0 else f"{oku}億円"
    else:ps=f"{price:,}万円"
    title=f"{ward} {ps}"
    if station:title+=f" {station}"
    if yield_pct:title+=f" {yield_pct}%"

    geo=geocode(address,ward)
    return {"url":url,"source":source,"category":category,"ward":ward,
            "title":title,"price":price,"yield_pct":yield_pct,"size":size,
            "built":built,"built_year":built_year,"station":station,"walk_min":walk_min,
            "floor":floor,"is_first_floor":is_first_floor,"structure":structure,"address":address,
            "is_new":is_new,"is_reduced":is_reduced,"is_vacant":is_vacant,
            "nego_rate":nego_rate,"nego_rate_pct":round(nego_rate*100,1),
            "est_net_yield":est_net_yield,
            "lat":geo["lat"],"lng":geo["lng"],"geo_source":geo["source"],
            "scraped_at":TODAY}

WARD_CENTER={"台東区":(35.7126,139.7800),"墨田区":(35.7107,139.8015),"江東区":(35.6727,139.8171),
             "荒川区":(35.7360,139.7834),"足立区":(35.7751,139.8046),"文京区":(35.7080,139.7521),
             "大田区":(35.5613,139.7160),"江戸川区":(35.7068,139.8680)}

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
        prop=parse_property(text,full,"store",ward,"goo不動産")
        if prop:
            if prop.get("size") and prop["size"]<MIN_SIZE_SQM:continue
            # 駅徒歩フィルタ
            if prop.get("walk_min") and prop["walk_min"]>MAX_WALK_MIN:continue
            if prop.get("est_net_yield") is not None and prop["est_net_yield"]<MIN_NET_YIELD:continue
            props.append(prop)
    log.info(f"  goo: {len(props)} passed (net≥{MIN_NET_YIELD}%)")
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
    store=RENT_DATA_BY_CATEGORY["store"]["data"].get(ward,{})
    return {"store_tsubo":store.get("坪単価",1.5)}

def scrape_rent():
    rent={}
    for item in suumo_rent_urls():
        w=item["ward"]
        rent[w]=get_fallback_rent(w)
        time.sleep(DELAY)
    return rent

def main():
    Path(DATA_DIR).mkdir(exist_ok=True);all_props=[]
    log.info("=== 健美家（店舗のみ）===")
    for t in kenbiya_urls():
        all_props.extend(scrape_list_page(t["url"],t["category"],t["ward"]))
        time.sleep(DELAY)
    log.info("=== goo不動産 ===")
    all_props.extend(scrape_goo())
    log.info("=== 重複排除 ===")
    uniq=dedup(all_props)
    log.info("=== 賃料相場 ===")
    rent=scrape_rent()
    wc={w:{"store":sum(1 for p in uniq if p["ward"]==w)} for w in JOTO_WARDS.values()}
    # 統計
    total_scraped=len(uniq)
    by_source={}
    for p in uniq:
        s=p["source"]
        by_source[s]=by_source.get(s,0)+1
    out={"scraped_at":TODAY,"total_properties":total_scraped,"properties":uniq,"rent_data":rent,
         "ward_counts":wc,"rent_by_category":RENT_DATA_BY_CATEGORY,"by_source":by_source}
    path=os.path.join(DATA_DIR,"properties.json")
    with open(path,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了: {total_scraped}件（実質利回り{MIN_NET_YIELD}%以上）===")
    for s,c in by_source.items():
        log.info(f"  {s}: {c}件")

if __name__=="__main__":
    main()
