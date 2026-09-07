# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://github.com/neuralinitiative/claude-dnd-skill/security/advisories/new)
rather than opening a public issue.

Include what you can: the affected file or script, what an attacker could do
with it, and the steps to reproduce. A proof of concept helps but is not
required to file.

We aim to acknowledge a report within three working days and to keep you
updated while it is being worked on. If a fix ships, you are credited in the
advisory unless you would rather not be.

## Supported versions

Fixes land on `main` and go out in the next release. There are no long-lived
maintenance branches, so please check against `main` before reporting.

## Scope

This skill runs locally inside Claude Code and reads and writes campaign files
on your own machine. The things worth reporting are:

- Any script under `skills/` or `scripts/` that writes outside its campaign
  directory, or that can be made to by crafted campaign content
- Command or path injection reachable from campaign files, imported PDFs, or
  other untrusted input
- Anything in the optional `dice-server/` that is reachable from outside
  localhost, or that discloses more than dice results
- Secrets or credentials committed to this repository

Out of scope: the behaviour of Claude itself, findings that require an
attacker to already control the machine, and reports from automated scanners
without a demonstrated impact on this codebase.

## A note on scanner findings

Automated secret scanners have flagged constants in this repository whose
*names* contain words like `TOKEN` but which hold Markdown labels, not
credentials. `RULESET_LABEL` in `skills/dnd/scripts/migrate_ruleset.py` is one
such case and was renamed for exactly that reason. If a scan flags something
here, please check what the value actually is before filing, and say so in the
report if you believe it is a true positive.
