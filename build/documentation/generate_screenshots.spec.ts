/**
 * @fileoverview Automated UI documentation screenshot generator using Playwright.
 * Connects directly to the live running Astrometrics backend on port 5000,
 * overlays active running telescope telemetry onto the WebSocket feed,
 * suppresses transient notification toasts, scrolls/pitches the Planetarium sky view upward
 * with Cataloged unchecked and a star selected (opening PlanetariumInfoCard),
 * ensures Alcor is selected in Astronomy Manager, and persists publication-quality
 * screenshots into the asset directory.
 */

import { test, Page } from '@playwright/test';
import * as path from 'path';

const OUTPUT_DIR = path.join(process.cwd(), 'documentation', 'user_interface', 'user_guides', 'assets');

/**
 * Configure browser page with live backend connectivity and active telescope telemetry overlay.
 * @param page The Playwright Page instance.
 */
async function setupLiveBackend(page: Page) {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('backendUrl', 'http://127.0.0.1:5000');
    } catch {
      // Ignore storage errors
    }

    // Wrap native WebSocket to inject live running telescope telemetry onto the real stream
    const RealWebSocket = (window as any).WebSocket;
    class RunningTelescopeWebSocket extends RealWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);

        const emitRunningTelemetry = () => {
          const telemetryMsg = new MessageEvent('message', {
            data: JSON.stringify({
              type: 'UI_EVENT',
              action: 'system_state_update',
              payload: {
                telescope: {
                  ra: "16h 41m 42s",
                  dec: "+36° 27′ 40″",
                  altitude: "68.4",
                  azimuth: "142.1",
                  trackingStatus: "Tracking",
                  connectionStatus: "Connected",
                  temperature: "-10.0",
                  humidity: "34.0",
                  filter: "SPEC",
                  focuserPosition: 24500
                },
                health: {
                  resources: {
                    system_ram_usage_percent: 28.4,
                    vram_status: "Optimal"
                  },
                  indi: {
                    status: "Connected"
                  }
                }
              }
            })
          });
          this.dispatchEvent(telemetryMsg);
        };

        this.addEventListener('open', () => {
          setTimeout(emitRunningTelemetry, 150);
          setInterval(emitRunningTelemetry, 2000);
        });
      }
    }

    (window as any).WebSocket = RunningTelescopeWebSocket;
  });
}

/**
 * Helper to prepare page navigation and suppress transient toast banners.
 */
async function preparePage(page: Page, mode: string, extraQuery: string = '') {
  await page.addInitScript((m) => {
    try {
      localStorage.setItem('appMode', m);
    } catch {
      // Ignore
    }
  }, mode);

  const query = extraQuery ? `&${extraQuery}` : '';
  await page.goto(`http://localhost:5173/?mode=${encodeURIComponent(mode)}${query}`);
  await page.waitForSelector('.mode-selector__button', { state: 'visible', timeout: 15000 });

  // Ensure top header mode dropdown label displays current mode
  const modeBtn = page.locator('.mode-selector__button');
  const currentModeText = (await modeBtn.textContent().catch(() => '')) || '';
  if (!currentModeText.includes(mode)) {
    await modeBtn.click();
    const modeItem = page.locator(`.mode-selector__item:has-text("${mode}")`).first();
    if (await modeItem.isVisible({ timeout: 3000 }).catch(() => false)) {
      await modeItem.click();
      await page.waitForTimeout(500);
    }
  }

  // Forcefully suppress all notification toasts in captures
  await page.addStyleTag({
    content: `
      .toast-container, .toast, [role="alert"], .toast-message, .toast-success, .toast-error, .toast-info {
        display: none !important;
        opacity: 0 !important;
        visibility: hidden !important;
        pointer-events: none !important;
      }
    `
  });
}

