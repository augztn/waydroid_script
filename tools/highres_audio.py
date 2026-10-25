#!/usr/bin/env python3
"""Inspect and safely manage Waydroid high-resolution audio policy settings.

The stock Waydroid HAL hard-codes 48 kHz S16_LE playback. This helper does not
pretend that an XML-only change enables high-resolution audio. It supports
status reporting and a reversible policy-only test behind an explicit flag.
A matching, source-built high-resolution HAL is required for a complete fix.
"""
from __future__ import annotations

import configparser
import os
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

CONFIG_PATH = Path(os.environ.get("WAYDROID_CONFIG", "/var/lib/waydroid/waydroid.cfg"))
STATE_DIR = Path("/var/lib/waydroid-script/highres-audio")
STATE_FILE = STATE_DIR / "state.ini"
SUPPORTED_RATES = ("44100", "48000", "88200", "96000", "176400", "192000")
HAL_NAME = "audio.primary.waydroid.so"


def _config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"Waydroid config not found: {CONFIG_PATH}")
    cfg.read(CONFIG_PATH)
    if "waydroid" not in cfg:
        raise RuntimeError(f"Missing [waydroid] section in {CONFIG_PATH}")
    return cfg


def _overlay_paths() -> tuple[Path, Path, Path]:
    """Resolve the persisted vendor overlay and fail closed for unsupported layout."""
    cfg = _config()
    if cfg.get("waydroid", "mount_overlays", fallback="False").lower() != "true":
        raise RuntimeError(
            "This command currently supports Waydroid overlayfs only. "
            "Non-overlay image updates require a separate transactional mount workflow."
        )
    overlay_root = Path("/var/lib/waydroid/overlay")
    vendor = overlay_root / "vendor"
    policy = vendor / "etc" / "audio_policy_configuration.xml"
    hal = next((p for p in (
        vendor / "lib64" / "hw" / HAL_NAME,
        vendor / "lib" / "hw" / HAL_NAME,
    ) if p.is_file()), vendor / "lib64" / "hw" / HAL_NAME)
    return vendor, policy, hal


def _read_state() -> configparser.ConfigParser:
    state = configparser.ConfigParser()
    if STATE_FILE.is_file():
        state.read(STATE_FILE)
    return state


def _save_state(policy: Path, backup: Path) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = configparser.ConfigParser()
    state["highres_audio"] = {
        "policy_path": str(policy),
        "policy_backup": str(backup),
    }
    temporary = STATE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        state.write(handle)
    os.replace(temporary, STATE_FILE)


def enable(policy_only: bool = False) -> None:
    _vendor, policy, hal = _overlay_paths()
    if not policy.is_file():
        raise RuntimeError(f"Audio policy XML not found: {policy}")
    if not hal.is_file():
        raise RuntimeError(f"Waydroid audio HAL not found: {hal}")

    text = policy.read_text(encoding="utf-8")
    if "AUDIO_FORMAT_PCM_24_BIT_PACKED" in text:
        print("The audio policy already advertises packed 24-bit PCM; no policy change made.")
        print("Check the installed HAL and active stream format separately.")
        return

    if not policy_only:
        print("No changes made.")
        print("The stock HAL hard-codes 48 kHz S16_LE output; policy XML alone cannot enable 24-bit audio.")
        print("Install a source-built HAL matching your Waydroid Android branch and architecture first.")
        print("Use --policy-only only for testing; the stock HAL may reject the advertised formats.")
        return

    expected = (
        'format="AUDIO_FORMAT_PCM_16_BIT"\n'
        '                             samplingRates="48000" channelMasks="AUDIO_CHANNEL_OUT_STEREO"'
    )
    if expected not in text:
        raise RuntimeError("Could not identify the expected primary-output XML profile; no changes made")

    replacement = text.replace(
        expected,
        'format="AUDIO_FORMAT_PCM_16_BIT,AUDIO_FORMAT_PCM_24_BIT_PACKED,AUDIO_FORMAT_PCM_8_24_BIT,AUDIO_FORMAT_PCM_32_BIT"\n'
        '                             samplingRates="' + ",".join(SUPPORTED_RATES)
        + '" channelMasks="AUDIO_CHANNEL_OUT_STEREO"',
        1,
    )
    if replacement == text:
        raise RuntimeError("Audio policy was not changed; no file was written")
    ET.fromstring(replacement)

    backup = policy.with_name(policy.name + ".waydroid-script-backup")
    if backup.exists():
        raise RuntimeError(f"Backup already exists at {backup}; restore or inspect it before retrying")

    # Commit the backup first. Keep it if later operations fail so recovery is possible.
    shutil.copy2(policy, backup)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _save_state(policy, backup)
    temporary = policy.with_suffix(policy.suffix + ".tmp")
    try:
        temporary.write_text(replacement, encoding="utf-8")
        ET.parse(temporary)
        os.replace(temporary, policy)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    print("Updated the policy XML only. This does not enable high-res playback without a compatible patched HAL.")
    print("If audio fails, run: sudo venv/bin/python3 main.py highres-audio disable")


def disable() -> None:
    state = _read_state()
    if "highres_audio" in state:
        section = state["highres_audio"]
        policy = Path(section.get("policy_path", ""))
        backup = Path(section.get("policy_backup", ""))
    else:
        try:
            _vendor, policy, _hal = _overlay_paths()
        except RuntimeError as exc:
            raise RuntimeError(f"No backup state exists. {exc}") from exc
        backup = policy.with_name(policy.name + ".waydroid-script-backup")

    if not backup.is_file():
        raise RuntimeError(f"Policy backup not found: {backup}; refusing to modify anything")
    if not policy.is_file():
        raise RuntimeError(f"Policy path no longer exists: {policy}")

    temporary = policy.with_suffix(policy.suffix + ".restore.tmp")
    try:
        shutil.copy2(backup, temporary)
        ET.parse(temporary)
        os.replace(temporary, policy)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    backup.unlink()
    STATE_FILE.unlink(missing_ok=True)
    print("Restored the original Waydroid audio policy XML.")
    print("If you separately installed a custom HAL, restore that HAL separately.")


def status() -> None:
    try:
        _vendor, policy, hal = _overlay_paths()
    except RuntimeError as exc:
        print(str(exc))
        return
    print(f"Policy XML: {policy} ({'present' if policy.is_file() else 'missing'})")
    print(f"HAL library: {hal} ({'present' if hal.is_file() else 'missing'})")
    if policy.is_file():
        contents = policy.read_text(encoding="utf-8")
        if "AUDIO_FORMAT_PCM_24_BIT_PACKED" in contents:
            print("The policy advertises packed 24-bit PCM. This alone does not prove HAL support.")
        else:
            print("The policy does not advertise packed 24-bit PCM.")
    state = _read_state()
    print("A backup is recorded by this helper." if "highres_audio" in state else
          "No active policy backup is recorded by this helper.")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Waydroid high-resolution audio policy helper")
    parser.add_argument("action", choices=("enable", "disable", "status"))
    parser.add_argument(
        "--policy-only",
        action="store_true",
        help="Testing only: edit policy XML without a patched HAL (may cause audio failure)",
    )
    args = parser.parse_args()
    try:
        if args.action == "enable":
            enable(policy_only=args.policy_only)
        elif args.action == "disable":
            disable()
        else:
            status()
    except (OSError, RuntimeError, ET.ParseError) as exc:
        print(f"High-resolution audio operation failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
