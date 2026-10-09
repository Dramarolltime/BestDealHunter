#!/usr/bin/env node
// Fail-closed preparation of approved, current deal graphics for publishing.
// No checkout claims are inferred from a retailer's advertised price.
const fs=require('node:fs'),{execFileSync}=require('node:child_process');
const path=require('node:path');
const pending=JSON.parse(fs.readFileSync('pending_deals.json','utf8'));
const live=JSON.parse(fs.readFileSync('deals.json','utf8'));
const today=new Date().toISOString().slice(0,10);
const eligible=pending.filter(d=>d.publication_eligible===true && d.status==='verified' &&
d.checkout_verified===true && d.verified_date===today &&
d.image && /^images\/[\w./-]+\.(png|jpg|jpeg|webp)$/i.test(d.image) && !d.image.includes('..') &&
fs.existsSync(d.image) && typeof d.checkout_evidence==='string' && d.checkout_evidence.length>20 &&
typeof d.expiration_evidence==='string' && d.expiration_evidence.length>10 &&
Number(d.price)>0 && Number(d.original_price)>=Number(d.price)*2 &&
!live.some(x=>x.url===d.url));
if(!eligible.length){console.log('No checkout-verified, image-ready, nonduplicate deals. No publishing.');process.exit(0)}
// Output candidates only. Separate verified publisher must explicitly handle external posting.
fs.writeFileSync('approved-deals.json',JSON.stringify(eligible,null,2)+'\n');
console.log('Approved '+eligible.length+' deal(s) for hosting and publication staging.');
