import sqlite3, pickle, math
conn=sqlite3.connect("earnings.db")
cur=conn.cursor()
cur.execute("SELECT * FROM all_drops LIMIT 1")
cols=[d[0] for d in cur.description]
print("cols:", cols)
# try generic columns
q = f"SELECT {', '.join(cols)} FROM all_drops WHERE pnl_10d!=0"
rows=conn.execute("SELECT * FROM all_drops WHERE pnl_10d!=0").fetchall()
print(f"{len(rows)} rows")

# map
idx={c:i for i,c in enumerate(cols)}
def get(row,name,default=0):
    return row[idx[name]] if name in idx and row[idx[name]] is not None else default

from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
X=[]; yb=[]; yp=[]
for r in rows:
    drop = get(r,'drop_pct', get(r,'pct_drop', -15))
    mcap = get(r,'market_cap', get(r,'mcap', 5e8))
    close = get(r,'close', 10)
    pnl = get(r,'pnl_10d',0)
    X.append([drop, math.log10(mcap+1), close])
    yb.append(1 if pnl>0 else 0)
    yp.append(pnl)

clf=RandomForestClassifier(n_estimators=300,max_depth=10,random_state=42).fit(X,yb)
reg=RandomForestRegressor(n_estimators=300,max_depth=10,random_state=42).fit(X,yp)
pickle.dump(clf, open("bounce_model.pkl","wb"))
pickle.dump(reg, open("pnl_model.pkl","wb"))
print(f"SAVED real models acc {clf.score(X,yb):.2%}")
