# Agent instructions pointer (StateCat deliverable 4)

Add this one line to the project's agent instructions file. It is
the primary read-first and session-start check mechanism for
StateCat, because it sits where every agent session starts. The
skill alone is advisory and may not fire, so this pointer carries
both steps.

```text
Read STATE.md first, then run statecat check, before asking about or working on this project.
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

Read STATE.md first, then run statecat check, before asking about or working on this project.

## Project rules
...
```

Do not change the pointer's meaning when you copy it. If a project
uses a different state file name, change only the file name in the
line, and keep the read-then-check order.
