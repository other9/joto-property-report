"""スクレイパー 速度最適化版
35リストページ + 最大60詳細ページ = 約3分で完了
"""
import json,os,re,time,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from config import JOTO_WARDS,PROPERTY_CATEGORIES,BUDGET,kenbiya_urls,suumo_rent_urls,DATA_DIR,RENT_DATA_BY_CATEGORY

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)
JST=timezone(timedelta(hours=9));TODAY=datetime.now(JST).strftime("%Y-%m-%d")
HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept":"text/html,application/xhtml+xml",
    "Accept-Language":"ja,en;q=0.9",
}
DELAY=1.0
MAX_DETAIL_PER_WARD=3      # 区×カテゴリあたり最大3件
MAX_ATTEMPTS_PER_WARD=6     # 3件取れなくても6URLで打ち切り
GLOBAL_MAX_DETAILS=60       # 全体で60件取ったら終了

_global_count=0

def collect_urls(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=15)
        if r.status_code!=200:return []
        soup=BeautifulSoup(r.text,"lxml")
    except:return []
    seen=set();urls=[]
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href:continue
        full=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full not in seen:seen.add(full);urls.append(full)
    return urls

def fetch_detail(url,category,ward):
    try:
        r=requests.get(url,headers=HEADERS,timeout=15)
        if r.status_code!=200:return None
        soup=BeautifulSoup(r.text,"lxml")
        t=soup.get_text(separator=" · ",strip=True)
    except:return None

    pm=re.search(r"(?:価格|販売価格)\s*·?\s*([\d,]+)万円",t)
    if not pm:return None
    price=int(pm.group(1).replace(",",""))
    ym=re.search(r"利回り\s*·?\s*([\d.]+)[％%]",t);yield_pct=float(ym.group(1)) if ym else None
    sm=re.search(r"(\S+駅)\s*徒歩(\d+)分",t);station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None;walk_min=int(sm.group(2)) if sm else None
    am=re.search(r"(?:住所|所在地)\s*·?\s*東京都([\w区][\w\d丁目\-]*)",t);address=f"東京都{am.group(1)}" if am else f"東京都{ward}"
    bm=re.search(r"築年月\s*·?\s*(\d{4})年(\d{1,2})月",t);built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None
    szm=re.search(r"(?:専有面積|建物面積|面積|延床)\s*·?\s*([\d.]+)\s*m[²㎡]",t);size=float(szm.group(1)) if szm else None
    lszm=re.search(r"(?:土地面積|敷地)\s*·?\s*([\d.]+)\s*m[²㎡]",t);land_size=float(lszm.group(1)) if lszm else None
    fm=re.search(r"(\S+造)\s*(\d+)階[/／](\d+)階建",t);floor=f"{fm.group(2)}階／{fm.group(3)}階建" if fm else None;structure=fm.group(1) if fm else ""
    um=re.search(r"総戸数\s*·?\s*(\d+)戸",t);total_units=int(um.group(1)) if um else None
    lm=re.search(r"間取り\s*·?\s*(\w+)",t);layout=lm.group(1) if lm else ""
    nm=re.search(r"物件名\s*·?\s*(\S+)",t);prop_name=nm.group(1) if nm else ""
    tt=soup.find("title");title=tt.string.split("｜")[0].strip() if tt and tt.string else f"{ward} {price}万円"
    if prop_name and prop_name not in title:title=f"{prop_name}（{title}）"
    geo=geocode(address,ward)
    return {"url":url,"source":"健美家／HOMES","category":category,"ward":ward,
            "title":title,"price":price,"yield_pct":yield_pct,"size":size,"land_size":land_size,
            "built":built,"station":station,"walk_min":walk_min,"floor":floor,"structure":structure,
            "layout":layout,"address":address,"prop_name":prop_name,"total_units":total_units,
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
        parent=link
        for _ in range(6):
            if parent.parent:parent=parent.parent
        text=parent.get_text(separator="|",strip=True)
        ward=next((w for w in JOTO_WARDS.values() if w in text),None)
        if not ward:continue
        pm=re.search(r"([\d,]+)万円",text)
        if not pm:continue
        price=int(pm.group(1).replace(",",""))
        ym=re.search(r"([\d.]+)[％%]",text);yield_pct=float(ym.group(1)) if ym else None
        szm=re.search(r"([\d.]+)\s*m[²㎡]",text);size=float(szm.group(1)) if szm else None
        bm=re.search(r"(\d{4})年(\d{1,2})月",text);built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None
        sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text);station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
        geo=geocode(f"東京都{ward}",ward)
        props.append({"url":full,"source":"goo不動産","category":"store","ward":ward,
                      "title":f"{ward} {price}万円","price":price,"yield_pct":yield_pct,
                      "size":size,"built":built,"station":station,"walk_min":int(sm.group(2)) if sm else None,
                      "floor":None,"address":f"東京都{ward}",
                      "lat":geo["lat"],"lng":geo["lng"],"geo_source":geo["source"],"scraped_at":TODAY})
    log.info(f"  goo: {len(props)} props")
    return props

def dedup(props):
    seen_urls=set();s1=[]
    for p in props:
        if p["url"] not in seen_urls:seen_urls.add(p["url"]);s1.append(p)
    groups={}
    for p in s1:
        key=f"{p['ward']}_{p['price']}_{p.get('size','')}_{p.get('built','')}"
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
    global _global_count
    Path(DATA_DIR).mkdir(exist_ok=True)
    all_props=[]
    _global_count=0

    log.info("=== スクレイピング開始 ===")
    for t in kenbiya_urls():
        if _global_count>=GLOBAL_MAX_DETAILS:
            log.info(f"  グローバル上限到達({GLOBAL_MAX_DETAILS}件)、残りスキップ")
            break
        cat=t["category"];ward=t["ward"]
        urls=collect_urls(t["url"])
        log.info(f"  {ward} {cat}: {len(urls)} URLs → max{MAX_DETAIL_PER_WARD}件取得")
        time.sleep(DELAY)

        fetched=0;attempts=0
        for u in urls:
            if fetched>=MAX_DETAIL_PER_WARD or attempts>=MAX_ATTEMPTS_PER_WARD or _global_count>=GLOBAL_MAX_DETAILS:
                break
            prop=fetch_detail(u,cat,ward)
            attempts+=1
            if prop:
                all_props.append(prop);fetched+=1;_global_count+=1
            time.sleep(DELAY)
        log.info(f"    → {fetched}件取得 (attempts:{attempts}, global:{_global_count})")

    log.info(f"=== 健美家: {_global_count}件 ===")
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
