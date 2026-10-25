#!/usr/bin/env python3
"""Manage an opt-in 24-bit PCM configuration for the host PipeWire PulseAudio layer.

This does not modify Android system/vendor images and cannot guarantee that
Waydroid applications or the physical audio device use 24-bit end-to-end.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

CONFIG_DIR = Path.home() / ".config" / "pipewire" / "pipewire-pulse.conf.d"
CONFIG_FILE = CONFIG_DIR / "90-waydroid-24bit.conf"
BACKUP_SUFFIX = ".waydroid-script-backup"

# pipewire-pulse's PulseAudio-compatible module option supports these formats.
# s24le is packed 24-bit PCM; s24-32le is 24-bit PCM in a 32-bit container.
CONFIG_CONTENT = '''# Managed by waydroid_script: optional 24-bit PCM defaults.
# This sets the PulseAudio-compatible server's default sink format; it does not
# change Android AudioFlinger capabilities or force every physical device to
# support 24-bit output. Remove this file to revert the setting.
pulse.default.format = "s24le"
'''


def _run(args: Iterable[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and return decoded output."""
    return subprocess.run(
        list(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=check, timeout=15,
    )


def _desktop_user_home() -> Path:
    """Resolve the invoking user's home even when the CLI was run via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError as exc:
            raise RuntimeError(f"Cannot resolve invoking user {sudo_user!r}") from exc
    return Path.home()


def _pipewire_pulse_available() -> bool:
    return shutil.which("pactl") is not None and shutil.which("systemctl") is not None


def _restart_user_audio(home: Path) -> None:
    """Ask the desktop user's user manager to restart PipeWire PulseAudio."""
    uid = None
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        uid = pwd.getpwnam(sudo_user).pw_uid
    else:
        uid = os.getuid()

    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    bus = Path(env["XDG_RUNTIME_DIR"]) / "bus"
    if bus.exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    # The file is installed even if no active user manager is available; don't
    # turn a successful configuration write into a misleading hard failure.
    result = subprocess.run(
        ["systemctl", "--user", "restart", "pipewire-pulse.service"],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, check=False,
    )
    if result.returncode:
        print("Configuration written. Could not restart the user audio service automatically.")
        print("Restart PipeWire/PulseAudio from your desktop session or log out and back in.")


def enable() -> None:
    home = _desktop_user_home()
    config_dir = home / ".config" / "pipewire" / "pipewire-pulse.conf.d"
    config_file = config_dir / "90-waydroid-24bit.conf"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Preserve an existing user file rather than overwriting custom content.
    if config_file.exists():
        backup = config_file.with_name(config_file.name + ".waydroid-script-backup")
        if not backup.exists():
            shutil.copy2(config_file, backup)

    if config_file.exists() and config_file.read_text(encoding="utf-8") == CONFIG_CONTENT:
        print(f"24-bit audio configuration is already enabled: {config_file}")
        return

    config_file.write_text(CONFIG_CONTENT, encoding="utf-8")
    print(f"Wrote PipeWire PulseAudio configuration: {config_file}")
    print("Note: 24-bit output still depends on application, Android/Waydroid and hardware support.")
    _restart_user_audio(home)


def disable() -> None:
    home = _desktop_user_home()
    config_file = home / ".config" / "pipewire" / "pipewire-pulse.conf.d" / "90-waydroid-24bit.conf"
    backup = config_file.with_name(config_file.name + BACKUP_SUFFIX)

    if not config_file.exists():
        print("24-bit audio override is not installed.")
        return

    if backup.exists():
        shutil.copy2(backup, config_file)
        backup.unlink()
        print(f"Restored previous configuration: {config_file}")
    else:
        config_file.unlink()
        print(f"Removed 24-bit audio configuration: {config_file}")
    _restart_user_audio(home)


def status() -> None:
    home = _desktop_user_home()
    config_file = home / ".config" / "pipewire" / "pipewire-pulse.conf.d" / "90-waydroid-24bit.conf"
    if config_file.exists():
        print(f"24-bit audio override file exists: {config_file}")
    else:
        print("24-bit audio override is not enabled by this script.")

    pactl = shutil.which("pactl")
    if pactl:
        result = _run([pactl, "info"], check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "Server Name:" in line or "Default Sample Specification:" in line:
                    print(line.strip())
        else:
            print("Could not query the active PulseAudio-compatible server.")
    else:
        print("pactl is not installed; unable to inspect the active audio server.")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Optional reversible host 24-bit PCM audio configuration")
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
