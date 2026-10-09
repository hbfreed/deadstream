# Fresh install on Raspberry Pi OS Lite (64-bit)

Installs the Time Machine onto a fresh Raspberry Pi OS Lite (trixie, 64-bit) card instead of the
prebuilt image. It keeps the upstream layout: user `deadhead`, the venv symlinked at
`~/timemachine`, and the systemd services from `timemachine/bin`.

Tested on: Pi 3A+ with the original Time Machine board. Steps marked **(unverified)** haven't
been run on hardware yet.

## 1. Flash the card

In Raspberry Pi Imager, choose **Raspberry Pi OS Lite (64-bit)** and set these in the OS customisation settings:

- hostname: anything (it no longer matters, because the update service is masked in step 7)
- username **`deadhead`** (the service files hardcode `/home/deadhead`)
- Wi-Fi SSID and password, and your Wi-Fi country
- enable SSH with your public key

Flash with Imager 2.x. Balena Etcher and older Imager versions don't apply these settings to trixie
images, and the Pi then boots with no user and no Wi-Fi.

## 2. Passwordless sudo

The Time Machine calls `sudo` without a prompt (to restart services, shut down, scan local archives).
On trixie, the user Imager creates needs a password for sudo, so this has to be run once from a
terminal where you can type that password:

```bash
ssh -t deadhead@timemachine.local 'echo "deadhead ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_deadhead-nopasswd && sudo chmod 440 /etc/sudoers.d/010_deadhead-nopasswd && sudo visudo -c'
```

## 3. System packages and settings

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y --no-install-recommends git rsync libmpv2 python3-dev build-essential \
    swig liblgpio-dev pulseaudio pulseaudio-utils wireless-tools net-tools
curl -LsSf https://astral.sh/uv/install.sh | sh

