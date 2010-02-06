#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-bootstrap» - Ubiquity plugin for Dell Factory Process
#
# Copyright (C) 2010, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>
#
# This is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this application; if not, write to the Free Software Foundation, Inc., 51
# Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
##################################################################################

from ubiquity.plugin import *
from ubiquity import misc
import Dell.recovery_common as magic
import subprocess
import os

NAME = 'dell-bootstrap'
AFTER = None
BEFORE = 'language'
WEIGHT = 12

#Gtk widgets
class PageGtk(PluginUI):
    def __init__(self, controller, *args, **kwargs):
        self.plugin_widgets = None

        file = open('/proc/cmdline')
        self.cmdline = file.readline().strip('\n')
        file.close()
        
        oem = 'UBIQUITY_OEM_USER_CONFIG' in os.environ

        with misc.raised_privileges():
            self.genuine = magic.check_vendor()

        self.reinstall = 'REINSTALL' in self.cmdline
        self.dvdboot = 'DVDBOOT' in self.cmdline

        if (self.reinstall or self.dvdboot or not self.genuine) and not oem:
            try:
                import gtk
                builder = gtk.Builder()
                builder.add_from_file('/usr/share/ubiquity/gtk/stepDellBootstrap.ui')
                builder.connect_signals(self)
                self.controller = controller
                self.plugin_widgets = builder.get_object('stepDellBootstrap')
                self.automated_recovery = builder.get_object('automated_recovery')
                self.interactive_recovery = builder.get_object('interactive_recovery')
                self.hidden_radio = builder.get_object('hidden_radio')
                if not self.genuine:
                    builder.get_object('interactive_recovery_box').hide()
                    builder.get_object('automated_recovery_box').hide()
                    self.automated_recovery.set_sensitive(False)
                    self.interactive_recovery.set_sensitive(False)
                    builder.get_object('genuine_box').show()
                elif not self.dvdboot:
                    builder.get_object('interactive_recovery_box').hide()
                    self.interactive_recovery.set_sensitive(False)
            except Exception, e:
                self.debug('Could not create Dell Bootstrap page: %s', e)
        else:
            if not (self.reinstall or self.dvdboot):
                self.debug('Disabling %s because of problems with cmdline: [%s]', NAME, self.cmdline)
            elif oem:
                self.debug('Disabling %s because of running in OEM mode', NAME)

    def plugin_get_current_page(self):
        if not self.genuine:                               
            self.controller.allow_go_forward(False)
        return self.plugin_widgets

    def get_type(self):
        """Returns the type of recovery to do from GUI"""
        if self.automated_recovery.get_active():
            return "automatic"
        elif self.interactive_recovery.get_active():
            return "interactive"
        else:
            return ""

    def set_type(self,type):
        """Sets the type of recovery to do in GUI"""
        if type == "automatic":
            self.automated_recovery.set_active(True)
        elif type == "interactive":
            self.interactive_recovery.set_active(True)
        else:
            self.hidden_radio.set_active(True)
            self.controller.allow_go_forward(False)

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.controller.allow_go_forward(True)

class Page(Plugin):
    def copy_rp(self):
        """Copies content to the recovery partition"""
        pass

    def install_grub(self):
        """Installs grub on the recovery partition"""
        pass

    def remove_extra_partitions(self):
        """Removes partitions 3 and 4 for the process to start"""
        pass

    def kexec(self):
        """attempts to kexec a new kernel and falls back to a reboot"""
        pass

    def unset_drive_preseeds(self):
        """Unsets any preseeds that are related to setting a drive"""

    def prepare(self, unfiltered=False):
        type = self.db.get('dell-recovery/recovery_type')
        self.ui.set_type(type)
        return Plugin.prepare(self, unfiltered=unfiltered)

    def ok_handler(self):
        type = self.ui.get_type()
        self.preseed('dell-recovery/recovery_type', type)
        # User recovery - need to copy RP
        if type == "automatic":
            self.copy_rp()
            self.kexec()
        # User recovery - resizing drives
        elif type == "interactive":
            self.unset_drive_preseeds()
        # Factory install and post kexec
        else:
            self.remove_extra_partitions()
            self.install_grub()
        return Plugin.ok_handler(self)
        

#Currently we have actual stuff that's run as a late command
#class Install(InstallPlugin):
#
#    def install(self, target, progress, *args, **kwargs):
#        return InstallPlugin.install(self, target, progress, *args, **kwargs)

