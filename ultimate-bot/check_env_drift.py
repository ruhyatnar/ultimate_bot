#!/usr/bin/env python3
"""
check_env_drift.py — guard against `.env` / `.env.example` drift.

`ultimate-bot/.env` is the engine's source of truth. `.env.example` is its
documented mirror — and it is not just documentation: `status.py` falls back to
it as a config source when `.env` is missing, and seeds a new `.env` from it.
When the two disagree the template lies about what the bot is running, and
because a tuner push from the dashboard writes `.env` (never the template), that
drift happens easily and silently.

This check parses both files strictly, compares the key sets and values, refuses
to let a real credential reach the template, and exits non-zero on divergence so
it can gate a commit or a deploy.

    ./venv/bin/python3 check_env_drift.py                 # check -> exit 1 on drift
    ./venv/bin/python3 check_env_drift.py --update        # re-mirror the template from .env
    ./venv/bin/python3 check_env_drift.py --quiet         # findings only: no verdict/advice block
    python3 check_env_drift.py                            # stdlib only, any interpreter

`--quiet` keeps the per-check findings but drops the trailing verdict and advice, so an
embedder (the `.githooks/pre-commit` hook) can print its own single, fully-qualified fix
command instead of offering a second, less runnable one.

Optional path overrides (useful for testing or a second host):

    ./venv/bin/python3 check_env_drift.py --env /path/to/.env --template /path/to/.env.example

Exit codes: 0 = in sync (or no `.env` present to compare), 1 = drift/failure.
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(ROOT, ".env")
DEFAULT_TEMPLATE = os.path.join(ROOT, ".env.example")

# Keys that are EXPECTED to differ: .env holds the real value, the template a
# placeholder (or nothing). Add to this list only when the value is genuinely
# per-host or secret — every other key must match exactly.
EXEMPT = {
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_PRIVATE_KEY_PATH",
    "DISCORD_WEBHOOK_URL",
}

# What the template is allowed to hold for an exempt key. A real value here means
# a credential is about to be committed — the check fails.
PLACEHOLDER_RULES = {
    "BINANCE_API_KEY": lambda v: v == "" or "your" in v.lower() or "example" in v.lower(),
    "BINANCE_API_SECRET": lambda v: v == "" or "your" in v.lower() or "example" in v.lower(),
    "BINANCE_PRIVATE_KEY_PATH": lambda v: v == "" or (not v.startswith("/") and v.startswith(".")),
    "DISCORD_WEBHOOK_URL": lambda v: v == "",
}

# Never echo a value for a key that looks sensitive, even if it is not exempt
# (defensive: a new secret key must not be printed by this tool).
SENSITIVE_HINTS = ("KEY", "SECRET", "WEBHOOK", "TOKEN", "PASSWORD", "PRIVATE", "PEM")

ACTIVE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
COMMENTED_RE = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

BOLD, DIM, RED, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"

# Strip colour when the output is captured (npm, CI, a pipe) or NO_COLOR is set.
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    BOLD = DIM = RED = GREEN = YELLOW = RESET = ""


def _fmt(ok, label, detail="", warn=False):
    tag = f"{GREEN}PASS{RESET}" if ok else (f"{YELLOW}WARN{RESET}" if warn else f"{RED}FAIL{RESET}")
    print(f"  [{tag}] {label}" + (f"\n         {detail}" if detail else ""))


def _is_sensitive(key):
    return any(h in key.upper() for h in SENSITIVE_HINTS)


def redact(key, value):
    """Show a value unless its key looks sensitive (defence in depth)."""
    return "<redacted>" if _is_sensitive(key) else value


def parse(path):
    """Parse a dotenv file.

    Returns (active, commented, duplicates, inline_comment_lines). A "commented"
    key is one that appears only as `#KEY=`; the docs forbid inline `# comments`
    on value lines because this dotenv build folds them into the value, so those
    lines are reported but do not fail the check.
    """
    active, commented, duplicates, inline = {}, set(), [], []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                m = COMMENTED_RE.match(line)
                if m:
                    commented.add(m.group(1))
                continue
            m = ACTIVE_RE.match(line)
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            if "#" in value:
                inline.append((lineno, key))
            if key in active:
                duplicates.append((key, lineno))
            active[key] = value
    return active, commented, duplicates, inline


def compare(env, tmpl):
    """Return a list of problem strings describing every divergence."""
    problems = []

    for label, keys in (("only in .env", sorted(set(env) - set(tmpl))),
                        ("only in .env.example", sorted(set(tmpl) - set(env)))):
        if keys:
            problems.append(f"{len(keys)} key(s) {label}: {', '.join(keys)}")

    drifted = [k for k in sorted(set(env) & set(tmpl))
               if env[k] != tmpl[k] and k not in EXEMPT]
    for k in drifted:
        problems.append(f"{k} differs: .env={redact(k, env[k])!r} .env.example={redact(k, tmpl[k])!r}")

    bad_placeholders = []
    for k in sorted(EXEMPT & set(tmpl)):
        rule = PLACEHOLDER_RULES.get(k)
        if rule and not rule(tmpl[k]):
            bad_placeholders.append(k)
    if bad_placeholders:
        problems.append(
            "template holds a REAL value for credential key(s): "
            + ", ".join(f"{k} ({redact(k, tmpl[k])})" for k in bad_placeholders)
        )

    # A non-exempt key that looks like a secret and is mirrored verbatim means a
    # credential outside the known list is on its way into the repository.
    mirrored = [k for k in sorted(set(env) & set(tmpl))
                if k not in EXEMPT and _is_sensitive(k) and env[k] and env[k] == tmpl[k]]
    if mirrored:
        problems.append(
            "sensitive-looking key mirrored verbatim into the template: " + ", ".join(mirrored)
            + " (clear it in the template, or add it to EXEMPT if it is genuinely safe to share)"
        )

    return problems


def update(env, tmpl_path, env_path):
    """Re-mirror the template from .env (comments and credential keys untouched)."""
    with open(tmpl_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    seen, changed, added, commented_out, skipped = set(), [], [], [], []
    out = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            m = ACTIVE_RE.match(raw.rstrip("\n"))
            if m:
                key, value = m.group(1), m.group(2)
                seen.add(key)
                if key in env and key not in EXEMPT and env[key] != value:
                    if _is_sensitive(key):
                        # Never copy something that smells like a credential into
                        # a tracked file, whatever its name is.
                        skipped.append(key)
                        out.append(raw)
                        continue
                    newline = raw.replace(f"{key}={value}", f"{key}={env[key]}", 1)
                    changed.append((key, value, env[key]))
                    out.append(newline)
                    continue
                if key not in env:
                    commented_out.append(key)
                    out.append(f"# not set in the live .env\n# {raw.lstrip()}")
                    continue
                out.append(raw)
                continue
        out.append(raw)

    for key in sorted(set(env) - seen):
        if key in EXEMPT:
            continue
        if _is_sensitive(key):
            skipped.append(key)
            continue
        added.append(key)

    if added or changed or commented_out:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        if added:
            out.append("\n# --- Present in .env but missing from this template "
                       "(added by check_env_drift.py --update) ---\n")
            for key in added:
                out.append(f"{key}={env[key]}\n")

        with open(tmpl_path, "w", encoding="utf-8") as fh:
            fh.writelines(out)

    for key, old, new in changed:
        print(f"  updated {key}: {redact(key, old)!r} -> {redact(key, new)!r}")
    for key in commented_out:
        print(f"  commented out {key} (not set in .env)")
    for key in added:
        print(f"  appended  {key}={redact(key, env[key])!r}")
    for key in skipped:
        print(f"  {YELLOW}SKIPPED{RESET} {key} — name looks sensitive and it is not in EXEMPT; "
              f"refusing to mirror it into a tracked file")
    if not (changed or added or commented_out):
        print(f"  nothing to update — the template already mirrors "
              f"{os.path.basename(env_path)}")
    else:
        print(f"{DIM}  wrote {tmpl_path} (from {os.path.basename(env_path)}){RESET}")


def main():
    ap = argparse.ArgumentParser(description="Check .env against .env.example for drift.")
    ap.add_argument("--env", default=DEFAULT_ENV, help="live config file (default: ultimate-bot/.env)")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE, help="template to verify (default: ultimate-bot/.env.example)")
    ap.add_argument("--update", action="store_true", help="re-mirror the template from the live .env, then verify")
    ap.add_argument("--quiet", action="store_true",
                    help="print the findings but omit the closing verdict/advice block")
    args = ap.parse_args()

    print(f"{BOLD}env drift check{RESET} — {os.path.relpath(args.env, ROOT)} vs {os.path.relpath(args.template, ROOT)}")

    if not os.path.exists(args.template):
        print(f"  [{RED}FAIL{RESET}] template not found: {args.template}")
        return 1
    if not os.path.exists(args.env):
        # Fresh clone / CI without secrets: there is nothing to compare against.
        print(f"  [{YELLOW}SKIP{RESET}] no {os.path.basename(args.env)} here — nothing to compare. "
              f"Copy the template to .env on a real host to enable this check.")
        return 0

    env, env_c, env_dupes, env_inline = parse(args.env)
    tmpl, tmpl_c, tmpl_dupes, tmpl_inline = parse(args.template)
    print(f"{DIM}  .env: {len(env)} active keys | .env.example: {len(tmpl)} active keys{RESET}")

    if args.update:
        print(f"\n{BOLD}--update{RESET}")
        update(env, args.template, args.env)
        tmpl, tmpl_c, tmpl_dupes, tmpl_inline = parse(args.template)
        print()

    failures = 0

    dupes = env_dupes + tmpl_dupes
    if dupes:
        failures += 1
        _fmt(False, f"duplicate keys ({len(dupes)})",
             ", ".join(f"{k} (line {ln})" for k, ln in dupes))
    else:
        _fmt(True, f"no duplicate keys in either file ({len(env)} / {len(tmpl)})")

    inline = env_inline + tmpl_inline
    if inline:
        _fmt(False, f"inline comment on a value line ({len(inline)})",
             ", ".join(f"{k} (line {ln})" for ln, k in inline)
             + " — this dotenv build folds '#' into the value; move the comment above the line",
             warn=True)
    else:
        _fmt(True, "no inline comments on value lines")

    problems = compare(env, tmpl)
    if problems:
        failures += 1
        _fmt(False, f"{len(problems)} difference(s) between .env and .env.example")
        for p in problems:
            print(f"         - {p}")
    else:
        shared = len(set(env) & set(tmpl))
        _fmt(True, f"key sets identical ({shared} keys) and all non-exempt values match")

    exempt_seen = sorted(EXEMPT & set(env) & set(tmpl))
    if exempt_seen:
        _fmt(True, f"{len(exempt_seen)} credential key(s) differ by design, template placeholders intact",
             ", ".join(exempt_seen))

    leaked = [k for k in tmpl if k not in EXEMPT and _is_sensitive(k)]
    if leaked:
        _fmt(False, f"sensitive-looking key outside the exempt list: {', '.join(leaked)}",
             "add it to EXEMPT + PLACEHOLDER_RULES if it is meant to differ", warn=True)

    if failures:
        if not args.quiet:
            print()
            print(f"{RED}{BOLD}DRIFT DETECTED{RESET} — {failures} problem group(s).")
            print(f"  Re-mirror the template from the live config with:  "
                  f"{BOLD}{os.path.basename(sys.argv[0])} --update{RESET}")
            print(f"{DIM}  (Only do that once you are sure .env — not the template — holds the "
                  f"values you intend to run.){RESET}")
        return 1

    if not args.quiet:
        print()
        print(f"{GREEN}{BOLD}IN SYNC{RESET} — .env and .env.example agree on all {len(env)} keys "
              f"(credentials excluded by design).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
