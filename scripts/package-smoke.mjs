import {
  client,
  methods,
  ndJsonStream,
  PROTOCOL_VERSION,
} from "@agentclientprotocol/sdk";
import { execFile, spawn } from "node:child_process";
import { constants } from "node:fs";
import {
  access,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { clearTimeout, setTimeout } from "node:timers";
import { dirname, join } from "node:path";
import { Readable, Writable } from "node:stream";
import { fileURLToPath } from "node:url";
import { once } from "node:events";

const REPOSITORY_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

async function within(promise, stage, timeoutMs = 10_000) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`Package smoke timeout at ${stage}`)),
      timeoutMs,
    );
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

function run(command, args, options) {
  return new Promise((resolve, reject) => {
    execFile(
      command,
      args,
      { ...options, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error !== null) {
          reject(error);
          return;
        }
        resolve({ stdout, stderr });
      },
    );
  });
}

async function runNpm(args, cwd) {
  const npmExecPath = process.env.npm_execpath;
  if (npmExecPath === undefined) {
    throw new Error("npm_execpath is required");
  }
  return run(process.execPath, [npmExecPath, ...args], {
    cwd,
    env: process.env,
  });
}

async function verifyImport(consumerDirectory) {
  const script = join(consumerDirectory, "import-smoke.mjs");
  await writeFile(
    script,
    'import { DEFAULT_LIMITS } from "@kcrong/agy-acp";\n' +
      'if (DEFAULT_LIMITS.maxSessions !== 16) process.exit(1);\n',
  );
  await run(process.execPath, [script], {
    cwd: consumerDirectory,
    env: process.env,
  });
}

async function verifyPackagedCli(consumerDirectory, packageRoot) {
  const metadata = JSON.parse(
    await readFile(join(packageRoot, "package.json"), "utf8"),
  );
  if (metadata.bin?.["agy-acp"] !== "./dist/cli.js") {
    throw new Error("Unexpected package bin mapping");
  }

  const cliEntry = join(packageRoot, "dist", "cli.js");
  const bin =
    process.platform === "win32"
      ? { command: process.execPath, args: [cliEntry] }
      : {
          command: join(consumerDirectory, "node_modules", ".bin", "agy-acp"),
          args: [],
        };
  if (process.platform !== "win32") {
    await access(bin.command, constants.X_OK);
  }

  const fakeAgy = join(REPOSITORY_ROOT, "tests", "fixtures", "fake-agy.mjs");
  const child = spawn(
    bin.command,
    [
      ...bin.args,
      "--agy-path",
      process.execPath,
      "--agy-arg",
      fakeAgy,
    ],
    {
      cwd: consumerDirectory,
      env: {
        HOME: process.env.HOME,
        LANG: "C.UTF-8",
        PATH: process.env.PATH,
      },
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  const exited = once(child, "exit");
  let stderrBytes = 0;
  let textUpdates = 0;
  child.stderr.on("data", (chunk) => {
    stderrBytes += chunk.byteLength;
  });
  const testClient = client({ name: "package-smoke" }).onNotification(
    methods.client.session.update,
    ({ params }) => {
      if (
        params.update.sessionUpdate === "agent_message_chunk" &&
        params.update.content.type === "text"
      ) {
        textUpdates += 1;
      }
    },
  );
  const connection = testClient.connect(
    ndJsonStream(
      Writable.toWeb(child.stdin),
      Readable.toWeb(child.stdout),
    ),
  );

  try {
    try {
      await within(
        connection.agent.request(methods.agent.initialize, {
          protocolVersion: PROTOCOL_VERSION,
          clientCapabilities: {},
        }),
        "initialize",
      );
      const session = await within(
        connection.agent.request(methods.agent.session.new, {
          cwd: consumerDirectory,
          mcpServers: [],
        }),
        "session/new",
      );
      const result = await within(
        connection.agent.request(methods.agent.session.prompt, {
          sessionId: session.sessionId,
          prompt: [{ type: "text", text: "package smoke" }],
        }),
        "session/prompt",
      );
      await within(
        connection.agent.request(methods.agent.session.close, {
          sessionId: session.sessionId,
        }),
        "session/close",
      );
      if (result.stopReason !== "end_turn" || textUpdates !== 1) {
        throw new Error("Unexpected ACP result");
      }
    } finally {
      connection.close();
      child.stdin.end();
    }

    const [code, signal] = await within(exited, "CLI exit");
    if (code !== 0 || signal !== null || stderrBytes !== 0) {
      throw new Error("Packaged CLI did not exit cleanly");
    }
  } catch (error) {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
    throw error;
  }
}

async function main() {
  const temporaryBase =
    process.env.KIROCREW_SCRATCH ?? process.env.RUNNER_TEMP ?? tmpdir();
  await mkdir(temporaryBase, { recursive: true });
  const workspace = await mkdtemp(join(temporaryBase, "agy-acp-package-"));

  try {
    const packDirectory = join(workspace, "pack");
    const consumerDirectory = join(workspace, "consumer");
    await mkdir(packDirectory);
    await mkdir(consumerDirectory);

    const packed = await runNpm(
      [
        "pack",
        "--ignore-scripts",
        "--json",
        "--pack-destination",
        packDirectory,
      ],
      REPOSITORY_ROOT,
    );
    const packResult = JSON.parse(packed.stdout)[0];
    const paths = new Set(packResult.files.map((file) => file.path));
    for (const required of ["LICENSE", "README.md", "SECURITY.md", "package.json"]){
      if (!paths.has(required)) {
        throw new Error("Required package file missing");
      }
    }
    if ([...paths].some((path) => path.endsWith(".map") || path.startsWith("tests/"))) {
      throw new Error("Forbidden package file included");
    }

    await writeFile(
      join(consumerDirectory, "package.json"),
      JSON.stringify({
        name: "agy-acp-clean-consumer",
        version: "1.0.0",
        private: true,
        type: "module",
      }),
    );
    const tarball = join(packDirectory, packResult.filename);
    await runNpm(
      [
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--prefix",
        consumerDirectory,
        tarball,
      ],
      REPOSITORY_ROOT,
    );

    const packageRoot = join(
      consumerDirectory,
      "node_modules",
      "@kcrong",
      "agy-acp",
    );
    await verifyImport(consumerDirectory);
    await verifyPackagedCli(consumerDirectory, packageRoot);
    process.stdout.write("package-smoke-ok\n");
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
}

void main().catch(() => {
  process.stderr.write("package-smoke-failed\n");
  process.exitCode = 1;
});
