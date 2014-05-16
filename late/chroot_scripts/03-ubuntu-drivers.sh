#!/bin/sh

. /usr/share/dell/scripts/fifuncs ""

IFHALT "Run ubuntu-drivers autoinstall"
echo 'APT::Get::AllowUnauthenticated "true";' > /etc/apt/apt.conf.d/99disable_authentication
/usr/bin/ubuntu-drivers autoinstall
rm /etc/apt/apt.conf.d/99disable_authentication
IFHALT "Done with ubuntu-drivers autoinstall"
