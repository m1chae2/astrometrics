/**
 * @fileoverview Electron Forge configuration for packaging and distribution.
 */

import path from 'path';
import fs from 'fs';
import os from 'os';
import { execFileSync } from 'child_process';

// __dirname replacement for ESM (now at project root)
const __dirname = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'));

// The app's freedesktop identity. The AppStream ID, the desktop file name, and
// the window's Wayland app_id (electron/platforms/linux.js) must all use it.
const APP_ID = 'io.github.m1chae2.Astrometrics';
const DESKTOP_FILE_NAME = `${APP_ID}.desktop`;

/**
 * Fixes up a built .deb package so it carries the app's freedesktop identity.
 *
 * electron-installer-debian (the engine behind @electron-forge/maker-deb) has
 * no config option for shipping arbitrary extra files, and it names the
 * desktop file after the package name ("astrometrics.desktop"). It has no
 * setting for a different name. So this function unpacks the .deb it
 * produced, then:
 *   - renames /usr/share/applications/astrometrics.desktop to
 *     io.github.m1chae2.Astrometrics.desktop
 *   - adds the AppStream metainfo file under /usr/share/metainfo/
 * and repacks the .deb in place with dpkg-deb.
 *
 * @param {string} debPath Absolute path to the built .deb artifact.
 */
