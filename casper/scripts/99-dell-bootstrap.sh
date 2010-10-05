#!/bin/sh
#
#       <99-dell-bootstrap.sh>
#
#       Loads the ubiquity dell bootstrap plugin into place
#
#       Copyright 2008-2011 Dell Inc.
#           Mario Limonciello <Mario_Limonciello@Dell.com>
#           Hatim Amro <Hatim_Amro@Dell.com>
#           Michael E Brown <Michael_E_Brown@Dell.com>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
# vim:ts=8:sw=8:et:tw=0

PREREQ=""
DESCRIPTION="Running DELL bootstrap..."

prereqs ()
{
	echo "$PREREQ"
}

case $1 in
# get pre-requisites
prereqs)
	prereqs
	exit 0
	;;
esac

. /scripts/casper-functions
load_confmodule

log_begin_msg "$DESCRIPTION"

export DEBIAN_HAS_FRONTEND=
export DEBCONF_REDIR=
export DEBIAN_FRONTEND=noninteractive

#Set up all preseeds
casper-set-selections "/conf/ubuntu.seed"
if grep -q "dell-recovery/dual_boot_seed" /proc/cmdline 2>&1 >/dev/null; then
    casper-set-selections "/conf/dual.seed"
fi

#Force ubiquity to run in automatic
sed -i "s/\$debug\ \$automatic\ \$choose/--automatic/" /root/etc/init/ubiquity.conf

#Build custom pool (static and dynamic)
if [ ! -x /root/usr/share/dell/scripts/pool.sh ]; then
    mkdir -p /root/usr/share/dell/scripts/
    cp /scripts/pool.sh /root/usr/share/dell/scripts/
fi
chroot /root /root/usr/share/dell/scripts/pool.sh

#install if not installed, otherwise this will upgrade
chroot /root apt-get install dell-recovery -y --no-install-recommends

#only if we are in factory or bto-a
if chroot /root apt-cache show fist 2>/dev/null 1>/dev/null; then
    chroot /root apt-get install fist -y
fi

#Install EFI Grub
if [ -d /sys/firmware/efi ]; then
    #grub-pc is in livefs, need grub-efi-amd64
    chroot /root apt-get install grub-efi-amd64 -y
fi

#Emergency installer fixes
if [ -e /root/cdrom/scripts/emergency.sh ]; then
    . /root/cdrom/scripts/emergency.sh
fi

# Clear out debconf database backup files to save memory.
rm -f /root/var/cache/debconf/*.dat-old

log_end_msg

exit 0

