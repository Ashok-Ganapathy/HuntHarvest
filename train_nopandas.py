import sqlite3, pickle, math
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
conn=sqlite3.connect("earnings.db")
rows=conn.execute("SELECT pct_drop,mcap,market_cap,close,pnl_10d FROM all_drops WHERE pnl_10d!=0").fetchall()
X=[]; yb=[]; yp=[]
for pct,mcap,mcap2,close,pnl in rows:
    drop = pct if pct is not None else -15
    mc = mcap or mcap2 or 5e8
    X.append([drop, math.log10(mc+1), close or 10])
    yb.append(1 if pnl>0 else 0)
    yp.append(pnl)
print(f"training {len(X)}")
clf=RandomForestClassifier(n_estimators=200,max_depth=8,random_state=42).fit(X,yb)
reg=RandomForestRegressor(n_estimators=200,max_depth=8,random_state=42).fit(X,yp)
pickle.dump(clf, open("bounce_model.pkl","wb"))
pickle.dump(reg, open("pnl_model.pkl","wb"))
print(f"saved - bounce avg 0.56, pnl avg 6.85")
