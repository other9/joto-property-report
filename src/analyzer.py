"""Claude API分析 + 総括コメント生成"""
import json,os,re,logging
import anthropic
from config import BUDGET,LOAN_PARAMS,SCORING_WEIGHTS,CLAUDE_MODEL,CLAUDE_MAX_TOKENS,DATA_DIR,PROPERTY_CATEGORIES,RENT_DATA_BY_CATEGORY

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)

def build_prompt(properties,rent_data,category):
    cl=PROPERTY_CATEGORIES[category]["label"]
    cat_rent=RENT_DATA_BY_CATEGORY.get(category,{}).get("data",rent_data)
    return f"""あなたは東京の不動産投資アナリストです。対象8区（台東・墨田・江東・荒川・足立・文京・大田・江戸川）の{cl}を分析してください。

## 投資条件
自己資金上限:{BUDGET['self_fund_max']//10000}万 融資上限:{BUDGET['loan_max']//10000}万 総予算:{BUDGET['total_max']//10000}万
金利:{LOAN_PARAMS['interest_rate']*100}% 期間:{LOAN_PARAMS['term_years']}年 LTV:{LOAN_PARAMS['ltv_max']*100}% DSCR下限:{LOAN_PARAMS['dscr_min']}

## 物件データ（重複排除済み）
{json.dumps(properties,ensure_ascii=False,indent=2)}

## {cl}の賃料相場
{json.dumps(cat_rent,ensure_ascii=False,indent=2)}

## 出力ルール
1. JSON配列のみ出力。前置き・バッククォート不要。[ で始まり ] で終わる
2. 全10件が異なる物件。urlは元データからコピー
3. lat/lngがfallbackの物件はlat_estimated/lng_estimatedに推定座標

[{{"url":"元URL","rank":1,"score":92,
"score_breakdown":{{"location":28,"yield_return":18,"tenant_demand":19,"future_value":14,"capital_eff":13}},
"tenant_type":"想定テナント3-4業種",
"estimated_rent":"想定月額賃料",
"rent_reference":"参考賃貸物件・坪単価・相場整合性",
"analysis":"150字以内の分析",
"loan_analysis":{{"feasibility":"A/B/C","reason":"80字以内",
"recommended_plan":{{"self_fund":2000,"loan":5000,"monthly_repay":22.0,"dscr":1.35}}}},
"lat_estimated":35.7126,"lng_estimated":139.7800,
"pros":["1","2","3"],"cons":["1","2"],"over_budget":false}}]

スコア: location({SCORING_WEIGHTS['location']}) yield({SCORING_WEIGHTS['yield_return']}) demand({SCORING_WEIGHTS['tenant_demand']}) future({SCORING_WEIGHTS['future_value']}) efficiency({SCORING_WEIGHTS['capital_eff']})

## カテゴリ別の追加評価基準
- 区分マンション: 物件全体の総戸数が多い大規模マンション（50戸以上）はlocationとfuture_valueで加点。管理組合が安定し修繕積立金が潤沢な傾向があるため。100戸以上は更に高評価。
- 店舗・事務所: 1階（路面店）はlocationとtenant_demandで大幅加点（+5〜8点）。視認性・集客力が圧倒的に高い。2階以上・地下は減点。

融資: A=新耐震築30年以内駅10分利回5%以上 B=旧耐震だが立地良好 C=現金推奨
予算超過物件は over_budget:true。"""

def build_editorial_prompt(all_results, data_summary, rent_data):
    """総括コメント生成プロンプト"""
    summary_lines=[]
    for ck,ct in PROPERTY_CATEGORIES.items():
        rs=all_results.get(ck,[])
        ds=data_summary.get(ck,{})
        if rs:
            top=rs[0]
            summary_lines.append(f"【{ct['label']}】取得{ds.get('total',0)}件（予算内{ds.get('in_budget',0)}件）。1位: {top.get('title','')[:30]} {top.get('price',0)}万円 利回り{top.get('yield_pct','不明')}%")
        else:
            summary_lines.append(f"【{ct['label']}】取得{ds.get('total',0)}件（予算内{ds.get('in_budget',0)}件）。分析対象なし。")
    return f"""あなたは不動産投資アドバイザーです。以下の分析結果を踏まえ、投資家向けの総括コメントを300字以内で書いてください。

{chr(10).join(summary_lines)}

以下の観点を含めてください:
- 今週の注目物件（1-2件）とその理由
- カテゴリ横断での投資戦略（ポートフォリオ提案）
- 区分マンションは大規模物件（総戸数50戸以上）の有無に言及
- 店舗は1階路面店の有無に言及
- 対象エリア（台東・墨田・江東・荒川・足立・文京・大田・江戸川区）の市況感
- 投資家へのアクションアドバイス

プレーンテキストのみ（JSON・マークダウン不要）。簡潔かつ具体的に。"""

def build_market_prompt(ward_counts,rent_data):
    return f"""東京都の対象8区（台東区・墨田区・江東区・荒川区・足立区・文京区・大田区・江戸川区）の不動産投資市況を200字で概説。
取得データ: {json.dumps(ward_counts,ensure_ascii=False)}
地価動向、各区の特徴（文京区=教育・住環境、大田区=羽田空港・町工場再開発）、投資家の注目点を含めて。プレーンテキストのみ。"""

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
        log.info(f"  {category}: {len(raw)} chars")
        results=extract_json(raw)
        if results is None:
            log.error(f"  {category}: JSON失敗: {raw[:300]}");return []
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
        log.info(f"  {category}: {len(merged)}件")
        return merged[:10]
    except Exception as e:
        log.error(f"  {category}: {e}");return []

def generate_text(client,prompt,label):
    log.info(f"=== {label} ===")
    try:
        msg=client.messages.create(model=CLAUDE_MODEL,max_tokens=1000,
            messages=[{"role":"user","content":prompt}])
        txt=msg.content[0].text.strip()
        log.info(f"  {label}: {len(txt)}文字")
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

    # カテゴリ別分析
    results={}
    for ck in PROPERTY_CATEGORIES:
        cp=[p for p in data["properties"] if p["category"]==ck]
        log.info(f"--- {PROPERTY_CATEGORIES[ck]['label']}: {len(cp)}件 ---")
        results[ck]=analyze(client,cp,data["rent_data"],ck)

    # データサマリー
    data_summary={}
    for ck in PROPERTY_CATEGORIES:
        cp=[p for p in data["properties"] if p["category"]==ck]
        data_summary[ck]={"total":len(cp),"in_budget":sum(1 for p in cp if p["price"]<=BUDGET["total_max"]//10000)}

    # 市況概説
    market_summary=generate_text(client,
        build_market_prompt(data["ward_counts"],data["rent_data"]),"市況概説")

    # ★ 総括コメント（冒頭に表示）
    editorial=generate_text(client,
        build_editorial_prompt(results,data_summary,data["rent_data"]),"総括コメント")

    out={"analyzed_at":data["scraped_at"],"results":results,"rent_data":data["rent_data"],
         "rent_by_category":data.get("rent_by_category",RENT_DATA_BY_CATEGORY),
         "ward_counts":data["ward_counts"],"budget":BUDGET,"loan_params":LOAN_PARAMS,
         "market_summary":market_summary,"data_summary":data_summary,
         "editorial":editorial}
    op=os.path.join(DATA_DIR,"analysis.json")
    with open(op,"w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    total=sum(len(v) for v in results.values())
    log.info(f"=== 完了: 計{total}件 ===")

if __name__=="__main__":
    main()
