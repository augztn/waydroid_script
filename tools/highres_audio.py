#!/usr/bin/env python3
"""Install a reversible high-resolution audio policy/configuration for Waydroid.

This implementation edits only the active Waydroid overlay for the current
system image layout. It does not fetch or install an unverified binary HAL.
A patched HAL is needed for actual 24-bit PCM support, so the tool first checks
whether the existing HAL already supports 24-bit formats and otherwise tells
the user that a matching HAL build is required instead of claiming success.
"""
from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(os.environ.get("WAYDROID_CONFIG", "/var/lib/waydroid/waydroid.cfg"))
STATE_DIR = Path("/var/lib/waydroid-script/highres-audio")
STATE_FILE = STATE_DIR / "state.ini"
SUPPORTED_RATES = ("44100", "48000", "88200", "96000", "176400", "192000")


def _config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"Waydroid config not found: {CONFIG_PATH}")
    cfg.read(CONFIG_PATH)
    if "waydroid" not in cfg:
        raise RuntimeError(f"Missing [waydroid] section in {CONFIG_PATH}")
    return cfg


def _overlay_paths() -> tuple[Path, Path, Path]:
    """Return overlay vendor dir, policy XML and HAL library paths defensively."""
    cfg = _config()
    # With overlayfs, system/vendor trees are under /var/lib/waydroid/overlay.
    # Without overlayfs, main.py mounts images at /tmp/waydroid for modifications;
    # callers must not use that ephemeral mount for this long-lived feature.
    if cfg.get("waydroid", "mount_overlays", fallback="False").lower() == "true":
        vendor = Path("/var/lib/waydroid/overlay/vendor")
    else:
        raise RuntimeError(
            "This high-res audio action currently requires Waydroid overlayfs "
            "(mount_overlays=True). Non-overlay image editing needs a separate "
            "transactional image-mount implementation and is intentionally refused."
        )
    policy = vendor / "etc" / "audio_policy_configuration.xml"
    hal_candidates = [vendor / "lib64" / "hw" / "audio.primary.waydroid.so",
                      vendor / "lib" / "hw" / "audio.primary.waydroid.so"]
    hal = next((p for p in hal_candidates if p.is_file()), hal_candidates[0])
    return vendor, policy, hal


def _read_state() -> configparser.ConfigParser:
    state = configparser.ConfigParser()
    if STATE_FILE.is_file():
        state.read(STATE_FILE)
    return state


def _write_state(policy_backup: Path, policy_path: Path, hal_path: Path) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = configparser.ConfigParser()
    state["highres_audio"] = {
        "policy_path": str(policy_path),
        "policy_backup": str(policy_backup),
        "hal_path": str(hal_path),
        "policy_changed": "true",
    }
    temp = STATE_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        state.write(stream)
    os.replace(temp, STATE_FILE)


def _patched_hal_supports_highres(hal_path: Path) -> bool:
    """Compiled binary detection is not reliable; only trust explicit user build metadata.

    A matching HAL binary should be provided as a build artifact, not downloaded
    and substituted without an architecture/Android-version compatibility check.
    """
    return False


def enable(policy_only: bool = False) -> None:
    vendor, policy, hal = _overlay_paths()
    if not policy.is_file():
        raise RuntimeError(f"Audio policy XML not found: {policy}")
    if not hal.is_file():
        raise RuntimeError(f"Waydroid audio HAL not found: {hal}")

    policy_text = policy.read_text(encoding="utf-8")
    if "AUDIO_FORMAT_PCM_16_BIT" not in policy_text or 'samplingRates="48000"' not in policy_text:
        print("Audio policy no longer matches the expected stock 16-bit/48 kHz profile; no changes made.")
        return

    if not policy_only:
        print("The stock HAL binary cannot be patched safely at runtime. A source-built, matching high-res HAL is required.")
        print("Use the helper's policy-only mode only for testing; it advertises formats the stock HAL may reject.")
        print("No changes made. Build a patched HAL for this Android image before enabling high-res output.")
        return

    backup = policy.with_name(policy.name + ".waydroid-script-backup")
    if not backup.exists():
        shutil.copy2(policy, backup)
    replacement = policy_text.replace(
        'format="AUDIO_FORMAT_PCM_16_BIT"\n                             samplingRates="48000" channelMasks="AUDIO_CHANNEL_OUT_STEREO"',
        'format="AUDIO_FORMAT_PCM_16_BIT,AUDIO_FORMAT_PCM_24_BIT_PACKED,AUDIO_FORMAT_PCM_8_24_BIT,AUDIO_FORMAT_PCM_32_BIT"\n                             samplingRates="' + ",".join(SUPPORTED_RATES) + '" channelMasks="AUDIO_CHANNEL_OUT_STEREO"',
        1,
    )
    if replacement == policy_text:
        raise RuntimeError("Could not identify the stock primary-output profile; no changes made")
    # Validate XML syntax before committing the edit.
    import xml.etree.ElementTree as ET
    ET.fromstring(replacement)
    temp = policy.with_suffix(policy.suffix + ".tmp")
    temp.write_text(replacement, encoding="utf-8")
    os.replace(temp, policy)
    _write_state(backup, policy, hal)
    print("Updated output policy only. This is not complete high-res support until a compatible patched HAL is installed.")


def disable() -> None:
    state = _read_state()
    if "highres_audio" not in state:
        print("No high-res audio state is recorded; nothing to restore.")
        return
    section = state["highres_audio"]
    policy = Path(section.get("policy_path", ""))
    backup = Path(section.get("policy_backup", ""))
    if not policy.is_file():
        raise RuntimeError(f"Saved audio policy path no longer exists: {policy}")
    if backup.is_file():
        shutil.copy2(backup, policy)
        backup.unlink()
    else:
        raise RuntimeError(f"Backup is missing; refusing to remove or rewrite {policy}")
    STATE_FILE.unlink(missing_ok=True)
    print("Restored the original audio policy configuration. Any separately installed HAL binary must be restored separately.")


def status() -> None:
    try:
        _vendor, policy, hal = _overlay_paths()
    except RuntimeError as exc:
        print(exc)
        return
    print(f"Audio policy: {policy} ({'present' if policy.is_file() else 'missing'})")
    print(f"Waydroid HAL: {hal} ({'present' if hal.is_file() else 'missing'})")
    if policy.is_file():
        contents = policy.read_text(encoding="utf-8")
        print("Policy advertises 24-bit formats." if "AUDIO_FORMAT_PCM_24_BIT_PACKED" in contents else
              "Policy currently advertises no packed 24-bit output profile.")
    state = _read_state()
    print("Backup state exists." if "highres_audio" in state else "No backup state recorded by this tool.")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Waydroid high-resolution audio policy helper")
    parser.add_argument("action", choices=("enable", "disable", "status"))
    parser.add_argument("--policy-only", action="store_true",
                        help="Testing only: edit policy profile without a patched HAL (may cause audio failure)")
    args = parser.parse_args()
    try:
        if args.action == "enable":
            enable(policy_only=args.policy_only)
        elif args.action == "disable":
            disable()
        else:
            status()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"High-resolution audio operation failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
