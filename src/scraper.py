import json,os,re,time,logging,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from config import JOTO_WARDS,PROPERTY_CATEGORIES,BUDGET,kenbiya_urls,suumo_rent_urls,DATA_DIR

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
        log.error(f"  FAILED {url}: {e}")
        return props

    # デバッグ: /re_ リンクの数を表示
    all_links=soup.select("a[href*='/re_']")
    log.info(f"  {ward} {category}: Found {len(all_links)} /re_ links in HTML")

    if len(all_links)==0:
        # HTMLの一部を表示して構造を確認
        text_preview=soup.get_text()[:500].replace("\n"," ")
        log.warning(f"  HTML preview: {text_preview}")

    seen=set()
    for link in all_links:
        href=link.get("href","")
        if href in seen:continue
        seen.add(href)
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        parent=link
        for _ in range(5):
            if parent.parent:parent=parent.parent
        text=parent.get_text(separator="|",strip=True)
        prop=parse_text(text,full_url,category,ward,"健美家／HOMES")
        if prop:props.append(prop)
    log.info(f"  {ward} {category}: {len(props)} properties parsed")
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
    title=next((p.strip()[:60] for p in text.split("|") if len(p.strip())>5 and "万円" not in p[:10]),"")
    return {"url":url,"source":source,"category":category,"ward":ward,"title":title,"price":price,"yield_pct":yield_pct,"size":size,"built":built,"station":station,"walk_min":walk_min,"floor":floor,"address":f"東京都{ward}","scraped_at":TODAY}

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

def get_fallback_rent(ward):
    d={"台東区":{"1R":9.5,"1K":10.2,"1LDK":14.8,"2LDK":19.5,"3LDK":24.0,"store_tsubo":2.2},"墨田区":{"1R":8.8,"1K":9.5,"1LDK":13.5,"2LDK":17.0,"3LDK":21.0,"store_tsubo":1.8},"江東区":{"1R":9.2,"1K":10.0,"1LDK":14.0,"2LDK":18.5,"3LDK":23.0,"store_tsubo":2.0},"荒川区":{"1R":7.8,"1K":8.5,"1LDK":12.0,"2LDK":15.0,"3LDK":19.0,"store_tsubo":1.5},"足立区":{"1R":6.5,"1K":7.2,"1LDK":10.0,"2LDK":12.5,"3LDK":15.0,"store_tsubo":1.2},"葛飾区":{"1R":6.3,"1K":7.0,"1LDK":9.8,"2LDK":12.0,"3LDK":14.5,"store_tsubo":1.1},"江戸川区":{"1R":6.5,"1K":7.0,"1LDK":10.0,"2LDK":12.0,"3LDK":14.5,"store_tsubo":1.1}}
    return d.get(ward,{"1R":7.0,"1K":8.0,"1LDK":11.0,"2LDK":14.0,"3LDK":17.0,"store_tsubo":1.5})

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
    log.info("=== 健美家 スクレイピング開始 ===")
    for t in kenbiya_urls():
        all_p.extend(scrape_kenbiya_page(t["url"],t["category"],t["ward"]))
        time.sleep(DELAY)
    log.info("=== goo不動産 スクレイピング開始 ===")
    all_p.extend(scrape_goo())
    seen=set();uniq=[]
    for p in all_p:
        if p["url"] not in seen:seen.add(p["url"]);uniq.append(p)
    log.info("=== 賃料相場 取得 ===")
    rent=scrape_rent()
    wc={w:{c:sum(1 for p in uniq if p["ward"]==w and p["category"]==c) for c in PROPERTY_CATEGORIES} for w in JOTO_WARDS.values()}
    out={"scraped_at":TODAY,"total_properties":len(uniq),"properties":uniq,"rent_data":rent,"ward_counts":wc}
    path=os.path.join(DATA_DIR,"properties.json")
    with open(path,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了: {len(uniq)}件 → {path} ===")
    if len(uniq)==0:
        log.error("⚠ 物件が0件です。スクレイピングがブロックされている可能性があります。")
        log.error("  上のログで HTTP ステータスコードと /re_ links の数を確認してください。")

if __name__=="__main__":
    main()
