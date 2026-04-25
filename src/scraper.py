"""スクレイパー v3 - 一覧ページ方式（高速＋正確）

詳細ページ個別取得は廃止。一覧ページから各物件の「最小コンテナ」を
特定してURLとデータを正確に紐付ける。

35ページ × 1秒 + 賃料7ページ = 約1分で完了。
"""
import json,os,re,time,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup,Tag
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

# ═══════════════════════════════════════
# コア: 一覧ページから物件を抽出
# ═══════════════════════════════════════
def find_property_container(link_tag):
    """
    /re_ リンクから親を辿り、そのリンク「だけ」を含む
    最小のコンテナを探す。これにより隣の物件のデータが混入しない。
    """
    el=link_tag
    for _ in range(6):
        parent=el.parent
        if parent is None or parent.name in ('body','html','[document]'):
            return el
        # このparentに /re_ リンクが1つだけならOK
        re_links=[a for a in parent.select("a[href*='/re_']") if "/re_" in a.get("href","")]
        if len(re_links)==1:
            el=parent
            continue
        elif len(re_links)>1:
            # 複数リンクがある→現在のelが最小コンテナ
            return el
    return el

def scrape_list_page(url,category,ward):
    """一覧ページから物件リストを抽出（最小コンテナ方式）"""
    props=[]
    try:
        resp=requests.get(url,headers=HEADERS,timeout=15)
        if resp.status_code!=200:
            log.warning(f"  LIST {resp.status_code}: {url}");return props
        soup=BeautifulSoup(resp.text,"lxml")
    except Exception as e:
        log.error(f"  FAIL: {e}");return props

    seen_urls=set()
    all_links=soup.select("a[href*='/re_']")

    for link in all_links:
        href=link.get("href","")
        if "/re_" not in href:continue
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full_url in seen_urls:continue
        seen_urls.add(full_url)

        # 最小コンテナを見つける
        container=find_property_container(link)
        text=container.get_text(separator=" ",strip=True)

        prop=parse_property(text,full_url,category,ward)
        if prop:
            props.append(prop)

    log.info(f"  {ward} {category}: {len(all_links)} links → {len(props)} parsed")
    return props

def parse_property(text,url,category,ward):
    """テキストブロックから物件情報をパース"""
    # 価格（必須）
    pm=re.search(r"([\d,]+)\s*万円",text)
    if not pm:return None
    price=int(pm.group(1).replace(",",""))

    # 利回り
    ym=re.search(r"([\d.]+)\s*[％%]",text)
    yield_pct=float(ym.group(1)) if ym else None

    # 面積
    szm=re.search(r"([\d.]+)\s*m[²㎡]",text)
    size=float(szm.group(1)) if szm else None

    # 土地面積（別途）
    lsm=re.search(r"土地[：:]?\s*([\d.]+)\s*m[²㎡]",text)
    land_size=float(lsm.group(1)) if lsm else None

    # 築年月
    bm=re.search(r"(\d{4})年(\d{1,2})月",text)
    built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None

    # 駅
    sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text)
    station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
    walk_min=int(sm.group(2)) if sm else None

    # 階数
    fm=re.search(r"(\d+)階[／/](\d+)階建",text)
    floor=f"{fm.group(1)}階／{fm.group(2)}階建" if fm else None

    # 構造
    stm=re.search(r"(RC造|SRC造|S造|木造|軽量鉄骨造|鉄骨造|W造)",text)
    structure=stm.group(1) if stm else ""

    # 総戸数
    um=re.search(r"(\d+)\s*戸",text)
    total_units=int(um.group(1)) if um else None

    # 住所
    am=re.search(r"東京都([\w]+区[\w\d丁目\-]*)",text)
    address=f"東京都{am.group(1)}" if am else f"東京都{ward}"

    # タイトル（テキストの最初の意味ある部分）
    title=""
    for part in text.split():
        if len(part)>3 and not re.match(r"^[\d,]+万",part) and "%" not in part and "㎡" not in part:
            title=part[:50]
            break
    if not title:
        title=f"{ward} {price}万円"
    if station:
        title=f"{title} {station}"

    # ジオコーディング
    geo=geocode(address,ward)

    return {"url":url,"source":"健美家／HOMES","category":category,"ward":ward,
            "title":title,"price":price,"yield_pct":yield_pct,"size":size,
            "land_size":land_size,"built":built,"station":station,"walk_min":walk_min,
            "floor":floor,"structure":structure,"total_units":total_units,
            "address":address,
            "lat":geo["lat"],"lng":geo["lng"],"geo_source":geo["source"],
            "scraped_at":TODAY}

# ═══════════════════════════════════════
# ジオコーディング
# ═══════════════════════════════════════
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

# ═══════════════════════════════════════
# goo不動産
# ═══════════════════════════════════════
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
        container=find_property_container(link)
        text=container.get_text(separator=" ",strip=True)
        ward=next((w for w in JOTO_WARDS.values() if w in text),None)
        if not ward:continue
        prop=parse_property(text,full,"store",ward)
        if prop:
            prop["source"]="goo不動産"
            props.append(prop)
    log.info(f"  goo: {len(props)} props")
    return props

# ═══════════════════════════════════════
# 重複排除
# ═══════════════════════════════════════
def dedup(props):
    # URL重複排除
    seen_urls=set();s1=[]
    for p in props:
        if p["url"] not in seen_urls:seen_urls.add(p["url"]);s1.append(p)
    # 実体重複排除（価格+面積+築年月+区）
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

# ═══════════════════════════════════════
# 賃料相場
# ═══════════════════════════════════════
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

# ═══════════════════════════════════════
# メイン
# ═══════════════════════════════════════
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
