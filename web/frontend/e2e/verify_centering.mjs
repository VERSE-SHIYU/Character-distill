import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  // Open the standalone verification page
  await page.goto('file:///' + __dirname.replace(/\\/g, '/') + '/verify_textarea_centering.html');
  await page.waitForLoadState('networkidle');

  // Screenshot full page
  await page.screenshot({ path: __dirname + '/screenshots/textarea-centering.png', fullPage: true });

  // Check computed styles of the NEW desktop textarea (single line)
  const desktopNew = await page.evaluate(() => {
    const ta = document.querySelectorAll('.chat-textarea')[0];
    const cs = getComputedStyle(ta);
    return {
      'min-height': cs.minHeight,
      'padding-top': cs.paddingTop,
      'padding-bottom': cs.paddingBottom,
      'line-height': cs.lineHeight,
      'field-h': ta.style.getPropertyValue('--field-h') || cs.getPropertyValue('--field-h'),
      'field-line': ta.style.getPropertyValue('--field-line') || cs.getPropertyValue('--field-line'),
    };
  });
  console.log('Desktop new:', JSON.stringify(desktopNew, null, 2));

  // Check computed styles of the OLD desktop
  const desktopOld = await page.evaluate(() => {
    const ta = document.querySelector('.old-desktop');
    const cs = getComputedStyle(ta);
    return {
      'min-height': cs.minHeight,
      'padding-top': cs.paddingTop,
      'padding-bottom': cs.paddingBottom,
      'line-height': cs.lineHeight,
    };
  });
  console.log('Desktop old:', JSON.stringify(desktopOld, null, 2));

  // The key verification: top padding should equal bottom padding
  const desktopNewPadding = await page.evaluate(() => {
    const ta = document.querySelectorAll('.chat-textarea')[0];
    const cs = getComputedStyle(ta);
    return {
      topPx: parseFloat(cs.paddingTop),
      bottomPx: parseFloat(cs.paddingBottom),
    };
  });
  const diff = Math.abs(desktopNewPadding.topPx - desktopNewPadding.bottomPx);
  console.log(`Desktop padding diff: ${diff.toFixed(2)}px (${diff < 0.5 ? '✓ SYMMETRIC' : '✗ ASYMMETRIC'})`);

  // Apply mobile override and check
  const mobilePadding = await page.evaluate(() => {
    const ta = document.querySelector('.chat-textarea.mobile');
    ta.style.setProperty('--field-h', '36px');
    const cs = getComputedStyle(ta);
    return {
      'min-height': cs.minHeight,
      'padding-top': parseFloat(cs.paddingTop),
      'padding-bottom': parseFloat(cs.paddingBottom),
      topPx: parseFloat(cs.paddingTop),
      bottomPx: parseFloat(cs.paddingBottom),
    };
  });
  const mobileDiff = Math.abs(mobilePadding.topPx - mobilePadding.bottomPx);
  console.log(`Mobile padding diff: ${mobileDiff.toFixed(2)}px (${mobileDiff < 0.5 ? '✓ SYMMETRIC' : '✗ ASYMMETRIC'})`);

  await browser.close();
})();
