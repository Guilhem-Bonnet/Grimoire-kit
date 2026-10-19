import { createRequire } from 'node:module';import { pathToFileURL } from 'node:url';
const require=createRequire(process.env.NODE_PATH+'/');const {chromium}=require('playwright');
const [, , f, outdir, ...times]=process.argv;
const b=await chromium.launch({args:['--enable-gpu','--use-angle=vulkan','--ignore-gpu-blocklist','--allow-file-access-from-files']});const p=await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
p.on('console',m=>{if(m.type()==='error')console.error('[page]',m.text())});p.on('pageerror',e=>console.error('[err]',e.message));
await p.goto(pathToFileURL(f).href+'?render');await p.evaluate(()=>document.fonts.ready);
for(const t of times){await p.evaluate(ms=>window.__seek(ms),+t*1000);await p.screenshot({path:`${outdir}/f-${t}.png`})}
await b.close();console.log('ok');
