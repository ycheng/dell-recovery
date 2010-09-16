#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-default-ui»
#
# Ubiquity plugin for Setting Default Desktop UI
#
# Sets the default UI
#
# In dynamic mode, will set the UI according to LOB, CPU, GFX
#
# Copyright (C) 2010, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>

import sys
import debconf
import os
from Dell.recovery_common import match_system_device, find_supported_ui

try:
    from ubiquity.plugin import *
    from ubiquity import install_misc
except ImportError:
    sys.path.insert(0, '/usr/lib/ubiquity/')
    from ubiquity.plugin import *
    from ubiquity import install_misc

#We have to run before usersetup because it creates a conffile that will
#be reverted on firstboot
NAME = 'dell-default-ui'
BEFORE = 'usersetup'
WEIGHT = 12
OEM = False

class Install(InstallPlugin):

    def debug(self, string):
        """Allows the plugin to be ran standalone"""
        if self.target == '__main__':
            print string
        else:
            InstallPlugin.debug(string)
            
    def install(self, target, progress, *args, **kwargs):
        if 'UBIQUITY_OEM_USER_CONFIG' in os.environ:
            return InstallPlugin.install(self, target, progress, *args, **kwargs)

        self.target = target
        BIZ_CLIENT  = [ 'latitude',
                        'optiplex',
                        'vostro',
                        'precision',
                      ]

        SANDY_BRIDGE = [ '0x0100',
                         '0x0102',
                         '0x0112',
                         '0x0104',
                         '0x0106',
                         '0x0116',
                         '0x0126',
                         '0x0108',
                         '0x010A',
                         '0x0122']

        uies = find_supported_ui()

        ui = 'dynamic'
        if progress is not None:
            try:
                ui = progress.get('dell-oobe/user-interface')
            except debconf.DebconfError, e:
                pass

        #Dynamic tests
        if ui == 'dynamic':
            pci_blacklist = False
            for pci in SANDY_BRIDGE:
                if match_system_device('pci', '0x8086', pci):
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
                #See https://bugs.launchpad.net/dell/+bug/639856 for why this is here
                if 'une-efl' in uies:
                    ui = 'une-efl'
                else:
                    ui = 'une'
                self.debug("%s: Atom class CPU %s matched. setting to %s" % (NAME, cpu, ui))
            elif lob in BIZ_CLIENT:
                self.debug("%s: Business Client LOB %s matched. setting to ude" % (NAME, lob))
                ui = 'ude'
            else:
                ui = 'une'
                self.debug("%s: Falling back to une." % NAME)
        else:
            self.debug("%s: explicitly setting session to %s." %(NAME, ui))

        if ui in uies and os.path.exists(self.target + '/usr/lib/gdm/gdm-set-default-session'):
            install_misc.chrex(self.target, '/usr/lib/gdm/gdm-set-default-session', ui)
        else:
            self.debug("%s: Setting user inteface to %s is not supported." % (NAME, ui))

if __name__ == '__main__':
    install = Install(None, None)
    install.install( __name__, None)
