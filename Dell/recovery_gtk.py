#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# «recovery_gtk» - Dell Recovery GTK Frontend
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
################################################################################

import os
import subprocess
import dbus
import sys

from gi.repository import Gtk,GLib

from Dell.recovery_common import (DOMAIN, LOCALEDIR, UIDIR, SVGDIR, DBUS_INTERFACE_NAME,
                                  DBUS_BUS_NAME, dbus_sync_call_signal_wrapper,
                                  PermissionDeniedByPolicy, check_version)

#Translation support
from gettext import gettext as _
from gettext import bindtextdomain, textdomain

class DellRecoveryToolGTK:
    """GTK implementation of the Dell Recovery suite for Linux"""
    def __init__(self, recovery):

        #setup locales
        bindtextdomain(DOMAIN, LOCALEDIR)
        textdomain(DOMAIN)
        self.tool_widgets = Gtk.Builder()
        self.tool_widgets.add_from_file(os.path.join(UIDIR, 'tool_selector.ui'))
        Gtk.Window.set_default_icon_from_file(os.path.join(SVGDIR, 'dell-dvd.svg'))

        translate_widgets(self.tool_widgets)
        self.tool_widgets.connect_signals(self)

        #hide restore from HDD unless there is a recovery partition
        if not (recovery and os.path.exists('/etc/grub.d/99_dell_recovery')):
            for item in ['button', 'image', 'label']:
                self.tool_widgets.get_object('restore_system_' + item).hide()

        #about dialog
        self.about_box = None

        #variables
        self.rp = recovery
        self._dbus_iface = None

#### Polkit enhanced ###
    def backend(self):
        '''Return D-BUS backend client interface.

        This gets initialized lazily.
        '''
        if self._dbus_iface is None:
            try:
                bus = dbus.SystemBus()
                self._dbus_iface = dbus.Interface(bus.get_object(DBUS_BUS_NAME,
                                                  '/RecoveryMedia'),
                                                  DBUS_INTERFACE_NAME)
            except dbus.DBusException as msg:
                self.dbus_exception_handler(msg)
                sys.exit(1)
            except Exception as msg:
                self.show_alert(Gtk.MessageType.ERROR, "Exception", str(msg),
                           parent=self.tool_widgets.get_object('tool_selector'))

        return self._dbus_iface

    def dbus_exception_handler(self, msg, parent=None, fallback=None):
        """Common handler used for dbus type exceptions"""
        if msg.get_dbus_name() == 'org.freedesktop.DBus.Error.FileNotFound':
            text = _("Cannot connect to dbus")
        if msg.get_dbus_name() == PermissionDeniedByPolicy._dbus_error_name:
            text = _("Permission Denied")
        else:
            text = msg.get_dbus_message()

        if not parent:
            parent = self.tool_widgets.get_object('tool_selector')

        self.show_alert(Gtk.MessageType.ERROR, _("Exception"), text, parent)

        if fallback:
            parent.hide()
            fallback.show()

