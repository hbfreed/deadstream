#!/bin/bash

# config.txt moved to /boot/firmware in Raspberry Pi OS bookworm
config_txt=/boot/firmware/config.txt
[ -f $config_txt ] || config_txt=/boot/config.txt

read lines words chars filename <<<$(cat $config_txt | grep dtoverlay=gpio-shutdown | grep -v ^# | wc)

if [ "$lines" -eq "0" ]; then
   echo "version 1";
   exit 0
fi
echo "version 2"
