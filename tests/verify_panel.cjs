/* Headless interaction checks; outputs are kept in 2.0/.research. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.CUP_NODE_MODULES + '/playwright');
const root = path.resolve(__dirname, '..');
const fragment = fs.readFileSync(path.join(root, 'previews/demo.html'), 'utf8').replace(/const data = (.+);/, (_, serialized) => {
  const data=JSON.parse(serialized);
  data.daily_spending=[{id:'demo',day:data.today,amount:'1.25',state:'success',unit:'USD'}];
  return 'const data = '+JSON.stringify(data)+';';
});
const styles = `:root{color-scheme:dark;--background:#181b22;--foreground:#eef1f7;--card:#242933;--card-foreground:#eef1f7;--popover:#2e3540;--popover-foreground:#eef1f7;--muted-foreground:#aab4c5;--border:#394353;--viz-series-1:#85a8fa;--viz-series-2:#cb9bfc;--viz-series-3:#71c7b7;--viz-series-4:#e7b96b;--viz-series-5:#e79bc3;--viz-series-6:#8ccbe8}*{box-sizing:border-box}body{margin:0;padding:20px;background:var(--background);font:14px system-ui}.viz-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{padding:15px;border:1px solid var(--border);border-radius:12px;background:var(--card)}.viz-stat-value{font-size:28px;font-weight:600;margin:6px 0}.viz-controls,.viz-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.btn,.form-select{font:inherit;color:var(--foreground);background:var(--card);border:1px solid var(--border);border-radius:7px;padding:7px 10px}.btn-primary{background:var(--foreground);color:var(--background)}.btn-ghost{background:transparent}.form-label{display:flex;gap:7px;align-items:center}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:8px;border-bottom:1px solid var(--border);overflow-wrap:anywhere}.text-end{text-align:right}[hidden]{display:none!important}`;
async function run(){
  const browser = await chromium.launchPersistentContext(path.join(root,'.research','browser-profile'),{headless:true,channel:'chrome'});
  const errors=[];
  try {
    const page = await browser.newPage();
    page.on('pageerror', e=>errors.push(String(e)));
    for(const width of [736,320]){
      await page.setViewportSize({width,height:900});
      await page.setContent('<!doctype html><meta charset="utf-8"><style>'+styles+'</style>'+fragment);
      await page.waitForSelector('svg rect');
      assert.equal(await page.locator('[data-role="today"]').innerText(),'896,994');
      assert.equal(await page.locator('[data-role="total"]').innerText(),'3.59M');
      assert.equal(await page.locator('[data-role="demo"]').isVisible(),true);
      assert.match(await page.locator('[data-role="balances"]').innerText(),/账号今日消费 1\.25/);
      assert.match(await page.locator('[data-role="balances"]').innerText(),/包含其他软件调用/);
      await page.locator('[data-mode="cost"]').click();
      assert.equal(await page.locator('[data-role="axis-label"]').innerText(),'最近七天 · 费用估算');
      const hit=page.locator('[data-role="chart"] rect[aria-label]').last();
      await hit.hover();
      assert.equal(await page.locator('[data-role="tooltip"]').isVisible(),true);
      const tip=await page.locator('[data-role="tooltip"]').innerText();
      assert.match(tip,/模型 A/);assert.match(tip,/0\.15/);
      assert.ok((await page.locator('[data-role="tooltip"]').boundingBox()).width<=220);
      await page.locator('[data-role="view"]').click();
      assert.equal(await page.locator('[data-role="models"]').isVisible(),true);
      assert.equal(await page.locator('[data-role="tooltip"]').isVisible(),false);
      assert.equal(await page.locator('tbody tr').count(),3);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
      await page.locator('[data-role="view"]').click();
      await page.screenshot({path:path.join(root,'.research','panel-'+width+'.png'),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: 736/320px; Token M; amount toggle; stacked tooltip; model view; no overflow or JavaScript errors.');
  } finally {await browser.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
