#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-default-ui»
#
# Ubiquity plugin for Setting Default Desktop UI
#
# Sets the default UI
#
# In dynamic mode, will set the UI according to LOB and
# screen resolution
#
# Copyright (C) 2010, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>

import sys
import debconf
import subprocess
import math
import os
from Dell.recovery_common import match_system_device

try:
    from ubiquity.plugin import *
except ImportError:
    sys.path.insert(0, '/usr/lib/ubiquity/')
    from ubiquity.plugin import *

NAME = 'dell-default-ui'
AFTER = 'usersetup'
BEFORE = None
WEIGHT = 12

class Install(InstallPlugin):

    def debug(self, string):
        """Allows the plugin to be ran standalone"""
        if self.target == '__main__':
            print string
        else:
            InstallPlugin.debug(string)
            
    def install(self, target, progress, *args, **kwargs):
        if not 'UBIQUITY_OEM_USER_CONFIG' in os.environ:
            return InstallPlugin.install(self, target, progress, *args, **kwargs)

        self.target = target
        BIZ_CLIENT  = [ 'latitude',
                        'optiplex',
                        'vostro',
                        'precision',
                      ]

        SANDY_BRIDGE = [ '0100',
                         '0102',
                         '0112',
                         '0104',
                         '0106',
                         '0116',
                         '0126',
                         '0108',
                         '010A']

        ui = 'dynamic'
        if progress is not None:
            try:
                ui = progress.get('dell-oobe/user-interface')
            except debconf.DebconfError, e:
                pass

        #look on /proc/cmdline for an override option (it's more difficult to preseed into oem-config)
        with open('/proc/cmdline', 'r') as cmdline:
            cmd = cmdline.readline().strip().split()
            for item in cmd:
                if item.startswith('dell-oobe/user-interface=') and target != '__main__':
                    ui = item.split('=')[1]
                    with open(os.path.join(target, 'etc/default/grub'), 'r') as grub:
                        default_grub = grub.readlines()
                    with open(os.path.join(target, 'etc/default/grub'), 'w') as grub:
                        for line in default_grub:
                            if 'GRUB_CMDLINE_LINUX_DEFAULT' in line:
                                line = line.replace('GRUB_CMDLINE_LINUX_DEFAULT="%s ' % item, 'GRUB_CMDLINE_LINUX_DEFAULT="')
                            grub.write(line)
                    from ubiquity import install_misc
                    install_misc.chrex(target, 'update-grub')

        #Dynamic tests
        if ui == 'dynamic':
            #UDE available?
            if not os.path.exists('/usr/share/xsessions/gnome.desktop'):
                pass

            #Test for Sugar Bay
            pci_blacklist = False
            for pci in SANDY_BRIDGE:
                if match_system_device('pci', '8086', pci):
                    pci_blacklist = True
                    break

            with open('/sys/class/dmi/id/product_name','r') as dmi:
                lob = dmi.readline().lower().split()[0]

            cpu = 'unknown'
            with open('/proc/cpuinfo', 'r') as rfd:
                for line in rfd.readlines():
                    if line.startswith('model name'):
                        cpu = line.split(':')[1].strip().lower()
                        break
                        
            if pci_blacklist:
                self.debug("%s: Sandy Bridge PCI device %s matched. setting to ude" % (NAME, pci))
                ui = 'ude'
            elif 'atom' in cpu:
                self.debug("%s: Atom class CPU %s matched. setting to une" % (NAME, cpu))
                ui = 'une'
            elif lob in BIZ_CLIENT:
                self.debug("%s: Business Client LOB %s matched. setting to ude" % (NAME, lob))
                ui = 'ude'
            else:
                ui = 'une'
                self.debug("%s: Falling back to une." % NAME)
        else:
            self.debug("%s: explicitly setting session to %s." %(NAME,ui))

        if ui == 'ude':
            if os.path.exists('/usr/lib/gdm/gdm-set-default-session'):
                subprocess.call(['/usr/lib/gdm/gdm-set-default-session', 'gnome'])
            else:
                self.debug("%s: Unable to set default session, gdm-set-default-session not on system.")
        elif ui == 'une':
            pass

if __name__ == '__main__':
    install = Install(None, None)
    install.install( __name__, None)
