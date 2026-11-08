const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = 'E:/AI-Market-Workbench';
process.env.TEMP = root+'/tmp'; process.env.TMP = root+'/tmp';
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args:['--disable-background-networking','--disable-component-update','--no-first-run']});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.url().includes('/v1/ai-analysis-tasks')&&r.request().method()==='POST')requests.push({url:r.url(),status:r.status()})});
  try {
    await page.goto('http://127.0.0.1:8080/',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#chart svg [data-bar-id]')||document.querySelector('#status').className==='bad',null,{timeout:120000});
    if(!await page.locator('#chart svg [data-bar-id]').count())throw Error(await page.locator('#status').innerText());
    const suffix=process.argv.includes('--subject-eth')?'-eth':'';
    if(suffix){
      await page.selectOption('#subject','crypto-eth');await page.locator('#load').click();
      await page.waitForFunction(()=>current?.subject==='crypto-eth'&&document.querySelector('#chart svg [data-bar-id]'),null,{timeout:120000});
    }
    const audit=await page.evaluate(()=>({
      status:document.querySelector('#status').textContent,modelMode:document.querySelector('#ai-mode').value,
      candleCount:document.querySelectorAll('[data-bar-id]').length,
      fvgIds:[...document.querySelectorAll('[data-fvg-id]')].map(e=>e.getAttribute('data-fvg-id')),
      divergenceIds:[...document.querySelectorAll('[data-divergence-id]')].map(e=>e.getAttribute('data-divergence-id')),
      sources:document.querySelector('#sources').textContent,
      evidenceMatches:[...document.querySelectorAll('[data-fvg-id]')].every(e=>current.bundle.fvg.some(f=>f.fvg_id===e.getAttribute('data-fvg-id'))),
      candleMatches:[...document.querySelectorAll('[data-bar-id]')].every(e=>current.bars.bars.some(b=>b.bar_id===e.getAttribute('data-bar-id')))
    }));
    await page.screenshot({path:root+`/reports/browser-market${suffix}.png`,fullPage:true});
    if(process.argv.includes('--click-ai')){
      await page.locator('#analyze').click();
      await page.waitForFunction(()=>!document.querySelector('#analyze').disabled,null,{timeout:420000});
      audit.aiStatus=await page.locator('#status').innerText();
      audit.aiText=await page.locator('#analysis').innerText();
      audit.aiSucceeded=audit.aiStatus==='真实 AI 回答与证据已记录';
      await page.screenshot({path:root+'/reports/browser-real-ai.png',fullPage:true});
    }
    audit.checkedAt=new Date().toISOString();audit.errors=errors;audit.taskPosts=requests;
    fs.writeFileSync(root+`/reports/browser-acceptance${suffix}.json`,JSON.stringify(audit,null,2));
    console.log(JSON.stringify({...audit,aiText:undefined,sources:undefined}));
    if(errors.length||audit.aiSucceeded===false)process.exitCode=1;
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
