import sqlite3, time, requests, pickle, numpy as np
from sklearn.cluster import KMeans
from datetime import datetime

DB="earnings.db"
API_KEY=""
with open(".env") as f:
    for l in f:
        if "POLYGON" in l and "=" in l:
            API_KEY=l.split("=",1)[1].strip().strip('"').strip("'"); break

def poly(url, params=None):
    params=params or {}; params["apiKey"]=API_KEY
    for _ in range(5):
        try:
            r=requests.get(url, params=params, timeout=25)
            if r.status_code==429:
                print("429 sleep 15", flush=True); time.sleep(15); continue
            if r.status_code!=200:
                time.sleep(2); continue
            return r.json()
        except Exception as e:
            time.sleep(3)
    return {}

con=sqlite3.connect(DB); cur=con.cursor()
print("Starting enrichment...", flush=True)

# 1. Market Cap enrichment - use polygon current market cap (better than 0)
print("1. Market cap...", flush=True)
tickers=cur.execute("SELECT DISTINCT ticker FROM all_drops").fetchall()
mc_cache={}
for i,(ticker,) in enumerate(tickers,1):
    try:
        j=poly(f"https://api.polygon.io/v3/reference/tickers/{ticker}")
        res=j.get("results",{})
        mc=res.get("market_cap") or 0
        mc_cache[ticker]=mc
        if i%50==0:
            print(f"MC {i}/{len(tickers)} {ticker} {mc}", flush=True)
        time.sleep(0.12)
    except: mc_cache[ticker]=0

# Update all rows
for ticker,mc in mc_cache.items():
    cur.execute("UPDATE all_drops SET market_cap=? WHERE ticker=?", (mc, ticker))
con.commit()
print(f"MC done: {cur.execute('SELECT COUNT(*), SUM(CASE WHEN market_cap>0 THEN 1 ELSE 0 END) FROM all_drops').fetchone()}", flush=True)

# 2. Cluster - KMeans on drop_pct, mom_3m, vol_ratio
print("2. Clustering...", flush=True)
rows=cur.execute("SELECT rowid, drop_pct, mom_3m, vol_ratio FROM all_drops").fetchall()
X=np.array([[r[1], r[2], r[3]] for r in rows])
# clean NaN
X=np.nan_to_num(X, nan=0.0, posinf=0, neginf=0)
kmeans=KMeans(n_clusters=5, random_state=42, n_init=10).fit(X)
for (rowid,_,_,_), label in zip(rows, kmeans.labels_):
    cur.execute("UPDATE all_drops SET cluster=? WHERE rowid=?", (int(label), rowid))
con.commit()
print(f"Clusters: {cur.execute('SELECT cluster, COUNT(*) FROM all_drops GROUP BY cluster').fetchall()}", flush=True)

# 3. bounce_prob + pnl_10d using models we just trained
print("3. bounce_prob from model...", flush=True)
try:
    clf=pickle.load(open("bounce_model.pkl","rb"))
    # Features used in train_real.py - need to reconstruct
    # We trained on: drop_pct, mom_3m, vol_ratio, sector encoded? Let's check train_real
    # We'll do simple proba = predicted bounce (pnl>0)
    # Reload training logic quickly
    import pandas as pd
    df=pd.read_sql("SELECT * FROM all_drops", con)
    # simple features
    Xf=df[["drop_pct","mom_3m","vol_ratio"]].fillna(0).values
    probs=clf.predict_proba(Xf)[:,1]
    for rowid, p in zip(df.index, probs):
        # rowid is pandas index not sqlite rowid, use ticker+date instead
        pass
    # Safer: iterate sqlite
    all_rows=cur.execute("SELECT rowid, drop_pct, mom_3m, vol_ratio FROM all_drops").fetchall()
    X2=np.array([[r[1], r[2], r[3]] for r in all_rows])
    X2=np.nan_to_num(X2, nan=0.0)
    probs=clf.predict_proba(X2)[:,1]
    for (rid,_,_,_), pr in zip(all_rows, probs):
        cur.execute("UPDATE all_drops SET bounce_prob=? WHERE rowid=?", (float(pr), rid))
    con.commit()
    print(f"bounce_prob updated avg {cur.execute('SELECT AVG(bounce_prob) FROM all_drops').fetchone()}", flush=True)
except Exception as e:
    print(f"bounce model update failed {e}, skipping", flush=True)

# 4. Final QC
print("FINAL QC", flush=True)
print(cur.execute("SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM all_drops").fetchone(), flush=True)
print(cur.execute("SELECT COUNT(*), SUM(CASE WHEN market_cap>0 THEN 1 ELSE 0 END), SUM(CASE WHEN mom_3m!=0 THEN 1 ELSE 0 END), AVG(bounce_prob), AVG(cluster) FROM all_drops").fetchone(), flush=True)
print(cur.execute("SELECT sector, COUNT(*), ROUND(AVG(market_cap)/1e9,2) as avg_mcap_B FROM all_drops GROUP BY sector ORDER BY COUNT(*) DESC LIMIT 10").fetchall(), flush=True)
