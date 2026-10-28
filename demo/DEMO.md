# StateCat demo

Run it yourself, in an empty folder with `statecat.py` beside it:

```sh
sh /path/to/statecat/demo/run-demo.sh
```

## What happens

A fresh agent, with no memory of this project, asks three questions
it would normally have to reconstruct from old chats:

- What is already decided?
- What is open right now?
- Is there anything I should not bring up again?

With StateCat, it reads `STATE.md` once and gets all three.

## Terminal session

```text
$ python3 statecat.py init "Demo Bakery"
created STATE.md
created STATE-ARCHIVE.md

$ python3 statecat.py add Decided "Sell at the Saturday market" --because "foot traffic is highest there"
added #1

$ python3 statecat.py add Open "Bake the test batch"
added #2

$ python3 statecat.py add "Waiting on human" "Pick a launch date"
added #3

$ python3 statecat.py add Parked "Mobile app"
added #4

$ python3 statecat.py show
# Project: Demo Bakery
> About: Demo Bakery project state.
> Purpose: Keep the current truth about this project in one file.
> Key paths: STATE.md, STATE-ARCHIVE.md, .statecat/backups/
> Last ID used: #4

## Decided

- #1 [2026-10-26] Sell at the Saturday market, because foot traffic is highest there

## Open

- #2 [2026-10-26] Bake the test batch

## Waiting on human

- #3 [2026-10-26] Pick a launch date

## Waiting on others

## Parked

- #4 [2026-10-26] Mobile app

$ python3 statecat.py check
check: clean
```

## The point, in one line

The next session does not re-ask "where should we sell?" (Decided
#1), does not miss the test batch (Open #2), and does not
re-propose the mobile app (Parked #4) without owner approval.

For a complete, realistic state file, see `../example/`. For the
acceptance fixture a second agent can run, see `../fixture/`.
