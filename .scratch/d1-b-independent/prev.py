import json, sqlite3, os
from PIL import Image
DB=os.path.expanduser("~/backseat-data/snapshots/2026-10-19/backseat.db")
con=sqlite3.connect(f"file:{DB}?mode=ro&immutable=1",uri=True); con.row_factory=sqlite3.Row
d=json.load(open("sample.json"))
out=[]
for i,r in enumerate(d["rows"]):
    prev=con.execute("select id,ts_end,content,obs_ids from messages where level='small' and id<? order by id desc limit 1",(r["msg_id"],)).fetchone()
    oids=json.loads(prev["obs_ids"] or "[]")
    refs=[con.execute("select id,ts_wall,ref from observations where id=?",(o,)).fetchone() for o in oids]
    refs=[x for x in refs if x and x["ref"] and os.path.exists(x["ref"])]
    if not refs: continue
    rep=refs[-1]
    tag=f"p{i+1:02d}_m{prev['id']}_o{rep['id']}"
    im=Image.open(rep["ref"]); im.thumbnail((420,420)); im.save(f"view/{tag}.jpg",quality=80)
    out.append({"i":i+1,"msg":r["msg_id"],"prev_msg":prev["id"],"prev_obs":rep["id"],"prev_content":prev["content"],"view":f"view/{tag}.jpg"})
json.dump(out,open("prev_plan.json","w"),ensure_ascii=False,indent=1)
print(len(out))
for p in out: print(p["i"],p["msg"],p["prev_msg"],p["prev_obs"],p["view"])
