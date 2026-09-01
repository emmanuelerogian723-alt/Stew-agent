#!/usr/bin/env node
/**
 * Stew Code — Terminal AI Agent CLI
 * A Claude-Code-style interactive terminal agent powered by S.T.E.W Agent.
 * No external dependencies — uses Node's built-in fetch + readline.
 */

import readline from "node:readline";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const BASE_URL = "https://stew-agent-r3m7.onrender.com/v1";
const CONFIG_DIR = path.join(os.homedir(), ".stew");
const CONFIG_FILE = path.join(CONFIG_DIR, "config.json");

const COLORS = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  gold: "\x1b[38;5;220m",
  cyan: "\x1b[38;5;51m",
  green: "\x1b[38;5;46m",
  red: "\x1b[38;5;196m",
  gray: "\x1b[38;5;244m",
};

function c(color, text) {
  return `${COLORS[color]}${text}${COLORS.reset}`;
}

function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf-8"));
    }
  } catch (e) {}
  return {};
}

function saveConfig(cfg) {
  if (!fs.existsSync(CONFIG_DIR)) fs.mkdirSync(CONFIG_DIR, { recursive: true });
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2));
}

function printBanner() {
  console.log(c("gold", "╔═══════════════════════════════════════════════╗"));
  console.log(c("gold", "║") + "   " + c("bold", "🧠 STEW CODE") + c("gray", "  — terminal AI agent") + "        " + c("gold", "║"));
  console.log(c("gold", "║") + c("gray", "   Powered by S.T.E.W Agent · type /help for cmds") + "  " + c("gold", "║"));
  console.log(c("gold", "╚═══════════════════════════════════════════════╝"));
  console.log();
}

async function promptApiKey(rl) {
  return new Promise((resolve) => {
    console.log(c("cyan", "No API key found. Get one free at:"));
    console.log(c("gray", "  https://stew-agent-r3m7.onrender.com/dashboard"));
    rl.question(c("bold", "\nPaste your stew_ API key: "), (answer) => {
      resolve(answer.trim());
    });
  });
}

async function streamChat(messages, model, apiKey) {
  const res = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages,
      stream: true,
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`HTTP ${res.status}: ${err}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  let buffer = "";

  process.stdout.write(c("green", "stew  ") );

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const data = trimmed.slice(5).trim();
      if (data === "[DONE]") continue;
      try {
        const json = JSON.parse(data);
        const delta = json.choices?.[0]?.delta?.content;
        if (delta) {
          process.stdout.write(delta);
          full += delta;
        }
      } catch (e) {
        // ignore parse errors on partial chunks
      }
    }
  }
  console.log("\n");
  return full;
}

async function main() {
  const args = process.argv.slice(2);
  let config = loadConfig();

  // Handle CLI flags
  if (args[0] === "--set-key" || args[0] === "login") {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const key = await promptApiKey(rl);
    config.apiKey = key;
    saveConfig(config);
    console.log(c("green", "\n✓ API key saved to ~/.stew/config.json"));
    rl.close();
    return;
  }

  if (args[0] === "--model" && args[1]) {
    config.model = args[1];
    saveConfig(config);
    console.log(c("green", `✓ Default model set to ${args[1]}`));
    return;
  }

  if (args[0] === "--version" || args[0] === "-v") {
    console.log("stew-code v1.0.0");
    return;
  }

  printBanner();

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  rl.on("close", () => {
    console.log(c("gray", "\nbye 👋"));
    process.exit(0);
  });

  if (!config.apiKey) {
    config.apiKey = await promptApiKey(rl);
    saveConfig(config);
    console.log(c("green", "✓ Saved. You won't be asked again.\n"));
  }

  const model = config.model || "stew-default";
  console.log(c("gray", `model: ${model}  ·  /help for commands  ·  /exit to quit\n`));

  const history = [];

  const ask = () => {
    rl.question(c("bold", "you    "), async (input) => {
      const text = input.trim();

      if (!text) return ask();

      if (text === "/exit" || text === "/quit") {
        rl.close();
        return;
      }

      if (text === "/clear") {
        history.length = 0;
        console.log(c("gray", "✓ conversation cleared\n"));
        return ask();
      }

      if (text === "/help") {
        console.log(c("gray", `
commands:
  /help          show this menu
  /clear         clear conversation history
  /model <name>  switch model for this session (e.g. stew-fast, stew-mistral)
  /exit          quit
`));
        return ask();
      }

      if (text.startsWith("/model ")) {
        const newModel = text.replace("/model ", "").trim();
        config.model = newModel;
        console.log(c("gray", `✓ switched to ${newModel}\n`));
        return ask();
      }

      history.push({ role: "user", content: text });

      try {
        const reply = await streamChat(history, config.model || model, config.apiKey);
        history.push({ role: "assistant", content: reply });
      } catch (e) {
        console.log(c("red", `\n✗ Error: ${e.message}\n`));
      }

      ask();
    });
  };

  ask();
}

main().catch((e) => {
  console.error(c("red", `Fatal error: ${e.message}`));
  process.exit(1);
});
