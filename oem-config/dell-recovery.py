#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-recovery» - OEM Config plugin for Dell-Recovery Media
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
import subprocess
import os
import Dell.recovery_common as magic
import dbus

NAME = 'dell-recovery'
AFTER = 'usersetup'
BEFORE = None
WEIGHT = 12

#Gtk widgets
class PageGtk(PluginUI):
    def __init__(self, *args, **kwargs):
        up,  rp  = magic.find_partitions('','')
        dvd, usb = magic.find_burners()
        oem = 'UBIQUITY_OEM_USER_CONFIG' in os.environ
        if rp and oem:
            try:
                import gtk
                builder = gtk.Builder()
                builder.add_from_file('/usr/share/ubiquity/gtk/stepRecoveryMedia.ui')
                builder.connect_signals(self)
                self.plugin_widgets = builder.get_object('stepRecoveryMedia')
                self.usb_media = builder.get_object('save_to_usb')
                self.dvd_media = builder.get_object('save_to_dvd')
                self.none_media = builder.get_object('save_to_none')
                if not dvd:
                    builder.get_object('dvd_vbox').set_sensitive(False)
                if not usb:
                    builder.get_object('usb_vbox').set_sensitive(False)
            except Exception, e:
                self.debug('Could not create Dell Recovery page: %s', e)
                self.plugin_widgets = None
        else:
            if not rp:
                self.debug('Disabling %s because of problems with partitions: up[%s] and rp[%s]', NAME, up, rp)
            self.plugin_widgets = None

    def get_type(self):
        """Returns the type of recovery to do from GUI"""
        if self.usb_media.get_active():
            return "usb"
        elif self.dvd_media.get_active():
            return "dvd"
        else:
            return "none"

    def set_type(self,type):
        """Sets the type of recovery to do in GUI"""
        if type == "usb":
            self.usb_media.set_active(True)
        elif type == "dvd":
            self.dvd_media.set_active(True)
        else:
            self.none_media.set_active(True)

class Page(Plugin):
    def prepare(self, unfiltered=False):
        destination = self.db.get('dell-recovery/destination')
        self.ui.set_type(destination)
        return Plugin.prepare(self, unfiltered=unfiltered)

    def ok_handler(self):
        destination = self.ui.get_type()
        self.preseed('dell-recovery/destination', destination)
        Plugin.ok_handler(self)

class Install(InstallPlugin):
    def update_progress_gui(self, progress_text, progress_percent):
        """Function called by the backend to update the progress in a frontend"""
        self.progress._db.subst('dell-recovery/build_progress', 'MESSAGE', progress_text)
        self.progress._db.subst('dell-recovery/build_progress', 'PERCENT', progress_percent)
        self.progress.info('dell-recovery/build_progress')

    def install(self, target, progress, *args, **kwargs):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.progress=progress
        type = self.db.get('dell-recovery/destination')
        if type != "none":
            up,  rp  = magic.find_partitions('','')
            dvd, usb = magic.find_burners()
            file = os.path.join('/tmp/dell.iso')
            try:
                bus = dbus.SystemBus()
                dbus_iface = dbus.Interface(bus.get_object(magic.DBUS_BUS_NAME, '/RecoveryMedia'),
                                            magic.DBUS_INTERFACE_NAME)
            except Exception, e:
                self.debug('Exception in %s install function, creating dbus backend: %s', NAME, str(e))
                return

            progress.info('dell-recovery/build_start')

            #Determine internal version number of image
            (version,date) = dbus_iface.query_bto_version(rp)
            version = magic.increment_bto_version(version)

            #Build image
            try:
                magic.dbus_sync_call_signal_wrapper(dbus_iface,
                                                    'create_ubuntu',
                                                    {'report_progress':self.update_progress_gui},
                                                    up,
                                                    rp,
                                                    version,
                                                    file)
            except dbus.DBusException, e:
                self.debug('Exception in %s install function calling backend: %s', NAME, str(e))
                return

            #Close backend
            try:
                dbus_iface.request_exit()
            except dbus.DBusException, e:
                if hasattr(e, '_dbus_error_name') and e._dbus_error_name == \
                        'org.freedesktop.DBus.Error.ServiceUnknown':
                    pass
                else:
                    sys.debug("Received %s when closing recovery-media-backend DBus service",str(e))
                    return

            #Launch burning tool
            if type == "dvd":
                cmd=dvd + [file]
            elif type == "usb":
                cmd=usb + [file]
            else:
                cmd=None
            if cmd:
                progress.info('dell-recovery/burning')
                subprocess.call(cmd)

            #Clean up when done
            if os.path.exists(file):
                os.remove(file)

        return InstallPlugin.install(self, target, progress, *args, **kwargs)

