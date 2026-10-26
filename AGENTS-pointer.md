# Agent instructions pointer (StateCat deliverable 4)

Add this one line to the project's agent instructions file. It is
the primary read-first and session-start check mechanism for
StateCat, because it sits where every agent session starts. The
skill alone is advisory and may not fire, so this pointer carries
both steps.

```text
Read STATE.md first, then run python3 statecat.py check, before asking about or working on this project.
```

## Where to put it

Put the line at the top of the file your project's agents actually
read. Common filenames are AGENTS.md, CLAUDE.md, or a project
instructions file. Which filename each target project uses is the
only open decision left in the StateCat scope (revision 5, section
11); it does not block evaluation or build, and the template ships
with the common filenames noted here.

## Example snippet

```markdown
# Project instructions

Read STATE.md first, then run python3 statecat.py check, before asking about or working on this project.

## Project rules
...
```

Do not change the pointer's meaning when you copy it. If a project
uses a different state file name, change only the file name in the
line, and keep the read-then-check order.

## If statecat.py lives elsewhere

The pointer above assumes `statecat.py` is in the project root.
If it lives elsewhere, use that path in the pointer instead, for
example:

```text
Read STATE.md first, then run python3 ~/workspace/statecat/statecat.py check, before asking about or working on this project.
```

Alternatively, copy `statecat.py` and the optional `statecat`
launcher from the repo root into your project root, and the
pointer can use `./statecat check` on Unix. On Windows, always
use the `python3 statecat.py check` form.
