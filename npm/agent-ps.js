#!/usr/bin/env node
// Runs the zipapp that sits beside this file.
//
// The package exists so that people who install their coding agents with npm
// can install this the same way. It is still a Python program: this only finds
// an interpreter and gets out of the way.
"use strict";

var spawnSync = require("child_process").spawnSync;
var path = require("path");

var app = path.join(__dirname, "..", "agent-ps");
var args = process.argv.slice(2);
var tried = [];

for (var i = 0, names = ["python3", "python"]; i < names.length; i++) {
  // stdio is inherited rather than piped, because the interface is a curses
  // one and curses needs a real terminal on the other end.
  var run = spawnSync(names[i], [app].concat(args), { stdio: "inherit" });
  if (run.error && run.error.code === "ENOENT") {
    tried.push(names[i]);
    continue;
  }
  if (run.error) {
    process.stderr.write("agent-ps: " + run.error.message + "\n");
    process.exit(1);
  }
  if (run.signal) {
    process.exit(1);
  }
  process.exit(run.status === null ? 1 : run.status);
}

process.stderr.write(
  "agent-ps needs Python 3.8 or later, and none was found on PATH.\n" +
  "Looked for: " + tried.join(", ") + "\n"
);
process.exit(1);
