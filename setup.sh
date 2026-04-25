#!/bin/bash
set -e
echo "🏗️ ファイル生成中..."
mkdir -p .github/workflows src templates

cat > .gitignore << 'EOF'
data/
output/
__pycache__/
*.pyc
.venv/
EOF

cat > requirements.txt << 'EOF'
anthropic>=0.40.0
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
jinja2>=3.1.0
EOF

cat > .github/workflows/daily_report.yml << 'EOF'
name: 城東エリア不動産レポート自動生成
on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:
permissions:
  contents: write
  pages: write
jobs:
  generate-report:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - name: Scrape
        run: python src/scraper.py
      - name: Analyze
        run: python src/analyzer.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Generate report
        run: python src/report_generator.py
      - uses: actions/upload-artifact@v4
        with:
          name: report-${{ github.run_number }}
          path: output/
          retention-days: 90
      - uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./output
          keep_files: true
EOF

touch src/__init__.py

cat > src/config.py << 'EOF'
JOTO_WARDS = {"taito-ku":"台東区","sumida-ku":"墨田区","koto-ku":"江東区","arakawa-ku":"荒川区","adachi-ku":"足立区","katsushika-ku":"葛飾区","edogawa-ku":"江戸川区"}
PROPERTY_CATEGORIES = {"store":{"label":"売り店舗・事務所","kenbiya_path":"pp6","budget_max":50000000},"condo":{"label":"区分マンション","kenbiya_path":"pp0","budget_max":50000000},"house":{"label":"戸建て","kenbiya_path":"pp3","budget_max":50000000}}
def kenbiya_urls():
    urls = []
    for wk,wn in JOTO_WARDS.items():
        for ck,c in PROPERTY_CATEGORIES.items():
            urls.append({"source":"kenbiya","category":ck,"ward_key":wk,"ward":wn,"url":f"https://www.kenbiya.com/{c['kenbiya_path']}/s/tokyo/{wk}/","label":f"{wn} {c['label']}"})
    return urls
def suumo_rent_urls():
    base="https://suumo.jp/chintai/soba/tokyo/sc_"
    codes={"台東区":"taito","墨田区":"sumida","江東区":"koto","荒川区":"arakawa","足立区":"adachi","葛飾区":"katsushika","江戸川区":"edogawa"}
    return [{"ward":w,"url":f"{base}{c}/"} for w,c in codes.items()]
BUDGET={"self_fund_max":20000000,"loan_max":30000000,"total_max":50000000}
LOAN_PARAMS={"interest_rate":0.025,"term_years":25,"ltv_max":0.80,"dscr_min":1.20}
SCORING_WEIGHTS={"location":30,"yield_return":20,"tenant_demand":20,"future_value":15,"capital_eff":15}
CLAUDE_MODEL="claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS=8000
OUTPUT_DIR="output"
DATA_DIR="data"
EOF

cat > src/scraper.py << 'EOF'
import json,os,re,time,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from config import JOTO_WARDS,PROPERTY_CATEGORIES,BUDGET,kenbiya_urls,suumo_rent_urls,DATA_DIR
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)
JST=timezone(timedelta(hours=9));TODAY=datetime.now(JST).strftime("%Y-%m-%d")
HEADERS={"User-Agent":"JotoPropertyReport/1.0","Accept-Language":"ja,en;q=0.9"};DELAY=2

