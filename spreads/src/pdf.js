// HTML -> PDF via headless Chromium. Page setup matches the Apps Script export:
// letter, portrait, quarter-inch margins, no gridlines or sheet names.
import { chromium } from 'playwright-core';

export async function htmlToPdf(html) {
  const launchOpts = {};
  if (process.env.CHROMIUM_PATH) launchOpts.executablePath = process.env.CHROMIUM_PATH;
  const browser = await chromium.launch(launchOpts);
  try {
    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'load' });
    return await page.pdf({
      format: 'Letter',
      landscape: false,
      printBackground: true,
      margin: { top: '0.25in', bottom: '0.25in', left: '0.25in', right: '0.25in' }
    });
  } finally {
    await browser.close();
  }
}
