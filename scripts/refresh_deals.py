#!/usr/bin/env python3
"""Validate approved retailer deal entries; never invent prices or discounts."""
import json, datetime, pathlib, urllib.parse
ROOT=pathlib.Path(__file__).resolve().parents[1]
source=ROOT/"deals-source.json"
data=json.loads(source.read_text())
allowed={"Amazon","Walmart","Target","Best Buy","eBay"}
out=[]
for item in data:
    if not isinstance(item,dict): continue
    try:
        store=item["store"]; title=item["title"]; url=item["url"]
        price=float(item["price"]); original=float(item["original_price"])
        verified=datetime.date.fromisoformat(item["verified_date"])
        parsed=urllib.parse.urlparse(url)
        if store not in allowed or not isinstance(title,str) or not title.strip(): continue
        if parsed.scheme!="https" or not parsed.hostname: continue
        if price<=0 or original<=price: continue
        if (original-price)/original < .5: continue
        if (datetime.date.today()-verified).days not in range(0,3): continue
        out.append({"store":store,"title":title.strip()[:180],"url":url,"price":round(price,2),"original_price":round(original,2),"discount_percent":round(100*(original-price)/original),"verified_date":str(verified),"category":str(item.get("category","Other"))[:40]})
    except (KeyError,TypeError,ValueError,OverflowError): continue
(ROOT/"deals.json").write_text(json.dumps(out,indent=2)+"\n")
print(f"Published {len(out)} verified, recent 50%+ deal entries; no products fabricated.")
