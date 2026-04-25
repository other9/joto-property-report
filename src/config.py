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