#### Callbacks ###
    def top_button_clicked(self, widget):
        """Callback for a button pushed in the main UI"""
        #Restore System Button
        if widget == self.tool_widgets.get_object('restore_system_button'):
            tool_selector = self.tool_widgets.get_object('tool_selector')
            try:
                tool_selector.set_sensitive(False)
                dbus_sync_call_signal_wrapper(self.backend(),
                                              "enable_boot_to_restore",
                                              {})
                bus = dbus.SessionBus()
                obj = bus.get_object('org.gnome.SessionManager',
                                     '/org/gnome/SessionManager')
                iface = dbus.Interface(obj, 'org.gnome.SessionManager')
                iface.RequestReboot()
                self.destroy()
            except dbus.DBusException as msg:
                self.dbus_exception_handler(msg)
            tool_selector.set_sensitive(True)
            
            #don't do further processing
            return False
        #Restore Media Button
        elif widget == self.tool_widgets.get_object('build_os_media_button'):
            self.tool_widgets.get_object('tool_selector').set_sensitive(False)

            #continue
            return True

    def menu_item_clicked(self, widget):
        """Callback for help menu items"""
        if widget == self.tool_widgets.get_object('get_help_menu_item'):
            # run yelp
            proc = subprocess.Popen(["yelp", "ghelp:dell-recovery"])
            # collect the exit status (otherwise we leave zombies)
            GLib.timeout_add_seconds(1, lambda proc: proc.poll() == None, proc)
        elif widget == self.tool_widgets.get_object('about_menu_item'):
            tool_selector = self.tool_widgets.get_object('tool_selector')
            if not self.about_box:
                self.about_box = Gtk.AboutDialog()
                self.about_box.set_version(check_version())
                self.about_box.set_name(_("Dell Recovery"))
                self.about_box.set_copyright(_("Copyright 2008-2010 Dell Inc."))
                self.about_box.set_website("http://www.dell.com/ubuntu")
                self.about_box.set_authors(["Mario Limonciello"])
                self.about_box.set_destroy_with_parent(True)
                self.about_box.set_modal(True)
                self.about_box.set_transient_for(tool_selector)
            tool_selector.set_sensitive(False)
            self.about_box.run()
            self.about_box.hide()
            tool_selector.set_sensitive(True)

#### GUI Functions ###
# This application is functional via command line by using the above functions #



    def run(self):
        """Runs the GTK application's main functions"""
        self.tool_widgets.get_object('tool_selector').show()
        Gtk.main()

    def show_alert(self, alert_type, header, body=None, parent=None):
        """Displays an alert message"""
        dialog_hig = self.tool_widgets.get_object('dialog_hig')
        label_hig  = self.tool_widgets.get_object('label_hig')
        image_hig  = self.tool_widgets.get_object('image_hig')
        tool_selector = self.tool_widgets.get_object('tool_selector')
        
        if parent is not None:
            dialog_hig.set_transient_for(parent)
        else:
            dialog_hig.set_transient_for(tool_selector)

        message = "<b><big>%s</big></b>" % header
        if not body == None:
            message = "%s\n\n%s" % (message, body)
            print(body, file=sys.stderr)
        label_hig.set_markup(message)
        
        if alert_type == Gtk.MessageType.ERROR:
            image_hig.set_property("stock", "gtk-dialog-error")
        elif alert_type == Gtk.MessageType.WARNING:
            image_hig.set_property("stock", "gtk-dialog-warning")
        elif alert_type == Gtk.MessageType.INFO:
            image_hig.set_property("stock", "gtk-dialog-info")

        res = self.tool_widgets.get_object('dialog_hig').run()
        self.tool_widgets.get_object('dialog_hig').hide()
        if res == Gtk.ResponseType.CLOSE:
            return True
        return False

    def destroy(self, widget=None, data=None):
        """Closes any open backend connections and stops GTK threads"""
        try:
            if self._dbus_iface is not None:
                self.backend().request_exit()
        except dbus.DBusException as msg:
            if hasattr(msg, '_dbus_error_name') and msg.get_dbus_name() == \
                    'org.freedesktop.DBus.Error.ServiceUnknown':
                pass
            else:
                print("%s when closing DBus service from %s (data: %s)" %
                      (str(msg), widget.get_name(), data))
        Gtk.main_quit()

def translate_widgets(widgets):
    """Translates all widgets to the specified domain"""
    widgets.set_translation_domain(DOMAIN)
    for widget in widgets.get_objects():
        if isinstance(widget, Gtk.Label):
            widget.set_property('can-focus', False)
            widget.set_text(_(widget.get_text()))
        elif isinstance(widget, Gtk.RadioButton):
            widget.set_label(_(widget.get_label()))
        elif isinstance(widget, Gtk.Button):
            widget.set_label(_(widget.get_label()))
        elif isinstance(widget, Gtk.Window):
            title = widget.get_title()
            if title:
                widget.set_title(_(widget.get_title()))
