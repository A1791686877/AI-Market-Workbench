const fs=require('node:fs');
const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root='E:/AI-Market-Workbench';process.env.TEMP=root+'/tmp';process.env.TMP=root+'/tmp';
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args:['--disable-background-networking','--disable-component-update','--no-first-run']});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],taskPosts=[],checks=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('ai-analysis-tasks'))taskPosts.push(r.url())});
  try {
    await page.goto('http://127.0.0.1:8080/');
    await page.waitForFunction(()=>typeof current!=='undefined'&&current&&document.querySelector('#status').textContent.includes('已加载'),null,{timeout:120000});
    for(const subject of ['crypto-btc','crypto-eth']) for(const leg of ['SPOT','PERP']){
      await page.selectOption('#subject',subject);await page.selectOption('#leg',leg);
      await page.locator('#load').click();
      await page.waitForFunction(({subject,leg})=>current?.subject===subject&&current.bars.instrument.instrument_id.endsWith(leg)&&document.querySelector('#status').textContent.includes('已加载'),{subject,leg},{timeout:120000});
      checks.push(await page.evaluate(()=>{
        const b=current.bundle,bars=current.bars.bars;const fvgs=[...document.querySelectorAll('[data-fvg-id]')],divs=[...document.querySelectorAll('[data-divergence-id]')];
        return {subject:current.subject,instrument:current.bars.instrument.instrument_id,barCount:document.querySelectorAll('[data-bar-id]').length,
          candlesMatch:[...document.querySelectorAll('[data-bar-id]')].every(e=>bars.some(b=>b.bar_id===e.dataset.barId)),
          fvgCount:fvgs.length,fvgMatch:fvgs.every(e=>{const f=b.fvg.find(f=>f.fvg_id===e.dataset.fvgId),text=e.textContent;return f&&text.includes(f.lower+'–'+f.upper)&&text.includes(f.fill_status+'/'+f.validity_status)&&text.includes('ATR='+f.atr_at_creation)}),
          divergenceCount:divs.length,divergenceMatch:divs.every(e=>{const d=b.divergences.find(d=>d.divergence_id===e.dataset.divergenceId);return d&&e.textContent.includes(d.signal_at)&&Date.parse(d.signal_at)<=Date.parse(bars.at(-1).close_at)}),
          vpvrMatch:[...document.querySelectorAll('[data-level]')].filter(e=>['POC','VAH','VAL'].includes(e.dataset.level)).every(e=>e.textContent.includes(b.vpvr[e.dataset.level.toLowerCase()])),
          quality:current.bars.quality};
      }));
    }
    await page.getByRole('button',{name:'仅核对七周期快照（不调用模型）',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#analysis').textContent.includes('公开市场上下文')||document.querySelector('#analysis .bad'),null,{timeout:180000});
    const previewText=await page.locator('#analysis').innerText();
    await page.screenshot({path:root+'/reports/quality-browser.png',fullPage:true});
    const previewPassed=await page.locator('#analysis .bad').count()===0&&(previewText.match(/\/ AVAILABLE \//g)||[]).length===14;
    const passed=previewPassed&&!errors.length&&!taskPosts.length&&checks.every(c=>c.barCount>0&&c.candlesMatch&&c.fvgMatch&&c.divergenceMatch&&c.vpvrMatch);
    fs.writeFileSync(root+'/reports/quality-browser.json',JSON.stringify({checkedAt:new Date().toISOString(),passed,checks,errors,taskPosts,previewText},null,2));
    console.log(JSON.stringify({passed,checks:checks.map(c=>({instrument:c.instrument,fvgCount:c.fvgCount,divergenceCount:c.divergenceCount})),errors,modelTaskPosts:taskPosts.length}));
    if(!passed)process.exitCode=1;
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
