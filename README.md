# Waydroid Extras Script

Script to add GApps and other stuff to Waydroid!

# Installation/Usage

## Interactive terminal interface

```bash
git clone https://github.com/casualsnek/waydroid_script
cd waydroid_script
python3 -m venv venv
venv/bin/pip install -r requirements.txt
sudo venv/bin/python3 main.py
```

![image-20230430013103883](assets/img/README/image-20230430013103883.png)

![image-20230430013119763](assets/img/README/image-20230430013119763.png)

![image-20230430013148814](assets/img/README/image-20230430013148814.png)

## Command Line

```bash
git clone https://github.com/casualsnek/waydroid_script
cd waydroid_script
python3 -m venv venv
venv/bin/pip install -r requirements.txt
# install something
sudo venv/bin/python3 main.py install {gapps, magisk, libndk, libhoudini, nodataperm, smartdock, microg, mitm}
# uninstall something
sudo venv/bin/python3 main.py uninstall {gapps, magisk, libndk, libhoudini, nodataperm, smartdock, microg}
# get Android device ID
sudo venv/bin/python3 main.py certified
# some hacks
sudo venv/bin/python3 main.py hack {nodataperm, hidestatusbar}
# check, install or undo high-resolution audio support
sudo venv/bin/python3 main.py highres-audio status
sudo venv/bin/python3 main.py highres-audio enable
sudo venv/bin/python3 main.py highres-audio disable
```

## Waydroid high-resolution audio

Stock Waydroid's audio HAL opens its output path at 16-bit/48 kHz. The primary output profile in its audio-policy XML also advertises only 16-bit/48 kHz. This is an Android-side limitation: changing PipeWire settings by itself cannot make the stock HAL send higher-resolution PCM. See the [Waydroid HAL source](https://github.com/waydroid/android_hardware_waydroid/blob/lineage-20/audio/audio_hw.c) and [audio policy XML](https://github.com/waydroid/android_hardware_waydroid/blob/lineage-20/audio/audio_policy_configuration.xml).

