"""Claude API分析 - 店舗特化・実質利回り5%スクリーニング"""
import json,os,re,logging
import anthropic
from config import (BUDGET,LOAN_PARAMS,SCORING_WEIGHTS,CLAUDE_MODEL,CLAUDE_MAX_TOKENS,
                    DATA_DIR,PROPERTY_CATEGORIES,RENT_DATA_BY_CATEGORY,
                    TOTAL_EXPENSE_RATIO,ACQUISITION_COST_RATIO,MIN_NET_YIELD,SOURCE_BONUS)

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)

def build_prompt(properties,rent_data):
    cat_rent=RENT_DATA_BY_CATEGORY["store"]["data"]
    return f"""あなたは東京の不動産投資アナリスト（店舗・事務所専門）です。
以下の物件はすべて「推定実質利回り{MIN_NET_YIELD}%以上」で事前スクリーニング済みです。

## 投資条件
自己資金上限:{BUDGET['self_fund_max']//10000}万 融資上限:{BUDGET['loan_max']//10000}万 総予算:{BUDGET['total_max']//10000}万
金利:{LOAN_PARAMS['interest_rate']*100}% 期間:{LOAN_PARAMS['term_years']}年

## 実質利回りの算定方法（各物件のest_net_yieldに事前計算済み）
実質利回り ＝（年間賃料×{1-TOTAL_EXPENSE_RATIO}）÷（指値後価格×{1+ACQUISITION_COST_RATIO}）
経費率: 賃料の{int(TOTAL_EXPENSE_RATIO*100)}%（管理費+修繕+固都税+空室+保険）
取得諸経費: 物件価格の{ACQUISITION_COST_RATIO*100}%
指値率: 物件ごとにnego_rate_pctに記載（新着3%〜空室築古15%）

## ソース評価ルール（重要）
健美家／HOMESは投資家向けの高値付けが多いため基準評価。
goo不動産など健美家以外のソースの物件は+3〜5点のボーナスを付与すること。
各物件の"source"フィールドを必ず確認し、スコアリングに反映すること。

## 物件データ（実質利回り{MIN_NET_YIELD}%以上のみ・重複排除済み）
{json.dumps(properties,ensure_ascii=False,indent=2)}

## 店舗賃料相場（万円/坪・月）
{json.dumps(cat_rent,ensure_ascii=False,indent=2)}

## 出力ルール
1. JSON配列のみ出力。前置き・バッククォート不要。[ で始まり ] で終わる
2. 全15件が異なる物件。urlは元データからコピー
3. 1階路面店はlocation+tenant_demandで+5〜8点加点
4. 健美家以外のソースは+3〜5点ボーナス
5. lat/lngがfallbackの物件はlat_estimated/lng_estimatedに推定座標

[{{"url":"元URL","rank":1,"score":92,
"score_breakdown":{{"location":25,"net_yield":22,"tenant_demand":18,"future_value":14,"capital_eff":13}},
"source_bonus":0,
"tenant_type":"想定テナント3-4業種",
"estimated_rent":"想定月額賃料",
"rent_reference":"参考賃貸物件・坪単価",
"analysis":"150字以内。指値の妥当性と実質利回りの根拠を含めること",
"negotiation_comment":"指値交渉の見通し（50字以内）",
"loan_analysis":{{"feasibility":"A/B/C","reason":"80字以内",
"recommended_plan":{{"self_fund":2000,"loan":5000,"monthly_repay":22.0,"dscr":1.35}}}},
"lat_estimated":35.71,"lng_estimated":139.78,
"pros":["1","2","3"],"cons":["1","2"],"over_budget":false}}]

スコア配分: location({SCORING_WEIGHTS['location']}) net_yield({SCORING_WEIGHTS['net_yield']}) tenant_demand({SCORING_WEIGHTS['tenant_demand']}) future_value({SCORING_WEIGHTS['future_value']}) capital_eff({SCORING_WEIGHTS['capital_eff']})
融資: A=新耐震築30年以内駅10分実質利回5%以上 B=旧耐震だが立地良好 C=現金推奨
予算超過は over_budget:true。"""

def build_editorial_prompt(results, data_summary, by_source):
    rs=results.get("store",[])
    top3=rs[:3]
    top_lines=[f"  {p.get('rank','')}位: {p.get('title','')[:30]} 表面{p.get('yield_pct','')}% 推定実質{p.get('est_net_yield','')}% 指値{p.get('nego_rate_pct','')}% [{p.get('source','')}]" for p in top3]
    return f"""あなたは不動産投資アドバイザーです。以下の店舗物件分析結果を踏まえ、投資家向けの総括コメントを300字以内で書いてください。

対象: 東京8区の売り店舗・事務所（推定実質利回り5%以上のみ）
取得件数: {data_summary.get('store',{}).get('total',0)}件（予算内{data_summary.get('store',{}).get('in_budget',0)}件）
ソース別: {json.dumps(by_source,ensure_ascii=False)}

上位3件:
{chr(10).join(top_lines)}

以下を含めること:
- 今週の注目物件と指値交渉の見通し
- 1階路面店の有無と評価
- 健美家以外のソースで見つかった掘り出し物の有無
- 実質利回り5%ラインでのスクリーニング結果の所感
- 投資家へのアクションアドバイス

プレーンテキストのみ。"""

