#!/usr/bin/env python3
"""Install/restore a checksum-guarded Waydroid high-resolution audio overlay.

Patch recipe adapted from the MIT-licensed community project:
https://github.com/willbeez/waydroid-apple-music
It supports one tested x86_64 Android 13 vendor image only. All source and
patched-file hashes are checked; unknown images are rejected without changes.

The Waydroid overlay provides a 32-bit/192 kHz host audio link on that image.
An optional host PipeWire drop-in can pin the shared graph to 192 kHz; this
affects all desktop audio and is disabled by default. Neither setting guarantees
bit-perfect playback because Android routing/mixing, the app and output device
remain part of the path.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("WAYDROID_CONFIG", "/var/lib/waydroid/waydroid.cfg"))
DEFAULT_VENDOR_IMAGE = Path("/var/lib/waydroid/images/vendor.img")
OVERLAY_ROOT = Path("/var/lib/waydroid/overlay/vendor")
STATE_DIR = Path("/var/lib/waydroid-script/highres-audio")
STATE_PATH = STATE_DIR / "state.json"
BACKUP_DIR = STATE_DIR / "backups"
PIPEWIRE_DROPIN_NAME = "99-waydroid-script-highres.conf"
PIPEWIRE_CONTENT = """# Managed by waydroid_script. Restored by highres-audio disable.
# This pins the shared desktop graph to 192 kHz; other apps may be resampled.
context.properties = {
    default.clock.rate = 192000
    default.clock.allowed-rates = [ 192000 ]
}
"""

FILES: dict[str, dict[str, Any]] = {
    "lib/hw/audio.primary.waydroid.so": {
        "source_sha256": "cac012cfc7d8b1852635afdf34d160f7b9524e927fba3f17c9424b1b2b639cd2",
        "patched_sha256": "8dfce910cf5ab8d9c09d41931929f9c44d5d7dbfc39209bea43dc485c97aa181",
        "edits": {
            0x0894: ("80bb0000", "00ee0200"),
            0x1D4C: ("02000000", "0a000000"),
            0x1D59: ("80bb0000", "00ee0200"),
            0x1DAA: ("80bb0000", "00ee0200"),
            0x1DB1: ("01000000", "03000000"),
            0x1DCE: ("80bb0000", "00ee0200"),
            0x1DD3: ("02", "0a"),
        },
    },
    "lib64/hw/audio.primary.waydroid.so": {
        "source_sha256": "f44860755c9674289322cf1394477a1c840be312dc795a85b932ae98d487072d",
        "patched_sha256": "1359f72b9a54a364f334f12e1c1851c78743a0865ec506cf2bfa9a1e2ab3e740",
        "edits": {
            0x0D84: ("80bb0000", "00ee0200"),
            0x2185: ("02000000", "0a000000"),
            0x219A: ("80bb0000", "00ee0200"),
            0x21ED: ("80bb0000", "00ee0200"),
            0x21FC: ("01000000", "03000000"),
            0x221A: ("80bb0000", "00ee0200"),
            0x2220: ("02000000", "0a000000"),
        },
    },
}
POLICY_PATH = "etc/audio_policy_configuration.xml"
POLICY_SOURCE_SHA256 = "b649d92f3fd2d3a08630da296d5c9ddac39d6978780ff268446623f959585c7b"
POLICY_PATCHED_SHA256 = "10c5585054a1dd3bc0aa622947f63639b9a3eff68b58a0934d58a5102fd3defa"
POLICY_OLD = (
    b'format="AUDIO_FORMAT_PCM_16_BIT"\n'
    b'                             samplingRates="48000" '
    b'channelMasks="AUDIO_CHANNEL_OUT_STEREO"'
)
POLICY_NEW = (
    b'format="AUDIO_FORMAT_PCM_32_BIT"\n'
    b'                             samplingRates="192000" '
    b'channelMasks="AUDIO_CHANNEL_OUT_STEREO"'
)
MANAGED_FILES = {
    "lib/hw/audio.primary.waydroid.so": ("lib", "hw", "audio.primary.waydroid.so"),
    "lib64/hw/audio.primary.waydroid.so": ("lib64", "hw", "audio.primary.waydroid.so"),
    POLICY_PATH: ("etc", "audio_policy_configuration.xml"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _config() -> configparser.ConfigParser:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"Waydroid config not found: {CONFIG_PATH}")
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONFIG_PATH)
    if "waydroid" not in cfg:
        raise RuntimeError(f"Missing [waydroid] section in {CONFIG_PATH}")
    return cfg


def _vendor_image_path() -> Path:
    cfg = _config()
    images_dir = Path(cfg.get("waydroid", "images_path", fallback=str(DEFAULT_VENDOR_IMAGE.parent)))
    return images_dir / "vendor.img"


def _extract(vendor_image: Path, relative: str) -> bytes:
    if not shutil.which("debugfs"):
        raise RuntimeError("debugfs is required; install e2fsprogs")
    result = subprocess.run(
        ["debugfs", "-R", f"cat /{relative}", str(vendor_image)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"Cannot extract /{relative} from {vendor_image}: {detail}")
    return result.stdout


def patch_binary(relative: str, source: bytes) -> bytes:
    """Patch one precisely identified HAL binary; reject all unknown inputs."""
    if relative not in FILES:
        raise ValueError(f"No tested high-resolution patch exists for {relative}")
    spec = FILES[relative]
    actual_hash = sha256(source)
    if actual_hash != spec["source_sha256"]:
        raise ValueError(
            f"Unsupported {relative}: expected source SHA-256 "
            f"{spec['source_sha256']}, found {actual_hash}"
        )
    patched = bytearray(source)
    for offset, (expected_hex, replacement_hex) in spec["edits"].items():
        expected = bytes.fromhex(expected_hex)
        replacement = bytes.fromhex(replacement_hex)
        if len(expected) != len(replacement):
            raise ValueError(f"Patch length mismatch at offset 0x{offset:x}")
        actual = bytes(patched[offset:offset + len(expected)])
        if actual != expected:
            raise ValueError(
                f"Unexpected bytes at offset 0x{offset:x} in {relative}: "
                f"expected {expected.hex()}, found {actual.hex()}"
            )
        patched[offset:offset + len(expected)] = replacement
    output = bytes(patched)
    output_hash = sha256(output)
    if output_hash != spec["patched_sha256"]:
        raise ValueError(
            f"Patched SHA-256 mismatch for {relative}: {output_hash}; refusing output"
        )
    return output


def patch_policy(source: bytes) -> bytes:
    actual_hash = sha256(source)
    if actual_hash != POLICY_SOURCE_SHA256:
        raise ValueError(
            f"Unsupported {POLICY_PATH}: expected source SHA-256 "
            f"{POLICY_SOURCE_SHA256}, found {actual_hash}"
        )
    if source.count(POLICY_OLD) != 1:
        raise ValueError("Could not identify the known primary output profile")
    patched = source.replace(POLICY_OLD, POLICY_NEW, 1)
    if sha256(patched) != POLICY_PATCHED_SHA256:
        raise ValueError("Patched audio policy checksum did not match the tested output")
    return patched


def build_overlay(vendor_image: Path, output_root: Path) -> dict[str, str]:
    """Validate all source files, patch them in memory, and only then write output."""
    if not vendor_image.is_file():
        raise FileNotFoundError(f"Waydroid vendor image not found: {vendor_image}")

    outputs: dict[str, bytes] = {}
    for relative in FILES:
        outputs[relative] = patch_binary(relative, _extract(vendor_image, relative))
    outputs[POLICY_PATH] = patch_policy(_extract(vendor_image, POLICY_PATH))

    manifest: dict[str, str] = {}
    for relative, data in outputs.items():
        destination = output_root / "vendor" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(0o644)
        manifest[relative] = sha256(data)
    return manifest


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _read_state() -> dict[str, Any] | None:
    if not STATE_PATH.is_file():
        return None
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read audio patch state {STATE_PATH}: {exc}") from exc
    if payload.get("version") != 1 or not isinstance(payload.get("files"), dict):
        raise RuntimeError("Audio patch state is invalid or unsupported; refusing to overwrite it")
    return payload


def _overlay_path(relative: str) -> Path:
    return OVERLAY_ROOT.joinpath(*MANAGED_FILES[relative])


def _set_overlay_config(value: str | None) -> None:
    cfg = _config()
    if value is None:
        cfg.remove_option("waydroid", "mount_overlays")
    else:
        cfg.set("waydroid", "mount_overlays", value)

    fd, temp_name = tempfile.mkstemp(prefix="waydroid.cfg.", suffix=".tmp", dir=CONFIG_PATH.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            cfg.write(stream, space_around_delimiters=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, CONFIG_PATH)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _desktop_identity() -> tuple[Path, int, int]:
    """Resolve the real desktop account; never put user settings in root's home."""
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        raise RuntimeError(
            "--host-pipewire-192k requires running the command with sudo from the desktop user's session"
        )
    try:
        record = pwd.getpwnam(sudo_user)
    except KeyError as exc:
        raise RuntimeError(f"Cannot resolve desktop user {sudo_user!r}") from exc
    return Path(record.pw_dir), record.pw_uid, record.pw_gid


