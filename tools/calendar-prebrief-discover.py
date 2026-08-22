#!/usr/bin/env python3
"""EventKit catalog/selection helper; identifiers never reach stdout."""
from __future__ import annotations
import hashlib,json,os,stat,sys,time
from pathlib import Path
def secure_new(p:Path):
 if p.exists() or p.is_symlink(): raise RuntimeError()
 return os.fdopen(os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
def rows():
 from EventKit import EKEventStore
 s=EKEventStore.alloc().init(); ok={'v':None}; s.requestFullAccessToEventsWithCompletion_(lambda x,_:ok.__setitem__('v',bool(x)))
 for _ in range(100):
  if ok['v'] is not None: break
  time.sleep(.1)
 if ok['v'] is not True: raise RuntimeError()
 out=[]
 for c in s.calendarsForEntityType_(0) or []:
  i,t=str(c.calendarIdentifier()),str(c.title()); src=str(c.source().title()) if c.source() else ''
  out.append((t,src,i))
 return sorted(out,key=lambda x:(x[0],x[1],x[2]))
def main():
 try:
  cmd=sys.argv[1]; r=rows()
  catalog=[{'index':i,'title':t,'source':s,'calendar_key':hashlib.sha256(('calendar\0'+ident).encode()).hexdigest()} for i,(t,s,ident) in enumerate(r)]
  if cmd=='catalog' and len(sys.argv)==3:
   body={'version':1,'calendars':catalog}; body['catalog_digest']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
   with secure_new(Path(sys.argv[2])) as f: json.dump(body,f,separators=(',',':'))
  elif cmd=='allowlist' and len(sys.argv)>4:
   catalog_path=Path(sys.argv[3]); st=catalog_path.lstat()
   if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600: raise RuntimeError()
   saved=json.load(catalog_path.open(encoding='utf-8'))
   projection={'version':1,'calendars':catalog}
   digest=hashlib.sha256(json.dumps(projection,sort_keys=True,separators=(',',':')).encode()).hexdigest()
   if not isinstance(saved,dict) or saved.get('calendars')!=catalog or saved.get('catalog_digest')!=digest: raise RuntimeError()
   chosen=[int(x) for x in sys.argv[4:]]
   if not chosen or len(set(chosen))!=len(chosen) or any(x<0 or x>=len(r) for x in chosen): raise RuntimeError()
   with secure_new(Path(sys.argv[2])) as f: json.dump({'version':1,'calendars':[{'identifier':r[x][2],'sponsor':'joe'} for x in chosen]},f)
  else: return 64
  return 0
 except Exception:return 78
if __name__=='__main__':raise SystemExit(main())
