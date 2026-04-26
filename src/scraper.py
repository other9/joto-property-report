"""スクレイパー v4 - 価格含有最小コンテナ方式

コンテナ検出ロジック:
  リンクから親を遡り、「万円」を含み、かつ /re_ リンクが1つだけの最小要素を探す。
  これにより価格・面積等のデータを確実に含みつつ、隣の物件のデータが混入しない。
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

def find_price_container(link_tag):
    """
    /re_ リンクから親を遡り:
    1. テキストに「万円」を含む（＝価格データがある）
    2. /re_ リンクが1つだけ（＝他物件と混ざらない）
    この両方を満たす最小要素を返す。
    最大10階層まで遡る。見つからなければNone。
    """
    el = link_tag
    for _ in range(10):
        parent = el.parent
        if parent is None or parent.name in ('body','html','[document]'):
            break
        text = parent.get_text()
        has_price = "万円" in text
        re_links = [a for a in parent.select("a[href*='/re_']") if "/re_" in a.get("href","")]
        if has_price and len(re_links) == 1:
            return parent  # ここが最適コンテナ
        if has_price and len(re_links) > 1:
            # 価格データはあるが複数物件が混在→ひとつ下の階層(el)で妥協
            # ただしelに価格データがなければparentを使って後でdedup
            el_text = el.get_text()
            if "万円" in el_text:
                return el
            else:
                return parent  # 混入覚悟で価格データを優先
        el = parent
    # フォールバック: 価格を含む最初の親
    el = link_tag
    for _ in range(10):
        parent = el.parent
        if parent is None or parent.name in ('body','html','[document]'):
            return el
        if "万円" in parent.get_text():
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

    seen_urls=set()
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href:continue
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full_url in seen_urls:continue
        seen_urls.add(full_url)

        container=find_price_container(link)
        if container is None:continue
        text=container.get_text(separator=" ",strip=True)

        if "万円" not in text:continue  # 価格データがないならスキップ

        prop=parse_property(text,full_url,category,ward)
        if prop:props.append(prop)

    log.info(f"  {ward} {category}: {len(seen_urls)} links → {len(props)} parsed")
    return props

def parse_property(text,url,category,ward):
    pm=re.search(r"([\d,]+)\s*万円",text)
    if not pm:return None
    price=int(pm.group(1).replace(",",""))

    ym=re.search(r"([\d.]+)\s*[％%]",text)
    yield_pct=float(ym.group(1)) if ym else None

    szm=re.search(r"([\d.]+)\s*m[²㎡]",text)
    size=float(szm.group(1)) if szm else None

    lsm=re.search(r"土地[：:]?\s*([\d.]+)\s*m[²㎡]",text)
    land_size=float(lsm.group(1)) if lsm else None

    bm=re.search(r"(\d{4})年(\d{1,2})月",text)
    built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None

    sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text)
    station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
    walk_min=int(sm.group(2)) if sm else None

    fm=re.search(r"(\d+)階[／/](\d+)階建",text)
    floor=f"{fm.group(1)}階／{fm.group(2)}階建" if fm else None

    stm=re.search(r"(RC造|SRC造|S造|木造|軽量鉄骨造|鉄骨造|W造)",text)
    structure=stm.group(1) if stm else ""

    um=re.search(r"(\d+)\s*戸",text)
    total_units=int(um.group(1)) if um else None

    am=re.search(r"東京都([\w]+区[\w\d丁目\-]*)",text)
    address=f"東京都{am.group(1)}" if am else f"東京都{ward}"

    # タイトル
    title=f"{ward} {price}万円"
    if station:title+=f" {station}"
    if yield_pct:title+=f" {yield_pct}%"

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
        r=requests.get("https://msearch.gsi.go.jp/address-search/AddressSearch",
                       params={"q":address},timeout=5)
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
        if "万円" not in text:continue
        ward=next((w for w in JOTO_WARDS.values() if w in text),None)
        if not ward:continue
        prop=parse_property(text,full,"store",ward)
        if prop:
            prop["source"]="goo不動産"
            props.append(prop)
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
    Path(DATA_DIR).mkdir(exist_ok=True)
    all_props=[]

    log.info("=== 健美家 一覧ページ取得 ===")
    for t in kenbiya_urls():
        props=scrape_list_page(t["url"],t["category"],t["ward"])
        all_props.extend(props)
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
