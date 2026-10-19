import { createRequire } from 'node:module';import { pathToFileURL } from 'node:url';import fs from 'node:fs';
const require=createRequire(process.env.NODE_PATH+'/');const {chromium}=require('playwright');
const [, , f, out]=process.argv;const b=await chromium.launch();const p=await b.newPage({viewport:{width:1920,height:1080}});
p.on('pageerror',e=>console.error('[err]',e.message));await p.goto(pathToFileURL(f).href+'?render');
const c=await p.evaluate(()=>window.__cues);fs.writeFileSync(out,JSON.stringify(c,null,1));console.log('cues',Object.keys(c).join(','),c.type.length,'frappes',c.pop.length,'pops',c.coins.length,'pièces');await b.close();
