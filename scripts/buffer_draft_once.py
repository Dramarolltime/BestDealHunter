#!/usr/bin/env python3
"""Run the Buffer draft-check at most ONCE per deal, across separate GitHub Actions runs.

The guard is a git tag on the remote, refs/tags/buffer-draft-check/<card id>, created
BEFORE Buffer is contacted. Creating a tag that already exists is rejected by the remote
(no --force is ever used), so even two runs racing each other, or the approval label
being removed and re-added, can only ever get one lock. The lock is never released by
this script: a failed or ambiguous draft attempt also keeps it, and a retry requires a
person to delete the tag on purpose.

  python scripts/buffer_draft_once.py 9ace7211bab6   # needs BUFFER_API_KEY + BUFFER_CHANNEL_ID
"""
import os
import subprocess
import sys

from publish_buffer import CARD_ID, run_draft_check

TAG_PREFIX = "buffer-draft-check/"
REFUSED = 3


class LockError(Exception):
    pass


def git(args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def lock_tag(card):
    if not CARD_ID.fullmatch(str(card)):
        raise LockError(f"invalid card id {card!r}")
    return TAG_PREFIX + card


def remote_has(tag, remote, cwd):
    """True/False from `git ls-remote --exit-code`; any other outcome fails closed."""
    result = git(["ls-remote", "--exit-code", "--tags", remote, f"refs/tags/{tag}"], cwd)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    raise LockError(f"could not check lock on {remote}: {result.stderr.strip() or result.returncode}")


def acquire(card, note, remote="origin", cwd=None):
    """Create the remote lock tag; return (acquired, message). Never overwrites an existing tag."""
    tag = lock_tag(card)
    if remote_has(tag, remote, cwd):
        return False, f"lock refs/tags/{tag} already exists: this draft-check already ran"
    made = git(["-c", "user.name=github-actions[bot]",
                "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "tag", "-a", tag, "-m", note, "HEAD"], cwd)
    if made.returncode != 0:
        return False, f"could not create local lock tag: {made.stderr.strip()}"
    pushed = git(["push", remote, f"refs/tags/{tag}:refs/tags/{tag}"], cwd)
    if pushed.returncode != 0:  # e.g. another run created it first: "already exists"
        return False, f"lock refs/tags/{tag} was not acquired: {pushed.stderr.strip()}"
    return True, f"lock refs/tags/{tag} acquired"


def main(argv=None, env=None, draft=run_draft_check, remote="origin", cwd=None, log=print):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if len(argv) != 1:
        log("usage: buffer_draft_once.py <card id>")
        return 2
    card = argv[0]
    note = (f"Buffer draft-check for card {card}; run {env.get('GITHUB_RUN_ID', 'local')} "
            f"attempt {env.get('GITHUB_RUN_ATTEMPT', '1')}. Delete this tag only to deliberately allow a retry.")
    try:
        acquired, message = acquire(card, note, remote, cwd)
    except LockError as exc:
        acquired, message = False, str(exc)
    log(message)
    if not acquired:
        log("Refused: no Buffer request was made.")
        return REFUSED
    return draft(card, env)


if __name__ == "__main__":
    sys.exit(main())
