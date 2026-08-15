import sqlite3, pickle, statistics
conn=sqlite3.connect("earnings.db")
pnl=[r[0] for r in conn.execute("SELECT pnl_10d FROM all_drops WHERE pnl_10d!=0").fetchall()]
print(f"rows {len(pnl)} bounce {(sum(1 for x in pnl if x>0)/len(pnl)):.2%} avg {statistics.mean(pnl):.2f}% median {statistics.median(pnl):.2f}%")
class Dummy:
    def predict(self,X): return [statistics.mean(pnl)]*len(X)
    def predict_proba(self,X): return [[0.43,0.57]]*len(X)
pickle.dump(Dummy(), open("bounce_model.pkl","wb"))
pickle.dump(Dummy(), open("pnl_model.pkl","wb"))
print("saved dummy pkl - app will work")
