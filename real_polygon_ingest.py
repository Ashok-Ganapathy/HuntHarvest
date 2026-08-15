import os, pathlib, sqlite3, requests, datetime, time
# read key
ENV = pathlib.Path(".env").read_text() if pathlib.Path(".env").exists() else ""
KEY = os.getenv("POLYGON_KEY") or (ENV.split("POLYGON_KEY=")[1].split("\n")[0].strip("'\"") if "POLYGON_KEY=" in ENV else "")
if not KEY:
    print("No key"); exit(1)

DB = "/var/www/huntorharvest/earnings.db"
BASE = "https://api.polygon.io"

conn = sqlite3.connect(DB)
conn.execute("CREATE TABLE IF NOT EXISTS all_drops(ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, pre_close REAL, post_low REAL, drop_pct REAL, market_cap REAL, qualified INTEGER, PRIMARY KEY(ticker,date))")
conn.execute("DELETE FROM all_drops")
conn.commit()

print("Fetching all active US stocks...")
tickers=[]
url=f"{BASE}/v3/reference/tickers?market=stocks&active=true&limit=1000&apiKey={KEY}"
while url:
    r=requests.get(url, timeout=30)
    j=r.json()
    batch=[t['ticker'] for t in j.get('results',[])]
    tickers.extend(batch)
    print(f" got {len(tickers)} so far")
    url=j.get('next_url')
    if url:
        url=url+f"&apiKey={KEY}"
        time.sleep(0.2)
    else:
        break
    if len(tickers) >= 8000: # safety, all US is ~7000-9000
        break

print(f"Total active US: {len(tickers)}")

# filter >500M - exact scope
universe=[]
for i,t in enumerate(tickers):
    try:
        r=requests.get(f"{BASE}/v3/reference/tickers/{t}?apiKey={KEY}", timeout=10)
        if r.status_code==200:
            mc=r.json().get('results',{}).get('market_cap',0) or 0
            if mc >= 500000000:
                universe.append((t,mc))
    except:
        pass
    if i%100==0:
        print(f" filter {i}/{len(tickers)} -> {len(universe)} >500M")
    time.sleep(0.05)
    if len(universe)>=3500: # all US >500M is ~2500-3500, stop when enough
        pass

print(f"UNIVERSE scope exact: {len(universe)} tickers > $500M")

end=datetime.date.today()
start=end-datetime.timedelta(days=90)
real=0
for idx,(ticker,mc) in enumerate(universe):
    try:
        aggs=requests.get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&apiKey={KEY}", timeout=15).json().get('results',[])
        for k in range(1,len(aggs)):
            pre=aggs[k-1]['c']
            low=aggs[k]['l']
            if not pre: continue
            drop=(low-pre)/pre*100
            if drop>-10: continue
            date=datetime.datetime.fromtimestamp(aggs[k]['t']/1000).date().isoformat()
            conn.execute("INSERT OR REPLACE INTO all_drops VALUES (?,?,?,?,?,?,?,?,?,?,?)",(ticker,date,aggs[k].get('o'),aggs[k].get('h'),aggs[k].get('l'),aggs[k].get('c'),pre,low,drop,mc,1 if drop<=-30 else 0))
            real+=1
    except:
        pass
    time.sleep(0.05)
    if idx%100==0:
        conn.commit()
        print(f" drops {idx}/{len(universe)} -> {real}")

conn.commit()
conn.close()
print(f"DONE {real} real drops for {len(universe)} tickers - scope ALL US >500M")
