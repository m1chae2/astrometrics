/**
 * Purpose: Frontend UI tools for type-checking, linting, testing, and accessibility auditing.
 * Provides native JS/TS diagnostics tools for AI agents in the UI domain.
 */

import { exec } from "child_process";
import * as fs from "fs";
import * as path from "path";

/**
 * ### Description
 * Executes a shell command and returns stdout and stderr in a structured object.
 */
function runCommand(cmd: string, cwd: string): Promise<{ success: boolean; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    exec(cmd, { cwd }, (error, stdout, stderr) => {
      resolve({
        success: !error,
        stdout: stdout.trim(),
        stderr: stderr.trim()
      });
    });
  });
}

/**
 * ### Description
 * Runs Vitest unit tests for the frontend app.
 */
export async function runFrontendTests(repoRoot: string) {
  const result = await runCommand("npm run test:unit", repoRoot);
  return {
    status: result.success ? "success" : "failed",
    stdout: result.stdout,
    stderr: result.stderr
  };
}

/**
 * ### Description
 * Runs type-checking (tsc) and linting (eslint) to verify frontend code integrity.
 */
export async function diagnoseCode(repoRoot: string) {
  const tscResult = await runCommand("npm run type-check", repoRoot);
  const eslintResult = await runCommand("npm run lint", repoRoot);

  return {
    status: (tscResult.success && eslintResult.success) ? "success" : "failed",
    typeChecking: {
      success: tscResult.success,
      stdout: tscResult.stdout,
      stderr: tscResult.stderr
    },
    linting: {
      success: eslintResult.success,
      stdout: eslintResult.stdout,
      stderr: eslintResult.stderr
    }
  };
}

/**
 * ### Description
 * Audits UI TSX components recursively under the ui directory to ensure proper ARIA attributes,
 * keyboard accessibility (tabIndex), and alt text for img elements.
 */
export async function auditAccessibility(repoRoot: string) {
  const uiDir = path.join(repoRoot, "ui");
  const report: { file: string; issues: string[] }[] = [];

  if (!fs.existsSync(uiDir)) {
    return { status: "error", message: `UI directory not found at ${uiDir}` };
  }

  /**
   * ### Description
   * Recursively scans folders to identify TSX files and run regex audits.
   */
  function scan(dir: string) {
    const list = fs.readdirSync(dir);
    for (const item of list) {
      const fullPath = path.join(dir, item);
      const stat = fs.statSync(fullPath);

      if (stat.isDirectory()) {
        if (item !== "node_modules" && item !== "dist") {
          scan(fullPath);
        }
      } else if (stat.isFile() && /\.(tsx|ts|jsx|js)$/.test(item)) {
        const content = fs.readFileSync(fullPath, "utf-8");
        const fileIssues: string[] = [];

        // 1. Check for img elements missing alt attributes
        const imgMatches = content.match(/<img[^>]*>/g) || [];
        for (const img of imgMatches) {
          if (!/alt\s*=\s*/.test(img)) {
            fileIssues.push(`Missing 'alt' attribute on image element: ${img.trim()}`);
          }
        }

        // 2. Check for button elements missing aria-label or textual children
        const buttonMatches = content.match(/<button[^>]*>/g) || [];
        for (const btn of buttonMatches) {
          const hasAria = /aria-label\s*=\s*/.test(btn);
          const isIconOnly = btn.includes("Icon") || btn.includes("icon");
          if (isIconOnly && !hasAria) {
            fileIssues.push(`Potential icon-only button missing 'aria-label' attribute: ${btn.trim()}`);
          }
        }

        // 3. Check for raw click handlers on div/span without role or tabIndex
        const clickDivs = content.match(/<(div|span)[^>]*onClick[^>]*>/g) || [];
        for (const element of clickDivs) {
          const hasRole = /role\s*=\s*/.test(element);
          const hasTabIndex = /tabIndex\s*=\s*/.test(element);
          if (!hasRole || !hasTabIndex) {
            fileIssues.push(`Interactive elements (${element.trim().split(" ")[0]}>) with click handler missing 'role' or 'tabIndex' for keyboard navigation.`);
          }
        }

        if (fileIssues.length > 0) {
          report.push({
            file: path.relative(repoRoot, fullPath),
            issues: fileIssues
          });
        }
      }
    }
  }

  scan(uiDir);

  return {
    status: "success",
    totalFilesAudited: report.length,
    accessibilityComplianceScore: report.length === 0 ? 100 : Math.max(0, 100 - report.reduce((sum, f) => sum + f.issues.length, 0)),
    auditedIssues: report
  };
}

/**
 * ### Description
 * Runs production bundle compilation check (npm run build) for the frontend app.
 */
export async function buildCheck(repoRoot: string) {
  const result = await runCommand("npm run build", repoRoot);
  return {
    status: result.success ? "success" : "failed",
    stdout: result.stdout,
    stderr: result.stderr
  };
}