def scrape_kenbiya_page(url,category,ward):
    props=[]
    try:
        r=requests.get(url,headers=HEADERS,timeout=30);r.raise_for_status();soup=BeautifulSoup(r.text,"lxml")
    except Exception as e:
        log.warning(f"Failed: {url}: {e}");return props
    seen=set()
    for link in soup.select("a[href*='/re_']"):
        href=link.get("href","")
        if "/re_" not in href or href in seen:continue
        seen.add(href)
        full_url=f"https://www.kenbiya.com{href}" if href.startswith("/") else href
        parent=link
        for _ in range(5):
            if parent.parent:parent=parent.parent
        text=parent.get_text(separator="|",strip=True)
        prop=parse_text(text,full_url,category,ward,"健美家／HOMES")
        if prop:props.append(prop)
    log.info(f"  {ward} {category}: {len(props)} found")
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
    try:
        r=requests.get("https://house.goo.ne.jp/toushi/office/area_tokyo.html",headers=HEADERS,timeout=30);r.raise_for_status();soup=BeautifulSoup(r.text,"lxml")
    except Exception as e:
        log.warning(f"goo failed: {e}");return props
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
    log.info("=== 健美家 ===")
    for t in kenbiya_urls():
        all_p.extend(scrape_kenbiya_page(t["url"],t["category"],t["ward"]));time.sleep(DELAY)
    log.info("=== goo不動産 ===")
    all_p.extend(scrape_goo())
    seen=set();uniq=[]
    for p in all_p:
        if p["url"] not in seen:seen.add(p["url"]);uniq.append(p)
    log.info("=== 賃料相場 ===")
    rent=scrape_rent()
    wc={w:{c:sum(1 for p in uniq if p["ward"]==w and p["category"]==c) for c in PROPERTY_CATEGORIES} for w in JOTO_WARDS.values()}
    out={"scraped_at":TODAY,"total_properties":len(uniq),"properties":uniq,"rent_data":rent,"ward_counts":wc}
    path=os.path.join(DATA_DIR,"properties.json")
    with open(path,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了: {len(uniq)}件 → {path} ===")

if __name__=="__main__":
    main()
EOF

cat > src/analyzer.py << 'EOF'
import json,os,logging
import anthropic
from config import BUDGET,LOAN_PARAMS,SCORING_WEIGHTS,CLAUDE_MODEL,CLAUDE_MAX_TOKENS,DATA_DIR,PROPERTY_CATEGORIES
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)

def build_prompt(properties,rent_data,category):
    cl=PROPERTY_CATEGORIES[category]["label"]
    return f"""あなたは東京の不動産投資アナリストです。城東7区の{cl}を分析してください。

## 投資条件
自己資金上限:{BUDGET['self_fund_max']//10000}万 融資上限:{BUDGET['loan_max']//10000}万
金利:{LOAN_PARAMS['interest_rate']*100}% 期間:{LOAN_PARAMS['term_years']}年 LTV:{LOAN_PARAMS['ltv_max']*100}% DSCR下限:{LOAN_PARAMS['dscr_min']}

## 物件データ
{json.dumps(properties,ensure_ascii=False,indent=2)}

## 賃料相場
{json.dumps(rent_data,ensure_ascii=False,indent=2)}

## 出力
JSONのみ出力（バッククォート不要）。上位10件をスコア降順:
[{{"url":"","rank":1,"score":92,"score_breakdown":{{"location":28,"yield_return":18,"tenant_demand":19,"future_value":14,"capital_eff":13}},"tenant_type":"想定テナント3-4業種","estimated_rent":"想定月額賃料","rent_reference":"参考賃貸物件・坪単価・相場整合性","analysis":"150字以内","loan_analysis":{{"feasibility":"A/B/C","reason":"80字以内","recommended_plan":{{"self_fund":1280,"loan":3000,"monthly_repay":13.5,"dscr":1.35}}}},"pros":["1","2","3"],"cons":["1","2"],"over_budget":false}}]

スコア: location({SCORING_WEIGHTS['location']}) yield({SCORING_WEIGHTS['yield_return']}) demand({SCORING_WEIGHTS['tenant_demand']}) future({SCORING_WEIGHTS['future_value']}) efficiency({SCORING_WEIGHTS['capital_eff']})
融資: A=新耐震築30年以内駅10分利回5%以上 B=旧耐震だが立地良好 C=現金推奨
rent_referenceには同エリア同規模の賃貸事例・坪単価・相場整合性を必ず含めること。"""

def analyze(client,properties,rent_data,category):
    if not properties:return []
    bmax=BUDGET["total_max"]//10000
    in_b=[p for p in properties if p["price"]<=bmax]
    over=sorted([p for p in properties if p["price"]>bmax],key=lambda x:x.get("yield_pct") or 0,reverse=True)
    sel=(in_b+over[:max(10-len(in_b),5)])[:30]
    log.info(f"  {category}: {len(sel)}件送信")
    try:
        msg=client.messages.create(model=CLAUDE_MODEL,max_tokens=CLAUDE_MAX_TOKENS,messages=[{"role":"user","content":build_prompt(sel,rent_data,category)}])
        txt=msg.content[0].text.strip()
        if txt.startswith("```"):txt=txt.split("\n",1)[1].rsplit("```",1)[0]
        results=json.loads(txt)
        um={p["url"]:p for p in sel}
        for r in results:
            if r["url"] in um:r.update({k:v for k,v in um[r["url"]].items() if k not in r})
        log.info(f"  {category}: {len(results)}件完了")
        return results
    except Exception as e:
        log.error(f"  {category}: {e}");return []

