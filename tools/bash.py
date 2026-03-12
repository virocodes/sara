"""Bash tool — run shell commands and return output."""

import subprocess

name = "bash"
description = "Run a bash command and return its output."
input_schema = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The bash command to run."}
    },
    "required": ["command"],
}


def run(inp):
    result = subprocess.run(
        inp["command"], shell=True, capture_output=True, text=True, timeout=120
    )
    output = result.stdout
    if result.stderr:
        output += "\n" + result.stderr
    if result.returncode != 0:
        output += f"\n[exit code: {result.returncode}]"
    return output.strip() or "(no output)"
