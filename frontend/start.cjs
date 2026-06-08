const { existsSync } = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const frontendRoot = __dirname;
const port = process.env.PORT || "3000";
const hostname = process.env.HOSTNAME || "0.0.0.0";
const standaloneServer = path.join(
  frontendRoot,
  ".next",
  "standalone",
  "server.js",
);
const nextBinary =
  process.platform === "win32"
    ? path.join(frontendRoot, "node_modules", ".bin", "next.cmd")
    : path.join(frontendRoot, "node_modules", ".bin", "next");

function run(command, args) {
  const child = spawn(command, args, {
    stdio: "inherit",
    cwd: frontendRoot,
    env: {
      ...process.env,
      HOSTNAME: hostname,
      PORT: port,
    },
  });

  child.on("exit", (code) => {
    process.exit(code ?? 1);
  });

  child.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
}

if (existsSync(standaloneServer)) {
  run(process.execPath, [standaloneServer]);
} else {
  run(nextBinary, ["start", "--hostname", hostname, "--port", port]);
}
