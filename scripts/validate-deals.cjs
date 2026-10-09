#!/usr/bin/env node
// Fail closed: validate listings before they reach the public feed.
const fs=require('node:fs');
const deals=JSON.parse(fs.readFileSync('deals.json','utf8'));
if(!Array.isArray(deals)) throw Error('deals.json must be an array');
const seen=new Set(),errors=[];
const allowed=['macys.com','bestbuy.com','amazon.com','walmart.com','target.com','ebay.com','homedepot.com','lowes.com'];
for(const [i,d] of deals.entries()){
 const label='deal '+(i+1);
 const fail=s=>errors.push(label+': '+s);
 if(!d.title||!d.store)fail('missing title/store');
 let url;try{url=new URL(d.url);if(url.protocol!=='https:')fail('URL must use HTTPS')}catch{fail('invalid product URL')}
 if(url){const host=url.hostname.toLowerCase().replace(/^www\./,'');if(!allowed.some(h=>host===h||host.endsWith('.'+h))&&!d.affiliate_disclosure)fail('unrecognized retailer/link domain requires affiliate disclosure');const key=host+url.pathname.toLowerCase()+url.searchParams.get('ID');if(seen.has(key))fail('duplicate product URL');seen.add(key)}
 if(!Number.isFinite(d.price)||!Number.isFinite(d.original_price)||d.price<=0||d.original_price<=d.price)fail('invalid prices');
 else if((1-d.price/d.original_price)*100<50 && d.section!=='affiliate' && d.affiliate_section!==true)fail('discount below 50%');
 if(!/^\d{4}-\d{2}-\d{2}$/.test(d.verified_date||''))fail('missing verified_date');
 if(!d.source||!d.price_evidence)fail('missing source or price_evidence');
 if(d.promo_code&&!d.promo_note)fail('promo_code requires promo_note with terms');
 if(d.image && !(/^images\/[\w./-]+\.(jpg|jpeg|png|webp)$/i.test(d.image)||/^https:\/\//.test(d.image)))fail('invalid image path');
}
if(errors.length){console.error(errors.join('\n'));process.exitCode=1}else console.log('Validated '+deals.length+' deals; publication evidence still requires human or retailer checkout verification.');
