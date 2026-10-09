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

The options page (serve_options, port 9090) uses about 38 MB, all the time, for a page used now and then.
Start it on demand instead: systemd listens on 9090, starts serve_options on a visit (the first page load
takes about 2 seconds), and stops it after 10 idle minutes.

```bash
D=docs/options-on-demand
sudo cp $D/serve_options-proxy.socket $D/serve_options-proxy.service /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/serve_options.service.d
sudo cp $D/serve_options.service.d-ondemand.conf /etc/systemd/system/serve_options.service.d/ondemand.conf
sudo systemctl daemon-reload
sudo systemctl disable --now serve_options
sudo systemctl enable --now serve_options-proxy.socket
```

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

Debian's shairport-sync is classic AirPlay only, which holds about 2 seconds of audio: a phone on weak Wi-Fi
drops out. AirPlay 2 (from Music, Podcasts, ...) sends audio well ahead, so build Shairport Sync 5 with AirPlay 2
and its timing helper NQPTP on the Pi (about 20 minutes on a 3A+; `docs/airplay/build_ap2.sh`):

```bash
bash docs/airplay/build_ap2.sh          # apt build deps, then builds ~/src/nqptp and ~/src/shairport-sync
cd ~/src/nqptp && sudo make install && sudo systemctl enable --now nqptp
cd ~/src/shairport-sync && sudo make install   # /usr/local/bin/shairport-sync and its systemd unit
cd ~/deadstream
sudo cp docs/airplay/shairport-sync.conf /etc/shairport-sync.conf
# lets shairport-sync take its D-Bus name, and the Time Machine end a session (DropSession)
sudo cp docs/airplay/shairport-sync-timemachine.conf /etc/dbus-1/system.d/
# the Time Machine restarts PulseAudio; shairport-sync has to stop and start with it to reconnect
sudo mkdir -p /etc/systemd/system/shairport-sync.service.d /etc/systemd/system/pulseaudio.service.d
sudo cp docs/airplay/shairport-sync.service.d-timemachine.conf /etc/systemd/system/shairport-sync.service.d/timemachine.conf
sudo cp docs/airplay/pulseaudio.service.d-airplay.conf /etc/systemd/system/pulseaudio.service.d/airplay.conf
sudo usermod -a -G pulse-access shairport-sync
sudo systemctl daemon-reload && sudo systemctl reload dbus && sudo systemctl enable --now shairport-sync
```

The Time Machine notices shairport-sync by `/etc/shairport-sync.conf` and reads its metadata pipe
(`/tmp/shairport-sync-metadata`).

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

## 11. Maintenance

Security updates at night, recovery from running out of memory, and a weekly check for a newer
Shairport Sync (built from source, so Debian's updates don't reach it). Files are in `docs/maintenance/`.

```bash
D=docs/maintenance
sudo apt install -y --no-install-recommends earlyoom unattended-upgrades
# the Time Machine comes back after a crash, or after earlyoom stops it
sudo mkdir -p /etc/systemd/system/timemachine.service.d
sudo cp $D/timemachine.service.d-restart.conf /etc/systemd/system/timemachine.service.d/restart.conf
# under 8% memory available, stop the Time Machine instead of grinding in swap
sudo cp $D/earlyoom /etc/default/earlyoom
# Debian security and stable updates at 3:30-4:30 am, never at boot (Raspberry Pi kernels and firmware are left alone)
sudo cp $D/20auto-upgrades /etc/apt/apt.conf.d/20auto-upgrades
sudo mkdir -p /etc/systemd/system/apt-daily.timer.d /etc/systemd/system/apt-daily-upgrade.timer.d
sudo cp $D/apt-daily.timer.d-night.conf /etc/systemd/system/apt-daily.timer.d/night.conf
sudo cp $D/apt-daily-upgrade.timer.d-night.conf /etc/systemd/system/apt-daily-upgrade.timer.d/night.conf
# Sundays: is there a newer Shairport Sync? The answer shows at ssh login
sudo install -m 755 $D/shairport-update-check /usr/local/sbin/
sudo cp $D/shairport-update-check.service $D/shairport-update-check.timer /etc/systemd/system/
sudo install -m 755 $D/90-shairport-update /etc/update-motd.d/
sudo systemctl daemon-reload
sudo systemctl enable --now earlyoom apt-daily.timer apt-daily-upgrade.timer shairport-update-check.timer
```

