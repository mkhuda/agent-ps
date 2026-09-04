"""Reopening a session in a new terminal."""

import json
import os
import shlex
import shutil
import subprocess
import sys


def resume_command(backend, session_id, cwd):
    """Shell line that reopens a session in the directory it ran in."""
    where = shlex.quote(cwd) if cwd else "."
    return f"cd {where} && {backend.resume(shlex.quote(session_id))}"


def open_in_terminal(command):
    """Run a command in a new terminal window or tab.

    Falls through the terminals that can be scripted, and returns a message
    either way so the caller can show what happened.
    """
    if sys.platform == "darwin":
        if os.environ.get("TERM_PROGRAM") == "iTerm.app":
            script = f'''
                tell application "iTerm"
                    tell current window
                        create tab with default profile
                        tell current session to write text {json.dumps(command)}
                    end tell
                end tell
            '''
        else:
            script = f'''
                tell application "Terminal"
                    do script {json.dumps(command)}
                    activate
                end tell
            '''
        try:
            # a terminal that hangs on the AppleScript would otherwise freeze
            # the whole view, since this runs on the drawing thread
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, OSError):
            return False, "The terminal did not respond."
        if result.returncode == 0:
            return True, "Opened in a new tab."
        return False, "Could not open a terminal tab."

    for launcher in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"],
                     ["konsole", "-e"], ["xterm", "-e"]):
        if shutil.which(launcher[0]):
            try:
                subprocess.Popen(launcher + ["sh", "-c", command],
                                 start_new_session=True)
                return True, "Opened in a new window."
            except OSError:
                continue
    return False, "No terminal emulator found."
