// End-to-end browser rehearsal of the DEMO.md workspace script.
//
// Runs only against the disposable demo workspace, never milaan.db:
//   ./stop_milaan_demo.sh
//   rm -f demo/sarcathon_demo.db demo/sarcathon_agent.db
//   ./start_milaan_demo.sh --demo-db
//   NODE_PATH=whatsapp_service/node_modules node scripts/demo_e2e.js http://127.0.0.1:8501 . /tmp/milaan-shots
//
// Uses the Puppeteer copy installed with whatsapp_service. Every send is a
// simulated (mock) delivery; no WhatsApp message is sent.
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const [base, repo, shots] = process.argv.slice(2);
const report = [];
const errors = [];
let failed = 0;
const check = (name, ok, detail) => { report.push({ name, ok: !!ok, detail: ok ? undefined : detail }); if (!ok) failed++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1536, height: 1024 });
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('dialog', (d) => { errors.push('dialog: ' + d.message()); d.dismiss(); });
  const state = () => page.evaluate(() => fetch('/api/state').then((r) => r.json()));
  const toasts = () => page.$$eval('#toasts .toast', (els) => els.map((e) => e.textContent));
  const go = async (v) => { await page.evaluate((x) => setView(x), v); await sleep(300); };
  const upload = async (kind, file) => {
    await page.evaluate((k) => openUploadModal(k), kind);
    const input = await page.$('#modal-file-input');
    await input.uploadFile(file);
    await sleep(1200);
    const notice = await page.$eval('#modal-preview-box', (e) => e.innerText);
    await page.click('#modal-upload-btn');
    await sleep(1500);
    return notice;
  };

  await page.goto(base + '/#dashboard', { waitUntil: 'networkidle0' });
  await sleep(600);
  const s0 = await state();
  check('demo workspace starts empty', s0.invoices.length === 0 && s0.gstr2b.imports.length === 0, s0.invoices.length);
  if (!(s0.database.demo === true && /sarcathon_demo\.db$/.test(s0.database.path))) {
    console.error('Refusing to run: the server is not using demo/sarcathon_demo.db.');
    process.exit(3);
  }
  check('demo database is used and labelled', true);
  check('header badge says Demo Database', (await page.$eval('#mode-text', (e) => e.innerText)) === 'Demo Database');

  // A1 invoices
  await go('invoices');
  let n = await upload('invoices', path.join(repo, 'demo/demo_purchase_invoices.csv'));
  check('A1 preview 5 of 6 valid', /5 of 6 row/.test(n), n);
  const s1 = await state();
  check('A1 5 invoices imported, DEMO-106 rejected', s1.invoices.length === 5 && !s1.invoices.some((i) => i.number === 'DEMO-106'));

  // A2 GSTR-2B JSON
  await go('gstr2b');
  n = await upload('gstr2b', path.join(repo, 'demo/demo_gstr2b_portal.json'));
  check('A2 preview all 5 valid', /All 5 row/.test(n), n);
  const s2 = await state();
  check('A2 json batch 5 accepted', s2.gstr2b.imports[0].format === 'json' && s2.gstr2b.imports[0].accepted === 5, s2.gstr2b.imports[0]);

  // A3 reconcile
  await go('reconciliation');
  await page.evaluate(() => executeReconcile());
  await sleep(2000);
  const s3 = await state();
  const r = Object.fromEntries(s3.reconciliation.results.map((x) => [x.number, x]));
  check('A3 three matched with zero differences', ['DEMO-101', 'DEMO-102', 'DEMO-104'].every((k) => r[k].status === 'MATCHED' && r[k].difference === 0 && r[k].taxable_difference === 0));
  check('A3 DEMO-103 mismatch ₹300', r['DEMO-103'].status === 'AMOUNT_MISMATCH' && r['DEMO-103'].difference === 300);
  check('A3 DEMO-105 missing, no invented values', r['DEMO-105'].status === 'MISSING_FROM_2B' && r['DEMO-105'].twob_gst === null);
  check('A3 DEMO-999 in 2B only', r['DEMO-999'] && r['DEMO-999'].status === 'IN_2B_NOT_IN_BOOKS');
  const rowsShown = await page.$$eval('table.data-table tbody tr', (t) => t.length);
  check('A3 table shows 6 results', rowsShown === 6, rowsShown);
  await page.screenshot({ path: `${shots}/demo-reconciliation.png` });

  // A4 drafts + number
  await go('followups');
  await page.evaluate(() => executeGenerateFollowups());
  await sleep(1200);
  let s4 = await state();
  const d = s4.followups[0];
  check('A4 one draft, Needs review (no number)', s4.followups.length === 1 && d.status === 'NEEDS_REVIEW' && d.workflow_status === 'NEEDS_REVIEW', s4.followups);
  await page.evaluate((id) => openDraftModal(id), d.id);
  await sleep(700);
  await page.type('#draft-phone', '+91 90000 12345');
  await page.evaluate((id) => saveDraftPhone(id), d.id);
  await sleep(900);
  s4 = await state();
  check('A4 number saved -> Pending, masked', s4.followups[0].status === 'DRAFT' && s4.followups[0].workflow_status === 'PENDING' && !/90000 12345/.test(JSON.stringify(s4)), s4.followups[0]);

  // A5 approve + simulate
  await page.click('#draft-approve-check');
  await page.evaluate((id) => approveActiveDraft(id), d.id);
  await sleep(1000);
  check('A5 approved -> In progress', (await state()).followups[0].workflow_status === 'IN_PROGRESS');
  const modalText = await page.$eval('#draft-modal-content', (e) => e.innerText);
  // The modal must state the delivery mode truthfully, which depends on how
  // this machine is configured: asserting "off" unconditionally would demand
  // a false statement whenever the WhatsApp Web bridge is live. Either way a
  // simulated send must be offered and nothing may be sent without the two
  // confirmations below.
  const prov = (await state()).provider || {};
  const expected = prov.webjs_live
    ? /Live WhatsApp Web sending is enabled/
    : prov.cloud
      ? /WhatsApp Cloud API delivery is configured/
      : /Live WhatsApp delivery is off/;
  check(
    `A5 modal states the delivery mode (${prov.webjs_live ? 'webjs live' : prov.cloud ? 'cloud' : 'off'})`,
    expected.test(modalText) && /simulat/i.test(modalText),
    modalText.slice(0, 300)
  );
  await page.click('#send-optin'); await page.click('#send-confirm');
  await page.evaluate((id) => sendDraft(id, 'mock'), d.id);
  await sleep(1000);
  const det = await page.evaluate((id) => fetch(`/api/followups/${id}`).then((x) => x.json()), d.id);
  check('A5 one mock delivery only', det.deliveries.length === 1 && det.deliveries[0].provider === 'mock', det.deliveries);
  check('A5 simulation does not resolve', (await state()).followups[0].workflow_status === 'IN_PROGRESS');
  await page.evaluate(() => closeModal());

  // A6 next cycle
  await go('gstr2b');
  n = await upload('gstr2b', path.join(repo, 'demo/demo_gstr2b_portal_next.json'));
  const s6 = await state();
  const nextBatch = s6.gstr2b.imports[0];
  check('A6 preview offers Check required import', /Check required/.test(n), n);
  check('A6 next statement imported, row Check required', nextBatch.total === 1 && s6.gstr2b.rows.find((x) => x.batch === nextBatch.id).validation === 'needs_review', nextBatch);

  // A7 verify -> Resolved
  await go('followups');
  await page.evaluate((id) => openDraftModal(id), d.id);
  await sleep(700);
  await page.select('#verify-batch', String(nextBatch.id));
  await page.evaluate((id) => verifyDraft(id), d.id);
  await sleep(1200);
  const s7 = await state();
  check('A7 recovery VERIFIED -> Resolved', s7.followups[0].recovery_status === 'VERIFIED' && s7.followups[0].workflow_status === 'RESOLVED', s7.followups[0]);
  await page.evaluate(() => closeModal());
  await go('followups');
  const status = await page.$eval('table.data-table tbody tr td:nth-child(9)', (e) => e.innerText.trim());
  check('A7 table shows Resolved', status === 'Resolved', status);

  // Dashboard consistency + all six pages render
  for (const v of ['dashboard', 'invoices', 'gstr2b', 'reconciliation', 'followups', 'settings']) {
    await go(v);
    await sleep(400);
    await page.screenshot({ path: `${shots}/demo-${v}.png` });
  }
  await go('dashboard');
  const dash = await page.$eval('#main-content', (e) => e.innerText);
  check('dashboard shows recovered ITC', /₹576 recovered/.test(dash), dash.slice(0, 400));
  check('dashboard eligible ITC equals sum of valid invoice GST', /₹5,508/.test(dash) && /Across 5 invoices/.test(dash), dash.slice(0, 300));

  const unexpected = errors.filter((e) => !/status of 400/.test(e));
  check('no unexpected console errors', unexpected.length === 0, unexpected);
  console.log(JSON.stringify({ failed, report }, null, 1));
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