test.describe('Documentation Screenshots Generator (Live Backend)', () => {
  test.beforeEach(async ({ page }) => {
    // 1920x1080 desktop documentation resolution
    await page.setViewportSize({ width: 1920, height: 1080 });
    await setupLiveBackend(page);
  });

  test('Capture screenshot for Image Viewer', async ({ page }) => {
    await preparePage(page, 'Image Viewer');

    // Select M 13 in the target list if available
    const m13Option = page.locator('.selectable-list__item:has-text("M 13"), label:has-text("M 13")').first();
    if (await m13Option.isVisible({ timeout: 3000 }).catch(() => false)) {
      await m13Option.click();
    } else {
      const firstTarget = page.locator('.selectable-list__item, label').first();
      if (await firstTarget.isVisible().catch(() => false)) {
        await firstTarget.click();
      }
    }

    await page.waitForTimeout(2000);
    const outputPath = path.join(OUTPUT_DIR, 'image_viewer.png');
    await page.screenshot({ path: outputPath, fullPage: true });
    console.log(`Saved screenshot: ${outputPath}`);
  });

  test('Capture screenshot for Astronomy Manager', async ({ page }) => {
    await preparePage(page, 'Astronomy Manager', 'star=Alcor');

    // Wait for AstronomyDisplay to mount and list to load
    await page.waitForSelector('.astronomy-display', { state: 'visible', timeout: 15000 });
    await page.waitForSelector('.astronomy-display .selectable-list__item', { state: 'visible', timeout: 15000 });

    // Type Alcor into the active Astronomy Manager search input
    const searchInput = page.locator('.astronomy-display input.entry, .astronomy-display input[placeholder*="name or ID"]').first();
    await searchInput.fill('Alcor');
    await page.waitForTimeout(1000);

    // Click on Alcor in the filtered list
    const alcorOption = page.locator('.astronomy-display .selectable-list__item:has-text("Alcor"), .astronomy-display label:has-text("Alcor")').first();
    await alcorOption.waitFor({ state: 'visible', timeout: 8000 });
    await alcorOption.click();

    // Wait for Plotly plots to mount and render spectra and photometry
    await page.waitForSelector('.astronomy-display .js-plotly-plot, .astronomy-display .plotly, .astronomy-display svg.main-svg', { timeout: 10000 }).catch(() => {});
    await page.waitForSelector('.star-summary-card__title:has-text("Alcor")', { timeout: 8000 }).catch(() => {});

    await page.waitForTimeout(3000);
    const outputPath = path.join(OUTPUT_DIR, 'astronomy_manager.png');
    await page.screenshot({ path: outputPath, fullPage: true });
    console.log(`Saved screenshot: ${outputPath}`);
  });

  test('Capture screenshot for Planetarium', async ({ page }) => {
    await preparePage(page, 'Planetarium');

    // Wait for the canvas and toolbar to appear
    await page.waitForSelector('canvas', { state: 'visible', timeout: 15000 });
    await page.waitForSelector('.planetarium-toolbar', { state: 'visible', timeout: 15000 });

    // Ensure "Cataloged" overlay toggle is unchecked
    const catalogedCheckbox = page.locator('label.planetarium-toolbar__item:has-text("Cataloged") input[type="checkbox"]');
    if (await catalogedCheckbox.isVisible({ timeout: 5000 }).catch(() => false)) {
      if (await catalogedCheckbox.isChecked()) {
        await catalogedCheckbox.uncheck();
      }
    }

    await page.waitForTimeout(800);

    // Zoom into field of view (~30°) using Ctrl+ArrowUp
    // Click canvas first to ensure it has focus
    const canvas = page.locator('canvas').first();
    const box = await canvas.boundingBox();
    if (box) {
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      await page.waitForTimeout(200);

      for (let i = 0; i < 7; i++) {
        await page.keyboard.press('Control+ArrowUp');
        await page.waitForTimeout(60);
      }

      // Pitch altitude upward into the celestial sphere away from the ground using mouse drag
      await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.4);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.9, { steps: 20 });
      await page.mouse.up();

      await page.waitForTimeout(1500);
    }

    // Click on a celestial star near the reticle/center to open the PlanetariumInfoCard
    if (box) {
      // Click at coordinates near center/reticle
      await page.mouse.click(box.x + box.width * 0.52, box.y + box.height * 0.48);
      await page.waitForTimeout(600);
      const infoCard = page.locator('.planetarium-info-card');
      if (!await infoCard.isVisible().catch(() => false)) {
        await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.52);
        await page.waitForTimeout(600);
      }
    }

    // Wait for info card to appear
    await page.waitForSelector('.planetarium-info-card', { state: 'visible', timeout: 8000 }).catch(() => {});

    await page.waitForTimeout(2000);
    const outputPath = path.join(OUTPUT_DIR, 'planetarium.png');
    await page.screenshot({ path: outputPath, fullPage: true });
    console.log(`Saved screenshot: ${outputPath}`);
  });

  test('Capture screenshot for Image Processing', async ({ page }) => {
    await preparePage(page, 'Image Processing');

    // Select M 13 in the target list
    const m13Option = page.locator('.selectable-list__item:has-text("M 13"), label:has-text("M 13")').first();
    if (await m13Option.isVisible({ timeout: 3000 }).catch(() => false)) {
      await m13Option.click();
    } else {
      const firstTarget = page.locator('.selectable-list__item, label').first();
      if (await firstTarget.isVisible().catch(() => false)) {
        await firstTarget.click();
      }
    }

    await page.waitForTimeout(2000);
    const outputPath = path.join(OUTPUT_DIR, 'image_processing.png');
    await page.screenshot({ path: outputPath, fullPage: true });
    console.log(`Saved screenshot: ${outputPath}`);
  });

  test('Capture screenshot for Observatory Manager', async ({ page }) => {
    await preparePage(page, 'Observatory Manager');

    // Select primary INDI device if present
    const deviceBtn = page.locator('.indi-devices__btn').first();
    if (await deviceBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await deviceBtn.click();
    }

    await page.waitForTimeout(2000);
    const outputPath = path.join(OUTPUT_DIR, 'observatory_manager.png');
    await page.screenshot({ path: outputPath, fullPage: true });
    console.log(`Saved screenshot: ${outputPath}`);
  });

  test('Capture screenshot for Observation Manager', async ({ page }) => {
    await preparePage(page, 'Observation Manager');

    const m13Option = page.locator('.selectable-list__item:has-text("M 13"), label:has-text("M 13")').first();
    if (await m13Option.isVisible({ timeout: 3000 }).catch(() => false)) {
      await m13Option.click();
    }

    await page.waitForTimeout(2000);
    const outputPath = path.join(OUTPUT_DIR, 'observation_manager.png');
    await page.screenshot({ path: outputPath, fullPage: true });
    console.log(`Saved screenshot: ${outputPath}`);
  });
});
