/**
 * @fileoverview Electron Forge configuration for packaging and distribution.
 */

import path from 'path';
import fs from 'fs';
import os from 'os';
import { execFileSync } from 'child_process';

// __dirname replacement for ESM (now at project root)
const __dirname = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'));

/**
 * Injects the AppStream metainfo file into a built .deb package.
 *
 * electron-installer-debian (the engine behind @electron-forge/maker-deb) has
 * no config option for shipping arbitrary extra files, so the only way to get
 * a file under /usr/share/metainfo/ into the package is to unpack the .deb it
 * produced, add the file, and repack it in place with dpkg-deb.
 *
 * @param {string} debPath Absolute path to the built .deb artifact.
 */
function injectAppStreamMetainfo(debPath) {
  const metainfoSource = path.join(__dirname, 'assets', 'metainfo', 'astrometrics.metainfo.xml');
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-deb-'));
  try {
    execFileSync('dpkg-deb', ['-R', debPath, workDir]);
    const metainfoDestDir = path.join(workDir, 'usr', 'share', 'metainfo');
    fs.mkdirSync(metainfoDestDir, { recursive: true });
    fs.copyFileSync(metainfoSource, path.join(metainfoDestDir, 'astrometrics.metainfo.xml'));
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
          categories: ['Science', 'Education', 'Graphics'],
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
            injectAppStreamMetainfo(artifactPath);
          }
        }
      }
      return makeResults;
    }
  }
};
