import sqlite3, pickle, numpy as np
con=sqlite3.connect("earnings.db")
cur=con.cursor()
clf=pickle.load(open("bounce_model.pkl","rb"))
rows=cur.execute("SELECT rowid, drop_pct, mom_3m, vol_ratio FROM all_drops").fetchall()
X=[]
for r in rows:
  X.append([float(r[1]), float(r[2]), float(r[3])])
import numpy as np
X=np.nan_to_num(np.array(X), nan=0.0)
probs=clf.predict_proba(X)[:,1]
for (rid,_,_,_), p in zip(rows, probs):
  cur.execute("UPDATE all_drops SET bounce_prob=? WHERE rowid=?", (float(p), rid))
con.commit()
print(cur.execute("SELECT AVG(bounce_prob), COUNT(*) FROM all_drops").fetchone())
