/* Capture only the synthetic demo, never local account reports. */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.CUP_NODE_MODULES + '/playwright');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'docs', 'images');
const styles = `
:root{color-scheme:dark;--background:#181b22;--foreground:#eef1f7;--card:#242933;--card-foreground:#eef1f7;--popover:#2e3540;--popover-foreground:#eef1f7;--muted-foreground:#aab4c5;--border:#394353;--viz-series-1:#85a8fa;--viz-series-2:#cb9bfc;--viz-series-3:#71c7b7;--viz-series-4:#e7b96b;--viz-series-5:#e79bc3;--viz-series-6:#8ccbe8}
*{box-sizing:border-box}body{margin:0;padding:28px;background:var(--background);font:14px "Segoe UI","Microsoft YaHei UI",sans-serif}
.viz-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{padding:18px;border:1px solid var(--border);border-radius:12px;background:var(--card)}
.viz-stat-value{font-size:32px;font-weight:600;margin:6px 0}.viz-controls,.viz-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn,.form-select{font:inherit;color:var(--foreground);background:var(--card);border:1px solid var(--border);border-radius:7px;padding:7px 10px}.btn-primary{background:var(--foreground);color:var(--background)}.btn-ghost{background:transparent}
.form-label{display:flex;gap:7px;align-items:center}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:12px;border-bottom:1px solid var(--border);overflow-wrap:anywhere}.text-end{text-align:right}[hidden]{display:none!important}
`;

async function run() {
  fs.mkdirSync(output, {recursive: true});
  const fragment = fs.readFileSync(path.join(root, 'previews/demo.html'), 'utf8')
    .replace(/const data = (.+);/, (_, serialized) => {
      const data = JSON.parse(serialized);
      if (!data.demo) throw new Error('Only explicitly simulated data may be published.');
      data.daily_spending = [{id: 'demo', day: data.today, amount: '1.25', state: 'success', unit: 'USD'}];
      return 'const data = ' + JSON.stringify(data) + ';';
    });
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 960, height: 1000}, deviceScaleFactor: 2});
    await page.route('**/*', route => route.abort());
    await page.setContent('<!doctype html><meta charset="utf-8"><style>' + styles + '</style>' + fragment);
    await page.waitForSelector('svg rect');
    const panel = page.locator('#codex-usage-pet-panel');
    if (!(await page.locator('[data-role="demo"]').isVisible())) throw new Error('Demo label missing.');
    await panel.screenshot({path: path.join(output, 'panel-tokens.png')});
    await page.locator('[data-mode="cost"]').click();
    await page.locator('[data-role="chart"] rect[aria-label]').last().hover();
    if (!(await page.locator('[data-role="tooltip"]').isVisible())) throw new Error('Tooltip missing.');
    await panel.screenshot({path: path.join(output, 'panel-cost-tooltip.png')});
    await page.locator('[data-role="view"]').click();
    if (!(await page.locator('[data-role="models"]').isVisible())) throw new Error('Model table missing.');
    await panel.screenshot({path: path.join(output, 'panel-models.png')});
    console.log('Saved three simulated panel screenshots to docs/images.');
  } finally {
    await browser.close();
  }
}

run().catch(error => {console.error(error); process.exitCode = 1;});
