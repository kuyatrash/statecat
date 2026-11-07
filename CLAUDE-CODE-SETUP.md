# Set up StateCat in Claude Code (paste-in prompt)

Open Claude Code in your project and paste everything inside the box
below as one message. It installs StateCat if needed, creates the
state files, and adds a SessionStart hook so every new, resumed,
cleared, or compacted session starts with STATE.md already in the
agent's context. Run it again any time: each step is safe to repeat.

```text
Set up StateCat in this project so you always start from its state. Do these steps in order and show me the output of each command.

1. Get the script. If statecat.py is not in the project root and the `statecat` command is not installed, download it:
   curl -fsSLO https://raw.githubusercontent.com/kuyatrash/statecat/main/statecat.py
   Use `python3 statecat.py` for every command below (or `statecat` if that is what is installed).

2. Create the state. If STATE.md does not exist in the project root, run:
   python3 statecat.py init "<this project's name>"
   This also adds the read-first pointer to AGENTS.md (or CLAUDE.md) and gitignores .statecat/. If STATE.md already exists, skip this step and run `python3 statecat.py pointer --install` instead.

3. Add the SessionStart hook. Run:
   python3 statecat.py hook --install
   If that fails because this statecat version has no `hook` command, edit .claude/settings.json yourself instead: keep every existing setting, and add this entry to the list at hooks.SessionStart (create the file, "hooks", or "SessionStart" if missing; do not add it twice):
   {"hooks": [{"type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 statecat.py show && python3 statecat.py check; exit 0"}]}
   If .claude/settings.json exists but is not valid JSON, stop and tell me instead of overwriting it.

4. Verify. Show me the SessionStart part of .claude/settings.json, then run `python3 statecat.py check` and tell me the result.

5. From now on in this project: before asking me about or working on anything, read STATE.md. Do not re-ask anything in Decided or bring up anything in Parked unless I do. When we decide something or new work comes up, record it with `python3 statecat.py add <Bucket> "<text>"` (Buckets: Decided, Open, "Waiting on human", "Waiting on others", Parked).

When you are done, tell me in two or three lines what changed, and remind me that the hook starts working from the next session (or after /clear).
```

Once the hook is in place you can check it worked: start a new
session and ask "what's decided in this project?" The agent should
answer from STATE.md without opening it first.