def main():
    dp=os.path.join(DATA_DIR,"properties.json")
    if not os.path.exists(dp):log.error("properties.json なし");return
    with open(dp,"r",encoding="utf-8") as f:data=json.load(f)
    key=os.getenv("ANTHROPIC_API_KEY")
    if not key:log.error("ANTHROPIC_API_KEY 未設定");return
    client=anthropic.Anthropic(api_key=key)
    results={}
    for ck in PROPERTY_CATEGORIES:
        cp=[p for p in data["properties"] if p["category"]==ck]
        log.info(f"--- {PROPERTY_CATEGORIES[ck]['label']}: {len(cp)}件 ---")
        results[ck]=analyze(client,cp,data["rent_data"],ck)
    out={"analyzed_at":data["scraped_at"],"results":results,"rent_data":data["rent_data"],"ward_counts":data["ward_counts"],"budget":BUDGET,"loan_params":LOAN_PARAMS}
    op=os.path.join(DATA_DIR,"analysis.json")
    with open(op,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了 → {op} ===")

if __name__=="__main__":
    main()
EOF

cat > src/report_generator.py << 'EOF'
import json,os,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
from jinja2 import Environment,FileSystemLoader
from config import DATA_DIR,OUTPUT_DIR,PROPERTY_CATEGORIES,JOTO_WARDS,BUDGET
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)

def detect_changes(cur,prev_path):
    ch={"new":[],"removed":[],"price_changed":[]}
    if not os.path.exists(prev_path):return ch
    try:
        with open(prev_path,"r",encoding="utf-8") as f:prev=json.load(f)
    except:return ch
    pu={p["url"]:p for r in prev.get("results",{}).values() for p in r}
    cu={p["url"]:p for r in cur.get("results",{}).values() for p in r}
    for u,p in cu.items():
        if u not in pu:ch["new"].append(p)
    for u,p in pu.items():
        if u not in cu:ch["removed"].append(p)
    for u in set(cu)&set(pu):
        cp,pp=cu[u].get("price",0),pu[u].get("price",0)
        if cp!=pp and pp>0:ch["price_changed"].append({**cu[u],"prev_price":pp,"price_diff":cp-pp})
    return ch

def main():
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    ap=os.path.join(DATA_DIR,"analysis.json")
    if not os.path.exists(ap):log.error("analysis.json なし");return
    with open(ap,"r",encoding="utf-8") as f:data=json.load(f)
    pp=os.path.join(OUTPUT_DIR,"latest_analysis.json")
    changes=detect_changes(data,pp)
    log.info(f"差分: +{len(changes['new'])} -{len(changes['removed'])} Δ{len(changes['price_changed'])}")
    env=Environment(loader=FileSystemLoader("templates"),autoescape=True)
    tmpl=env.get_template("report.html")
    rd=data.get("analyzed_at","")
    html=tmpl.render(report_date=rd,categories=PROPERTY_CATEGORIES,wards=JOTO_WARDS,results=data["results"],rent_data=data.get("rent_data",{}),ward_counts=data.get("ward_counts",{}),budget=BUDGET,changes=changes,total_props=sum(len(v) for v in data["results"].values()))
    with open(os.path.join(OUTPUT_DIR,"index.html"),"w",encoding="utf-8") as f:f.write(html)
    with open(os.path.join(OUTPUT_DIR,f"report_{rd}.html"),"w",encoding="utf-8") as f:f.write(html)
    with open(pp,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,indent=2)
    log.info("=== レポート生成完了 ===")

if __name__=="__main__":
    main()
EOF

