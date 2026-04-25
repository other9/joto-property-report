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
    "Accept-Encoding":"gzip, deflate, br",
}
DELAY=3

def scrape_kenbiya_page(url,category,ward):
    props=[]
    try:
        resp=requests.get(url,headers=HEADERS,timeout=30)
        log.info(f"  GET {url} -> {resp.status_code} ({len(resp.text)} bytes)")
        resp.raise_for_status()
        soup=BeautifulSoup(resp.text,"lxml")
    except Exception as e:
        log.error(f"  FAILED {url}: {e}");return props
    all_links=soup.select("a[href*='/re_']")
    log.info(f"  {ward} {category}: {len(all_links)} /re_ links")
    seen=set()
    for link in all_links:
        href=link.get("href","")
        if "/re_" not in href or href in seen:continue
        seen.add(href)
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        parent=link
        for _ in range(5):
            if parent.parent:parent=parent.parent
        text=parent.get_text(separator="|",strip=True)
        prop=parse_text(text,full_url,category,ward,"健美家／HOMES")
        if prop:props.append(prop)
    log.info(f"  {ward} {category}: {len(props)} parsed")
    return props

def parse_text(text,url,category,ward,source):
    pm=re.search(r"([\d,]+)万円",text)
    if not pm:return None
    price=int(pm.group(1).replace(",",""))
    ym=re.search(r"([\d.]+)[％%]",text);yield_pct=float(ym.group(1)) if ym else None
    am=re.search(r"([\d.]+)\s*m[²㎡]",text);size=float(am.group(1)) if am else None
    bm=re.search(r"(\d{4})年(\d{1,2})月",text);built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None
    sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text);station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
    walk_min=int(sm.group(2)) if sm else None
    fm=re.search(r"(\d+)階[／/](\d+)階建",text);floor=f"{fm.group(1)}階／{fm.group(2)}階建" if fm else None
    # 住所を詳しく取得
    addr_m=re.search(r"東京都([\w]+区[\w\d丁目\-]*)",text)
    address=f"東京都{addr_m.group(1)}" if addr_m else f"東京都{ward}"
    # タイトル: 物件名っぽい部分を優先抽出
    title=""
    for p in text.split("|"):
        p=p.strip()
        if len(p)>3 and "万円" not in p and "%" not in p and "㎡" not in p and "利回" not in p and p not in ("新着","値下げ","PR"):
            title=p[:60]
            break
    if not title:
        title=f"{ward} {station or ''} {price}万円"
    return {"url":url,"source":source,"category":category,"ward":ward,"title":title,
            "price":price,"yield_pct":yield_pct,"size":size,"built":built,
            "station":station,"walk_min":walk_min,"floor":floor,"address":address,"scraped_at":TODAY}

def scrape_goo():
    props=[]
    url="https://house.goo.ne.jp/toushi/office/area_tokyo.html"
    try:
        r=requests.get(url,headers=HEADERS,timeout=30)
        log.info(f"  GET goo -> {r.status_code} ({len(r.text)} bytes)")
        r.raise_for_status();soup=BeautifulSoup(r.text,"lxml")
    except Exception as e:
        log.error(f"  goo failed: {e}");return props
    seen=set()
    for link in soup.select("a[href*='/toushi/detail/']"):
        href=link.get("href","")
        if href in seen:continue
        seen.add(href)
        full_url=f"https://house.goo.ne.jp{href}" if href.startswith("/") else href
        parent=link
        for _ in range(6):
            if parent.parent:parent=parent.parent
        text=parent.get_text(separator="|",strip=True)
        ward=next((w for w in JOTO_WARDS.values() if w in text),None)
        if not ward:continue
        prop=parse_text(text,full_url,"store",ward,"goo不動産")
        if prop:props.append(prop)
    log.info(f"  goo: {len(props)} joto props")
    return props

def dedup_properties(props):
    """
    強力な重複排除:
    1. URL重複排除
    2. 物件実体の重複排除（同一棟の別部屋も1件に集約）
       キー = 区 + 価格 + 面積 + 築年月
       同じキーの物件が複数ある場合、最初の1件のみ残し、
       残った物件のtitleに「他N件同条件あり」を付記
    """
    # URL重複排除
    seen_urls=set();stage1=[]
    for p in props:
        if p["url"] not in seen_urls:
            seen_urls.add(p["url"]);stage1.append(p)

    # 物件実体の重複排除
    groups={}
    for p in stage1:
        key=f"{p['ward']}_{p['price']}_{p.get('size','')}_{p.get('built','')}"
        if key not in groups:
            groups[key]=[]
        groups[key].append(p)

    stage2=[]
    for key,group in groups.items():
        first=group[0]
        if len(group)>1:
            first["title"]=f"{first['title']}（他{len(group)-1}件同条件あり）"
            log.info(f"  同一物件集約: {first['ward']} {first['price']}万 {first.get('size','')}㎡ → {len(group)}件を1件に")
        stage2.append(first)

    log.info(f"  重複排除: {len(props)} → {len(stage1)} (URL) → {len(stage2)} (実体)")
    return stage2

def get_fallback_rent(ward):
    """統合された賃料データを返す（後方互換）"""
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
            r=requests.get(item["url"],headers=HEADERS,timeout=30)
            if r.status_code==200:
                text=r.text;data=get_fallback_rent(w)
                for ly,pat in {"1R":r"1R[^0-9]*([\d.]+)万","1K":r"1K[^0-9]*([\d.]+)万","1LDK":r"1LDK[^0-9]*([\d.]+)万","2LDK":r"2LDK[^0-9]*([\d.]+)万","3LDK":r"3LDK[^0-9]*([\d.]+)万"}.items():
                    m=re.search(pat,text)
                    if m:data[ly]=float(m.group(1))
                rent[w]=data
            else:rent[w]=get_fallback_rent(w)
        except:rent[w]=get_fallback_rent(w)
        time.sleep(DELAY)
    return rent

def main():
    Path(DATA_DIR).mkdir(exist_ok=True);all_p=[]
    log.info("=== 健美家 ===")
    for t in kenbiya_urls():
        all_p.extend(scrape_kenbiya_page(t["url"],t["category"],t["ward"]))
        time.sleep(DELAY)
    log.info("=== goo不動産 ===")
    all_p.extend(scrape_goo())
    log.info("=== 重複排除 ===")
    uniq=dedup_properties(all_p)
    log.info("=== 賃料相場 ===")
    rent=scrape_rent()
    wc={w:{c:sum(1 for p in uniq if p["ward"]==w and p["category"]==c) for c in PROPERTY_CATEGORIES} for w in JOTO_WARDS.values()}
    out={"scraped_at":TODAY,"total_properties":len(uniq),"properties":uniq,"rent_data":rent,"ward_counts":wc,
         "rent_by_category":RENT_DATA_BY_CATEGORY}
    path=os.path.join(DATA_DIR,"properties.json")
    with open(path,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了: {len(uniq)}件 → {path} ===")
    for ck,c in PROPERTY_CATEGORIES.items():
        n=sum(1 for p in uniq if p["category"]==ck)
        log.info(f"  {c['label']}: {n}件")

if __name__=="__main__":
    main()
