/**
 * Purpose: Decide which tools this MCP server offers to its client.
 *
 * Each MCP server has a manifest, `tool_manifest.json`, that gives every tool a class,
 * a category and a disposition. The one profile, `investigator`, is the rule for which
 * classes and dispositions a client may use. The rules themselves live in
 * `mcp_servers/common/profile.py`. The constants here come from `profileRules.ts`, which a
 * script writes from that Python file.
 *
 * The rules fail closed. A tool missing from the manifest is withheld. A missing or
 * unreadable manifest withholds every tool.
 */

import * as fs from "fs";
import { GAP_REPORT_GUIDANCE, GAP_REPORT_REMINDER, OFFERED_CLASSES, SERVED_DISPOSITIONS } from "./profileRules.js";

export { GAP_REPORT_GUIDANCE };

/** One tool's entry in the manifest. */
export interface ManifestEntry {
  tool_class?: string;
  category?: string;
  disposition?: string;
  interim_block?: string;
  note?: string;
}

/** The manifest file: tool name -> entry. */
export interface ToolManifest {
  tools: Record<string, ManifestEntry>;
}

/**
 * ### Description
 * Reads a server's tool manifest.
 *
 * @param {string} manifestPath The `tool_manifest.json` file.
 * @return {ToolManifest | null} The manifest, or null if the file is missing or has no `tools` table.
 */
export function loadManifest(manifestPath: string): ToolManifest | null {
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    if (manifest === null || typeof manifest !== "object" || typeof manifest.tools !== "object" || manifest.tools === null) {
      console.error(`The MCP tool manifest ${manifestPath} has no 'tools' table.`);
      return null;
    }
    return manifest as ToolManifest;
  } catch (error) {
    console.error(`Cannot read the MCP tool manifest ${manifestPath}: ${error}`);
    return null;
  }
}

/**
 * ### Description
 * Says why a tool is not offered.
 *
 * @param {ManifestEntry | undefined} entry The tool's manifest entry, if it has one.
 * @return {string | null} A plain reason, or null when the profile may use the tool.
 */
export function withheldReason(entry: ManifestEntry | undefined): string | null {
  if (entry === undefined) {
    return "not in the manifest";
  }
  if (entry.interim_block) {
    return `blocked for now: ${entry.interim_block}`;
  }
  if (!entry.tool_class || !OFFERED_CLASSES.has(entry.tool_class)) {
    return `class '${entry.tool_class}' is not offered`;
  }
  if (!entry.disposition || !SERVED_DISPOSITIONS.has(entry.disposition)) {
    return entry.note ? entry.note.replace(/\.$/, "") : `disposition '${entry.disposition}' is not offered`;
  }
  return null;
}

/**
 * ### Description
 * Finds the tools the profile may not use.
 *
 * @param {string[]} toolNames Every tool the server declares.
 * @param {ToolManifest | null} manifest The manifest. Null withholds every tool.
 * @return {Map<string, string>} Tool name -> why it is withheld.
 */
export function findWithheldTools(toolNames: string[], manifest: ToolManifest | null): Map<string, string> {
  const withheld = new Map<string, string>();
  for (const name of toolNames) {
    const reason =
      manifest === null
        ? "the manifest is missing or unreadable"
        : withheldReason(manifest.tools[name]);
    if (reason !== null) {
      withheld.set(name, reason);
    }
  }
  return withheld;
}

/**
 * ### Description
 * Writes the error for a call to a tool the client may not use.
 *
 * @param {string} toolName The tool the client asked for.
 * @param {string | undefined} reason Why the profile withholds it. Undefined when no server has the tool.
 * @return {string} An error that says why when known, and tells the client to file a gap report.
 */
export function refusalMessage(toolName: string, reason?: string): string {
  const detail = reason ? ` It is not available to you: ${reason}.` : "";
  return `Unknown tool ${toolName}.${detail} ${GAP_REPORT_REMINDER}`;
}
