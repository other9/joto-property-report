"""健美家スクレイパー - 詳細ページ個別取得方式

一覧ページからURLだけ収集し、各物件の詳細ページを個別にフェッチすることで
URLと物件情報の紐付けを100%正確にする。
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
    "Accept-Encoding":"gzip, deflate, br",
}
DELAY=3
MAX_DETAIL_PER_CATEGORY=20  # カテゴリ×区あたりの詳細取得上限

# ═══════════════════════════════════════════
# STEP 1: 一覧ページからURLだけ収集
# ═══════════════════════════════════════════
def collect_urls_from_list(url):
    """一覧ページから /re_ 物件URLだけを収集（テキスト解析しない）"""
    urls=[]
    try:
        resp=requests.get(url,headers=HEADERS,timeout=30)
        log.info(f"  LIST {url} -> {resp.status_code}")
        resp.raise_for_status()
        soup=BeautifulSoup(resp.text,"lxml")
    except Exception as e:
        log.error(f"  LIST FAILED {url}: {e}");return urls
    seen=set()
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href:continue
        full_url=href if href.startswith("http") else f"https://www.kenbiya.com{href}"
        if full_url not in seen:
            seen.add(full_url);urls.append(full_url)
    log.info(f"  → {len(urls)} URLs collected")
    return urls

# ═══════════════════════════════════════════
# STEP 2: 詳細ページから物件情報を取得
# ═══════════════════════════════════════════
def fetch_detail_page(url,category,ward):
    """物件詳細ページをフェッチしてパース"""
    try:
        resp=requests.get(url,headers=HEADERS,timeout=30)
        if resp.status_code!=200:
            log.warning(f"  DETAIL {resp.status_code}: {url}");return None
        text=resp.text
    except Exception as e:
        log.warning(f"  DETAIL FAILED {url}: {e}");return None

    # 詳細ページのテキストを構造化パース
    # 健美家詳細ページ: "価格 · XXXX万円 · 満室時利回り · X.XX％ · 交通 · ... · 住所 · ... · 築年月 · ..."
    soup=BeautifulSoup(text,"lxml")
    page_text=soup.get_text(separator=" · ",strip=True)

    # 価格
    pm=re.search(r"価格\s*·?\s*([\d,]+)万円",page_text)
    if not pm:return None
    price=int(pm.group(1).replace(",",""))

    # 利回り
    ym=re.search(r"利回り\s*·?\s*([\d.]+)[％%]",page_text)
    yield_pct=float(ym.group(1)) if ym else None

    # 交通（駅名・徒歩）
    tm=re.search(r"交通\s*·?\s*(.+?)(?:\s*·\s*満室|$)",page_text)
    station=None;walk_min=None
    if tm:
        transport=tm.group(1)
        sm=re.search(r"(\S+駅)\s*徒歩(\d+)分",transport)
        if sm:
            station=f"{sm.group(1)} 徒歩{sm.group(2)}分"
            walk_min=int(sm.group(2))

    # 住所
    am=re.search(r"住所\s*·?\s*東京都([\w区][\w\d丁目\-]+)",page_text)
    address=f"東京都{am.group(1)}" if am else f"東京都{ward}"

    # 物件名
    nm=re.search(r"物件名\s*·?\s*(\S+)",page_text)
    prop_name=nm.group(1) if nm else ""

    # 築年月
    bm=re.search(r"築年月\s*·?\s*(\d{4})年(\d{1,2})月",page_text)
    built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None

    # 建物構造/階数
    fm=re.search(r"構造/階数\s*·?\s*(\S+造)(\d+)階/(\d+)階建",page_text)
    structure=""
    floor=None
    if fm:
        structure=fm.group(1)
        floor=f"{fm.group(2)}階／{fm.group(3)}階建"

    # 専有面積
    szm=re.search(r"(?:専有面積|面積)\s*·?\s*([\d.]+)\s*m[²㎡]",page_text)
    size=float(szm.group(1)) if szm else None

    # 間取り
    lm=re.search(r"間取り\s*·?\s*(\w+)",page_text)
    layout=lm.group(1) if lm else ""

    # タイトル
    # ページタイトルから取得（「台東区 2,900万円 3.80% 区分マンション」形式）
    title_tag=soup.find("title")
    if title_tag and title_tag.string:
        title=title_tag.string.split("｜")[0].strip()
    else:
        title=f"{ward} {price}万円 {prop_name}"

    # 物件名があればタイトルに含める
    if prop_name and prop_name not in title:
        title=f"{prop_name}（{title}）"

    return {
        "url":url,"source":"健美家／HOMES","category":category,"ward":ward,
        "title":title,"price":price,"yield_pct":yield_pct,"size":size,
        "built":built,"station":station,"walk_min":walk_min,"floor":floor,
        "structure":structure,"layout":layout,"address":address,
        "prop_name":prop_name,"scraped_at":TODAY,
    }

# ═══════════════════════════════════════════
# STEP 3: goo不動産（従来方式、店舗のみ）
# ═══════════════════════════════════════════
def scrape_goo():
    props=[]
    url="https://house.goo.ne.jp/toushi/office/area_tokyo.html"
    try:
        r=requests.get(url,headers=HEADERS,timeout=30)
        log.info(f"  GET goo -> {r.status_code}")
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
        # gooは一覧からパース（詳細ページ構造が異なるため）
        pm=re.search(r"([\d,]+)万円",text)
        if not pm:continue
        price=int(pm.group(1).replace(",",""))
        ym=re.search(r"([\d.]+)[％%]",text);yield_pct=float(ym.group(1)) if ym else None
        szm=re.search(r"([\d.]+)\s*m[²㎡]",text);size=float(szm.group(1)) if szm else None
        bm=re.search(r"(\d{4})年(\d{1,2})月",text);built=f"{bm.group(1)}年{bm.group(2)}月" if bm else None
        sm=re.search(r"([\w]+駅)\s*歩?(\d+)分",text);station=f"{sm.group(1)} 徒歩{sm.group(2)}分" if sm else None
        props.append({"url":full_url,"source":"goo不動産","category":"store","ward":ward,
                      "title":f"{ward} {price}万円","price":price,"yield_pct":yield_pct,
                      "size":size,"built":built,"station":station,
                      "walk_min":int(sm.group(2)) if sm else None,
                      "floor":None,"address":f"東京都{ward}","scraped_at":TODAY})
    log.info(f"  goo: {len(props)} joto props")
    return props

# ═══════════════════════════════════════════
# 重複排除
# ═══════════════════════════════════════════
def dedup_properties(props):
    seen_urls=set();stage1=[]
    for p in props:
        if p["url"] not in seen_urls:
            seen_urls.add(p["url"]);stage1.append(p)
    # 同一物件（価格+面積+築年月+区）集約
    groups={}
    for p in stage1:
        key=f"{p['ward']}_{p['price']}_{p.get('size','')}_{p.get('built','')}"
        if key not in groups:groups[key]=[]
        groups[key].append(p)
    stage2=[]
    for key,group in groups.items():
        first=group[0]
        if len(group)>1:
            first["title"]=f"{first['title']}（他{len(group)-1}件同条件あり）"
            log.info(f"  同一集約: {first['ward']} {first['price']}万 x{len(group)}")
        stage2.append(first)
    log.info(f"  dedup: {len(props)} → {len(stage1)} → {len(stage2)}")
    return stage2

# ═══════════════════════════════════════════
# 賃料相場
# ═══════════════════════════════════════════
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
            r=requests.get(item["url"],headers=HEADERS,timeout=30)
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

# ═══════════════════════════════════════════
# メイン
# ═══════════════════════════════════════════
def main():
    Path(DATA_DIR).mkdir(exist_ok=True)
    all_props=[]

    log.info("=== STEP1: 一覧ページからURL収集 ===")
    url_pool={}  # {category: [(url, ward), ...]}
    for t in kenbiya_urls():
        cat=t["category"];ward=t["ward"]
        if cat not in url_pool:url_pool[cat]=[]
        urls=collect_urls_from_list(t["url"])
        for u in urls:
            url_pool[cat].append((u,ward))
        time.sleep(DELAY)

    log.info("=== STEP2: 詳細ページを個別取得 ===")
    for cat,url_list in url_pool.items():
        # URL重複排除
        seen=set();unique_urls=[]
        for u,w in url_list:
            if u not in seen:seen.add(u);unique_urls.append((u,w))
        log.info(f"  {cat}: {len(unique_urls)} unique URLs")

        fetched=0
        for url,ward in unique_urls:
            if fetched>=MAX_DETAIL_PER_CATEGORY*len(JOTO_WARDS):
                log.info(f"  {cat}: 上限到達 ({fetched}件)");break
            prop=fetch_detail_page(url,cat,ward)
            if prop:
                all_props.append(prop)
                fetched+=1
            time.sleep(DELAY)
        log.info(f"  {cat}: {fetched}件取得完了")

    log.info("=== STEP3: goo不動産 ===")
    all_props.extend(scrape_goo())

    log.info("=== STEP4: 重複排除 ===")
    uniq=dedup_properties(all_props)

    log.info("=== STEP5: 賃料相場 ===")
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
