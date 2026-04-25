"""健美家スクレイパー - 詳細ページ個別取得（最適化版）

取得量を制限してGitHub Actionsの30分タイムアウト内に収める。
- 一覧ページ: 21ページ（7区×3カテゴリ）
- 詳細ページ: 最大5件/区×カテゴリ = 最大105件
- ディレイ: 1.5秒
- 概算: 21×1.5 + 105×1.5 + 賃料7×1.5 ≒ 3.3分 + API3分 ≒ 7分
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
    "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":"ja,en;q=0.9",
}
DELAY=1.5
MAX_DETAIL_PER_WARD=5  # 区×カテゴリあたり最大5件

def collect_urls_from_list(url):
    """一覧ページからURLだけ収集"""
    urls=[]
    try:
        resp=requests.get(url,headers=HEADERS,timeout=20)
        if resp.status_code!=200:
            log.warning(f"  LIST {resp.status_code}: {url}");return urls
        soup=BeautifulSoup(resp.text,"lxml")
    except Exception as e:
        log.error(f"  LIST FAIL: {e}");return urls
    seen=set()
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href:continue
        full=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full not in seen:seen.add(full);urls.append(full)
    return urls

def fetch_detail(url,category,ward):
    """詳細ページから物件情報を取得"""
    try:
        resp=requests.get(url,headers=HEADERS,timeout=20)
        if resp.status_code!=200:return None
        soup=BeautifulSoup(resp.text,"lxml")
        t=soup.get_text(separator=" · ",strip=True)
    except:return None

    pm=re.search(r"価格\s*·?\s*([\d,]+)万円",t)
    if not pm:return None
    price=int(pm.group(1).replace(",",""))

    ym=re.search(r"利回り\s*·?\s*([\d.]+)[％%]",t)
    yield_pct=float(ym.group(1)) if ym else None

    sm=re.search(r"(\S+駅)\s*徒歩(\d+)分",t)
    station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
    walk_min=int(sm.group(2)) if sm else None

    am=re.search(r"住所\s*·?\s*東京都([\w区][\w\d丁目\-]*)",t)
    address=f"東京都{am.group(1)}" if am else f"東京都{ward}"

    bm=re.search(r"築年月\s*·?\s*(\d{4})年(\d{1,2})月",t)
    built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None

    szm=re.search(r"(?:専有面積|面積)\s*·?\s*([\d.]+)\s*m[²㎡]",t)
    size=float(szm.group(1)) if szm else None

    fm=re.search(r"(\S+造)(\d+)階[/／](\d+)階建",t)
    floor=f"{fm.group(2)}階／{fm.group(3)}階建" if fm else None
    structure=fm.group(1) if fm else ""

    lm=re.search(r"間取り\s*·?\s*(\w+)",t)
    layout=lm.group(1) if lm else ""

    nm=re.search(r"物件名\s*·?\s*(\S+)",t)
    prop_name=nm.group(1) if nm else ""

    # タイトル: ページtitleから
    tt=soup.find("title")
    title=tt.string.split("｜")[0].strip() if tt and tt.string else f"{ward} {price}万円"
    if prop_name and prop_name not in title:
        title=f"{prop_name}（{title}）"

    return {"url":url,"source":"健美家／HOMES","category":category,"ward":ward,
            "title":title,"price":price,"yield_pct":yield_pct,"size":size,
            "built":built,"station":station,"walk_min":walk_min,"floor":floor,
            "structure":structure,"layout":layout,"address":address,
            "prop_name":prop_name,"scraped_at":TODAY}

def scrape_goo():
    props=[]
    try:
        r=requests.get("https://house.goo.ne.jp/toushi/office/area_tokyo.html",headers=HEADERS,timeout=20)
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
        sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text)
        station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
        props.append({"url":full,"source":"goo不動産","category":"store","ward":ward,
                      "title":f"{ward} {price}万円","price":price,"yield_pct":yield_pct,
                      "size":size,"built":built,"station":station,
                      "walk_min":int(sm.group(2)) if sm else None,
                      "floor":None,"address":f"東京都{ward}","scraped_at":TODAY})
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
        if len(g)>1:
            first["title"]=f"{first['title']}（他{len(g)-1}件同条件あり）"
            log.info(f"  集約: {first['ward']} {first['price']}万 x{len(g)}")
        s2.append(first)
    log.info(f"  dedup: {len(props)}→{len(s1)}→{len(s2)}")
    return s2

def get_fallback_rent(ward):
    condo=RENT_DATA_BY_CATEGORY["condo"]["data"].get(ward,{})
    store=RENT_DATA_BY_CATEGORY["store"]["data"].get(ward,{})
    house=RENT_DATA_BY_CATEGORY["house"]["data"].get(ward,{})
    merged={**condo}
    merged["store_tsubo"]=store.get("坪単価",1.5)
    merged["house_3LDK"]=house.get("3LDK",15.0)
    return merged

def scrape_rent():
    rent={}
    for item in suumo_rent_urls():
        w=item["ward"]
        try:
            r=requests.get(item["url"],headers=HEADERS,timeout=20)
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
    total_detail_fetched=0

    log.info("=== 一覧→詳細 取得開始 ===")
    for t in kenbiya_urls():
        cat=t["category"];ward=t["ward"]
        urls=collect_urls_from_list(t["url"])
        log.info(f"  {ward} {cat}: {len(urls)} URLs")
        time.sleep(DELAY)

        # 区×カテゴリあたり最大5件の詳細を取得
        fetched=0
        for u in urls:
            if fetched>=MAX_DETAIL_PER_WARD:break
            prop=fetch_detail(u,cat,ward)
            if prop:
                all_props.append(prop)
                fetched+=1
                total_detail_fetched+=1
            time.sleep(DELAY)

    log.info(f"  健美家合計: {total_detail_fetched}件の詳細取得完了")

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

    log.info(f"=== 完了: {len(uniq)}件 → {path} ===")
    for ck,c in PROPERTY_CATEGORIES.items():
        n=sum(1 for p in uniq if p["category"]==ck)
        log.info(f"  {c['label']}: {n}件")

if __name__=="__main__":
    main()
