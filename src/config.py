"""設定 - 店舗特化版（実質利回り5%スクリーニング）"""

JOTO_WARDS = {
    "taito-ku":"台東区","sumida-ku":"墨田区","koto-ku":"江東区",
    "arakawa-ku":"荒川区","adachi-ku":"足立区",
    "bunkyo-ku":"文京区","ota-ku":"大田区",
    "edogawa-ku":"江戸川区",
}

PROPERTY_CATEGORIES = {
    "store":{"label":"売り店舗・事務所","kenbiya_path":"pp6","color":"#dc2626","icon":"🏪"},
}

def kenbiya_urls():
    urls = []
    for wk,wn in JOTO_WARDS.items():
        urls.append({"source":"kenbiya","category":"store","ward_key":wk,"ward":wn,
                     "url":f"https://www.kenbiya.com/pp6/s/tokyo/{wk}/",
                     "label":f"{wn} 売り店舗・事務所"})
    return urls

def suumo_rent_urls():
    base="https://suumo.jp/chintai/soba/tokyo/sc_"
    codes={"台東区":"taito","墨田区":"sumida","江東区":"koto","荒川区":"arakawa",
           "足立区":"adachi","文京区":"bunkyo","大田区":"ota","江戸川区":"edogawa"}
    return [{"ward":w,"url":f"{base}{c}/"} for w,c in codes.items()]

MIN_SIZE_SQM = 20
BUDGET={"self_fund_max":20000000,"loan_max":50000000,"total_max":70000000}
LOAN_PARAMS={"interest_rate":0.025,"term_years":25,"ltv_max":0.80,"dscr_min":1.20}

# スコアリング（100点満点）
SCORING_WEIGHTS={
    "location":25,       # 駅距離・視認性・1階か
    "net_yield":25,      # 推定実質利回り
    "tenant_demand":20,  # テナント需要
    "future_value":15,   # 将来性
    "capital_eff":15,    # 資金効率
}

CLAUDE_MODEL="claude-sonnet-4-6"
CLAUDE_MAX_TOKENS=8000
OUTPUT_DIR="output"
DATA_DIR="data"

# ═══════════════════════════════════════
# 実質利回り推定パラメータ
# ═══════════════════════════════════════

# 店舗の年間経費率（賃料に対する比率）
EXPENSE_RATIO = {
    "management_fee":    0.05,  # 管理費（賃料の5%）
    "repair_reserve":    0.05,  # 修繕積立金（賃料の5%）
    "property_tax_rate": 0.017, # 固定資産税+都市計画税（評価額の1.7%）
    "tax_assessment":    0.70,  # 固定資産税評価額 ≒ 物件価格の70%
    "vacancy_rate":      0.05,  # 空室率5%
    "insurance":         0.003, # 火災保険（物件価格の0.3%）
}
TOTAL_EXPENSE_RATIO = 0.22  # 簡易版: 賃料の22%が経費

# 取得諸経費
ACQUISITION_COST_RATIO = 0.075  # 物件価格の7.5%

# 指値率テーブル
NEGOTIATION_RATES = {
    "new_listing":      0.03,  # 新着物件 → 3%
    "normal":           0.05,  # 通常掲載 → 5%
    "long_listed":      0.08,  # 長期掲載（値下げなし） → 8%
    "price_reduced":    0.10,  # 値下げ済み → 10%
    "long_and_reduced": 0.12,  # 長期掲載＋値下げ済み → 12%
    "vacant_old":       0.15,  # 空室＋築古（35年以上） → 15%
}

# 実質利回り最低ライン
MIN_NET_YIELD = 5.0

# ソース評価ボーナス（健美家以外を高く評価）
SOURCE_BONUS = {
    "健美家／HOMES": 0,    # 基準（投資家向け高値付けが多い）
    "goo不動産":     5,    # +5点ボーナス
    "athome":        5,
    "楽待":          3,
    "default":       3,    # その他ソース
}

# 店舗賃料相場
RENT_DATA_BY_CATEGORY = {
    "store": {
        "label":"店舗・事務所 賃料相場（万円/坪・月）",
        "columns":["坪単価","20㎡参考","40㎡参考","60㎡参考"],
        "data":{
            "台東区":{"坪単価":2.2,"20㎡参考":"11〜14万","40㎡参考":"22〜28万","60㎡参考":"33〜42万"},
            "墨田区":{"坪単価":1.8,"20㎡参考":"9〜12万","40㎡参考":"18〜24万","60㎡参考":"27〜36万"},
            "江東区":{"坪単価":2.0,"20㎡参考":"10〜13万","40㎡参考":"20〜26万","60㎡参考":"30〜39万"},
            "荒川区":{"坪単価":1.5,"20㎡参考":"8〜10万","40㎡参考":"15〜20万","60㎡参考":"23〜30万"},
            "足立区":{"坪単価":1.2,"20㎡参考":"6〜8万","40㎡参考":"12〜16万","60㎡参考":"18〜24万"},
            "文京区":{"坪単価":2.5,"20㎡参考":"12〜16万","40㎡参考":"25〜32万","60㎡参考":"38〜48万"},
            "大田区":{"坪単価":1.6,"20㎡参考":"8〜10万","40㎡参考":"16〜21万","60㎡参考":"24〜32万"},
            "江戸川区":{"坪単価":1.1,"20㎡参考":"5〜7万","40㎡参考":"11〜15万","60㎡参考":"17〜22万"},
        }
    },
}
