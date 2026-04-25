JOTO_WARDS = {"taito-ku":"台東区","sumida-ku":"墨田区","koto-ku":"江東区","arakawa-ku":"荒川区","adachi-ku":"足立区","katsushika-ku":"葛飾区","edogawa-ku":"江戸川区"}

# 表示順: マンション → 戸建て → 店舗
PROPERTY_CATEGORIES = {
    "condo":{"label":"区分マンション","kenbiya_path":"pp1","budget_max":50000000},
    "house":{"label":"戸建て","kenbiya_path":"pp3","budget_max":50000000},
    "store":{"label":"売り店舗・事務所","kenbiya_path":"pp6","budget_max":50000000},
}

def kenbiya_urls():
    urls = []
    for wk,wn in JOTO_WARDS.items():
        for ck,c in PROPERTY_CATEGORIES.items():
            urls.append({"source":"kenbiya","category":ck,"ward_key":wk,"ward":wn,
                         "url":f"https://www.kenbiya.com/{c['kenbiya_path']}/s/tokyo/{wk}/",
                         "label":f"{wn} {c['label']}"})
    return urls

def suumo_rent_urls():
    base="https://suumo.jp/chintai/soba/tokyo/sc_"
    codes={"台東区":"taito","墨田区":"sumida","江東区":"koto","荒川区":"arakawa",
           "足立区":"adachi","葛飾区":"katsushika","江戸川区":"edogawa"}
    return [{"ward":w,"url":f"{base}{c}/"} for w,c in codes.items()]

BUDGET={"self_fund_max":20000000,"loan_max":30000000,"total_max":50000000}
LOAN_PARAMS={"interest_rate":0.025,"term_years":25,"ltv_max":0.80,"dscr_min":1.20}
SCORING_WEIGHTS={"location":30,"yield_return":20,"tenant_demand":20,"future_value":15,"capital_eff":15}
CLAUDE_MODEL="claude-sonnet-4-6"
CLAUDE_MAX_TOKENS=8000
OUTPUT_DIR="output"
DATA_DIR="data"

# カテゴリ別の賃料相場（フォールバック）
RENT_DATA_BY_CATEGORY = {
    "condo": {
        "label": "区分マンション賃料相場（万円/月）",
        "columns": ["1R","1K","1DK","1LDK","2LDK","3LDK"],
        "data": {
            "台東区":{"1R":9.5,"1K":10.2,"1DK":11.5,"1LDK":14.8,"2LDK":19.5,"3LDK":24.0},
            "墨田区":{"1R":8.8,"1K":9.5,"1DK":10.5,"1LDK":13.5,"2LDK":17.0,"3LDK":21.0},
            "江東区":{"1R":9.2,"1K":10.0,"1DK":11.0,"1LDK":14.0,"2LDK":18.5,"3LDK":23.0},
            "荒川区":{"1R":7.8,"1K":8.5,"1DK":9.5,"1LDK":12.0,"2LDK":15.0,"3LDK":19.0},
            "足立区":{"1R":6.5,"1K":7.2,"1DK":8.0,"1LDK":10.0,"2LDK":12.5,"3LDK":15.0},
            "葛飾区":{"1R":6.3,"1K":7.0,"1DK":7.8,"1LDK":9.8,"2LDK":12.0,"3LDK":14.5},
            "江戸川区":{"1R":6.5,"1K":7.0,"1DK":7.8,"1LDK":10.0,"2LDK":12.0,"3LDK":14.5},
        }
    },
    "house": {
        "label": "戸建て賃料相場（万円/月）",
        "columns": ["2LDK","3LDK","4LDK"],
        "data": {
            "台東区":{"2LDK":22.0,"3LDK":28.0,"4LDK":35.0},
            "墨田区":{"2LDK":18.0,"3LDK":23.0,"4LDK":28.0},
            "江東区":{"2LDK":20.0,"3LDK":25.0,"4LDK":30.0},
            "荒川区":{"2LDK":16.0,"3LDK":20.0,"4LDK":25.0},
            "足立区":{"2LDK":12.0,"3LDK":15.0,"4LDK":18.0},
            "葛飾区":{"2LDK":11.5,"3LDK":14.0,"4LDK":17.0},
            "江戸川区":{"2LDK":12.0,"3LDK":14.5,"4LDK":17.5},
        }
    },
    "store": {
        "label": "店舗・事務所賃料相場（万円/坪）",
        "columns": ["坪単価","20㎡","40㎡","60㎡"],
        "data": {
            "台東区":{"坪単価":2.2,"20㎡":"11〜14","40㎡":"22〜28","60㎡":"33〜42"},
            "墨田区":{"坪単価":1.8,"20㎡":"9〜12","40㎡":"18〜24","60㎡":"27〜36"},
            "江東区":{"坪単価":2.0,"20㎡":"10〜13","40㎡":"20〜26","60㎡":"30〜39"},
            "荒川区":{"坪単価":1.5,"20㎡":"8〜10","40㎡":"15〜20","60㎡":"23〜30"},
            "足立区":{"坪単価":1.2,"20㎡":"6〜8","40㎡":"12〜16","60㎡":"18〜24"},
            "葛飾区":{"坪単価":1.1,"20㎡":"5〜7","40㎡":"11〜15","60㎡":"17〜22"},
            "江戸川区":{"坪単価":1.1,"20㎡":"5〜7","40㎡":"11〜15","60㎡":"17〜22"},
        }
    },
}
