# Install Astrometrics Devflow VS Code extension (local)

This workspace includes a small status-bar extension that starts/stops the Vite+Electron dev flow.

Files:
- `.vscode/devflow/astrometrics-devflow.vsix` — Generated package (created by the `package:devflow` script).

How to create the VSIX (from repo root):

```bash
npm run package:devflow
```

This runs `vsce` via `npx` and writes the VSIX to `.vscode/devflow/astrometrics-devflow.vsix`.

Install the VSIX into VS Code (GUI):
1. Open VS Code.
2. Open the Extensions view (Ctrl+Shift+X).
3. Click the ellipsis (⋯) menu in the Extensions view.
4. Choose "Install from VSIX..." and pick `.vscode/devflow/astrometrics-devflow.vsix`.

Install from the command line:

```bash
# Install into the currently running VS Code
code --install-extension .vscode/devflow/astrometrics-devflow.vsix
```

After installing the extension, restart VS Code. The status-bar Start/Stop button will appear in your main VS Code window (left side). Clicking it will start the dev flow (Vite + Electron via nodemon). Use the same button to stop the running tasks.

Notes:
- If you prefer not to install, you can run the extension in an Extension Development Host by pressing F5 from this workspace.
- If `code` CLI isn't available, follow GUI steps above.
