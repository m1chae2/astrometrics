/**
 * Purpose: Main entrypoint for the Frontend/UI Model Context Protocol (MCP) server.
 * Instantiates the stdio transport, registers UI-specific tools, and executes them.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  type CallToolRequest,
} from "@modelcontextprotocol/sdk/types.js";
import { runFrontendTests, diagnoseCode, auditAccessibility, buildCheck } from "./tools.js";
import { GAP_REPORT_GUIDANCE, currentProfile, findWithheldTools, loadManifest, refusalMessage } from "./profile.js";
import * as path from "path";
import * as fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Find project repo root
let repoRoot = path.resolve(process.cwd());
if (!fs.existsSync(path.join(repoRoot, "package.json"))) {
  repoRoot = path.resolve(__dirname, "../../..");
}

const server = new Server(
  {
    name: "astrometrics-ui",
    version: "1.0.0",
  },
  {
    capabilities: {
      tools: {},
    },
    instructions: GAP_REPORT_GUIDANCE,
  }
);

/**
 * ### Description
 * Every tool this server declares, before the profile removes any.
 */
const TOOL_DEFINITIONS = [
      {
        name: "ui_run_tests",
        description: "Run the frontend unit test suite (vitest) to verify UI components integrity.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "ui_diagnose_code",
        description: "Run TypeScript type checking and linting to verify frontend code integrity.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "ui_audit_accessibility",
        description: "Audits JSX/TSX elements in the UI codebase for ARIA attributes, alt text, and tabIndex mappings.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "ui_build_check",
        description: "Runs production bundle build verification to ensure UI application compiles.",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
    ];

/**
 * ### Description
 * The tools the manifest does not allow for the chosen profile, with the reason for each.
 * The manifest sits next to the compiled `dist` folder.
 */
const withheldTools = findWithheldTools(
  TOOL_DEFINITIONS.map((tool) => tool.name),
  loadManifest(path.resolve(__dirname, "../tool_manifest.json")),
  currentProfile()
);

/**
 * ### Description
 * Registers the tools the profile allows to the client.
 */
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: TOOL_DEFINITIONS.filter((tool) => !withheldTools.has(tool.name)),
  };
});

/**
 * ### Description
 * Handles tool execution requests from the client.
 */
server.setRequestHandler(CallToolRequestSchema, async (request: CallToolRequest) => {
  const { name } = request.params;

  try {
    if (withheldTools.has(name)) {
      throw new Error(refusalMessage(name, withheldTools.get(name)));
    }
    switch (name) {
      case "ui_run_tests": {
        const result = await runFrontendTests(repoRoot);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
      case "ui_diagnose_code": {
        const result = await diagnoseCode(repoRoot);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
      case "ui_audit_accessibility": {
        const result = await auditAccessibility(repoRoot);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
      case "ui_build_check": {
        const result = await buildCheck(repoRoot);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
      default:
        throw new Error(refusalMessage(name));
    }
  } catch (error: any) {
    return {
      content: [{ type: "text", text: `Error: ${error.message}` }],
      isError: true,
    };
  }
});

/**
 * ### Description
 * Starts the server using stdio transport.
 */
async function run() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Astrometrics UI MCP Server running on stdio");
}

run().catch((error) => {
  console.error("Fatal error starting UI MCP Server:", error);
  process.exit(1);
});
