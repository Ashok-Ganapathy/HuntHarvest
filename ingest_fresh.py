
import os, sqlite3, requests, datetime, time, pathlib, json
POLYGON_KEY = os.getenv("POLYGON_KEY","74DMSl0HQK1PSLhQ4YVPW2sVq9HIgJ9I")
DB_PATH = pathlib.Path(__file__).parent/"earnings.db"

def init_db():
    conn=sqlite3.connect(DB_PATH)
    cur=conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS all_drops(
        ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        pre_close REAL, post_low REAL, drop_pct REAL, vol_ratio REAL,
        market_cap REAL, mom_3m REAL, surprise REAL, sector TEXT,
        cluster INTEGER, bounce_prob REAL, pnl_10d REAL, qualified INTEGER
    )""")
    cur.execute("CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT)")
    defaults={
        "market_cap_min":"500000000","drop_min":"-30",
        "sell_pct1":"50","sell_target1":"10","sell_pct2":"50","sell_target2":"20",
        "hold_days":"20","clusters":"5","theme":"dark"
    }
    for k,v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO config VALUES (?,?)",(k,v))
    conn.commit(); conn.close()

def fetch_polygon_earnings():
    # Fresh build - gets tickers with earnings drops > -10% last 5 years
    # Placeholder logic - full implementation uses Polygon /v3/reference/tickers + /v2/aggs
    # For demo, creates sample 200 rows structure that real ingestor will populate
    print(f"Building fresh DB with Polygon key {POLYGON_KEY[:6]}...")
    conn=sqlite3.connect(DB_PATH)
    cur=conn.cursor()
    cur.execute("DELETE FROM all_drops")
    # sample to show schema - real daily job will replace with real Polygon pulls
    import random
    sectors=["Technology","Healthcare","Consumer","Energy","Financials"]
    for i in range(5698):
        ticker=f"TKR{i%500}"
        drop = -random.uniform(5,75)
        mcap = random.uniform(1e8, 5e10)
        vol = random.uniform(0.5, 8)
        cur.execute("INSERT INTO all_drops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
            ticker, (datetime.date.today()-datetime.timedelta(days=random.randint(0,1800))).isoformat(),
            100,110,90,95,100, 90, drop, vol, mcap, random.uniform(-20,20), random.uniform(-5,5),
            random.choice(sectors), random.randint(0,4), random.uniform(0.1,0.9), random.uniform(-5,25),
            1 if (mcap>500_000_000 and drop < -30) else 0
        ))
    conn.commit(); conn.close()
    print("Fresh DB built: 5698 rows sample - replace with real Polygon aggregator in production")

if __name__=="__main__":
    init_db()
    fetch_polygon_earnings()
