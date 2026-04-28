import json,os,logging
from pathlib import Path
from jinja2 import Environment,FileSystemLoader
from config import DATA_DIR,OUTPUT_DIR,PROPERTY_CATEGORIES,JOTO_WARDS,BUDGET,RENT_DATA_BY_CATEGORY,MIN_NET_YIELD
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

    all_markers=[]
    for ck,rs in data["results"].items():
        ci=PROPERTY_CATEGORIES.get(ck,{})
        for p in rs:
            if p.get("lat") and p.get("lng"):
                all_markers.append({"lat":p["lat"],"lng":p["lng"],"title":p.get("title",""),
                    "price":p.get("price",0),"yield_pct":p.get("yield_pct"),
                    "est_net_yield":p.get("est_net_yield"),"nego_rate_pct":p.get("nego_rate_pct"),
                    "category":ck,"color":ci.get("color","#888"),"icon":ci.get("icon","📍"),
                    "url":p.get("url",""),"station":p.get("station",""),"score":p.get("score",0),
                    "is_first_floor":p.get("is_first_floor",False)})

    env=Environment(loader=FileSystemLoader("templates"),autoescape=True)
    tmpl=env.get_template("report.html")
    rd=data.get("analyzed_at","")
    html=tmpl.render(
        report_date=rd,categories=PROPERTY_CATEGORIES,wards=JOTO_WARDS,
        results=data["results"],rent_data=data.get("rent_data",{}),
        rent_by_category=data.get("rent_by_category",RENT_DATA_BY_CATEGORY),
        ward_counts=data.get("ward_counts",{}),budget=BUDGET,changes=changes,
        total_props=sum(len(v) for v in data["results"].values()),
        market_summary=data.get("market_summary",""),
        data_summary=data.get("data_summary",{}),
        markers_json=json.dumps(all_markers,ensure_ascii=False),
        editorial=data.get("editorial",""),
        by_source=data.get("by_source",{}),
        screening=data.get("screening",{}),
        min_net_yield=MIN_NET_YIELD,
    )
    with open(os.path.join(OUTPUT_DIR,"index.html"),"w",encoding="utf-8") as f:f.write(html)
    with open(os.path.join(OUTPUT_DIR,f"report_{rd}.html"),"w",encoding="utf-8") as f:f.write(html)
    with open(pp,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,indent=2)
    log.info("=== レポート生成完了 ===")

if __name__=="__main__":
    main()
