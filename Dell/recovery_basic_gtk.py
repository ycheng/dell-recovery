#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «recovery_basic_gtk» - Dell Recovery Media Generator
#
# Copyright (C) 2008-2010, Dell Inc.
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

from gi.repository import Gtk

from Dell.recovery_gtk import DellRecoveryToolGTK, translate_widgets
from Dell.recovery_common import (find_partitions, find_burners, UIDIR,
                                  increment_bto_version,
                                  dbus_sync_call_signal_wrapper,
                                  fetch_output)

#Translation support
from gettext import gettext as _

class BasicGeneratorGTK(DellRecoveryToolGTK):
    """The BasicGeneratorGTK is the GTK generator that solely generates images
       from the recovery partition on a machine.
    """
    def __init__(self, utility, recovery, version, media, target, overwrite):

        #Run the normal init first
        #This sets up lots of common variables as well as translation domain
        DellRecoveryToolGTK.__init__(self, recovery)

        #init the UI and translate widgets/connect signals
        self.widgets = Gtk.Builder()
        self.widgets.add_from_file(os.path.join(UIDIR,
                                   'recovery_media_creator.ui'))

        #insert pages in reverse order
        wizard = self.widgets.get_object('wizard')
        titles = {'conf_page': _("Confirm Selections"),
                 'media_type_page': _("Choose Media Type")
                }
        for page in ['conf_page', 'media_type_page']:
            wizard.insert_page(self.widgets.get_object(page), 0)
            wizard.set_page_title(wizard.get_nth_page(0), titles[page])

        Gtk.Window.set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.svg')
        translate_widgets(self.widgets)
        self.widgets.connect_signals(self)

        #setup spinner
        self.spinner = Gtk.Spinner()
        self.widgets.get_object('spinner_box').add(self.spinner)
        self.widgets.get_object('spinner_box').show_all()

        self._dbus_iface = None
        self.timeout = 0
        self.image = ''
        
        (self.cd_burn_cmd, self.usb_burn_cmd) = find_burners()

        try:
            self.release = fetch_output(['lsb_release', '-r', '-s']).strip('\n')
            self.distributor = fetch_output(['lsb_release', '-i', '-s']).strip('\n')
        except RuntimeError:
            self.release = '0.00'
            self.distributor = 'unknown'

        self.arch = ''

        for item in ['server', 'enterprise']:
            if item in self.distributor:
                self.distributor = self.distributor.split(item)[0]

        #set any command line arguments for this frontend
        self.up = utility
        self.widgets.get_object('version').set_text(version)
        self.media = media
        self.path = target
        self.overwrite = overwrite

    def check_preloaded_system(self):
        """Checks that the system this tool is being run on contains a
           utility partition and recovery partition"""

        #check any command line arguments
        if self.up and not os.path.exists(self.up):
            self.up = None
        if self.rp and not os.path.exists(self.rp):
            self.rp = None
        if self.up and self.rp:
            return True

        (self.up, self.rp) = find_partitions(self.up, self.rp)

        return self.rp

    def wizard_complete(self, widget, function=None, args=None):
        """Finished answering wizard questions, and can continue process"""

        #Fill in dynamic data
        if not self.widgets.get_object('version').get_text():
            self.toggle_spinner_popup(True)
            try:
                dbus_sync_call_signal_wrapper(self.backend(),
                                            'query_iso_information',
                                            {'report_iso_info': self.update_version_gui},
                                            self.rp)
            except dbus.DBusException, msg:
                self.toggle_spinner_popup(False)
                parent = self.widgets.get_object('wizard')
                self.dbus_exception_handler(msg, parent)
                return
            finally:
                self.toggle_spinner_popup(False)

        if not self.image:
            self.image = '%s-%s-%s-dell_%s.iso' % (self.distributor, self.release,
                                             self.arch,
                              self.widgets.get_object('version').get_text())

        #Check for existing image
        skip_creation = False
        if not self.overwrite and \
                              os.path.exists(os.path.join(self.path, self.image)):
            skip_creation = not show_question( \
                                     self.widgets.get_object('existing_dialog'))

        #GUI Elements
        self.widgets.get_object('wizard').hide()

        #Call our DBUS backend to build the ISO
        if not skip_creation:

            #try to open the file as a user first so when it's overwritten, it
            #will be with the correct permissions
            try:
                if not os.path.isdir(self.path):
                    os.makedirs(self.path)
                with open(os.path.join(self.path, self.image), 'w') as wfd:
                    pass
            except IOError:
                #this might have been somwehere that the system doesn't want us
                #writing files as a user, oh well, we tried
                pass

            #just create ISO, content is ready to go
            if not (function and args):
                self.widgets.get_object('action').set_text( \
                                                       _("Building Base image"))
                function = 'create_' + self.distributor
                args = (False,
                        self.up,
                        self.rp)

            #all functions require this at the end
            args += ( self.widgets.get_object('version').get_text(),
                      os.path.join(self.path, self.image))
            try:
                dbus_sync_call_signal_wrapper(self.backend(),
                                function,
                                {'report_progress':self.update_progress_gui},
                                *args)
                self.update_progress_gui(_("Opening Burner"), 1.00)
            except dbus.DBusException, msg:
                parent = self.widgets.get_object('progress_dialog')
                fallback = self.widgets.get_object('wizard')
                self.dbus_exception_handler(msg, parent, fallback)
                return

        self.burn()

    def burn(self):
        """Calls an external application for burning this ISO"""
        success = False
        self.hide_progress()

        while not success:
            success = True
            if self.widgets.get_object('dvdbutton').get_active():
                cmd = self.cd_burn_cmd + [os.path.join(self.path, self.image)]
            elif self.widgets.get_object('usbbutton').get_active():
                cmd = self.usb_burn_cmd + [os.path.join(self.path, self.image)]
            else:
                cmd = None
            if cmd:
                subprocess.call(cmd)

        header = _("Recovery Media Creation Process Complete")
        body = _("If you would like to archive another copy, the generated \
image has been stored under the filename:\n") + \
os.path.join(self.path, self.image)
        self.show_alert(Gtk.MessageType.INFO, header, body,
            parent=self.widgets.get_object('progress_dialog'))

        self.destroy(None)

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def top_button_clicked(self, widget):
        """Overridden method to make us generate OS media"""
        if not self.check_preloaded_system():
            header = _("Unable to proceed")
            inst = _("System does not appear to contain Dell factory installed \
partition layout.")
            self.show_alert(Gtk.MessageType.ERROR, header, inst,
                parent=self.widgets.get_object('wizard'))
            return

        if DellRecoveryToolGTK.top_button_clicked(self, widget):
            #show our page
            self.widgets.get_object('wizard').show()
    
            self.tool_widgets.get_object('tool_selector').hide()

    def check_close(self, widget, args=None):
        """Asks the user before closing the dialog"""
        response = self.widgets.get_object('close_dialog').run()
        if response == Gtk.ResponseType.YES:
            self.destroy()
        else:
            self.widgets.get_object('close_dialog').hide()
        return True

    def hide_progress(self):
        """Hides the progress bar"""
        self.widgets.get_object('progress_dialog').hide()
        while Gtk.events_pending():
            Gtk.main_iteration()

    def toggle_spinner_popup(self, force):
        wizard = self.widgets.get_object('wizard')
        popup = self.widgets.get_object('spinner_popup')
        if force:
            wizard.set_sensitive(False)
            self.spinner.start()
            popup.show()
        else:
            self.spinner.stop()
            popup.hide()
            wizard.set_sensitive(True)
        while Gtk.events_pending():
            Gtk.main_iteration()

    def update_version_gui(self, version, distributor, release, arch, output_text):
        """Stops any running spinners and updates GUI items"""

        if distributor:
            self.distributor = distributor
        if release:
            self.release = release
        if arch:
            self.arch = arch
        
        self.bto_base = bool(version)

        if self.bto_base:
            version = increment_bto_version(version)
        else:
            version = 'X00'

        self.widgets.get_object('version').set_text(version)
        return True

    def update_progress_gui(self, progress_text, progress):
        """Updates the progressbar to show what we are working on"""
        
        progressbar = self.widgets.get_object('progressbar')
        self.widgets.get_object('progress_dialog').show()

        if float(progress) < 0:
            progressbar.pulse()
        else:
            progressbar.set_fraction(float(progress)/100)
        if progress_text != None:
            self.widgets.get_object('action').set_markup("<i>" +\
                                                        _(progress_text)+"</i>")
        while Gtk.events_pending():
            Gtk.main_iteration()
        return True

    def build_page(self, widget, page=None):
        """Prepares our GTK assistant"""
        if page == self.widgets.get_object('media_type_page'):
            #fill in command line args
            if self.media == "dvd":
                self.widgets.get_object('dvdbutton').set_active(True)
            elif self.media == "usb":
                self.widgets.get_object('usbbutton').set_active(True)
            else:
                self.widgets.get_object('nomediabutton').set_active(True)
            #remove invalid options (missing burners)
            if self.cd_burn_cmd is None:
                self.widgets.get_object('dvd_box').hide()
                self.widgets.get_object('usbbutton').set_active(True)
            else:
                self.widgets.get_object('dvd_box').show()
            if self.usb_burn_cmd is None:
                self.widgets.get_object('usb_box').hide()
                if self.cd_burn_cmd is None:
                    self.widgets.get_object('nomediabutton').set_active(True)
            else:
                self.widgets.get_object('usb_box').show()

            self.widgets.get_object('wizard').set_page_complete(page, True)

        elif page == self.widgets.get_object('conf_page') or \
                     widget == self.widgets.get_object('version'):

            if self.widgets.get_object('dvdbutton').get_active():
                burn_type = self.widgets.get_object('dvdbutton').get_label()
            elif self.widgets.get_object('usbbutton').get_active():
                burn_type = self.widgets.get_object('usbbutton').get_label()
            else:
                burn_type = _("Image File")
            text  = ''
            text += "<b>" + _("Media Type: ") + '</b>' + burn_type + '\n'
            if self.rp:
                if not "/dev" in self.rp:
                    text += "<b>" + _("Recovery Partition: ") + '</b>'
                    text += self.rp + '\n'
                else:
                    text += "<b>" + _("Recovery Partition: ") + '</b>'
                    text += _("Included") + '\n'


            self.widgets.get_object('conf_text').set_markup(text)

            if page:
                self.widgets.get_object('wizard').set_page_complete(page, True)
                self.widgets.get_object('wizard').set_page_type(page, Gtk.AssistantPageType.CONFIRM)

def show_question(dialog):
    """Presents the user with a question"""
    response = dialog.run()
    dialog.hide()
    return (response == Gtk.ResponseType.YES)