sudo raspi-config nonint do_spi 0          # the ST7735 screen is on SPI
```

The screen code drives SPI chip-select CE0 (GPIO 8) itself, but the kernel SPI driver claims CE0 and CE1,
and lgpio refuses pins that are already claimed ("GPIO busy"). Load SPI without kernel chip-selects
by adding this to `/boot/firmware/config.txt`, after `dtparam=spi=on`, on a line of its own (a comment on
the same line makes the firmware ignore it):

```
dtoverlay=spi0-0cs
```

`wireless-tools` and `net-tools` provide `iwconfig`, `iwlist` and `ifconfig`, which
`connect_network` calls. `python3-dev`, `build-essential`, `swig` and `liblgpio-dev` are needed to build `lgpio`, which has no
aarch64 wheel for python 3.13. (`RPi.GPIO` is replaced by `rpi-lgpio`, because RPi.GPIO's edge detection
doesn't work on bookworm and later kernels.)

Check `/boot/firmware/config.txt`:

- `dtparam=audio=on` must be there for the 3.5 mm jack.
- I2C must stay **off**, because the Stop and Rewind buttons use GPIO 2 and 3.
- **v2 boards** (including v2.1) have a power button and need `dtoverlay=gpio-shutdown` (copy that line from the old card's
  `/boot/config.txt` if it had one). `board_version.sh` reads this line to tell the board versions apart, and on
  a v2 board it moves Rewind to GPIO 21.
- Add `enable_uart=1`, as upstream's update script did.
- Give the memory back from the GPU. Nothing uses HDMI or a camera, because the screen is driven
  over SPI. By default, 64 MB goes to the GPU firmware and the KMS driver reserves a 256 MB CMA pool
  out of the roughly 415 MB Linux sees. Comment out `dtoverlay=vc4-kms-v3d`, `max_framebuffers=2`,
  `camera_auto_detect=1` and `display_auto_detect=1`, and add `gpu_mem=16`.

## 4. PulseAudio in system mode

The player outputs to `pulse` and restarts the system-wide daemon itself. The upstream update
script used to do this setup. On a fresh card it has to be done by hand:

```bash
echo "default-server = /var/run/pulse/native" | sudo tee -a /etc/pulse/client.conf
echo "autospawn = no" | sudo tee -a /etc/pulse/client.conf
sudo usermod -a -G audio,video,spi,gpio,pulse,pulse-access deadhead
sudo usermod -a -G audio pulse
sudo usermod -a -G pulse,pulse-access root
echo "SystemMaxUse=200M" | sudo tee -a /etc/systemd/journald.conf
# keep only the system-wide daemon: stop the per-user instance from starting (unverified)
systemctl --user mask pulseaudio.service pulseaudio.socket
```

## 5. Wi-Fi workaround

Wi-Fi is managed by NetworkManager, which is set up from Imager. `connect_network` assumes Wi-Fi
is down if `/etc/wpa_supplicant/wpa_supplicant.conf` doesn't exist, and then starts the
knob-driven Wi-Fi picker on every boot. Creating an empty file is enough:

```bash
sudo mkdir -p /etc/wpa_supplicant && sudo touch /etc/wpa_supplicant/wpa_supplicant.conf
```

(Changing Wi-Fi with the knobs won't work on this OS. Use `sudo nmtui` over SSH.)

## 6. Install the code

From the laptop, in the repo:

```bash
rsync -av --delete --exclude .git --exclude '.venv' --exclude '*_ids' ./ deadhead@<pi>:deadstream/
```

On the Pi:

```bash
cd ~
env_name=env_$(date +%Y%m%d)
uv venv --python /usr/bin/python3 $env_name
uv pip sync --python $env_name/bin/python deadstream/requirements.pi.lock.txt
uv pip install --python $env_name/bin/python --no-deps ./deadstream
ln -sfn $env_name timemachine
```

To redeploy, repeat this step: rsync, then build a new `env_<date>`, then repoint the symlink.
The old env stays around for rolling back. Note that the downloaded tape index lives inside the env
(`lib/python3.13/site-packages/timemachine/metadata/*_ids`). Copy those folders across, or the new env
downloads them again on first start (about 10 seconds for the Dead).

## 7. Services

```bash
~/timemachine/bin/services.sh                  # installs and enables calibrate, connect_network,
                                               # timemachine, serve_options, pulseaudio
sudo rm /etc/systemd/system/update.service     # services.sh copies it in, and a unit file blocks masking
sudo systemctl mask update.service             # never pull upstream over this install
sudo reboot
```

With `update.service` masked, the three "Update code" triggers (holding Stop, the month-button
menu, and the web page) fail harmlessly. The screen shows "Code is up to Date".

## 8. Check

- The screen lights up and the knobs move the date.
- Dialling 12/19/73 and pressing Play plays the show through the 3.5 mm jack.
- `http://<pi>:9090` serves the options page.
- `journalctl -u timemachine -f` shows the log.

Memory reference: with the full Grateful Dead index loaded, the main process uses about 185 MB on
64-bit (measured on x86-64, where Python's memory use is about the same as on aarch64).

## 9. AirPlay (optional)

The box can also be an AirPlay speaker. shairport-sync plays into the same PulseAudio server as the
Time Machine. When a phone starts playing, the Time Machine pauses and shows the AirPlay track and artist. Any
knob turn, or Play, Select or Stop, ends the AirPlay session and gives the speakers back. The config
files are in `docs/airplay/`.

```bash
sudo apt install -y --no-install-recommends shairport-sync
sudo cp docs/airplay/shairport-sync.conf /etc/shairport-sync.conf
# lets shairport-sync take its D-Bus name, and the Time Machine end a session (DropSession)
sudo cp docs/airplay/shairport-sync-timemachine.conf /etc/dbus-1/system.d/
# the Time Machine restarts PulseAudio; shairport-sync has to restart with it to reconnect
sudo mkdir -p /etc/systemd/system/shairport-sync.service.d
sudo cp docs/airplay/shairport-sync.service.d-timemachine.conf /etc/systemd/system/shairport-sync.service.d/timemachine.conf
sudo usermod -a -G pulse-access shairport-sync
sudo systemctl daemon-reload && sudo systemctl reload dbus && sudo systemctl restart shairport-sync
```

The Time Machine notices shairport-sync by `/etc/shairport-sync.conf` and reads its metadata pipe
(`/tmp/shairport-sync-metadata`). shairport-sync uses about 17 MB of memory.

## 10. Official releases (optional)

`tools/map_releases.py` runs on a laptop. It matches official release folders (Dick's Picks, Dave's Picks,
...) to show dates, converts FLAC to Ogg Vorbis, and writes tape folders that the Time Machine reads as the
`Local_GratefulDead` collection. On a date with a release, the release plays first and a star marks it (hollow
for a partial show). The archive.org tapes are still there when you cycle through tapes by holding Select.

```bash
python tools/map_releases.py --out /path/to/timemachine-releases "/path/to/Music/Grateful Dead"
# check /path/to/timemachine-releases/report.txt; fix dates in overrides.toml in that folder and run again
rsync -a --delete /path/to/timemachine-releases/.release_audio/ deadhead@timemachine.local:archive/.release_audio/
rsync -a --delete /path/to/timemachine-releases/GratefulDead/official/ deadhead@timemachine.local:archive/GratefulDead/official/
```

The order of COLLECTIONS is the order of preference among the sources of each artist: `Local_GratefulDead,GratefulDead`
plays your releases first, `GratefulDead,Local_GratefulDead` the archive.org tapes. Releases holding only a little of a
show (bonus tracks) always come last.

On the Pi, `~/archive` must be a real folder. Upstream's code replaces a *symlink* there with one to a USB
stick at `/mnt/usb/archive`. Add `Local_GratefulDead` to COLLECTIONS, before `GratefulDead`
(`Local_GratefulDead,GratefulDead`). The Grateful Dead releases are about 28 GB as Ogg.
