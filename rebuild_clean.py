import os, sqlite3, time, requests
from datetime import datetime

DB="earnings.db"
API_KEY=""
with open(".env") as f:
    for line in f:
        if "POLYGON" in line and "=" in line:
            API_KEY=line.split("=",1)[1].strip().strip('"').strip("'")
            break
print(f"[{datetime.now()}] Key {API_KEY[:6]}... len={len(API_KEY)}", flush=True)
if len(API_KEY)<10:
    print("ERROR no key"); exit(1)

def poly(url, params=None):
    params=params or {}; params["apiKey"]=API_KEY
    for _ in range(5):
        try:
            r=requests.get(url, params=params, timeout=30)
            if r.status_code==429:
                print("429 sleep 15", flush=True); time.sleep(15); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"poly err {e} sleep5", flush=True); time.sleep(5)
    return {}

con=sqlite3.connect(DB); cur=con.cursor()
cur.execute("DROP TABLE IF EXISTS all_drops_new")
cur.execute("""CREATE TABLE all_drops_new (
 ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
 pre_close REAL, post_low REAL, drop_pct REAL, vol_ratio REAL,
 market_cap REAL, mom_3m REAL, surprise REAL, sector TEXT, cluster INT,
 bounce_prob REAL, pnl_10d REAL, qualified INT)""")
con.commit()

universe=[r[0] for r in cur.execute("SELECT DISTINCT ticker FROM all_drops").fetchall()]
print(f"Universe {len(universe)}", flush=True)
sector_cache={}
def get_sector(ticker):
    if ticker in sector_cache: return sector_cache[ticker]
    try:
        j=poly(f"https://api.polygon.io/v3/reference/tickers/{ticker}")
        res=j.get("results",{})
        sec=res.get("sic_description") or res.get("sector") or "Unknown"
        sector_cache[ticker]=sec; time.sleep(0.12); return sec
    except:
        sector_cache[ticker]="Unknown"; return "Unknown"

total=0
for idx,ticker in enumerate(universe,1):
    try:
        data=poly(f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/2023-08-01/2025-08-13", {"adjusted":"true","limit":50000,"sort":"asc"})
        bars=data.get("results") or []
        if len(bars)<80:
            if idx%100==0: print(f"{idx}/{len(universe)} {ticker} len {len(bars)} skip", flush=True)
            continue
        bars=sorted(bars, key=lambda x: x['t'])
        ins=0
        for i in range(63, len(bars)-11):
            pc=bars[i-1]['c'];
            if not pc: continue
            curr=bars[i]; drop=(curr['c']-pc)/pc*100
            if drop>-10: continue
            mom_base=bars[i-63]['c']; mom_3m=(pc-mom_base)/mom_base*100 if mom_base else 0.0
            avg_vol=sum(b['v'] for b in bars[i-20:i])/20; vol_ratio=curr['v']/avg_vol if avg_vol else 1.0
            future=bars[i+10]['c']; post_low=min(b['l'] for b in bars[i+1:i+11])
            pnl_10d=(future-curr['c'])/curr['c']*100 if curr['c'] else 0.0
            dt=datetime.fromtimestamp(curr['t']/1000).strftime('%Y-%m-%d')
            sec=get_sector(ticker) if ins==0 else sector_cache.get(ticker,"Unknown")
            cur.execute("INSERT INTO all_drops_new VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, dt, curr['o'], curr['h'], curr['l'], curr['c'], pc, post_low, drop, vol_ratio, 0, mom_3m, 0.0, sec, 0, 0.0, pnl_10d, 1))
            ins+=1
        if ins>0:
            con.commit(); total+=ins
        if idx%25==0 or ins>0:
            print(f"[{idx}/{len(universe)}] {ticker} +{ins} total={total}", flush=True)
        time.sleep(0.15)
    except Exception as e:
        print(f"{ticker} ex {e}", flush=True); continue

print(f"Done {total}", flush=True)
print(cur.execute("SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM all_drops_new").fetchone(), flush=True)
cur.execute("DROP TABLE IF EXISTS all_drops_old")
cur.execute("ALTER TABLE all_drops RENAME TO all_drops_old")
cur.execute("ALTER TABLE all_drops_new RENAME TO all_drops")
con.commit()
print("SWAPPED FINAL", cur.execute("SELECT COUNT(*) FROM all_drops").fetchone(), flush=True)
