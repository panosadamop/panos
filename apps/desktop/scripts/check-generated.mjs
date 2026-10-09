// Fails when src/api/schema.d.ts or src/api/events.ts are stale (M7-02 drift check).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const tmp = mkdtempSync(join(tmpdir(), "panos-gen-"));
const shell = process.platform === "win32";
try {
  const fresh = join(tmp, "schema.d.ts");
  execFileSync("npx", ["openapi-typescript", "../../docs/api/openapi.v1.json", "-o", fresh], { stdio: "pipe", shell });
  if (readFileSync(fresh, "utf8") !== readFileSync("src/api/schema.d.ts", "utf8")) {
    console.error("src/api/schema.d.ts is out of date: run `pnpm gen`");
    process.exit(1);
  }
  execFileSync("uv", ["run", "python", "../../scripts/export_event_types.py", "src/api/events.ts", "--check"], {
    stdio: "inherit",
    shell,
  });
  console.log("OK: generated API and event types are up to date");
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
