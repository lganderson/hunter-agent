/** Capture real UI screenshots using only the disposable fictional demo server. */
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const origin = 'http://127.0.0.1:4176';
const server = spawn('python3', ['scripts/demo_preview.py', '--port', '4176'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
let browser;
try {
  for (let attempt = 0; ; attempt++) {
    if (server.exitCode !== null) throw new Error(serverLog);
    // Wait for this process to announce its successful bind before probing.
    if (serverLog.includes('Fictional demo:')) {
      try { if ((await fetch(`${origin}/api/health`)).ok) break; } catch {}
    }
    if (attempt >= 100) throw new Error(`Demo did not start: ${serverLog}`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  await mkdir(`${root}/docs/screenshots`, { recursive: true });
  for (const [name, path] of [['dashboard', '/'], ['discovery', '/candidates?mode=discovery'], ['postings', '/postings']]) {
    await page.goto(origin + path);
    await page.getByRole('heading', { level: 1 }).waitFor();
    await page.waitForLoadState('networkidle');
    if (await page.locator('vite-error-overlay').count()) throw new Error('Framework error overlay');
    await page.screenshot({ path: `${root}/docs/screenshots/${name}.png`, animations: 'disabled' });
    console.log(`Captured docs/screenshots/${name}.png`);
  }
  if (errors.length) throw new Error(errors.join('\n'));
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
  await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit', resolve));
}