cat > templates/report.html << 'HTMLEOF'
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>城東エリア不動産投資レポート - {{ report_date }}</title>
<style>
:root{--bg:#f5f0e8;--card:#faf7f2;--wh:#fff;--dk:#1a1510;--md:#5a5040;--lt:#8a7a5e;--bd:#e0d8c8;--r:#c0392b;--o:#d4782b;--g:#2d7a30;--bl:#2a7ab5}
*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Helvetica Neue','Hiragino Sans',sans-serif;background:var(--bg);color:var(--dk);line-height:1.6}
.c{max-width:760px;margin:0 auto;padding:16px}header{text-align:center;padding:24px 0 16px}h1{font-family:Georgia,serif;font-size:22px;font-weight:800}
.sub{font-size:12px;color:var(--lt);margin-top:4px}.date{display:inline-block;margin-top:6px;padding:2px 12px;background:var(--r);color:#fff;border-radius:12px;font-size:11px;font-weight:700}
.bx{background:var(--wh);border:1px solid var(--bd);border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:12px;line-height:1.7;color:var(--md)}.bx strong{color:var(--dk)}
.al{background:#2d7a3010;border:1px solid #2d7a3030;color:#2d5a28}
.tabs{display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap}.tabs button{padding:6px 16px;border-radius:20px;border:1px solid var(--bd);background:0;color:var(--md);font-size:12px;font-weight:600;cursor:pointer}.tabs button.on{background:var(--r);color:#fff;border-color:var(--r)}
.rt{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}.rt th,.rt td{padding:6px 8px;border:1px solid var(--bd);text-align:center}.rt th{background:var(--dk);color:#fff}.rt tr:nth-child(even){background:var(--card)}
.cd{background:var(--card);border:1.5px solid #ddd5c8;border-radius:6px;margin-bottom:10px;overflow:hidden}.cd.ob{opacity:.9;background:#faf5ee}
.ch{padding:12px 16px;cursor:pointer}.ch:hover{background:#f5efe5}
.tr{display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap}
.rk{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;color:#fff;font-size:12px;font-weight:800;font-family:Georgia,serif;flex-shrink:0}
.rk1,.rk2,.rk3{background:var(--r)}.rk4,.rk5{background:var(--o)}.rkx{background:var(--lt)}
.tt{font-weight:700;font-size:14px;flex:1}.bg{padding:1px 7px;border-radius:10px;font-size:9px;font-weight:700;white-space:nowrap}.bgn{background:#2d7a3020;color:var(--g);border:1px solid #2d7a3033}.bgo{background:#e8433020;color:#c03030;border:1px solid #e8433033}
.mr{display:flex;flex-wrap:wrap;gap:2px 10px;font-size:11.5px;color:#7a6a58;padding-left:32px;margin-bottom:5px}
.pr{display:flex;flex-wrap:wrap;gap:2px 14px;align-items:baseline;padding-left:32px}.pv{font-family:Georgia,serif;font-weight:800;font-size:19px;color:var(--r)}.pv .u{font-size:13px}.yv{font-size:13px;font-weight:700;color:var(--g)}
.sb{display:flex;align-items:center;gap:6px;padding-left:32px;margin-top:4px}.st{flex:1;height:5px;background:#e8e0d4;border-radius:3px;overflow:hidden}.sf{height:100%;border-radius:3px}.sn{font-weight:800;font-size:15px;font-family:Georgia,serif;min-width:24px}
.dt{padding:0 16px 14px;border-top:1px solid #e8e0d4;display:none}.cd.open .dt{display:block}
.dg{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}.dl{font-size:10px;color:#a09080;font-weight:700;letter-spacing:1px;margin-bottom:3px}.dv{font-size:12.5px;font-weight:600}.er{font-size:15px;color:var(--g);font-weight:800}.at{font-size:12px;line-height:1.65;color:#3d3528;margin-bottom:10px}
.pc{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}.pc h4{font-size:10px;font-weight:700;margin-bottom:2px}.pc .p{color:var(--g)}.pc .c2{color:var(--r)}.pc div{font-size:11px;color:#3d3528;margin-bottom:1px}
.lb{padding:8px 12px;border-radius:4px;margin-bottom:8px;font-size:11.5px;color:var(--md)}.la{background:#2d7a3010;border:1px solid #2d7a3030}.lbb{background:#d4782b10;border:1px solid #d4782b30}.lc{background:#c0392b10;border:1px solid #c0392b30}
.fb{padding:6px 10px;background:#f0ebe2;border-radius:4px;font-size:11.5px;color:var(--md);margin-bottom:8px}
.lnk{display:inline-block;padding:5px 14px;background:var(--r);color:#fff;border-radius:4px;font-size:11.5px;font-weight:700;text-decoration:none}
.ls{background:var(--wh);border:1px solid var(--bd);border-radius:6px;padding:10px 14px;margin-top:16px}.lsh{cursor:pointer;display:flex;justify-content:space-between;align-items:center;font-weight:700;font-size:13px}.li{display:block;padding:6px 10px;margin-bottom:4px;background:var(--card);border-radius:4px;text-decoration:none;border:1px solid #e8e0d4;font-size:12px;font-weight:600}
.dis{margin-top:12px;padding:8px 12px;background:#eee8db;border-radius:6px;font-size:10.5px;color:#a09080;line-height:1.6}
</style></head><body>
<div class="c">
<header><h1>城東エリア 不動産投資レポート</h1><div class="sub">台東区・墨田区・江東区・荒川区・足立区・葛飾区・江戸川区</div><div class="date">🔄 {{ report_date }} 自動更新</div></header>
{% if changes and (changes.new or changes.removed or changes.price_changed) %}<div class="bx al"><strong>📢 変動</strong><br>{% for p in changes.removed %}・<strong style="color:var(--r)">成約</strong>: {{ p.get('title','') }} {{ p.get('price','?') }}万円<br>{% endfor %}{% for p in changes.new %}・<strong style="color:var(--g)">新規</strong>: {{ p.get('title','') }} {{ p.get('price','?') }}万円{% if p.get('yield_pct') %}（{{ p.yield_pct }}%）{% endif %}<br>{% endfor %}{% for p in changes.price_changed %}・<strong>価格変更</strong>: {{ p.get('title','') }} {{ p.prev_price }}万→{{ p.price }}万<br>{% endfor %}</div>{% endif %}
<div class="bx"><strong>概要</strong>：城東7区 <strong>{{ total_props }}件</strong>分析。予算=自己資金{{ budget.self_fund_max//10000 }}万+融資{{ budget.loan_max//10000 }}万。融資適格度A/B/C・賃料参考付き。</div>
<div class="bx"><strong>賃料相場</strong><table class="rt"><tr><th>区</th><th>1R</th><th>1K</th><th>1LDK</th><th>2LDK</th><th>3LDK</th><th>店舗坪単価</th></tr>{% for w,r in rent_data.items() %}<tr><td><strong>{{ w }}</strong></td><td>{{ r.get('1R','-') }}万</td><td>{{ r.get('1K','-') }}万</td><td>{{ r.get('1LDK','-') }}万</td><td>{{ r.get('2LDK','-') }}万</td><td>{{ r.get('3LDK','-') }}万</td><td>{{ r.get('store_tsubo','-') }}万/坪</td></tr>{% endfor %}</table></div>
<div class="tabs" id="tabs">{% for ck,ct in categories.items() %}<button onclick="showCat('{{ ck }}')" class="{{ 'on' if loop.first }}" data-c="{{ ck }}">{{ ct.label }}</button>{% endfor %}</div>
{% for ck,rs in results.items() %}<div class="cs" data-cat="{{ ck }}" style="{{ '' if loop.first else 'display:none' }}">{% for p in rs %}<div class="cd {{ 'ob' if p.get('over_budget') }}" id="c-{{ ck }}-{{ loop.index }}"><div class="ch" onclick="tog('c-{{ ck }}-{{ loop.index }}')"><div class="tr"><span class="rk {{ 'rk'~p.rank if p.rank<=5 else 'rkx' }}">{{ p.rank }}</span><span class="tt">{{ p.get('title','物件') }}</span>{% if p.get('is_new') %}<span class="bg bgn">NEW</span>{% endif %}{% if p.get('over_budget') %}<span class="bg bgo">予算超</span>{% endif %}<span style="font-size:14px;color:var(--lt)">▼</span></div><div class="mr"><span>📍{{ p.get('ward','') }}</span><span>🚉{{ p.get('station','') }}</span>{% if p.get('size') %}<span>📐{{ p.size }}㎡</span>{% endif %}{% if p.get('built') %}<span>🏗{{ p.built }}</span>{% endif %}</div><div class="pr"><span class="pv" {% if p.get('over_budget') %}style="color:#b08050"{% endif %}>{{ "{:,}".format(p.price) }}<span class="u">万円</span></span>{% if p.get('yield_pct') %}<span class="yv">利回り{{ p.yield_pct }}%</span>{% endif %}{% if p.get('floor') %}<span style="font-size:11px;color:#a09080">{{ p.floor }}</span>{% endif %}</div><div class="sb"><div class="st"><div class="sf" style="width:{{ p.score }}%;background:{{ '#c0392b' if p.score>=90 else '#d4782b' if p.score>=80 else '#8a7a5e' }}"></div></div><span class="sn" style="color:{{ '#c0392b' if p.score>=90 else '#d4782b' if p.score>=80 else '#8a7a5e' }}">{{ p.score }}</span></div></div>
<div class="dt"><div class="dg"><div><div class="dl">想定テナント</div><div class="dv">{{ p.get('tenant_type','') }}</div></div><div><div class="dl">想定賃料</div><div class="er">{{ p.get('estimated_rent','') }}</div></div></div>{% if p.get('rent_reference') %}<div style="margin-bottom:10px"><div class="dl">賃料参考データ</div><div class="at">{{ p.rent_reference }}</div></div>{% endif %}<div style="margin-bottom:10px"><div class="dl">分析</div><div class="at">{{ p.get('analysis','') }}</div></div><div class="pc"><div><h4 class="p">✓ メリット</h4>{% for i in p.get('pros',[]) %}<div>・{{ i }}</div>{% endfor %}</div><div><h4 class="c2">△ リスク</h4>{% for i in p.get('cons',[]) %}<div>・{{ i }}</div>{% endfor %}</div></div>{% if p.get('loan_analysis') %}{% set la=p.loan_analysis %}<div class="lb {{ 'la' if la.feasibility=='A' else 'lbb' if la.feasibility=='B' else 'lc' }}"><strong>融資: {{ la.feasibility }}</strong> {{ la.reason }}{% if la.get('recommended_plan') %}{% set rp=la.recommended_plan %}<br>自己{{ rp.self_fund }}万/融資{{ rp.loan }}万/月返済{{ rp.monthly_repay }}万/DSCR{{ rp.dscr }}倍{% endif %}</div>{% endif %}<div class="fb">{% if p.get('loan_analysis',{}).get('recommended_plan') %}{% set rp=p.loan_analysis.recommended_plan %}<strong>資金計画:</strong> 自己{{ "{:,}".format(rp.self_fund) }}万/融資{{ "{:,}".format(rp.loan) }}万/計{{ "{:,}".format(p.price) }}万{% if p.get('over_budget') %}<span style="color:#c03030;font-weight:700"> ※融資増額必要</span>{% endif %}{% endif %}</div><a href="{{ p.url }}" target="_blank" class="lnk">{{ p.get('source','') }}で詳細 →</a></div></div>{% endfor %}</div>{% endfor %}
<div class="ls"><div class="lsh" onclick="document.getElementById('lb').style.display=document.getElementById('lb').style.display==='none'?'block':'none'"><span>検索リンク集</span><span>▼</span></div><div id="lb" style="display:none;margin-top:8px">{% for wk,wn in wards.items() %}<a href="https://www.kenbiya.com/pp6/s/tokyo/{{ wk }}/" target="_blank" class="li" style="color:#c47a3a">健美家 {{ wn }}</a>{% endfor %}<a href="https://house.goo.ne.jp/toushi/office/area_tokyo.html" target="_blank" class="li" style="color:var(--bl)">goo不動産</a><a href="https://www.athome.co.jp/buy_store/tokyo/" target="_blank" class="li" style="color:#3a7ac4">アットホーム</a><a href="https://www.rakumachi.jp/syuuekibukken/area/prefecture/dim2002/" target="_blank" class="li" style="color:#5a9a3a">楽待</a></div></div>
<div class="dis">⚠ {{ report_date }}時点のWeb公開情報に基づくAI自動分析（GitHub Actions + Claude API）。融資適格度はAI推定であり実際の融資可否は金融機関審査によります。購入前に現地確認・重説・融資審査を必ず実施してください。</div>
</div>
<script>function showCat(c){document.querySelectorAll('.cs').forEach(s=>s.style.display='none');document.querySelector('[data-cat="'+c+'"]').style.display='block';document.querySelectorAll('#tabs button').forEach(b=>b.classList.remove('on'));document.querySelector('#tabs button[data-c="'+c+'"]').classList.add('on')}function tog(id){document.getElementById(id).classList.toggle('open')}document.addEventListener('DOMContentLoaded',()=>{const f=document.querySelector('.cd');if(f)f.classList.add('open')})</script>
</body></html>
HTMLEOF

cat > README.md << 'EOF'
# 城東エリア不動産投資レポート自動生成
GitHub Actions + Claude API で毎日AM9:00(JST)に自動実行。
## 対象
売り店舗・事務所 / 区分マンション / 戸建て（城東7区・5,000万以下）
## セットアップ
1. Secrets に ANTHROPIC_API_KEY を設定済み
2. Settings → Pages → gh-pages を有効化
3. Actions から手動実行テスト
EOF

echo ""; echo "✅ 全ファイル生成完了！"
echo ""; echo "📂 構成:"
find . -type f | grep -v '.git/' | sort
echo ""; echo "👉 次のコマンドを実行:"
echo "   git add ."
echo '   git commit -m "feat: 城東エリア不動産レポート自動生成"'
echo "   git push origin main"
