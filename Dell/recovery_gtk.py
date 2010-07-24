#!/usr/bin/python
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
##################################################################################

import os
import subprocess
import dbus
import sys

import gtk

from Dell.recovery_common import *

#Translation support
import gettext
from gettext import gettext as _

class DellRecoveryToolGTK:
    def __init__(self,rp):

        #setup locales
        gettext.bindtextdomain(domain, LOCALEDIR)
        gettext.textdomain(domain)
        self.tool_widgets = gtk.Builder()
        self.tool_widgets.add_from_file(os.path.join(UIDIR,'tool_selector.ui'))
        gtk.window_set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.svg')

        self.translate_widgets(self.tool_widgets)
        self.tool_widgets.connect_signals(self)

        #hide restore from HDD unless there is an RP
        if not rp:
            for object in ['button', 'image', 'label']:
                self.tool_widgets.get_object('restore_system_' + object).hide()

        #about dialog
        self.about_box = None

        #variables
        self.rp = rp
        self._dbus_iface = None

#### Polkit enhanced ###
    def backend(self):
        '''Return D-BUS backend client interface.

        This gets initialized lazily.
        '''
        if self._dbus_iface is None:
            try:
                bus = dbus.SystemBus()
                self._dbus_iface = dbus.Interface(bus.get_object(DBUS_BUS_NAME, '/RecoveryMedia'),
                                                  DBUS_INTERFACE_NAME)
            except Exception, e:
                if hasattr(e, '_dbus_error_name') and e._dbus_error_name == \
                    'org.freedesktop.DBus.Error.FileNotFound':
                    header = _("Cannot connect to dbus")
                    self.show_alert(gtk.MESSAGE_ERROR, header,
                        parent=self.widgets.get_object('progress_dialog'))
                    self.destroy(None)
                    sys.exit(1)
                else:
                    self.show_alert(gtk.MESSAGE_ERROR, "Exception", str(e),
                                    parent=self.widgets.get_object('progress_dialog'))

        return self._dbus_iface


#### Callbacks ###
    def restore_system_clicked(self, widget):
        try:
            self.tool_widgets.get_object('tool_selector').set_sensitive(False)
            dbus_sync_call_signal_wrapper(self.backend(),
                                          "enable_boot_to_restore",
                                          {})
            bus = dbus.SessionBus()
            obj = bus.get_object('org.gnome.SessionManager', '/org/gnome/SessionManager')
            iface = dbus.Interface(obj, 'org.gnome.SessionManager')
            iface.RequestReboot()
            self.destroy()
        except dbus.DBusException, e:
            if e._dbus_error_name == PermissionDeniedByPolicy._dbus_error_name:
                header = _("Permission Denied")
            else:
                header = str(e)
            self.show_alert(gtk.MESSAGE_ERROR, header,
                        parent=self.tool_widgets.get_object('tool_selector'))
        self.tool_widgets.get_object('tool_selector').set_sensitive(True)

    def build_os_media_clicked(self, widget):
        self.tool_widgets.get_object('tool_selector').set_sensitive(False)

    def about_menu_item_clicked(self, widget):
        if not self.about_box:
            self.about_box = gtk.AboutDialog()
            self.about_box.set_version(check_version())
            self.about_box.set_name(_("Dell Recovery"))
            self.about_box.set_copyright(_("Copyright 2008-2010 Dell Inc."))
            self.about_box.set_website("http://www.dell.com/ubuntu")
            self.about_box.set_authors(["Mario Limonciello"])
            self.about_box.set_destroy_with_parent(True)
            self.about_box.set_modal(True)
            self.about_box.set_transient_for(self.tool_widgets.get_object('tool_selector'))
        self.tool_widgets.get_object('tool_selector').set_sensitive(False)
        self.about_box.run()
        self.about_box.hide()
        self.tool_widgets.get_object('tool_selector').set_sensitive(True)
        

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def translate_widgets(self,widgets):
        widgets.set_translation_domain(domain)
        for widget in widgets.get_objects():
            if isinstance(widget, gtk.Label):
                widget.set_property('can-focus', False)
                widget.set_text(_(widget.get_text()))
            elif isinstance(widget, gtk.RadioButton):
                widget.set_label(_(widget.get_label()))
            elif isinstance(widget, gtk.Window):
                title = widget.get_title()
                if title:
                    widget.set_title(_(widget.get_title()))

    def run(self):
        self.tool_widgets.get_object('tool_selector').show()
        gtk.main()

    def hide_progress(self):
        """Hides the progress bar"""
        self.widgets.get_object('progress_dialog').hide()
        while gtk.events_pending():
            gtk.main_iteration()

    def show_alert(self, type, header, body=None, details=None, parent=None):
        if parent is not None:
             self.widgets.get_object('dialog_hig').set_transient_for(parent)
        else:
             self.widgets.get_object('dialog_hig').set_transient_for(self.widgets.get_object('progress_dialog'))

        message = "<b><big>%s</big></b>" % header
        if not body == None:
             message = "%s\n\n%s" % (message, body)
        self.widgets.get_object('label_hig').set_markup(message)

        if not details == None:
             buffer = self.widgets.get_object('textview_hig').get_buffer()
             buffer.set_text(str(details))
             self.widgets.get_object('expander_hig').set_expanded(False)
             self.widgets.get_object('expander_hig').show()

        if type == gtk.MESSAGE_ERROR:
             self.widgets.get_object('image_hig').set_property("stock", "gtk-dialog-error")
        elif type == gtk.MESSAGE_WARNING:
             self.widgets.get_object('image_hig').set_property("stock", "gtk-dialog-warning")
        elif type == gtk.MESSAGE_INFO:
             self.widgets.get_object('image_hig').set_property("stock", "gtk-dialog-info")

        res = self.widgets.get_object('dialog_hig').run()
        self.widgets.get_object('dialog_hig').hide()
        if res == gtk.RESPONSE_CLOSE:
            return True
        return False

    def show_question(self,dialog):
        """Presents the user with a question"""
        response = dialog.run()
        dialog.hide()
        if response == gtk.RESPONSE_YES:
            return True
        return False

    def ignore(*args):
        """Ignores a signal"""
        return True

    def destroy(self, widget=None, data=None):
        try:
            if self._dbus_iface is not None:
                self.backend().request_exit()
        except dbus.DBusException, e:
            if hasattr(e, '_dbus_error_name') and e._dbus_error_name == \
                    'org.freedesktop.DBus.Error.ServiceUnknown':
                pass
            else:
                print "Received %s when closing DBus service" % str(e)
        gtk.main_quit()
