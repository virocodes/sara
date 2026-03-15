"""Restart tool -- kills the current agent process and relaunches it."""

import os
import subprocess
import sys

name = "restart"
description = "Restart the agent process. Use this after making changes to agent.py or other core files. The agent will go down briefly then come back up."
input_schema = {
    "type": "object",
    "properties": {},
    "required": [],
}


def run(inp):
    agent_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent.py")
    python = sys.executable

    # find the main agent process pid (the one running agent.py)
    result = subprocess.run(
        f"pgrep -f 'python.*agent\\.py'",
        shell=True, capture_output=True, text=True
    )
    pids = result.stdout.strip().splitlines()

    # launch a background process that waits 1s then restarts agent.py
    subprocess.Popen(
        f"sleep 1 && {python} {agent_path}",
        shell=True,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # kill the main agent process(es)
    for pid in pids:
        try:
            os.kill(int(pid.strip()), 9)
        except Exception:
            pass
