import json,os,re,logging
import anthropic
from config import BUDGET,LOAN_PARAMS,SCORING_WEIGHTS,CLAUDE_MODEL,CLAUDE_MAX_TOKENS,DATA_DIR,PROPERTY_CATEGORIES,RENT_DATA_BY_CATEGORY

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)

def build_prompt(properties,rent_data,category):
    cl=PROPERTY_CATEGORIES[category]["label"]
    # カテゴリ別賃料を使用
    cat_rent=RENT_DATA_BY_CATEGORY.get(category,{}).get("data",rent_data)
    return f"""あなたは東京の不動産投資アナリストです。城東7区の{cl}を分析してください。

## 投資条件
自己資金上限:{BUDGET['self_fund_max']//10000}万 融資上限:{BUDGET['loan_max']//10000}万
金利:{LOAN_PARAMS['interest_rate']*100}% 期間:{LOAN_PARAMS['term_years']}年 LTV:{LOAN_PARAMS['ltv_max']*100}% DSCR下限:{LOAN_PARAMS['dscr_min']}

## 物件データ
以下の物件は重複排除済みです。各物件のurlは一意です。
{json.dumps(properties,ensure_ascii=False,indent=2)}

## {cl}の賃料相場（城東7区）
{json.dumps(cat_rent,ensure_ascii=False,indent=2)}

## 重要な出力ルール
1. JSON配列のみ出力。前置き・説明文・バッククォート(```)は絶対に不要。最初の文字は [ 最後は ]
2. **全10件が異なる物件であること**。同じ価格・面積・築年月の物件は1件だけ選ぶこと
3. urlフィールドは元データからそのままコピー
4. titleが「他N件同条件あり」を含む物件は、同一マンションの大量売出し案件。分析にその旨を記載すること

[{{"url":"元データURL","rank":1,"score":92,
"score_breakdown":{{"location":28,"yield_return":18,"tenant_demand":19,"future_value":14,"capital_eff":13}},
"tenant_type":"想定テナント3-4業種",
"estimated_rent":"想定月額賃料",
"rent_reference":"参考賃貸物件・坪単価・相場整合性（{cl}用の相場データを参照）",
"analysis":"150字以内の分析",
"loan_analysis":{{"feasibility":"A/B/C","reason":"80字以内",
"recommended_plan":{{"self_fund":1280,"loan":3000,"monthly_repay":13.5,"dscr":1.35}}}},
"pros":["1","2","3"],"cons":["1","2"],"over_budget":false}}]

スコア: location({SCORING_WEIGHTS['location']}) yield({SCORING_WEIGHTS['yield_return']}) demand({SCORING_WEIGHTS['tenant_demand']}) future({SCORING_WEIGHTS['future_value']}) efficiency({SCORING_WEIGHTS['capital_eff']})
融資: A=新耐震築30年以内駅10分利回5%以上 B=旧耐震だが立地良好 C=現金推奨
予算(5000万)超過物件は over_budget:true。"""

def build_market_prompt(ward_counts,rent_data):
    return f"""東京都の城東7区（台東区・墨田区・江東区・荒川区・足立区・葛飾区・江戸川区）の不動産投資市況について、200字程度で概説してください。

取得データ件数:
{json.dumps(ward_counts,ensure_ascii=False,indent=2)}

以下の観点を含めてください:
- 城東エリア全体の地価動向
- エリア別の特徴（台東区=インバウンド、墨田区=スカイツリー圏、足立区=割安感、など）
- 投資家にとっての注目ポイント

回答はプレーンテキストのみ（JSON不要、マークダウン不要）。"""

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
    """分析結果の重複排除（URL + 価格+面積）"""
    seen_urls=set();seen_specs=set();deduped=[];rank=1
    for r in results:
        url=r.get("url","")
        spec=f"{r.get('price','')}_{r.get('size','')}_{r.get('built','')}"
        if url and url in seen_urls:
            log.info(f"  結果dedup(URL): rank{r.get('rank','')}");continue
        if spec in seen_specs:
            log.info(f"  結果dedup(spec): rank{r.get('rank','')} {spec}");continue
        seen_urls.add(url);seen_specs.add(spec)
        r["rank"]=rank;rank+=1
        deduped.append(r)
    return deduped

def analyze(client,properties,rent_data,category):
    if not properties:return []
    bmax=BUDGET["total_max"]//10000
    in_b=[p for p in properties if p["price"]<=bmax]
    over=sorted([p for p in properties if p["price"]>bmax],key=lambda x:x.get("yield_pct") or 0,reverse=True)
    sel=(in_b+over[:max(10-len(in_b),5)])[:30]
    log.info(f"  {category}: {len(sel)}件送信（予算内{len(in_b)}件）")
    try:
        msg=client.messages.create(model=CLAUDE_MODEL,max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role":"user","content":build_prompt(sel,rent_data,category)}])
        raw=msg.content[0].text
        log.info(f"  {category}: response {len(raw)} chars, starts: {raw[:100]!r}")
        results=extract_json(raw)
        if results is None:
            log.error(f"  {category}: JSON抽出失敗。先頭300文字: {raw[:300]}");return []
        um={p["url"]:p for p in sel}
        merged=[]
        for r in results:
            url=r.get("url","")
            base=um.get(url,{}).copy()
            base.update(r)
            merged.append(base)
        merged=dedup_results(merged)
        log.info(f"  {category}: {len(merged)}件（dedup後）")
        return merged[:10]
    except Exception as e:
        log.error(f"  {category}: {e}");return []

def generate_market_summary(client,ward_counts,rent_data):
    """市況概説を生成"""
    log.info("=== 市況概説を生成 ===")
    try:
        msg=client.messages.create(model=CLAUDE_MODEL,max_tokens=1000,
            messages=[{"role":"user","content":build_market_prompt(ward_counts,rent_data)}])
        summary=msg.content[0].text.strip()
        log.info(f"  市況概説: {len(summary)}文字")
        return summary
    except Exception as e:
        log.error(f"  市況概説エラー: {e}")
        return "城東7区は都心地価高騰の波及を受け商業地・住宅地ともに上昇基調。台東区・江東区が上昇率トップ。"

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
    # 市況概説
    market_summary=generate_market_summary(client,data["ward_counts"],data["rent_data"])
    # データ取得現況サマリー
    data_summary={}
    for ck in PROPERTY_CATEGORIES:
        cp=[p for p in data["properties"] if p["category"]==ck]
        in_budget=sum(1 for p in cp if p["price"]<=BUDGET["total_max"]//10000)
        data_summary[ck]={"total":len(cp),"in_budget":in_budget}

    out={"analyzed_at":data["scraped_at"],"results":results,"rent_data":data["rent_data"],
         "rent_by_category":data.get("rent_by_category",RENT_DATA_BY_CATEGORY),
         "ward_counts":data["ward_counts"],"budget":BUDGET,"loan_params":LOAN_PARAMS,
         "market_summary":market_summary,"data_summary":data_summary}
    op=os.path.join(DATA_DIR,"analysis.json")
    with open(op,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    total=sum(len(v) for v in results.values())
    log.info(f"=== 完了: 計{total}件 → {op} ===")

if __name__=="__main__":
    main()
