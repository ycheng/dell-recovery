#!/bin/sh
#
#       <02-grub>
#
#       Rerun update-grub for any changes from added packages or logical
#       partition support that was added.
#
#       Copyright 2010 Dell Inc.
#           Mario Limonciello <Mario_Limonciello@Dell.com>
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

. /usr/share/dell/scripts/fifuncs ""

#make sure we have os-prober turned off one last time
if ! grep "^GRUB_DISABLE_OS_PROBER" /etc/default/grub >/dev/null; then
    echo "GRUB_DISABLE_OS_PROBER=true" >> /etc/default/grub
fi

IFHALT "Rerun GRUB update"
/usr/sbin/update-grub
IFHALT "Done with GRUB update"
