#!/usr/bin/env bash
#
# Install the versioned git hooks in .githooks/ into this clone.
#
# Git runs hooks from .git/hooks/, which is never committed — so the hook lives in
# .githooks/pre-commit (versioned) and this script links/copies it into place. Idempotent.
#
#   npm run hooks:install      # or: bash scripts/install-hooks.sh
#
# Teams can skip this entirely with a single line, which makes git read .githooks/
# directly (note: that is a git-config change, so this script does not do it for you):
#
#   git config core.hooksPath .githooks
#
set -euo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "install-hooks: not inside a git repository." >&2
    exit 1
}
cd "$root"

src=".githooks/pre-commit"
dst=".git/hooks/pre-commit"

[ -f "$src" ] || {
    echo "install-hooks: $src not found (run this from the repository root)." >&2
    exit 1
}

chmod +x "$src"
mkdir -p .git/hooks

# Don't silently clobber a hook someone else put here.
if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    backup="$dst.bak-$(date +%Y%m%d%H%M%S)"
    mv "$dst" "$backup"
    echo "install-hooks: existing hook moved aside -> $backup"
fi

# A symlink keeps the installed hook in step with the versioned one; fall back to
# a copy on filesystems where symlinks aren't available (or are disallowed).
if ln -sfn "../../$src" "$dst" 2>/dev/null && [ -x "$dst" ] && [ -L "$dst" ]; then
    echo "install-hooks: $dst -> $src (symlink)"
else
    cp "$src" "$dst"
    chmod +x "$dst"
    echo "install-hooks: $dst (copy of $src)"
fi

echo "install-hooks: done — commits now run the .env drift check (bypass: git commit --no-verify)"