def _prepare_pipewire_state() -> dict[str, Any]:
    home, uid, gid = _desktop_identity()
    if not shutil.which("pipewire"):
        raise RuntimeError("PipeWire was not detected; omit --host-pipewire-192k on PulseAudio-only systems")
    path = home / ".config" / "pipewire" / "pipewire.conf.d" / PIPEWIRE_DROPIN_NAME
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlinked PipeWire config: {path}")
    if path.exists() and not path.is_file():
        raise RuntimeError(f"PipeWire config path is not a regular file: {path}")
    return {
        "enabled": True,
        "path": str(path),
        "existed": path.is_file(),
        "backup": str(BACKUP_DIR / "pipewire-highres.conf.bak") if path.is_file() else None,
        "installed_sha256": sha256(PIPEWIRE_CONTENT.encode("utf-8")),
        "uid": uid,
        "gid": gid,
        "original_uid": path.stat().st_uid if path.is_file() else None,
        "original_gid": path.stat().st_gid if path.is_file() else None,
        "original_mode": path.stat().st_mode & 0o777 if path.is_file() else None,
    }


def _apply_pipewire_dropin(info: dict[str, Any]) -> None:
    path = Path(info["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlinked PipeWire config: {path}")
    temporary = path.with_name(path.name + ".highres-audio.new")
    try:
        temporary.write_text(PIPEWIRE_CONTENT, encoding="utf-8")
        temporary.chmod(0o644)
        os.chown(temporary, int(info["uid"]), int(info["gid"]))
        if sha256(temporary.read_bytes()) != info["installed_sha256"]:
            raise RuntimeError("Generated PipeWire drop-in failed checksum verification")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _restore_pipewire_dropin(info: dict[str, Any], *, rollback: bool = False) -> bool:
    """Restore/remove the drop-in only if it still matches the version we installed."""
    path = Path(info["path"])
    backup_text = info.get("backup")
    backup = Path(backup_text) if backup_text else None
    existed = bool(info.get("existed"))

    if existed and backup and backup.is_file() and path.is_file():
        if sha256(path.read_bytes()) == sha256(backup.read_bytes()):
            return True  # already restored by an earlier partial attempt
    elif not existed and not path.exists():
        return True  # already removed by an earlier partial attempt

    if not path.is_file() or sha256(path.read_bytes()) != info["installed_sha256"]:
        if rollback:
            raise RuntimeError(f"PipeWire drop-in changed during installation; refusing rollback of {path}")
        print(f"Leaving user-changed PipeWire config alone: {path}")
        return False

    if existed:
        if not backup or not backup.is_file():
            raise RuntimeError(f"Required PipeWire config backup is missing for {path}")
        temporary = path.with_name(path.name + ".highres-audio.restore")
        try:
            shutil.copy2(backup, temporary)
            os.chown(temporary, int(info["original_uid"]), int(info["original_gid"]))
            os.chmod(temporary, int(info["original_mode"]))
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    else:
        path.unlink()
    return True


def _restart_pipewire(uid: int) -> None:
    systemctl = shutil.which("systemctl")
    runtime_dir = Path(f"/run/user/{uid}")
    bus = runtime_dir / "bus"
    if not systemctl or not runtime_dir.is_dir() or not bus.exists():
        print("PipeWire drop-in saved/restored, but the desktop session could not be restarted automatically.")
        print("Restart PipeWire, PipeWire-Pulse and WirePlumber in your desktop session.")
        return
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    result = subprocess.run(
        [systemctl, "--user", "restart", "pipewire.service", "pipewire-pulse.service", "wireplumber.service"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    if result.returncode:
        print("PipeWire configuration saved/restored, but the service restart failed.")
        print("Restart PipeWire, PipeWire-Pulse and WirePlumber from your desktop session.")


def _install_overlay(
    staged_root: Path,
    manifest: dict[str, str],
    pipewire_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Back up existing overlay files and atomically install verified outputs."""
    if STATE_DIR.exists():
        raise RuntimeError(
            f"Audio patch state/backups already exist at {STATE_DIR}; inspect or restore them before retrying"
        )

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    file_state: dict[str, dict[str, Any]] = {}
    for relative in MANAGED_FILES:
        destination = _overlay_path(relative)
        staged = staged_root / "vendor" / relative
        if not staged.is_file() or sha256(staged.read_bytes()) != manifest[relative]:
            raise RuntimeError(f"Staged file failed verification: {staged}")
        backup = BACKUP_DIR / (relative.replace("/", "__") + ".bak")
        if destination.is_symlink():
            raise RuntimeError(f"Refusing to replace symlinked overlay entry: {destination}")
        existed = destination.is_file()
        if destination.exists() and not existed:
            raise RuntimeError(f"Refusing to replace non-file overlay entry: {destination}")
        if backup.exists():
            raise RuntimeError(f"Backup path already exists; refusing to overwrite it: {backup}")
        if existed:
            shutil.copy2(destination, backup)
        file_state[relative] = {
            "destination": str(destination),
            "backup": str(backup) if existed else None,
            "existed": existed,
            "patched_sha256": manifest[relative],
        }

    if pipewire_info and pipewire_info.get("enabled"):
        pipewire_path = Path(pipewire_info["path"])
        if pipewire_path.is_symlink():
            raise RuntimeError(f"Refusing to replace symlinked PipeWire config: {pipewire_path}")
        if pipewire_path.exists() and not pipewire_path.is_file():
            raise RuntimeError(f"PipeWire config is not a regular file: {pipewire_path}")
        if pipewire_info.get("existed"):
            pipewire_backup = Path(str(pipewire_info["backup"]))
            if pipewire_backup.exists():
                raise RuntimeError(f"PipeWire backup already exists; refusing to overwrite it: {pipewire_backup}")
            shutil.copy2(pipewire_path, pipewire_backup)

    cfg = _config()
    overlay_present = cfg.has_option("waydroid", "mount_overlays")
    overlay_previous = cfg.get("waydroid", "mount_overlays", fallback=None)
    payload: dict[str, Any] = {
        "version": 1,
        "installed": False,
        "vendor_image": str(_vendor_image_path()),
        "files": file_state,
        "config": {
            "path": str(CONFIG_PATH),
            "mount_overlays_present": overlay_present,
            "mount_overlays_previous": overlay_previous,
            "mount_overlays_applied": "True",
        },
        "pipewire": pipewire_info or {"enabled": False},
        "manifest": manifest,
    }
    _write_json_atomic(STATE_PATH, payload)

    try:
        for relative in MANAGED_FILES:
            destination = _overlay_path(relative)
            staged = staged_root / "vendor" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".highres-audio.new")
            shutil.copy2(staged, temporary)
            if sha256(temporary.read_bytes()) != manifest[relative]:
                raise RuntimeError(f"Staged copy verification failed: {destination}")
            os.replace(temporary, destination)
        _set_overlay_config("True")
        if pipewire_info and pipewire_info.get("enabled"):
            _apply_pipewire_dropin(pipewire_info)
        payload["installed"] = True
        _write_json_atomic(STATE_PATH, payload)
    except Exception:
        rollback_errors: list[str] = []
        if pipewire_info and pipewire_info.get("enabled"):
            try:
                _restore_pipewire_dropin(pipewire_info, rollback=True)
            except Exception as rollback_error:
                rollback_errors.append(f"{pipewire_info['path']}: {rollback_error}")
        for relative, item in reversed(list(file_state.items())):
            destination = Path(item["destination"])
            try:
                if item["existed"] and item["backup"] and Path(item["backup"]).is_file():
                    shutil.copy2(Path(item["backup"]), destination)
                else:
                    destination.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(f"{destination}: {rollback_error}")
        try:
            live = _config()
            if live.get("waydroid", "mount_overlays", fallback=None) == "True":
                if overlay_present:
                    _set_overlay_config(overlay_previous)
                else:
                    _set_overlay_config(None)
        except Exception as rollback_error:
            rollback_errors.append(f"{CONFIG_PATH}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                "Install failed and rollback was incomplete; backups retained at "
                f"{BACKUP_DIR}: " + "; ".join(rollback_errors)
            )
        shutil.rmtree(STATE_DIR, ignore_errors=True)
        raise
    return payload


def _stop_container() -> None:
    # Stop the session first so overlay files cannot be open in a mounted rootfs.
    subprocess.run(
        ["waydroid", "session", "stop"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    result = subprocess.run(
        ["waydroid", "container", "stop"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        status_result = subprocess.run(
            ["waydroid", "status"], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=15
        )
        if status_result.returncode or "Container: STOPPED" not in status_result.stdout:
            raise RuntimeError(f"Could not stop Waydroid container: {detail}")


def _reload_waydroid() -> None:
    result = subprocess.run(
        ["waydroid", "upgrade", "-o"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=180,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Waydroid reload failed: {detail}")


def enable(host_pipewire_192k: bool = False) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo: sudo venv/bin/python3 main.py highres-audio enable")
    if not shutil.which("waydroid"):
        raise RuntimeError("waydroid command not found")
    if not shutil.which("debugfs"):
        raise RuntimeError("debugfs is required; install e2fsprogs")
    if STATE_DIR.exists():
        raise RuntimeError(
            f"Audio patch state/backups already exist at {STATE_DIR}; inspect or restore them before retrying"
        )

    pipewire_info = _prepare_pipewire_state() if host_pipewire_192k else None
    image = _vendor_image_path()
    with tempfile.TemporaryDirectory(prefix="waydroid-highres-audio-") as temp_dir:
        staged_root = Path(temp_dir)
        # Check every source and output hash before stopping Waydroid or touching
        # its config/overlay. Unsupported vendor images fail without side effects.
        manifest = build_overlay(image, staged_root)
        _stop_container()
        _install_overlay(staged_root, manifest, pipewire_info)

    try:
        _reload_waydroid()
    except Exception as exc:
        print(f"WARNING: overlay installed but Waydroid reload failed: {exc}")
        print("Run 'sudo venv/bin/python3 main.py highres-audio disable' to restore it.")
        raise
    if pipewire_info and pipewire_info.get("enabled"):
        _restart_pipewire(int(pipewire_info["uid"]))
    print("Installed the checksum-verified 32-bit/192 kHz Waydroid audio overlay.")
    if pipewire_info:
        print("Also configured the desktop PipeWire graph at a fixed 192 kHz rate.")
    else:
        print("The host PipeWire graph was left unchanged; it may resample higher-rate streams.")
    print("This does not guarantee bit-perfect playback. Verify the active stream with pw-top.")
    print("Restore with: sudo venv/bin/python3 main.py highres-audio disable")


def disable() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo: sudo venv/bin/python3 main.py highres-audio disable")
    payload = _read_state()
    if payload is None:
        print("No high-resolution audio patch state found; nothing to restore.")
        return
    if not payload.get("installed"):
        raise RuntimeError(f"Audio patch state is incomplete. Inspect backups at {BACKUP_DIR}")

    _stop_container()
    skipped_paths: list[str] = []
    for _relative, item in payload["files"].items():
        destination = Path(item["destination"])
        backup = Path(item["backup"]) if item.get("backup") else None
        if item["existed"] and backup and backup.is_file():
            original_hash = sha256(backup.read_bytes())
        else:
            original_hash = None

        if item["existed"] and original_hash is not None and destination.is_file():
            if sha256(destination.read_bytes()) == original_hash:
                continue  # already restored in a previous partial run
        elif not item["existed"] and not destination.exists():
            continue  # already removed in a previous partial run

        if not destination.is_file() or sha256(destination.read_bytes()) != item["patched_sha256"]:
            skipped_paths.append(f"{destination} (changed or removed externally)")
            continue
        try:
            if item["existed"]:
                if not backup or not backup.is_file():
                    raise RuntimeError(f"Required backup is missing for {destination}")
                shutil.copy2(backup, destination)
            else:
                destination.unlink()
        except OSError as exc:
            raise RuntimeError(f"Could not restore {destination}: {exc}") from exc

    pipewire_info = payload.get("pipewire", {"enabled": False})
    if pipewire_info.get("enabled"):
        try:
            if not _restore_pipewire_dropin(pipewire_info):
                skipped_paths.append(f"{pipewire_info['path']} (changed by user)")
        except OSError as exc:
            raise RuntimeError(f"Could not restore PipeWire drop-in: {exc}") from exc

    config_state = payload.get("config", {})
    current = _config().get("waydroid", "mount_overlays", fallback=None)
    previous_present = config_state.get("mount_overlays_present", False)
    previous_value = config_state.get("mount_overlays_previous")
    if current == previous_value and bool(previous_present) == (previous_value is not None):
        pass  # already restored in an earlier partial run
    elif current == config_state.get("mount_overlays_applied"):
        if previous_present and previous_value is not None:
            _set_overlay_config(previous_value)
        else:
            _set_overlay_config(None)
    else:
        skipped_paths.append(f"{CONFIG_PATH} [waydroid].mount_overlays (changed by user)")

    try:
        _reload_waydroid()
    except Exception:
        print("WARNING: restore files were written but Waydroid reload failed.")
        print("State and backups were retained so you can retry after checking Waydroid.")
        raise

    if pipewire_info.get("enabled"):
        _restart_pipewire(int(pipewire_info["uid"]))

    if skipped_paths:
        print("Some entries were left unchanged because they were removed or modified externally:")
        for path in skipped_paths:
            print(f"  - {path}")
        print(f"Backups retained at: {BACKUP_DIR}")
    else:
        shutil.rmtree(STATE_DIR)
        print("Restored the previous Waydroid audio files and overlay setting.")


def status() -> None:
    try:
        image = _vendor_image_path()
    except (OSError, RuntimeError, configparser.Error) as exc:
        print(exc)
        image = DEFAULT_VENDOR_IMAGE
    print(f"Waydroid vendor image: {image} ({'present' if image.is_file() else 'missing'})")
    try:
        cfg = _config()
        print(f"mount_overlays: {cfg.get('waydroid', 'mount_overlays', fallback='unset')}")
    except (OSError, RuntimeError, configparser.Error) as exc:
        print(exc)
    payload = _read_state()
    if payload is None:
        print("High-resolution audio patch is not installed by this script.")
        return
    print("High-resolution audio patch state: installed" if payload.get("installed") else
          "High-resolution audio patch state: incomplete; inspect backups manually")
    for _relative, item in payload["files"].items():
        destination = Path(item["destination"])
        if destination.is_file():
            state = "patched" if sha256(destination.read_bytes()) == item["patched_sha256"] else "modified"
        else:
            state = "missing"
        print(f"{Path(destination).name}: {state}")
    pipewire_info = payload.get("pipewire", {"enabled": False})
    if pipewire_info.get("enabled"):
        path = Path(pipewire_info["path"])
        if path.is_file() and sha256(path.read_bytes()) == pipewire_info["installed_sha256"]:
            print(f"Host PipeWire graph pin: enabled ({path})")
        else:
            print(f"Host PipeWire config changed or missing ({path})")
    else:
        print("Host PipeWire graph was not changed by this patch.")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Manage the checksum-guarded Waydroid high-resolution audio overlay")
    parser.add_argument("action", choices=("enable", "disable", "status"))
    parser.add_argument(
        "--host-pipewire-192k",
        action="store_true",
        help="Also pin the host PipeWire graph to 192 kHz (affects all desktop audio)",
    )
    args = parser.parse_args()
    try:
        if args.action == "enable":
            enable(host_pipewire_192k=args.host_pipewire_192k)
        elif args.action == "disable":
            disable()
        else:
            status()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, configparser.Error) as exc:
        print(f"High-resolution audio operation failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
