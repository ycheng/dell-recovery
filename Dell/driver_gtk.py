#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# «driver_gtk» - Dell Driver Installer
#
# Copyright (C) 2012, Dell Inc.
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
################################################################################

import os
import dbus
import logging

#APT support
from aptdaemon.client import get_transaction
from aptdaemon.gtk3widgets import AptProgressBar, AptStatusLabel
from aptdaemon.enums import EXIT_SUCCESS

#GUI
from gi.repository import Gtk
from Dell.recovery_gtk import DellRecoveryToolGTK, translate_widgets
from Dell.recovery_common import (UIDIR, dbus_sync_call_signal_wrapper)

from gettext import gettext as _

class DriverGTK(DellRecoveryToolGTK):
    def __init__(self, recovery, utility, fname, mode):
        #Run the normal init first
        #This sets up lots of common variables as well as translation domain
        DellRecoveryToolGTK.__init__(self, recovery, utility, mode)

        #init the UI and translate widgets/connect signals
        self.widgets = Gtk.Builder()
        self.widgets.add_from_file(os.path.join(UIDIR,
                                   'driver_install.ui'))
        self._dbus_iface = None

        Gtk.Window.set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.svg')
        translate_widgets(self.widgets)
        self.widgets.connect_signals(self)

        self.file_dialog = Gtk.FileChooserDialog("Choose Driver Package",
                                        None,
                                        Gtk.FileChooserAction.OPEN,
                                        (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        self.file_dialog.set_default_response(Gtk.ResponseType.OK)
        file_filter = Gtk.FileFilter()
        file_filter.add_pattern("*.fish.tar.gz")
        self.file_dialog.set_filter(file_filter)
        self.fname = fname

        self.status_bar = self.widgets.get_object('driver_statusbar')
        self.current_context = 0
        self.status_bar.push(self.current_context, "Ready")

        self.spinner = Gtk.Spinner()
        self.widgets.get_object('driver_spinner_box').add(self.spinner)

        self.progress_bar = AptProgressBar()
        self.widgets.get_object('progress_bar_box').add(self.progress_bar)

        self.status_label = AptStatusLabel()
        self.widgets.get_object('apt_status_box').add(self.status_label)

    def toggle_spinner(self, message=None):
        self.widgets.get_object('driver_page').set_sensitive(message is None)
        if message:
            self.current_context += 1
            self.spinner.show()
            self.spinner.start()
            self.status_bar.push(self.current_context, message)
        else:
            self.spinner.stop()
            self.spinner.hide()
            self.status_bar.pop(self.current_context)
            self.current_context -= 1

    def run(self):
        '''overrides the regular run method if we have a file sent'''
        if self.fname:
            self.widgets.get_object('browse_vbox').hide()
            self.widgets.get_object('driver_window').show()
            self.validate_package()
            Gtk.main()
        else:
            DellRecoveryToolGTK.run(self)

    def install_driver(self, widget):
        '''Installs a driver package.  Activated when install is pressed'''
        transient_for = self.widgets.get_object('driver_window')
        args = (self.fname, self.rp)
        try:
            status = _("Preparing to install package...")
            logging.debug(status)
            self.toggle_spinner(status)
            dbus_sync_call_signal_wrapper(self.backend(),
                                    'install_driver_package',
                                    {"report_package_installed": self.package_installed,
                                    "report_progress" : self.update_labels},
                                    *args)
        except dbus.DBusException as msg:
            logging.error(msg)
            self.dbus_exception_handler(msg, transient_for)
            self.toggle_spinner()
            return

    def package_installed(self, exit_status, msg):
        transient_for = self.widgets.get_object('driver_window')
        if exit_status != EXIT_SUCCESS:
            if not msg:
                msg = _("Package installation failed")
            logging.error(msg)
            self.show_alert(Gtk.MessageType.ERROR, _("Package Install Failed"), msg , transient_for)
            self.toggle_spinner()
            return

        #backend: close backend
        # this handles clearing the apt cache
        # (as soon as temporary file is gone it's fixed)
        self.cleanup_backend()

        self.widgets.get_object('label_restart_required').set_text(\
                                     _("The computer needs to restart to "
                                       "finish installing this package. Please "
                                       "save your work before continuing."))

        self.toggle_spinner()
        self.widgets.get_object('driver_page').set_sensitive(False)
        self.widgets.get_object('frame_restart_required').show()
        self.progress_bar.hide()
        self.status_bar.hide()
        self.status_label.hide()

    def _request_reboot_via_session_manager(self):
        try:
            bus = dbus.SessionBus()
            proxy_obj = bus.get_object("org.gnome.SessionManager",
                                       "/org/gnome/SessionManager")
            iface = dbus.Interface(proxy_obj, "org.gnome.SessionManager")
            iface.RequestReboot()
        except dbus.DBusException:
            self._request_reboot_via_logind()
        except:
            pass
    
    def _request_reboot_via_logind(self):
        try:
            bus = dbus.SystemBus()
            proxy_obj = bus.get_object("org.freedesktop.login1",
                                       "/org/freedesktop/login1")
            iface = dbus.Interface(proxy_obj, "org.freedesktop.login1.Manager")
            iface.Reboot(False)
        except dbus.DBusException:
            pass

    def restart_clicked(self, widget, data=None):
        self._request_reboot_via_session_manager()

    def cancel_clicked(self, widget, data=None):
        '''cancel button clicked'''
        self.destroy()

    def validate_package(self):
        logging.debug("Validating package %s" % self.fname)
        self.toggle_spinner("Validating package...")
        try:
            dbus_sync_call_signal_wrapper(self.backend(),
                                            'validate_driver_package',
                                            {'report_package_info': self.update_driver_gui},
                                            self.fname)
        except dbus.DBusException as msg:
            transient_for = self.widgets.get_object('driver_window')
            logging.error(msg)         
            self.dbus_exception_handler(msg, transient_for)

    def browse_clicked(self, widget):
        '''browse button clicked'''
        response = self.file_dialog.run()
        self.file_dialog.hide()
        if response == Gtk.ResponseType.OK:
            self.fname=self.file_dialog.get_filename()
            self.validate_package()

    def update_driver_gui(self, valid, description, error_warning):
        """backend trying to update frontend"""
        text = "File: %s \n" % self.fname
        textbuffer = self.widgets.get_object('driver_textview').get_buffer()
        if valid < 0:
            self.widgets.get_object('driver_install_button').set_sensitive(False)
            text += "Error: %s \n" % error_warning
            self.widgets.get_object('browse_vbox').show()

        else:
            self.widgets.get_object('driver_install_button').set_sensitive(True)
            if valid == 0:
                text+= 'Warning: %s \n' % error_warning
                #show warning
            if type(description) == dbus.Array:
                for obj in description:
                    text += '%s \n' % obj
            else:
                text += '%s \n' % description
        logging.debug("setting textview: %s" % text)
        textbuffer.set_text(text)
        self.toggle_spinner()

    def update_labels(self, label, tid=''):
        """updates the transaction or progress label from backend"""
        if label:
            logging.debug(label)
            self.status_bar.pop(self.current_context)
            self.status_bar.push(self.current_context, label)
            self.status_label.hide()
        if tid:
            if type(tid) == dbus.String:
                logging.debug("APT transaction %s" % tid)
                trans = get_transaction(tid)
                self.progress_bar.show()
                self.status_label.show()
                self.progress_bar.set_transaction(trans)
                self.status_label.set_transaction(trans)
            else:
                num = float(tid)
                self.progress_bar.set_fraction(num)

    def top_button_clicked(self, widget):
        """Overridden method to make us install drivers"""
        if DellRecoveryToolGTK.top_button_clicked(self, widget):
            #show our page
            self.widgets.get_object('driver_window').show()
            #hide their page
            self.tool_widgets.get_object('tool_selector').hide()

