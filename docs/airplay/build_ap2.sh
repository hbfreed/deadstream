#!/bin/bash
# Build NQPTP and Shairport Sync 5.5.2 (AirPlay 2) on the Time Machine. Log: ~/ap2-build.log
set -ex
sudo apt-get update -q
sudo apt-get install -y -q --no-install-recommends build-essential git autoconf automake libtool \
    libpopt-dev libconfig-dev libasound2-dev libpulse-dev avahi-daemon libavahi-client-dev libssl-dev \
    libplist-dev libsodium-dev uuid-dev libgcrypt20-dev xxd libplist-utils libglib2.0-dev \
    libavutil-dev libavcodec-dev libavformat-dev libswresample-dev systemd-dev
mkdir -p ~/src && cd ~/src
[ -d nqptp ] || git clone --depth 1 --branch 1.2.8 https://github.com/mikebrady/nqptp.git
cd nqptp && autoreconf -fi && ./configure --with-systemd-startup && make -j2 && cd ..
[ -d shairport-sync ] || git clone --depth 1 --branch 5.5.2 https://github.com/mikebrady/shairport-sync.git
cd shairport-sync && autoreconf -fi
./configure --sysconfdir=/etc --with-pulseaudio --with-avahi --with-ssl=openssl --with-airplay-2 \
    --with-metadata --with-dbus-interface --with-systemd-startup
make -j2
echo BUILD-OK