This script includes a **strictly checksum-guarded 32-bit/192 kHz patch** adapted from the MIT-licensed [waydroid-apple-music project](https://github.com/willbeez/waydroid-apple-music). It extracts the HAL and policy files from your locally installed `vendor.img`, validates each known source SHA-256, applies fixed byte edits, validates each patched SHA-256, and only then stages an overlay. It does not download a HAL binary from the internet.

**Compatibility is currently limited to one tested x86_64 Android 13 MAINLINE GAPPS vendor image.** If your `vendor.img` or audio policy differs from the expected checksums, the patch refuses to make changes. Do not bypass these checks or use binary offsets on another image. Add support for a new image only after verifying its unpatched and patched binaries against the correct Waydroid branch and architecture.

The command preserves any existing overlay files and the original `mount_overlays` setting. It records rollback state, refuses to overwrite an existing patch state, stops Waydroid before changing files, and offers a restore command. The helper enables Waydroid's overlay configuration if needed; when disabling, it restores the prior value only if the setting has not been changed by you since installation.

```bash
# Check whether the local files/config are present
sudo venv/bin/python3 main.py highres-audio status

# Install the checksum-verified patch; supported images only
sudo venv/bin/python3 main.py highres-audio enable

# Restore the files and overlay setting saved before installation
sudo venv/bin/python3 main.py highres-audio disable
```

### What this patch does—and does not do

On the supported image, the patch changes the Waydroid audio link to 32-bit PCM at 192 kHz. It is intended to preserve high-resolution sample data through the host audio bridge. It does **not** guarantee bit-perfect playback: Android's mixer/routing, the Android app, PipeWire's graph/rate negotiation, and the actual output device still affect the final signal. Bluetooth output may resample or use a lossy codec. A stream reported as `S32LE 192000` is useful evidence, but by itself is not proof of bit-perfect end-to-end output.

After installation, restart or reopen playback and inspect the host graph with `pw-top` or `pw-cli`. Confirm the actual stream format and rate, and check the rate/format supported by your DAC. The patch does not force a global PipeWire clock rate because that would affect all audio on the desktop; users who need a fixed 192 kHz graph should set up and test that separately with a reversible PipeWire drop-in.

This is an opt-in experimental patch. Keep a backup of your Waydroid data and restore the original audio overlay before updating/reinitializing Waydroid. If your system is not the tested image, the intended result is a clear refusal—not a partially patched audio HAL.

## Dependencies

`lzip` is required for this script to work. Install it using your distribution's package manager:

### Arch, Manjaro and EndeavourOS based distributions

```bash
sudo pacman -S lzip e2fsprogs
```

### Debian and Ubuntu based distributions

```bash
sudo apt install lzip e2fsprogs
```

### RHEL, Fedora and Rocky based distributions

```bash
sudo dnf install lzip e2fsprogs
```

### openSUSE based distributions

```bash
sudo zypper install lzip e2fsprogs
```

## Install OpenGapps

![](assets/1.png)

Open terminal and switch to the directory where `main.py` is located, then run:

    sudo venv/bin/python3 main.py install gapps

Then launch Waydroid with:

    waydroid show-full-ui

After Waydroid has finished booting, open a terminal in the directory where `main.py` is located and run:

    sudo python3 main.py google

Copy the returned numeric ID, then open [Google's uncertified-device page](https://google.com/android/uncertified/?pli=1). Enter the ID and register it. Wait 10–20 minutes for the device to be registered. Then clear Google Play Services' cache and try logging in.

## Install Magisk

![](assets/2.png)

    sudo venv/bin/python3 main.py install magisk

Magisk will be installed on the next boot. Zygisk and modules like LSPosed should work now.

If you want to update Magisk, use `Direct Install into system partition` or run this script again.

This script only focuses on Magisk installation. For more management, see https://github.com/nitanmarcel/waydroid-magisk.

## Install libndk ARM translation

libndk_translation from Guybrush firmware. It can offer better performance than libhoudini on some AMD systems.

    sudo venv/bin/python3 main.py install libndk

## Install libhoudini ARM translation

Intel's libhoudini for Intel/AMD x86 CPUs, pulled from Microsoft's WSA 11 image.

    sudo venv/bin/python3 main.py install libhoudini

## Integrate Widevine DRM (L3)

![](assets/3.png)

    sudo venv/bin/python3 main.py install widevine

## Install Smart Dock

![](assets/4.png)
![](assets/5.png)

    sudo venv/bin/python3 main.py install smartdock

## Install a self-signed CA certificate

    sudo venv/bin/python3 main.py install mitm --ca-cert mycert.pem

## Granting full permission for apps data (HACK)

This is a temporary hack to combat against the apps permission issue on Android 11. Whenever an app is open it enables a property (`persist.sys.nodataperm`) to execute a script that grants full data permissions (777). The correct fix is to use `sdcardfs` or `esdfs`, which require kernel or Waydroid image changes.

![](assets/6.png)

```bash
sudo venv/bin/python3 main.py hack nodataperm
```

**WARNING:** Tested on `lineage-18.1-20230128-VANILLA-waydroid_x86_64.img`. This replaces `/system/framework/service.jar` and may prevent Waydroid from booting. If that happens, run:

```bash
sudo venv/bin/python3 main.py uninstall nodataperm
```

Alternatively, run the commands below in `sudo waydroid shell`. They may need to be repeated when installing new games, but are less invasive than replacing `service.jar`.

```bash
chmod 777 -R /sdcard/Android
chmod 777 -R /data/media/0/Android
chmod 777 -R /sdcard/Android/data
chmod 777 -R /data/media/0/Android/obb
chmod 777 -R /mnt/*/*/*/*/Android/data
chmod 777 -R /mnt/*/*/*/*/Android/obb
```

- https://github.com/supremegamers/device_generic_common/commit/2d47891376c96011b2ee3c1ccef61cb48e15aed6
- https://github.com/supremegamers/android_frameworks_base/commit/24a08bf800b2e461356a9d67d04572bb10b0e819

## Install microG, Aurora Store and Aurora Droid

![](assets/7.png)

```bash
sudo venv/bin/python3 main.py install microg
```

## Hide Status Bar

Before:

![Before](assets/8.png)

After:

![After](assets/9.png)

```bash
sudo venv/bin/python3 main.py hack hidestatusbar
```

## Get Android ID for device registration

    sudo venv/bin/python3 main.py certified

Register the printed Android ID before using Google apps on an uncertified image.

Star this repository if you find it useful. If you encounter a problem, create a GitHub issue.

## Error handling

- Magisk installed: N/A

Check [waydroid-magisk](https://github.com/nitanmarcel/waydroid-magisk).

## Credits

- [Waydroid](https://github.com/waydroid/waydroid)
- [MIT-licensed high-resolution patch recipe](https://github.com/willbeez/waydroid-apple-music)
- [Magisk Delta](https://huskydg.github.io/magisk-files/)
- [microG Project](https://microg.org)
- [Open GApps](https://opengapps.org)
- [Smart Dock](https://github.com/axel358/smartdock)
- [wd-scripts](https://github.com/electrikjesus/wd-scripts/)