def build_market_prompt(ward_counts,by_source):
    return f"""東京都の対象8区の店舗・事務所投資市況を200字で概説。
取得データ: {json.dumps(ward_counts,ensure_ascii=False)}
ソース別: {json.dumps(by_source,ensure_ascii=False)}
地価動向、エリア別の店舗需要の特徴、投資家の注目点を含めて。プレーンテキストのみ。"""

def extract_json(text):
    text=text.strip()
    try:return json.loads(text)
    except:pass
    m=re.search(r'```(?:json)?\s*\n?(.*?)\n?```',text,re.DOTALL)
    if m:
        try:return json.loads(m.group(1).strip())
        except:pass
    start=text.find('[');end=text.rfind(']')
    if start!=-1 and end!=-1 and end>start:
        try:return json.loads(text[start:end+1])
        except:pass
    return None

def dedup_results(results):
    seen_urls=set();seen_specs=set();deduped=[];rank=1
    for r in results:
        url=r.get("url","")
        spec=f"{r.get('price','')}_{r.get('size','')}_{r.get('built','')}"
        if (url and url in seen_urls) or spec in seen_specs:continue
        seen_urls.add(url);seen_specs.add(spec)
        r["rank"]=rank;rank+=1
        deduped.append(r)
    return deduped

def analyze(client,properties,rent_data):
    if not properties:return []
    bmax=BUDGET["total_max"]//10000
    in_b=[p for p in properties if p["price"]<=bmax]
    over=sorted([p for p in properties if p["price"]>bmax],key=lambda x:x.get("est_net_yield") or 0,reverse=True)
    sel=(in_b+over[:max(15-len(in_b),5)])[:30]
    log.info(f"  store: {len(sel)}件送信（予算内{len(in_b)}件）")
    try:
        msg=client.messages.create(model=CLAUDE_MODEL,max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role":"user","content":build_prompt(sel,rent_data)}])
        raw=msg.content[0].text
        log.info(f"  store: {len(raw)} chars, input_tokens={msg.usage.input_tokens}, output_tokens={msg.usage.output_tokens}")
        # APIコスト算定
        cost_input=msg.usage.input_tokens*3/1_000_000  # $3/MTok
        cost_output=msg.usage.output_tokens*15/1_000_000  # $15/MTok
        log.info(f"  store API cost: ${cost_input+cost_output:.4f} (in:{msg.usage.input_tokens} out:{msg.usage.output_tokens})")

        results=extract_json(raw)
        if results is None:
            log.error(f"  store: JSON失敗: {raw[:300]}");return []
        um={p["url"]:p for p in sel}
        merged=[]
        for r in results:
            url=r.get("url","")
            base=um.get(url,{}).copy()
            base.update(r)
            if base.get("geo_source")=="fallback" and r.get("lat_estimated"):
                base["lat"]=r["lat_estimated"];base["lng"]=r["lng_estimated"];base["geo_source"]="claude"
            merged.append(base)
        merged=dedup_results(merged)
        log.info(f"  store: {len(merged)}件")
        return merged[:15]
    except Exception as e:
        log.error(f"  store: {e}");return []

def generate_text(client,prompt,label):
    log.info(f"=== {label} ===")
    try:
        msg=client.messages.create(model=CLAUDE_MODEL,max_tokens=1000,
            messages=[{"role":"user","content":prompt}])
        txt=msg.content[0].text.strip()
        cost_in=msg.usage.input_tokens*3/1_000_000
        cost_out=msg.usage.output_tokens*15/1_000_000
        log.info(f"  {label}: {len(txt)}文字, cost=${cost_in+cost_out:.4f}")
        return txt
    except Exception as e:
        log.error(f"  {label}: {e}");return ""

def main():
    dp=os.path.join(DATA_DIR,"properties.json")
    if not os.path.exists(dp):log.error("properties.json なし");return
    with open(dp,"r",encoding="utf-8") as f:data=json.load(f)
    key=os.getenv("ANTHROPIC_API_KEY")
    if not key:log.error("ANTHROPIC_API_KEY 未設定");return
    client=anthropic.Anthropic(api_key=key)

    total_cost=0.0
    log.info("=== API呼び出し開始 ===")

    # 分析（1回のAPI呼び出し）
    results={"store":analyze(client,data["properties"],data["rent_data"])}

    # データサマリー
    ds={"store":{"total":len(data["properties"]),"in_budget":sum(1 for p in data["properties"] if p["price"]<=BUDGET["total_max"]//10000)}}

    # 市況概説
    by_source=data.get("by_source",{})
    market=generate_text(client,build_market_prompt(data["ward_counts"],by_source),"市況概説")

    # 総括
    editorial=generate_text(client,build_editorial_prompt(results,ds,by_source),"総括コメント")

    out={"analyzed_at":data["scraped_at"],"results":results,"rent_data":data["rent_data"],
         "rent_by_category":data.get("rent_by_category",RENT_DATA_BY_CATEGORY),
         "ward_counts":data["ward_counts"],"budget":BUDGET,"loan_params":LOAN_PARAMS,
         "market_summary":market,"data_summary":ds,"editorial":editorial,
         "by_source":by_source,
         "screening":{"min_net_yield":MIN_NET_YIELD,"expense_ratio":TOTAL_EXPENSE_RATIO,
                       "acquisition_cost":ACQUISITION_COST_RATIO}}
    op=os.path.join(DATA_DIR,"analysis.json")
    with open(op,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    log.info(f"=== 完了 ===")

if __name__=="__main__":
    main()
