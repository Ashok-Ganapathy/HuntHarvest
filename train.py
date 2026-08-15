import sqlite3, pickle, pathlib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

DB="earnings.db"
conn=sqlite3.connect(DB)
df=pd.read_sql("SELECT * FROM all_drops WHERE pnl_10d IS NOT NULL AND pnl_10d!= 0", conn)
print(f"DF {len(df)} cols={list(df.columns)}")

# pick features that exist
cands=["pct_drop","drop_pct","mcap","market_cap","close","volume","avg_volume"]
feats=[c for c in cands if c in df.columns]
if not feats:
    # fallback: use pct_drop if only that, else dummy
    df["pct_drop"] = df.get("pct_drop", -10)
    feats=["pct_drop"]

print(f"features {feats}")
X=df[feats].fillna(0)
y_bounce=(df["pnl_10d"]>0).astype(int)
y_pnl=df["pnl_10d"]

Xtr,Xte,yb_tr,yb_te,yp_tr,yp_te = train_test_split(X,y_bounce,y_pnl,test_size=0.2,random_state=42)

clf=RandomForestClassifier(n_estimators=200,max_depth=8,random_state=42).fit(Xtr,yb_tr)
reg=RandomForestRegressor(n_estimators=200,max_depth=8,random_state=42).fit(Xtr,yp_tr)

print("BOUNCE TEST:")
print(classification_report(yb_te, clf.predict(Xte)))
print(f"PNL R2 train {reg.score(Xtr,yp_tr):.3f} test {reg.score(Xte,yp_te):.3f}")

pickle.dump(clf, open("bounce_model.pkl","wb"))
pickle.dump(reg, open("pnl_model.pkl","wb"))
print("saved bounce_model.pkl, pnl_model.pkl")

# predict today unlabeled 7
df7=pd.read_sql("SELECT * FROM all_drops WHERE pnl_10d=0", conn)
if len(df7):
    X7=df7[feats].fillna(0)
    df7["pred_bounce"]=clf.predict_proba(X7)[:,1]
    df7["pred_pnl"]=reg.predict(X7)
    print(df7[["ticker","date","pred_bounce","pred_pnl"]].to_string())
