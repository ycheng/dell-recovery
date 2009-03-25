#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «recovery_dvd» - Dell Recovery DVD Creator
#
# Copyright (C) 2008-2009, Dell Inc.
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
import stat
import dbus
import gobject
import sys

import pygtk
pygtk.require("2.0")

import gtk
import gtk.glade

#Translation Support
domain='dell-recovery'
import gettext
from gettext import gettext as _
LOCALEDIR='/usr/share/locale'

#Glade directory
GLADEDIR = '/usr/share/dell/glade'
#GLADEDIR = '/home/test/dell-recovery/Dell'

#Resultant Image
ISO='ubuntu-dell-reinstall.iso'

class DVD():
    def __init__(self):

        #setup locales
        for module in (gettext, gtk.glade):
            module.bindtextdomain(domain, LOCALEDIR)
            module.textdomain(domain)

        self.glade = gtk.glade.XML(GLADEDIR + '/' + 'progress_dialogs.glade')
        for widget in self.glade.get_widget_prefix(""):
            setattr(self, widget.get_name(), widget)
#for some reason our glade doesn't want to translate
#force it all
            if isinstance(widget, gtk.Label):
                widget.set_property('can-focus', False)
                widget.set_text(_(widget.get_text()))
            elif isinstance(widget, gtk.RadioButton):
                widget.set_label(_(widget.get_label()))
            elif isinstance(widget, gtk.Window):
                title=widget.get_title()
                if title:
                    widget.set_title(_(title))
        self.glade.signal_autoconnect(self)

        self.destination = os.getenv('HOME') + '/' + ISO
        self.timeout = 0
        self.progress = 0

    def check_preloaded_system(self):
        """Checks that the system this tool is being run on contains a
           utility partition and recovery partition"""
        bus = dbus.SystemBus()
        hal_obj = bus.get_object('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
        hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')

        self.up=False
        self.rp=False

        udis = hal.FindDeviceByCapability('volume')
        for udi in udis:
            dev_obj = bus.get_object('org.freedesktop.Hal', udi)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')

            property = dev.GetProperty('volume.label')

            if 'DellUtility' in property:
                self.up=dev.GetProperty('block.device')
            elif 'install' in property or 'OS' in property:
                self.rp=dev.GetProperty('block.device')

            if self.up and self.rp:
                return True
        return False


    def burn(self,pid,error_code):
        """Calls an external application for burning this ISO"""
        success=False
        if error_code is 0:
            self.progress=1.00
            self.progress_text=_("Opening Burner")
            self.update_progress_gui()
            self.hide_progress()

            while not success:
                success=True
                if self.dvdbutton.get_active():
                    ret=subprocess.call(['brasero', '-i', self.destination])
                else:
                    ret=subprocess.call(['usb-creator', '--iso=' + self.destination, '-n'])
                if ret is not 0:
                    success=self.show_question(self.retry_dialog)

            header = _("Recovery Media Creation Process Complete")
            body = _("If you would like to archive another copy, the generated image has been stored in your home directory under the filename:") + ' ' + self.destination
            self.show_alert(gtk.MESSAGE_INFO, header, body,
                parent=self.progress_dialog)

        else:
            header = _("Could not build image")
            self.show_alert(gtk.MESSAGE_ERROR, header,
                parent=self.progress_dialog)

        self.destroy(None)

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def run(self):
        if self.check_preloaded_system():
            self.wizard.show()
        else:
            header=_("This tool requires that a Utility Partition and Linux Recovery partition are present to function.")
            inst = None
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
            return
        gtk.main()

    def hide_progress(self):
        """Hides the progress bar"""
        self.progress_dialog.hide()
        while gtk.events_pending():
            gtk.main_iteration()

    def show_alert(self, type, header, body=None, details=None, parent=None):
        if parent is not None:
             self.dialog_hig.set_transient_for(parent)
        else:
             self.dialog_hig.set_transient_for(self.progress_dialog)

        message = "<b><big>%s</big></b>" % header
        if not body == None:
             message = "%s\n\n%s" % (message, body)
        self.label_hig.set_markup(message)

        if not details == None:
             buffer = self.textview_hig.get_buffer()
             buffer.set_text(str(details))
             self.expander_hig.set_expanded(False)
             self.expander_hig.show()

        if type == gtk.MESSAGE_ERROR:
             self.image_hig.set_property("stock", "gtk-dialog-error")
        elif type == gtk.MESSAGE_WARNING:
             self.image_hig.set_property("stock", "gtk-dialog-warning")
        elif type == gtk.MESSAGE_INFO:
             self.image_hig.set_property("stock", "gtk-dialog-info")

        res = self.dialog_hig.run()
        self.dialog_hig.hide()
        if res == gtk.RESPONSE_CLOSE:
            return True
        return False

    def check_close(self,widget):
        """Asks the user before closing the dialog"""
        response = self.close_dialog.run()
        if response == gtk.RESPONSE_YES:
            self.destroy()
        else:
            self.close_dialog.hide()

    def show_question(self,dialog):
        """Presents the user with a question"""
        response = dialog.run()
        dialog.hide()
        if response == gtk.RESPONSE_YES:
            return False
        return True

    def update_progress_gui(self):
        """Updates the progressbar to show what we are working on"""
        self.progressbar.set_fraction(float(self.progress)/100)
        if self.progress_text != None:
            self.action.set_markup("<i>"+_(self.progress_text)+"</i>")
        while gtk.events_pending():
            gtk.main_iteration()
        return True

    def build_page(self,widget,page):
        """Prepares our GTK assistant"""

        if page == self.start_page:
            self.wizard.set_page_title(page,_("Welcome"))
            self.wizard.set_page_complete(page,True)
        elif page == self.media_type_page:
            self.wizard.set_page_title(page,_("Choose Media Type"))
            self.wizard.set_page_complete(page,True)
        elif page == self.conf_page:
            self.wizard.set_page_title(page,_("Confirm Selections"))

            #Fill in dynamic data
            if self.dvdbutton.get_active():
                type=self.dvdbutton.get_label()
            else:
                type=self.usbbutton.get_label()

            self.conf_text.set_text(_("Media Type: ") + type + '\n')
            self.wizard.set_page_complete(page,True)


    def create_dvd(self,widget):
        """Starts the DVD Creation Process"""

        #Check for existing image
        skip_creation=False
        if os.path.exists(self.destination):
            skip_creation=self.show_question(self.existing_dialog)

        #GUI Elements
        self.wizard.hide()
        self.progress_dialog.show()
        self.progress_dialog.connect('delete_event', self.ignore)
        self.action.set_text("Building Base image")

        #Full process for creating an image
        if not skip_creation:
            if os.getuid() == 0:
                sudo = []
            elif os.path.exists('/usr/bin/gksudo'):
                sudo = ['gksudo', '-k']
            elif os.path.exists('/usr/bin/kdesu'):
                sudo = ['kdesu', '--nonewdcop', '--']
            
            cmd = '/usr/share/dell/bin/create_iso.py' + \
                  ' -u ' + self.up + \
                  ' -r ' + self.rp + \
                  ' -i ' + self.destination
            sudo.append(cmd)
            self.pipe = subprocess.Popen(sudo, stdout=subprocess.PIPE,
            stderr=sys.stderr, universal_newlines=True)
            self.watch = gobject.io_add_watch(self.pipe.stdout,
                 gobject.IO_IN | gobject.IO_HUP,
                 self.data_available)
            # Wait for the process to complete
            gobject.child_watch_add(self.pipe.pid, self.burn)
        else:
            self.burn(None, 0)


    def data_available(self, source, condition):
        text = source.readline()
        if len(text) > 0:
            if len(text.split('%')) > 1:
                self.progress=text.split('%')[0]
            else:
                self.progress_text = text.strip('\n')
            if not self.timeout:
                self.timeout = gobject.timeout_add(2000, self.update_progress_gui)
            return True
        else:
            if self.timeout:
                gobject.source_remove(self.timeout)
            return False

    def ignore(*args):
        """Ignores a signal"""
        return True

    def destroy(self, widget=None, data=None):
        gtk.main_quit()
