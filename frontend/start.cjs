const { spawn } = require("child_process");

const frontendRoot = __dirname;
const port = process.env.PORT || "3000";
const hostname = process.env.HOSTNAME || "0.0.0.0";
const nextCli = require.resolve("next/dist/bin/next");

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

console.log("[pautalimpa-frontend] iniciando Next via next start");
run(process.execPath, [
  nextCli,
  "start",
  "--hostname",
  hostname,
  "--port",
  port,
]);
