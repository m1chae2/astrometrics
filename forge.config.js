/**
 * @fileoverview Electron Forge configuration for packaging and distribution.
 */

import path from 'path';

// __dirname replacement for ESM (now at project root)
const __dirname = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'));

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
        maintainer: 'Astrometrics Team <noreply@astrometrics.app>',
        homepage: 'https://github.com/m1chae2/astrometrics',
        icon: path.join(__dirname, 'assets', 'orbit.png'),
        categories: ['Science', 'Education', 'Utility'],
        mimeType: ['image/fits', 'application/fits', 'application/x-fits'],
        genericName: 'Astronomy Application',
        description: 'A clean modern looking visualization app for astrophotography images and spectroscopy',
        productName: 'Astrometrics',
        productDescription: 'A clean modern looking visualization app for astrophotography images and spectroscopy',
        depends: ['libnotify4', 'xdg-utils'], // Ensure notification and open support
        desktop: {
          Name: 'Astrometrics',
          GenericName: 'Astronomy Application',
          Comment: 'Astrometrics Image Viewer and Analyzer',
          Categories: 'Science;Education;Graphics;',
          MimeType: 'image/fits;application/fits;application/x-fits;'
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
  ]
};
