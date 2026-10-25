#!/usr/bin/env python3
"""Manage an opt-in, reversible 24-bit PCM host audio override.

This configures the PipeWire PulseAudio-compatible layer only. It does not
change Android AudioFlinger capabilities or guarantee 24-bit output end-to-end.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

BACKUP_SUFFIX = ".waydroid-script-backup"
CONFIG_CONTENT = '''# Managed by waydroid_script: optional 24-bit PCM defaults.
# Host PulseAudio-compatible server default format. Android and the physical
# output device may still negotiate or convert to another supported format.
pulse.default.format = "s24le"
'''


def _run(args: Iterable[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and return decoded output."""
    return subprocess.run(
        list(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=check, timeout=15,
    )


def _desktop_user() -> tuple[Path, int, int]:
    """Resolve the actual desktop user when the caller invoked the script via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        try:
            record = pwd.getpwnam(sudo_user)
        except KeyError as exc:
            raise RuntimeError(f"Cannot resolve invoking user {sudo_user!r}") from exc
        return Path(record.pw_dir), record.pw_uid, record.pw_gid
    return Path.home(), os.getuid(), os.getgid()


def _restart_user_audio(uid: int) -> None:
    """Restart pipewire-pulse in the target user's session when available."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        print("Configuration written; systemctl is unavailable, so restart audio manually.")
        return

    runtime_dir = Path(f"/run/user/{uid}")
    if not runtime_dir.is_dir():
        print("Configuration written; the desktop user's session is not active for automatic restart.")
        print("Restart your user audio service or log out and back in to apply it.")
        return

    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    bus = runtime_dir / "bus"
    if bus.exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"

    result = subprocess.run(
        [systemctl, "--user", "restart", "pipewire-pulse.service"],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, check=False,
    )
    if result.returncode:
        print("Configuration written, but PipeWire PulseAudio could not be restarted automatically.")
        print("Restart your user audio service or log out and back in to apply it.")


def _require_pipewire(home: Path) -> None:
    """Avoid writing a PipeWire-specific drop-in on PulseAudio-only systems."""
    if not (home / ".config" / "pipewire").is_dir() and not shutil.which("pipewire"):
        raise RuntimeError(
            "PipeWire was not detected. This command configures PipeWire's "
            "PulseAudio-compatible server, not standalone PulseAudio."
        )


def enable() -> None:
    home, uid, gid = _desktop_user()
    _require_pipewire(home)
    config_dir = home / ".config" / "pipewire" / "pipewire-pulse.conf.d"
    config_file = config_dir / "90-waydroid-24bit.conf"
    backup = config_file.with_name(config_file.name + BACKUP_SUFFIX)
    config_dir.mkdir(parents=True, exist_ok=True)

    if config_file.exists():
        current = config_file.read_text(encoding="utf-8")
        if current == CONFIG_CONTENT:
            print(f"24-bit audio override is already enabled: {config_file}")
            return
        # Preserve custom content once; never overwrite an earlier backup.
        if not backup.exists():
            shutil.copy2(config_file, backup)

    config_file.write_text(CONFIG_CONTENT, encoding="utf-8")
    if os.geteuid() == 0:
        os.chown(config_file, uid, gid)
        os.chown(config_dir, uid, gid)

    print(f"Wrote host audio configuration: {config_file}")
    print("This requests 24-bit PCM at the host audio layer; it does not prove end-to-end 24-bit playback.")
    _restart_user_audio(uid)


def disable() -> None:
    home, uid, _gid = _desktop_user()
    config_file = home / ".config" / "pipewire" / "pipewire-pulse.conf.d" / "90-waydroid-24bit.conf"
    backup = config_file.with_name(config_file.name + BACKUP_SUFFIX)

    if not config_file.exists():
        print("24-bit audio override is not installed.")
        return

    if backup.exists():
        shutil.copy2(backup, config_file)
        if os.geteuid() == 0:
            import pwd
            _record = pwd.getpwuid(uid)
            os.chown(config_file, _record.pw_uid, _record.pw_gid)
        backup.unlink()
        print(f"Restored previous configuration: {config_file}")
    else:
        config_file.unlink()
        print(f"Removed 24-bit audio configuration: {config_file}")
    _restart_user_audio(uid)


def status() -> None:
    home, _uid, _gid = _desktop_user()
    config_file = home / ".config" / "pipewire" / "pipewire-pulse.conf.d" / "90-waydroid-24bit.conf"
    print("24-bit host audio override is enabled." if config_file.exists() else
          "24-bit host audio override is not enabled by this script.")

    pactl = shutil.which("pactl")
    if not pactl:
        print("pactl is not installed; active audio server details are unavailable.")
        return
    result = _run([pactl, "info"], check=False)
    if result.returncode:
        print("Could not query the active PulseAudio-compatible server.")
        return
    for line in result.stdout.splitlines():
        if "Server Name:" in line or "Default Sample Specification:" in line:
            print(line.strip())


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Optional reversible host 24-bit PCM audio override")
    parser.add_argument("action", choices=("enable", "disable", "status"))
    args = parser.parse_args()
    try:
        {"enable": enable, "disable": disable, "status": status}[args.action]()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Audio configuration failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
