# -*- coding: UTF-8 -*-

# Copyright (C) 2006, 2007, 2008 Canonical Ltd.
# Written by Colin Watson <cjwatson@ubuntu.com>.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

# All these functions check whether gconftool-2 exists, so you may safely
# call them even if gconftool-2 is not guaranteed to be available.

import os
import pwd
import subprocess

_cached_gconftool_exists = None
def _gconftool_exists():
    global _cached_gconftool_exists
    if _cached_gconftool_exists is not None:
        return _cached_gconftool_exists

    _cached_gconftool_exists = find_on_path('gconftool-2')
    return _cached_gconftool_exists

def _gconf_dir():
    if 'SUDO_USER' in os.environ:
        d = os.path.expanduser('~%s/.gconf' % os.environ['SUDO_USER'])
    else:
        d = os.path.expanduser('~/.gconf')
    return 'xml:readwrite:%s' % d

def get(key):
    if not _gconftool_exists():
        return

    subp = subprocess.Popen(['gconftool-2', '--config-source', _gconf_dir(),
                             '--get', key],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            preexec_fn=drop_all_privileges)
    return subp.communicate()[0].rstrip('\n')

def get_list(key):
    if not _gconftool_exists():
        return

    gconf_dir = _gconf_dir()
    subp = subprocess.Popen(['gconftool-2', '--config-source', gconf_dir,
                             '--get-list-size', key],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            preexec_fn=drop_all_privileges)
    size = subp.communicate()[0].rstrip('\n')
    try:
        size = int(size)
    except ValueError:
        size = 0

    elements = []
    for i in range(size):
        subp = subprocess.Popen(['gconftool-2', '--config-source', gconf_dir,
                                 '--get-list-element', key, str(i)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=drop_all_privileges)
        elements.append(subp.communicate()[0].rstrip('\n'))

    return elements

def set(key, keytype, value):
    if not _gconftool_exists():
        return

    subprocess.call(['gconftool-2', '--set', key, '--type', keytype, value],
                    preexec_fn=drop_all_privileges)

def unset(key):
    if not _gconftool_exists():
        return

    subprocess.call(['gconftool-2', '--unset', key],
                    preexec_fn=drop_all_privileges)

def drop_privileges():
    if 'SUDO_GID' in os.environ:
        gid = int(os.environ['SUDO_GID'])
        os.setegid(gid)
    if 'SUDO_UID' in os.environ:
        uid = int(os.environ['SUDO_UID'])
        os.seteuid(uid)

def drop_all_privileges():
    # gconf needs both the UID and effective UID set.
    if 'SUDO_GID' in os.environ:
        gid = int(os.environ['SUDO_GID'])
        os.setregid(gid, gid)
    if 'SUDO_UID' in os.environ:
        uid = int(os.environ['SUDO_UID'])
        os.setreuid(uid, uid)
        os.environ['HOME'] = pwd.getpwuid(uid).pw_dir

def find_on_path(command):
    """Is command on the executable search path?"""
    if 'PATH' not in os.environ:
        return False
    path = os.environ['PATH']
    for element in path.split(os.pathsep):
        if not element:
            continue
        filename = os.path.join(element, command)
        if os.path.isfile(filename) and os.access(filename, os.X_OK):
            return True
    return False
