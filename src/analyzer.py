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
