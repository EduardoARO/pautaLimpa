const { spawn } = require("child_process");

const frontendRoot = __dirname;
const port = process.env.PORT || "3000";
// O Render injeta HOSTNAME com o nome do container; o bind precisa ser 0.0.0.0
// para o port scan detectar o serviço e evitar 502.
const hostname = "0.0.0.0";
const nextCli = require.resolve("next/dist/bin/next");

function run(command, args) {
  const env = { ...process.env, PORT: port };
  delete env.HOSTNAME;

  const child = spawn(command, args, {
    stdio: "inherit",
    cwd: frontendRoot,
    env,
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
