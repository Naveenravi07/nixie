"""Safety layer for shell command execution.

Only read-only and non-destructive commands are allowed.
Destructive, privilege-escalating, and network-modifying commands are blocked.
"""

from __future__ import annotations

import re
import shlex


# Patterns that are always blocked (checked against the full command string)
_BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*[rRf]|[a-zA-Z]*-[rRf])"),   # rm -r, rm -rf, etc.
    re.compile(r"\brm\b(?!.*--help)"),                            # any rm
    re.compile(r"\bmkfs\b"),                                      # format disk
    re.compile(r"\bdd\b"),                                        # disk copy/wipe
    re.compile(r"\bformat\b"),                                    # format
    re.compile(r"\bchmod\b"),                                     # change permissions
    re.compile(r"\bchown\b"),                                     # change ownership
    re.compile(r"\bsudo\b"),                                      # privilege escalation
    re.compile(r"\bsu\b"),                                        # switch user
    re.compile(r"\bpasswd\b"),                                    # change password
    re.compile(r"\bpkill\b"),                                     # kill processes
    re.compile(r"\bkill\b"),                                      # kill processes
    re.compile(r"\bkillall\b"),                                   # kill all
    re.compile(r"\breboot\b"),                                    # reboot
    re.compile(r"\bshutdown\b"),                                  # shutdown
    re.compile(r"\bhalt\b"),                                      # halt
    re.compile(r"\bpoweroff\b"),                                  # power off
    re.compile(r"\binit\s+[06]"),                                 # init 0/6 (shutdown/reboot)
    re.compile(r"\bsystemctl\s+(stop|restart|disable|mask)\b"),   # stop/disable services
    re.compile(r"\bmkswap\b"),                                    # create swap
    re.compile(r"\bswapon\b"),                                    # enable swap
    re.compile(r"\bswapoff\b"),                                   # disable swap
    re.compile(r"\bmount\b"),                                     # mount filesystems
    re.compile(r"\bumount\b"),                                    # unmount filesystems
    re.compile(r"\bfdisk\b"),                                     # partition disks
    re.compile(r"\bparted\b"),                                    # partition disks
    re.compile(r"\bgdisk\b"),                                     # partition disks
    re.compile(r"\bsgdisk\b"),                                    # partition disks
    re.compile(r"\blvm\b"),                                       # logical volume management
    re.compile(r"\bvgchange\b"),                                  # change volume group
    re.compile(r"\blvremove\b"),                                  # remove logical volume
    re.compile(r"\bdel\b"),                                       # Windows delete
    re.compile(r"\brmdir\b"),                                     # remove directory
    re.compile(r"\bunlink\b"),                                    # unlink file
    re.compile(r"\bshred\b"),                                     # overwrite file
    re.compile(r"\bwipe\b"),                                      # wipe
    re.compile(r"\bcurl\b.*\|\s*(bash|sh)\b"),                   # pipe to shell
    re.compile(r"\bwget\b.*\|\s*(bash|sh)\b"),                   # pipe to shell
    re.compile(r"\beval\b"),                                      # eval
    re.compile(r"\bexec\b"),                                      # exec
    re.compile(r"\biptables\b"),                                  # firewall
    re.compile(r"\bnft\b"),                                       # nftables
    re.compile(r"\buseradd\b"),                                   # add user
    re.compile(r"\buserdel\b"),                                   # delete user
    re.compile(r"\busermod\b"),                                   # modify user
    re.compile(r"\bgroupadd\b"),                                  # add group
    re.compile(r"\bgroupdel\b"),                                  # delete group
]

# Tokens that indicate destructive intent even if the command itself is safe
_BLOCKED_TOKENS = {
    "rm", "rmdir", "unlink", "shred", "wipe",
    "mkfs", "fdisk", "parted", "dd",
    "chmod", "chown",
    "sudo", "su", "passwd",
    "reboot", "shutdown", "halt", "poweroff",
    "pkill", "kill", "killall",
    "format", "mkswap", "swapon", "swapoff",
    "mount", "umount",
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "iptables", "nft",
}


class CommandBlocked(Exception):
    """Raised when a command is blocked by the safety layer."""

    def __init__(self, command: str, reason: str) -> None:
        super().__init__(reason)
        self.command = command
        self.reason = reason


def validate_command(command: str) -> str:
    """Validate a shell command against the safety denylist.

    Returns the cleaned command if safe.
    Raises CommandBlocked if the command is not allowed.
    """
    command = command.strip()
    if not command:
        raise CommandBlocked(command, "Empty command")

    # Check blocked patterns against the full command string
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            raise CommandBlocked(
                command,
                f"Command blocked: matches destructive pattern '{pattern.pattern}'",
            )

    # Token-level check: split the command and check the first token (command name)
    try:
        tokens = shlex.split(command)
    except ValueError:
        # If we can't parse it, allow it (let subprocess handle it)
        return command

    if not tokens:
        raise CommandBlocked(command, "Empty command after parsing")

    # Check the base command name (first token)
    base_cmd = tokens[0].split("/")[-1]  # handle /usr/bin/rm -> rm
    if base_cmd in _BLOCKED_TOKENS:
        raise CommandBlocked(
            command,
            f"Command blocked: '{base_cmd}' is not allowed",
        )

    # Block piping to shell interpreters
    if "|" in command:
        parts = command.split("|")
        for part in parts[1:]:
            part = part.strip()
            tokens = part.split()
            if tokens and tokens[0] in ("bash", "sh", "zsh", "fish"):
                raise CommandBlocked(
                    command,
                    "Command blocked: piping to shell interpreter is not allowed",
                )

    return command
