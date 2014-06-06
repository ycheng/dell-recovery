#!/bin/sh

. /usr/share/dell/scripts/fifuncs ""

IFHALT "Run ubuntu-drivers autoinstall"
echo 'APT::Get::AllowUnauthenticated "true";' > /etc/apt/apt.conf.d/99disable_authentication
for i in `ubuntu-drivers list`; do
    if ! dpkg-query -W $i >/dev/null 2>&1; then
        apt-get install --yes $i
    fi
done
rm /etc/apt/apt.conf.d/99disable_authentication
IFHALT "Done with ubuntu-drivers autoinstall"
