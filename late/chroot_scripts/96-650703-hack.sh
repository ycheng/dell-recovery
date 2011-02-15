#!/bin/sh
#
#       <96-650703-hack.sh>
#
#       A hack to work around  https://bugs.launchpad.net/ubuntu/+source/ubiquity/+bug/650703
#       - This removes the rc runlevel switch as potential target for oem-config during first
#         boot.
#
#
#       Copyright 2008-2011 Dell Inc.
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

IFHALT "Add a hack for LP: #650703"
sed -i "s,or starting uxlaunch$,or starting uxlaunch),; /or stopping rc/d" /etc/init/oem-config.conf
