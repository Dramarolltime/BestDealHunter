import json, os, re, textwrap
from datetime import date
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
DEALS=ROOT/"deals.json"
OUT=ROOT/"images"/"generated"
OUT.mkdir(parents=True,exist_ok=True)
deals=json.loads(DEALS.read_text())
font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
regular_path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
bold=lambda size:ImageFont.truetype(font_path,size)
regular=lambda size:ImageFont.truetype(regular_path,size)
changed=False
for d in deals:
    if d.get("image") or not d.get("title") or not d.get("url"): continue
    if d.get("expires_date") and d["expires_date"]<date.today().isoformat():continue
    original=float(d["original_price"]); price=float(d["price"])
    if original<=price or price<=0:continue
    pct=int((original-price)/original*100)
    slug=re.sub(r"[^a-z0-9]+","-",d["title"].lower()).strip("-")[:72]
    filename=f"{slug}.png"
    im=Image.new("RGB",(1080,1080),(14,25,48))
    draw=ImageDraw.Draw(im)
    draw.rounded_rectangle((44,42,1036,1038),radius=48,fill=(22,42,73))
    draw.text((88,86),"BESTDEALHUNTER",font=bold(49),fill=(117,241,195))
    draw.rounded_rectangle((88,196,535,326),radius=28,fill=(251,191,36))
    draw.text((119,224),f"{pct}% OFF",font=bold(73),fill=(20,28,48))
    draw.text((88,383),d["store"].upper(),font=bold(35),fill=(166,198,221))
    title=d["title"]
    lines=[]
    for word in title.split():
        if not lines or draw.textbbox((0,0),lines[-1]+" "+word,font=bold(49))[2]<=880:
            if not lines:lines.append(word)
            else:lines[-1]+=" "+word
        else:lines.append(word)
    for i,line in enumerate(lines[:4]):
        draw.text((88,459+i*67),line,font=bold(49),fill="white")
    draw.text((88,790),f"${price:,.2f}",font=bold(92),fill=(117,241,195))
    draw.text((90,918),f"Was ${original:,.2f}  |  Check price at retailer",font=regular(32),fill=(210,224,240))
    im.save(OUT/filename,optimize=True)
    d["image"]=f"images/generated/{filename}"
    changed=True
if changed:
    DEALS.write_text(json.dumps(deals,indent=2)+"\n")
print(f"Generated images; updated deal feed: {changed}")
