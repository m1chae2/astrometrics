/**
 * @fileoverview Tests for the UI MCP server's tool profiles.
 *
 * A profile limits which tools the server offers. These tests check the rules, the
 * failure cases (missing manifest, unknown tool, unknown profile), and that the
 * committed manifest agrees with the tools the server declares.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_PROFILE,
  PROFILE_ENVIRONMENT_VARIABLE,
  currentProfile,
  findWithheldTools,
  loadManifest,
  withheldReason,
} from "../src/profile";

const MANIFEST_PATH = path.resolve(__dirname, "../tool_manifest.json");
const SERVER_SOURCE = path.resolve(__dirname, "../src/index.ts");

describe("withheldReason", () => {
  it("offers a read-only tool with a served disposition", () => {
    expect(withheldReason({ tool_class: "observe", disposition: "merge" }, "investigator")).toBeNull();
  });

  it("withholds writers, device commands and code runners from the investigator", () => {
    for (const toolClass of ["change-data", "actuate", "unrestricted", "develop"]) {
      expect(withheldReason({ tool_class: toolClass, disposition: "keep" }, "investigator")).not.toBeNull();
    }
  });

  it("offers a develop tool to the developer profile only", () => {
    const entry = { tool_class: "develop", disposition: "keep" };
    expect(withheldReason(entry, "developer")).toBeNull();
    expect(withheldReason(entry, "investigator")).not.toBeNull();
  });

  it("withholds dropped, withheld, fixed and undecided tools", () => {
    for (const disposition of ["drop", "withhold", "fix", "undecided"]) {
      expect(withheldReason({ tool_class: "observe", disposition }, "investigator")).not.toBeNull();
    }
  });

  it("withholds a tool with an interim block and says why", () => {
    const reason = withheldReason(
      { tool_class: "observe", disposition: "merge", interim_block: "Loads everything." },
      "investigator"
    );
    expect(reason).toContain("Loads everything.");
  });

  it("withholds a merged tool and names its replacement", () => {
    const reason = withheldReason(
      { tool_class: "compute", disposition: "merged", merge_into: "observatory_night_history" },
      "investigator"
    );
    expect(reason).toContain("observatory_night_history");
  });

  it("withholds a tool that is not in the manifest", () => {
    expect(withheldReason(undefined, "investigator")).toBe("not in the manifest");
  });
});

describe("findWithheldTools", () => {
  it("withholds every tool when the manifest is missing", () => {
    expect([...findWithheldTools(["a", "b"], null, "investigator").keys()]).toEqual(["a", "b"]);
  });

  it("withholds only the tool the manifest lacks", () => {
    const manifest = { tools: { known: { tool_class: "observe", disposition: "keep" } } };
    expect([...findWithheldTools(["known", "new_tool"], manifest, "investigator").keys()]).toEqual(["new_tool"]);
  });
});

describe("currentProfile", () => {
  it("reads a known profile and falls back to the default for anything else", () => {
    expect(currentProfile({})).toBe(DEFAULT_PROFILE);
    expect(currentProfile({ [PROFILE_ENVIRONMENT_VARIABLE]: "developer" })).toBe("developer");
    expect(currentProfile({ [PROFILE_ENVIRONMENT_VARIABLE]: "root" })).toBe(DEFAULT_PROFILE);
  });
});

describe("loadManifest", () => {
  it("returns null for a missing file or a file with no tools table", () => {
    expect(loadManifest(path.join(os.tmpdir(), "no_such_manifest.json"))).toBeNull();
    const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "manifest-")), "manifest.json");
    fs.writeFileSync(file, "[1, 2]");
    expect(loadManifest(file)).toBeNull();
  });
});

describe("the committed manifest", () => {
  const declared = [...fs.readFileSync(SERVER_SOURCE, "utf-8").matchAll(/name:\s*"(ui_[a-z_]+)"/g)].map(
    (match) => match[1]
  );

  it("covers exactly the tools the server declares", () => {
    const manifest = loadManifest(MANIFEST_PATH);
    expect(manifest).not.toBeNull();
    expect(Object.keys(manifest!.tools).sort()).toEqual([...declared].sort());
  });

  it("offers no tool to the investigator and all of them to the developer", () => {
    const manifest = loadManifest(MANIFEST_PATH);
    expect(findWithheldTools(declared, manifest, "investigator").size).toBe(declared.length);
    expect(findWithheldTools(declared, manifest, "developer").size).toBe(0);
  });
});

describe("refusalMessage", () => {
  it("starts with the old error, says why, and points to the gap tool", async () => {
    const { refusalMessage, GAP_REPORT_GUIDANCE } = await import("../src/profile");
    const withheld = refusalMessage("ui_run_tests", "class 'develop' is not offered to the investigator profile");
    expect(withheld.startsWith("Unknown tool ui_run_tests.")).toBe(true);
    expect(withheld).toContain("class 'develop'");
    expect(withheld).toContain("report_capability_gap");
    expect(refusalMessage("nothing_like_it")).toContain("report_capability_gap");
    expect(GAP_REPORT_GUIDANCE).toContain("report_capability_gap");
  });
});