function applyFreedesktopIdentity(debPath) {
  const metainfoFileName = `${APP_ID}.metainfo.xml`;
  const metainfoSource = path.join(__dirname, 'assets', 'metainfo', metainfoFileName);
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-deb-'));
  try {
    execFileSync('dpkg-deb', ['-R', debPath, workDir]);
    const applicationsDir = path.join(workDir, 'usr', 'share', 'applications');
    fs.renameSync(
      path.join(applicationsDir, 'astrometrics.desktop'),
      path.join(applicationsDir, DESKTOP_FILE_NAME)
    );
    const metainfoDestDir = path.join(workDir, 'usr', 'share', 'metainfo');
    fs.mkdirSync(metainfoDestDir, { recursive: true });
    fs.copyFileSync(metainfoSource, path.join(metainfoDestDir, metainfoFileName));
    execFileSync('dpkg-deb', ['-b', workDir, debPath]);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
}

export default {
  // Ensure packager includes the Linux icon. electron-packager will pick this
  // up and the deb maker will include it in the packaged app.
  packagerConfig: {
    // Point to the icon path (no extension). Packager resolves .ico (Win) or .png (Linux).
    icon: path.join(__dirname, 'assets', 'orbit'),
    // Give the executable a canonical name.
    executableName: 'astrometrics',
    // Exclude heavy development folders and source files from the final package
    ignore: [
      /^\/\.git($|\/)/,
      /^\/\.github($|\/)/,
      /^\/\.husky($|\/)/,
      /^\/\.agent($|\/)/,
      /^\/\.claude($|\/)/,
      /^\/\.vscode($|\/)/,
      /^\/ui($|\/)/,
      /^\/tests($|\/)/,
      /^\/documentation($|\/)/,
      /^\/scripts($|\/)/,
      /^\/temp_ingest($|\/)/,
      /^\/submodules($|\/)/,
      // PyInstaller Optimization: Ignore venv and source
      /^\/\.venv($|\/)/,
      /^\/backend\/astrolib($|\/)/,
      /^\/backend\/routers($|\/)/,
      /^\/backend\/services($|\/)/,
      /^\/backend\/tests($|\/)/,
      /^\/backend\/uilib($|\/)/,
      /^\/backend\/utilities($|\/)/,
      /^\/backend\/.*\.py$/,
      /^\/backend\/.*\.sh$/,
      /^\/backend\/.*\.ipynb$/,
      // Keep backend/dist (where the exe lives) and config
      // Note: Forge ignores are "blacklists". If we ignore 'backend', we ignore 'dist' too.
      // So we ignore subfolders of backend, but NOT backend/dist.

      // Build artifacts
      /^\/build($|\/)/,
      /^\/\.cache($|\/)/,
      /^\/test-results($|\/)/,

      // Node Dev & Bloat
      /^\/node_modules\/@types($|\/)/,
      /^\/node_modules\/@typescript-eslint($|\/)/,
      /^\/node_modules\/typescript($|\/)/,
      // TypeScript 7 (aliased as @typescript/native), its per-platform
      // compiler binaries, and the TypeScript 6 copy the linter uses.
      /^\/node_modules\/@typescript($|\/)/,
      /^\/node_modules\/vitest($|\/)/,
      // Generic build/config ignores
      /eslint\.config\.js$/,
      /tsconfig.*\.json$/,
      /vite\.config\.js$/,
      /vitest\.config\.ts$/,
      /playwright\.config\.ts$/
    ],
    // OS Integration: File Associations
    fileAssociations: [
      {
        ext: 'fits',
        name: 'FITS Image',
        description: 'Flexible Image Transport System file',
        role: 'Editor',
        isPackage: false
      },
      {
        ext: 'fit',
        name: 'FITS Image',
        description: 'Flexible Image Transport System file',
        role: 'Editor',
        isPackage: false
      }
    ],
    // Disable ASAR to ensure backend execution works reliably
    asar: false,
    // unpack: '**/dist/backend/**' // Not needed if asar is false
  },

  makers: [
    {
      name: '@electron-forge/maker-deb',
      config: {
        // electron-installer-debian only reads its settings from `options`.
        // Keys placed directly on `config` are mostly ignored, which is how
        // `depends` and the desktop-entry settings used to go missing.
        options: {
          maintainer: 'Astrometrics Team <noreply@astrometrics.app>',
          homepage: 'https://github.com/m1chae2/astrometrics',
          icon: path.join(__dirname, 'assets', 'orbit.png'),
          categories: ['Science', 'Astronomy'],
          mimeType: ['image/fits', 'application/fits', 'application/x-fits'],
          genericName: 'Astronomy Application',
          description: 'A clean modern looking visualization app for astrophotography images and spectroscopy',
          productName: 'Astrometrics',
          productDescription: 'A clean modern looking visualization app for astrophotography images and spectroscopy',
          // Notifications, mime types, opening files, and the top-panel AppIndicator.
          depends: [
            'libnotify4',
            'xdg-utils',
            'shared-mime-info',
            'libayatana-appindicator3-1'
          ],
          // The built-in desktop template can't add launcher actions and sets
          // the wrong StartupWMClass; see the template's header for details.
          // (The file is renamed to the app ID after the build; see
          // applyFreedesktopIdentity.)
          desktopTemplate: path.join(__dirname, 'assets', 'desktop', 'astrometrics.desktop.ejs')
        }
      }
    },
    {
      name: '@electron-forge/maker-squirrel',
      config: {
        name: 'astrometrics',
        authors: 'Astrometrics Team',
        exe: 'astrometrics.exe',
        setupExe: 'AstrometricsSetup.exe',
        setupIcon: path.join(__dirname, 'assets', 'orbit.ico'),
        iconUrl: 'https://raw.githubusercontent.com/m1chae2/astrometrics/main/assets/orbit.ico',
        loadingGif: path.join(__dirname, 'assets', 'loading.gif'),
        description: 'A clean modern looking visualization app for astrophotography images and spectroscopy'
      }
    },
    {
      name: '@electron-forge/maker-zip',
      platforms: ['darwin', 'win32']
    }
  ],

  hooks: {
    async postMake(_forgeConfig, makeResults) {
      for (const result of makeResults) {
        if (result.platform !== 'linux') continue;
        for (const artifactPath of result.artifacts) {
          if (artifactPath.endsWith('.deb')) {
            applyFreedesktopIdentity(artifactPath);
          }
        }
      }
      return makeResults;
    }
  }
};
