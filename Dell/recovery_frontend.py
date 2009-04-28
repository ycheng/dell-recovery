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

import dbus.mainloop.glib

import pygtk
pygtk.require("2.0")

import gtk
import gtk.glade

from Dell.recovery_backend import UnknownHandlerException, PermissionDeniedByPolicy, BackendCrashError, polkit_auth_wrapper, dbus_sync_call_signal_wrapper, Backend, DBUS_BUS_NAME

#Translation Support
domain='dell-recovery'
import gettext
from gettext import gettext as _
LOCALEDIR='/usr/share/locale'

#Glade directory
GLADEDIR = '/usr/share/dell/glade'

#Resultant Image
ISO='/ubuntu-dell-reinstall.iso'

#Supported burners and their arguments
cd_burners = { 'brasero':['-i'],
               'nautilus-cd-burner':['--source-iso='] }
usb_burners = { 'usb-creator':['-n','--iso'] }

class Frontend:
    def __init__(self,up,rp,media,target,overwrite):

        #setup locales
        for module in (gettext, gtk.glade):
            module.bindtextdomain(domain, LOCALEDIR)
            module.textdomain(domain)
        self.glade = gtk.glade.XML(GLADEDIR + '/' + 'recovery_media_creator.glade')
        gtk.window_set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.png')
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

        self._dbus_iface = None

        self.timeout = 0

        self.check_burners()

        try:
            process=subprocess.Popen(['lsb_release','-d', '-s'], stdout=subprocess.PIPE)
            self.release=process.communicate()[0]
        except OSError:
            #if we don't have lsb_release sitting around, not a big deal
            self.release=None

        #set any command line arguments
        self.up=up
        self.rp=rp
        self.media=media
        self.target=target
        self.overwrite=overwrite

    def check_burners(self):
        """Checks for what utilities are available to burn with"""
        def which(program):
            import os
            def is_exe(fpath):
                return os.path.exists(fpath) and os.access(fpath, os.X_OK)

            fpath, fname = os.path.split(program)
            if fpath:
                if is_exe(program):
                    return program
            else:
                for path in os.environ["PATH"].split(os.pathsep):
                    exe_file = os.path.join(path, program)
                    if is_exe(exe_file):
                        return exe_file

            return None

        def find_command(array):
            for item in array:
                path=which(item)
                if path is not None:
                    return [path] + array[item]
            return None

        self.cd_burn_cmd = find_command(cd_burners)
        
        self.usb_burn_cmd = find_command(usb_burners)

    def check_preloaded_system(self):
        """Checks that the system this tool is being run on contains a
           utility partition and recovery partition"""
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        hal_obj = bus.get_object('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
        hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')

        #check any command line arguments
        if self.up is not None and not os.path.exists(self.up):
            header=_("Invalid utility partition") + _(" in command line arguments.  Falling back to HAL based detection.")
            inst = None
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                parent=self.progress_dialog)
            self.up=None
        if self.rp is not None and not os.path.exists(self.rp):
            header=_("Invalid recovery partition") + _(" in command line arguments.  Falling back to HAL based detection.")
            inst = None
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                parent=self.progress_dialog)
            self.rp=None
        if self.up is not None and self.rp is not None:
            return True

        udis = hal.FindDeviceByCapability('volume')
        for udi in udis:
            dev_obj = bus.get_object('org.freedesktop.Hal', udi)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')

            label = dev.GetProperty('volume.label')
            fs    = dev.GetProperty('volume.fstype')

            if not self.up and 'DellUtility' in label:
                self.up=dev.GetProperty('block.device')
            elif not self.rp and ('install' in label or 'OS' in label) and 'vfat' in fs:
                self.rp=dev.GetProperty('block.device')

            if self.up and self.rp:
                return True
        return False

    def wizard_complete(self,widget):
        """Finished answering wizard questions, and can continue process"""

        #Check for existing image
        skip_creation=False
        if os.path.exists(self.filechooserbutton.get_filename() + ISO) and not self.overwrite:
            skip_creation=self.show_question(self.existing_dialog)

        #GUI Elements
        self.wizard.hide()

        #Call our DBUS backend to build the ISO
        if not skip_creation:
            self.progress_dialog.connect('delete_event', self.ignore)
            self.action.set_text("Building Base image")
            #try to open the file as a user first so when it's overwritten, it
            #will be with the correct permissions
            try:
                file=open(self.filechooserbutton.get_filename() + ISO,'w')
                file.close()
            except IOError:
                #this might have been somwehere that the system doesn't want us
                #writing files as a user, oh well, we tried
                pass
            try:
                polkit_auth_wrapper(dbus_sync_call_signal_wrapper,
                    self.backend(),'create', {'report_progress':self.update_progress_gui},
                    self.up, self.rp, self.filechooserbutton.get_filename() + ISO)
            except dbus.DBusException, e:
                if e._dbus_error_name == PermissionDeniedByPolicy._dbus_error_name:
                    header = _("Permission Denied")
                else:
                    header = str(e)
                self.show_alert(gtk.MESSAGE_ERROR, header,
                            parent=self.progress_dialog)
                self.progress_dialog.hide()
                self.wizard.show()
                return
        self.burn(None)

    def burn(self,ret):
        """Calls an external application for burning this ISO"""
        success=False
        self.update_progress_gui(_("Opening Burner"),1.00)
        self.hide_progress()

        while not success:
            success=True
            if self.dvdbutton.get_active():
                cmd=self.cd_burn_cmd + [self.filechooserbutton.get_filename() + ISO]
            elif self.usbbutton.get_active():
                cmd=self.usb_burn_cmd + [self.filechooserbutton.get_filename() + ISO]
            else:
                cmd=None
            if cmd:
                subprocess.call(cmd)

        header = _("Recovery Media Creation Process Complete")
        body = _("If you would like to archive another copy, the generated image has been stored under the filename:") + ' ' + self.filechooserbutton.get_filename() + ISO
        self.show_alert(gtk.MESSAGE_INFO, header, body,
            parent=self.progress_dialog)

        self.destroy(None)

