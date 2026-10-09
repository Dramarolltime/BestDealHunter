#!/usr/bin/env node
// Upload a local deal graphic to Cloudinary and emit its permanent HTTPS URL.
// Required: CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET.
// Usage: node scripts/upload-deal-image.cjs path/to/image.png
const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const file = process.argv[2];
if (!file || !fs.existsSync(file)) { console.error('Usage: node scripts/upload-deal-image.cjs image.png'); process.exit(2); }
const { CLOUDINARY_CLOUD_NAME: cloud, CLOUDINARY_API_KEY: key, CLOUDINARY_API_SECRET: secret } = process.env;
if (![cloud,key,secret].every(Boolean)) { console.error('Missing Cloudinary credentials in environment.'); process.exit(2); }
const bytes = fs.readFileSync(file);
if (bytes.length > 10*1024*1024) { console.error('Image exceeds 10 MB upload limit.'); process.exit(2); }
const ext = path.extname(file).toLowerCase();
const mime = ({'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'})[ext];
if (!mime) { console.error('Unsupported image extension.'); process.exit(2); }
const publicId = 'bestdealhunter/' + crypto.createHash('sha256').update(bytes).digest('hex').slice(0,24);
const timestamp = Math.floor(Date.now()/1000);
const params = 'overwrite=false&public_id='+publicId+'&timestamp='+timestamp;
const signature = crypto.createHash('sha1').update(params+secret).digest('hex');
const body = new FormData();
body.append('file',new Blob([bytes],{type:mime}),path.basename(file));
body.append('api_key',key);
body.append('timestamp',String(timestamp));
body.append('public_id',publicId);
body.append('overwrite','false');
body.append('signature',signature);
(async()=>{
 const res = await fetch('https://api.cloudinary.com/v1_1/'+encodeURIComponent(cloud)+'/image/upload',{method:'POST',body});
 const json = await res.json();
 if (!res.ok || !json.secure_url) throw new Error('Cloudinary upload failed: '+(json.error?.message||res.status));
 if (!json.secure_url.startsWith('https://res.cloudinary.com/')) throw new Error('Unexpected image host');
 const check = await fetch(json.secure_url,{method:'HEAD'});
 if (!check.ok || !(check.headers.get('content-type')||'').startsWith('image/')) throw new Error('Public image verification failed');
 console.log(json.secure_url);
})().catch(err=>{console.error(err.message);process.exitCode=1;});