The hardware watchdog is on by default (systemd reboots the Pi if the system hangs for a minute).

## 12. Claude (MCP, optional)

Ask Claude, from the phone app or anywhere, to play a show. The Time Machine serves a control socket
(`timemachine/control.py`). `tools/timemachine_mcp.py` is an MCP server with music controls only (find shows, play,
pause, skip, volume) that talks to that socket. It starts when Claude connects (about 5 seconds, 75 MB) and stops after
5 idle minutes. Tailscale Funnel gives it a public HTTPS address, and a secret in the URL keeps others out.

It runs sandboxed, as its own user `tmmcp`: no sudo, no home, a read-only filesystem, and no network but localhost, so
a break-in through a bug could only play music. The control socket is in `/run/timemachine`, for the group `tmcontrol`.

```bash
# Tailscale: https://tailscale.com/download/linux/debian-trixie, then `sudo tailscale up` and open the link.
# In the admin console: DNS > HTTPS Certificates on, and allow Funnel for the Pi.
sudo groupadd -r tmcontrol
sudo useradd -r -U -M -d /nonexistent -s /usr/sbin/nologin tmmcp
sudo usermod -aG tmcontrol deadhead
sudo mkdir -p /opt/timemachine-mcp
sudo ~/.local/bin/uv venv /opt/timemachine-mcp/venv --python /usr/bin/python3
sudo ~/.local/bin/uv pip install --python /opt/timemachine-mcp/venv/bin/python "mcp>=2.3,<3"
sudo install -m 644 tools/timemachine_mcp.py /opt/timemachine-mcp/
# the sandbox can't write .pyc files, so compile them now (cold start 5 s instead of 12)
sudo /opt/timemachine-mcp/venv/bin/python -m compileall -q /opt/timemachine-mcp /usr/lib/python3.13
sudo python3 -c "import json,secrets; json.dump({'secret': secrets.token_urlsafe(32), 'host': '<pi>.<tailnet>.ts.net'}, open('/etc/timemachine-mcp.json','w'))"
sudo chown root:tmmcp /etc/timemachine-mcp.json && sudo chmod 640 /etc/timemachine-mcp.json
D=docs/claude-mcp
sudo cp $D/timemachine-control.tmpfiles.conf /etc/tmpfiles.d/timemachine-control.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/timemachine-control.conf
sudo mkdir -p /etc/systemd/system/timemachine.service.d
sudo cp $D/timemachine.service.d-control.conf /etc/systemd/system/timemachine.service.d/control.conf
sudo cp $D/timemachine-mcp-proxy.socket $D/timemachine-mcp-proxy.service $D/timemachine-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now timemachine-mcp-proxy.socket
sudo systemctl restart timemachine
sudo tailscale funnel --bg http://127.0.0.1:8765
```

Then in claude.ai: Settings > Connectors > Add custom connector, URL `https://<pi>.<tailnet>.ts.net/<secret>/mcp`
(the secret is in `/etc/timemachine-mcp.json`). It is then available in the Claude apps too. To shut others out
after a leak, put a new secret in the file and update the connector. After updating `tools/timemachine_mcp.py`,
reinstall it to `/opt/timemachine-mcp` and compile it again.

`systemd-analyze security timemachine-mcp.service` rates the sandbox 1.2 (0 is locked down, 10 is exposed).
For the network as a whole, see `docs/claude-mcp/tailscale-acl.md`: it keeps the Pi from opening connections to the
other machines on the tailnet.