#### Polkit enhanced ###
    def backend(self):
        '''Return D-BUS backend client interface.

        This gets initialized lazily.
        '''
        if self._dbus_iface is None:
            try:
                self._dbus_iface = Backend.create_dbus_client()
            except Exception, e:
                if hasattr(e, '_dbus_error_name') and e._dbus_error_name == \
                    'org.freedesktop.DBus.Error.FileNotFound':
                    header = _("Cannot connect to dbus")
                    self.show_alert(gtk.MESSAGE_ERROR, header,
                        parent=self.progress_dialog)
                    self.destroy(None)
                    sys.exit(1)
                else:
                    raise

        return self._dbus_iface

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

    def update_progress_gui(self,progress_text,progress):
        """Updates the progressbar to show what we are working on"""
        self.progress_dialog.show()
        self.progressbar.set_fraction(float(progress)/100)
        if progress_text != None:
            self.action.set_markup("<i>"+_(progress_text)+"</i>")
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
            #fill in command line args
            if self.media == "dvd":
                self.dvdbutton.set_active(True)
            elif self.media == "usb":
                self.usbbutton.set_active(True)
            else:
                self.nomediabutton.set_active(True)
            #remove invalid options (missing burners)
            if self.cd_burn_cmd is None:
                self.dvdbutton.set_sensitive(False)
                self.usbbutton.set_active(True)
            if self.usb_burn_cmd is None:
                self.usbbutton.set_sensitive(False)
                if self.cd_burn_cmd is None:
                    self.nomediabutton.set_active(True)

            self.wizard.set_page_complete(page,True)
        elif page == self.file_page:
            self.wizard.set_page_title(page,_("Choose Target Directory"))
            self.filechooserbutton.set_action(gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER)
            #fill in command line args
            if os.path.exists(self.target):
                self.filechooserbutton.set_current_folder(self.target)

            self.wizard.set_page_complete(page,True)
        elif page == self.conf_page:
            self.wizard.set_page_title(page,_("Confirm Selections"))

            #Fill in dynamic data
            if self.dvdbutton.get_active():
                type=self.dvdbutton.get_label()
            elif self.usbbutton.get_active():
                type=self.usbbutton.get_label()
            else:
                type=_("ISO Image")
            text = ''
            if self.release:
                text+=_("OS Release: ") + self.release
            text+=_("Utility Partition: ") + self.up + '\n'
            text+=_("Recovery Partition: ") + self.rp + '\n'
            text+=_("Media Type: ") + type + '\n'
            text+=_("File Name: ") + self.filechooserbutton.get_filename() + ISO + '\n'
            

            self.conf_text.set_text(text)
            self.wizard.set_page_complete(page,True)

    def ignore(*args):
        """Ignores a signal"""
        return True

    def destroy(self, widget=None, data=None):
        gtk.main_quit()
