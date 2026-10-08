# Fresh install on Raspberry Pi OS Lite (64-bit)

Installs the Time Machine onto a fresh Raspberry Pi OS Lite (trixie, 64-bit) card instead of the
prebuilt image. It keeps the upstream layout: user `deadhead`, the venv symlinked at
`~/timemachine`, and the systemd services from `timemachine/bin`.

Tested on: Pi 3A+ with the original Time Machine board. Steps marked **(unverified)** haven't
been run on hardware yet.

## 1. Flash the card

In Raspberry Pi Imager, choose **Raspberry Pi OS Lite (64-bit)** and set these in the OS customisation settings:

- hostname: anything (it no longer matters, because the update service is masked in step 6)
- username **`deadhead`** (the service files hardcode `/home/deadhead`)
- Wi-Fi SSID and password, and your Wi-Fi country
- enable SSH with your public key

The user Imager creates has passwordless sudo, which the Time Machine relies on.

## 2. System packages and settings

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git rsync libmpv2 python3-dev build-essential \
    pulseaudio pulseaudio-utils wireless-tools net-tools
curl -LsSf https://astral.sh/uv/install.sh | sh

sudo raspi-config nonint do_spi 0          # the ST7735 screen is on SPI
```

`wireless-tools` and `net-tools` provide `iwconfig`, `iwlist` and `ifconfig`, which
`connect_network` calls. `python3-dev` and `build-essential` are needed to build `RPi.GPIO`,
which has no aarch64 wheel.

Check `/boot/firmware/config.txt`:

- `dtparam=audio=on` must be there for the 3.5 mm jack.
- I2C must stay **off**, because the Stop and Rewind buttons use GPIO 2 and 3.
- **v2 boards** have a power button and need `dtoverlay=gpio-shutdown` (copy that line from the old card's
  `/boot/config.txt` if it had one). `board_version.sh` reads this line to tell the board versions apart, and on
  a v2 board it moves Rewind to GPIO 21.
- Add `enable_uart=1`, as upstream's update script did.

## 3. PulseAudio in system mode

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

## 4. Wi-Fi workaround

Wi-Fi is managed by NetworkManager, which is set up from Imager. `connect_network` assumes Wi-Fi
is down if `/etc/wpa_supplicant/wpa_supplicant.conf` doesn't exist, and then starts the
knob-driven Wi-Fi picker on every boot. Creating an empty file is enough:

```bash
sudo mkdir -p /etc/wpa_supplicant && sudo touch /etc/wpa_supplicant/wpa_supplicant.conf
```

(Changing Wi-Fi with the knobs won't work on this OS. Use `sudo nmtui` over SSH.)

## 5. Install the code

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

## 6. Services

```bash
~/timemachine/bin/services.sh                  # installs and enables calibrate, connect_network,
                                               # timemachine, serve_options, pulseaudio
sudo systemctl mask update.service             # never pull upstream over this install
sudo reboot
```

With `update.service` masked, the three "Update code" triggers (holding Stop, the month-button
menu, and the web page) fail harmlessly. The screen shows "Code is up to Date".

## 7. Check

- The screen lights up and the knobs move the date.
- Dialling 12/19/73 and pressing Play plays the show through the 3.5 mm jack.
- `http://<pi>:9090` serves the options page.
- `journalctl -u timemachine -f` shows the log.

Memory reference: with the full Grateful Dead index loaded, the main process uses about 185 MB on
64-bit (measured on x86-64, where Python's memory use is about the same as on aarch64).
