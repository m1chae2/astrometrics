/**
 * Purpose: Decide which tools this MCP server offers to its client.
 *
 * Each MCP server has a manifest, `tool_manifest.json`, that gives every tool a class,
 * a category and a disposition. A profile is a rule for which classes and dispositions
 * a client may use. This file repeats the rules in `astrometricslib/mcp/profile.py`,
 * because this server is written in TypeScript. Keep the two files in step.
 *
 * The rules fail closed. A tool missing from the manifest is withheld. A missing or
 * unreadable manifest withholds every tool. An unknown profile name falls back to the
 * default profile, `investigator`, which is read-only.
 */

import * as fs from "fs";

export const PROFILE_ENVIRONMENT_VARIABLE = "ASTROMETRICS_MCP_PROFILE";
export const DEFAULT_PROFILE = "investigator";

const PROFILE_CLASSES: Record<string, ReadonlySet<string>> = {
  investigator: new Set(["observe", "compute", "ingest", "ui-control"]),
  developer: new Set(["observe", "compute", "ingest", "ui-control", "develop"]),
};

const SERVED_DISPOSITIONS: ReadonlySet<string> = new Set(["keep", "merge"]);

/** One tool's entry in the manifest. */
export interface ManifestEntry {
  tool_class?: string;
  category?: string;
  disposition?: string;
  merge_into?: string;
  interim_block?: string;
}

/** The manifest file: tool name -> entry. */
export interface ToolManifest {
  tools: Record<string, ManifestEntry>;
}

/**
 * ### Description
 * Reads the profile name from the environment.
 *
 * @param {NodeJS.ProcessEnv} environment The environment to read.
 * @return {string} The named profile if it is known, otherwise the default profile.
 */
export function currentProfile(environment: NodeJS.ProcessEnv = process.env): string {
  const requested = environment[PROFILE_ENVIRONMENT_VARIABLE] ?? DEFAULT_PROFILE;
  if (requested in PROFILE_CLASSES) {
    return requested;
  }
  console.error(`Unknown MCP profile '${requested}'; using '${DEFAULT_PROFILE}' instead.`);
  return DEFAULT_PROFILE;
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
 * Says why a tool is not offered to a profile.
 *
 * @param {ManifestEntry | undefined} entry The tool's manifest entry, if it has one.
 * @param {string} profile A profile name.
 * @return {string | null} A plain reason, or null when the profile may use the tool.
 */
export function withheldReason(entry: ManifestEntry | undefined, profile: string): string | null {
  if (entry === undefined) {
    return "not in the manifest";
  }
  if (entry.interim_block) {
    return `blocked for now: ${entry.interim_block}`;
  }
  if (!entry.tool_class || !PROFILE_CLASSES[profile].has(entry.tool_class)) {
    return `class '${entry.tool_class}' is not offered to the ${profile} profile`;
  }
  if (entry.disposition === "merged") {
    return `it was replaced by ${entry.merge_into || "a newer tool"}`;
  }
  if (!entry.disposition || !SERVED_DISPOSITIONS.has(entry.disposition)) {
    return `disposition '${entry.disposition}' is not offered`;
  }
  return null;
}

/**
 * ### Description
 * Finds the tools a profile may not use.
 *
 * @param {string[]} toolNames Every tool the server declares.
 * @param {ToolManifest | null} manifest The manifest. Null withholds every tool.
 * @param {string} profile A profile name.
 * @return {Map<string, string>} Tool name -> why it is withheld.
 */
export function findWithheldTools(
  toolNames: string[],
  manifest: ToolManifest | null,
  profile: string
): Map<string, string> {
  const withheld = new Map<string, string>();
  for (const name of toolNames) {
    const reason =
      manifest === null
        ? "the manifest is missing or unreadable"
        : withheldReason(manifest.tools[name], profile);
    if (reason !== null) {
      withheld.set(name, reason);
    }
  }
  return withheld;
}

/** Instructions the server gives its client at the start of a session. Matches `GAP_REPORT_GUIDANCE` in Python. */
export const GAP_REPORT_GUIDANCE =
  "These tools look things up and calculate. The only write is bringing frames from the telescope into " +
  "the library. If none of the tools you can use can do what you need, stop. Do not look " +
  "for a workaround: do not chain tools to imitate a missing one, and do not ask for code to be run. " +
  "Call report_capability_gap on the astrometrics-gaps server. Say what you tried, why it fell short, " +
  "and what tool would help. Then tell the person you cannot do it with the current tools.";

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
  return (
    `Unknown tool ${toolName}.${detail} If you need this ability, stop and file a report with ` +
    "report_capability_gap on the astrometrics-gaps server. Do not look for a workaround."
  );
}
