#!/usr/bin/env python3
import json,subprocess
symbols=["XAUUSD_Zero_Spread","XAGUSD_Zero_Spread","EURUSD_Zero_Spread","GBPUSD_Zero_Spread","USDJPY_Zero_Spread","BTCUSD_Zero_Spread"]
out={}
for s in symbols:
 u=f"https://ticks.ex2archive.com/ticks/{s}/2026/09/Exness_{s}_2026_09.zip"
 p=subprocess.run(["curl","-sSIL","--max-time","20",u],capture_output=True,text=True)
 out[s]={"ok":p.returncode==0 and (" 200 " in p.stdout or "HTTP/2 200" in p.stdout),"url":u,"tail":p.stdout[-500:]}
print(json.dumps(out,indent=2))
open("/tmp/symbols.json","w").write(json.dumps(out,indent=2))
