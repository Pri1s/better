# Specs

Each part of the project has a spec in this folder. A spec is written before the code
and kept true afterwards, so that anyone picking the work up (a person or an agent) can
see what was intended, what exists, and what comes next without reading the history.

## Files

| File | What it holds |
|---|---|
| [STATUS.md](STATUS.md) | The status board. One line per spec, the task to resume from, and decisions waiting on the user. **Read this first.** |
| [principles.md](principles.md) | Rules that apply to every part. A spec may add to them, never relax them. |
| [decisions.md](decisions.md) | Choices that were not obvious, with the reason and what was rejected. |
| `NN-name.md` | One spec per part, numbered in build order. |

## Anatomy of a spec

1. **Status**: `not started`, `in progress`, `built` (code and tests exist) or `verified`
   (also run against real data, with the result recorded).
2. **Purpose**: what the part is for, in a few sentences.
3. **Requirements**: numbered statements (`R3.4`) that can each be checked. They follow
   the pattern *WHEN <situation> THE SYSTEM SHALL <behaviour>* where that fits. A
   requirement is done when a test or a recorded run demonstrates it; the test is named
   beside it.
4. **Design**: tables, modules, algorithms and commands. Enough that the code could be
   rewritten from it.
5. **Tasks**: an ordered checklist. Each task is small enough to finish and commit on
   its own, and names the requirements it serves.
6. **Results**: what happened when the part was run on real data, with numbers.
7. **Limits and open questions**.

## Working loop

For each task:

1. Read the task and the requirements it names. If the plan has changed, fix the spec
   first.
2. Write the tests for those requirements. They must fail before the code exists.
3. Write the code until `uv run pytest` and `uv run ruff check .` pass.
4. Run it on real data where the task says to, and record what happened under Results.
5. Tick the task, update [STATUS.md](STATUS.md), and commit spec, tests and code together.
6. When a group of tasks is finished, push the branch, open a pull request into `main`
   and merge it. The branch and pull request rules are in [AGENTS.md](../AGENTS.md).

Every pull request leaves STATUS.md true. If work stops mid-task, write one line under
"Resume here" saying what is half done, on which branch, and what state the files are in.

## Changing course

When a requirement turns out to be wrong or a design does not survive contact with the
data, change the spec in the same commit as the code and add an entry to
[decisions.md](decisions.md). Do not leave a spec describing something the code no
longer does.
