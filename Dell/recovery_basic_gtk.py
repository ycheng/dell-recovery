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
##################################################################################

import os
import subprocess
import dbus
import sys

import gtk

from Dell.recovery_gtk import DellRecoveryToolGTK
from Dell.recovery_common import *

#Translation support
import gettext
from gettext import gettext as _

class BasicGeneratorGTK(DellRecoveryToolGTK):
    def __init__(self,up,rp,version,media,target):

        #Run the normal init first
        DellRecoveryToolGTK.__init__(self,rp)

        #setup locales
        gettext.bindtextdomain(domain, LOCALEDIR)
        gettext.textdomain(domain)
        self.widgets = gtk.Builder()
        self.widgets.add_from_file(os.path.join(UIDIR,'recovery_media_creator.ui'))
        gtk.window_set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.svg')

        self.translate_widgets(self.widgets)
        self.widgets.connect_signals(self)

        self._dbus_iface = None

        self.timeout = 0

        (self.cd_burn_cmd, self.usb_burn_cmd) = find_burners()

        try:
            process=subprocess.Popen(['lsb_release','-r', '-s'], stdout=subprocess.PIPE)
            self.release=process.communicate()[0].strip('\n')
            process=subprocess.Popen(['lsb_release','-i', '-s'], stdout=subprocess.PIPE)
            self.distributor=process.communicate()[0].lower().strip('\n')
        except OSError:
            self.release='0.00'
            self.distributor='unknown'

        for item in ['server','enterprise']:
            if item in self.distributor:
                self.distributor=self.distributor.split(item)[0]

        #set any command line arguments for this frontend
        self.up=up
        self.widgets.get_object('version').set_text(version)
        self.media=media
        self.path=target

        self.ac=None

    def check_preloaded_system(self):
        """Checks that the system this tool is being run on contains a
           utility partition and recovery partition"""

        #check any command line arguments
        if self.up and not os.path.exists(self.up):
            self.up=None
        if self.rp and not os.path.exists(self.rp):
            self.rp=None
        if self.up and self.rp:
            return True

        (self.up,self.rp) = find_partitions(self.up,self.rp)

        return self.rp

    def wizard_complete(self, widget, function=None, args=None):
        """Finished answering wizard questions, and can continue process"""
        try:
            #Fill in dynamic data
            if not self.widgets.get_object('version').get_text():
                (version, distributor, release, output_text) = self.backend().query_iso_information(self.rp)
                if distributor:
                    self.distributor=distributor
                if release:
                    self.release=release
                version=increment_bto_version(version)
                self.widgets.get_object('version').set_text(version)
            self.iso = self.distributor + '-' + self.release + '-dell_' + self.widgets.get_object('version').get_text() + ".iso"
        except dbus.DBusException, e:
            if e._dbus_error_name == PermissionDeniedByPolicy._dbus_error_name:
                header = _("Permission Denied")
            else:
                header = str(e)
            self.show_alert(gtk.MESSAGE_ERROR, header,
                        parent=self.widgets.get_object('wizard'))
            return


        #Check for existing image
        skip_creation=False
        if os.path.exists(os.path.join(self.path, self.iso)) and not self.overwrite:
            skip_creation=not self.show_question(self.widgets.get_object('existing_dialog'))

        #GUI Elements
        self.widgets.get_object('wizard').hide()

        #Call our DBUS backend to build the ISO
        if not skip_creation:
            self.widgets.get_object('progress_dialog').connect('delete_event', self.ignore)

            #try to open the file as a user first so when it's overwritten, it
            #will be with the correct permissions
            try:
                if not os.path.isdir(self.path):
                    os.makedirs(self.path)
                file=open(os.path.join(self.path, self.iso),'w')
                file.close()
            except IOError:
                #this might have been somwehere that the system doesn't want us
                #writing files as a user, oh well, we tried
                pass

            #just create ISO, content is ready to go
            if not (function and args):
                self.widgets.get_object('action').set_text("Building Base image")
                function='create_' + self.distributor
                args=(self.up,
                      self.rp)

            #all functions require this at the end
            args += ( self.widgets.get_object('version').get_text(),
                      os.path.join(self.path,self.iso) )
            try:
                dbus_sync_call_signal_wrapper(self.backend(),
                                              function,
                                              {'report_progress':self.update_progress_gui},
                                              *args)
            except dbus.DBusException, e:
                if e._dbus_error_name == PermissionDeniedByPolicy._dbus_error_name:
                    self.show_alert(gtk.MESSAGE_ERROR, _("Permission Denied"),
                            parent=self.widgets.get_object('progress_dialog'))
                else:
                    self.show_alert(gtk.MESSAGE_ERROR, "Exception", e.get_dbus_message(),
                            parent=self.widgets.get_object('progress_dialog'))
                self.widgets.get_object('progress_dialog').hide()
                self.widgets.get_object('wizard').show()
                return

        self.burn(None)

    def burn(self,ret):
        """Calls an external application for burning this ISO"""
        success=False
        self.update_progress_gui(_("Opening Burner"),1.00)
        self.hide_progress()

        while not success:
            success=True
            if self.widgets.get_object('dvdbutton').get_active():
                cmd=self.cd_burn_cmd + [os.path.join(self.path, self.iso)]
            elif self.widgets.get_object('usbbutton').get_active():
                cmd=self.usb_burn_cmd + [os.path.join(self.path, self.iso)]
            else:
                cmd=None
            if cmd:
                subprocess.call(cmd)

        header = _("Recovery Media Creation Process Complete")
        body = _("If you would like to archive another copy, the generated image has been stored under the filename:\n") + os.path.join(self.path, self.iso)
        self.show_alert(gtk.MESSAGE_INFO, header, body,
            parent=self.widgets.get_object('progress_dialog'))

        self.destroy(None)

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def build_os_media_clicked(self, widget):
        """Overridden method to make us generate OS media"""
        if not self.check_preloaded_system():
            header=_("Unable to proceed")
            inst=_("System does not appear to contain Dell factory installed partition layout.")
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                parent=self.widgets.get_object('wizard'))
            return

        #hide the main page
        DellRecoveryToolGTK.build_os_media_clicked(self,None)

        #show our page
        self.widgets.get_object('wizard').show()

        self.tool_widgets.get_object('tool_selector').hide()

    def check_close(self, widget, args=None):
        """Asks the user before closing the dialog"""
        response = self.widgets.get_object('close_dialog').run()
        if response == gtk.RESPONSE_YES:
            self.destroy()
        else:
            self.widgets.get_object('close_dialog').hide()
        return True

    def hide_progress(self):
        """Hides the progress bar"""
        self.widgets.get_object('progress_dialog').hide()
        while gtk.events_pending():
            gtk.main_iteration()

    def update_progress_gui(self,progress_text,progress):
        """Updates the progressbar to show what we are working on"""
        self.widgets.get_object('progress_dialog').show()
        if float(progress) < 0:
            self.widgets.get_object('progressbar').pulse()
        else:
            self.widgets.get_object('progressbar').set_fraction(float(progress)/100)
        if progress_text != None:
            self.widgets.get_object('action').set_markup("<i>"+_(progress_text)+"</i>")
        while gtk.events_pending():
            gtk.main_iteration()
        return True

    def build_page(self,widget,page=None):
        """Prepares our GTK assistant"""

        if page == self.widgets.get_object('media_type_page'):
            self.widgets.get_object('wizard').set_page_title(page,_("Choose Media Type"))
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
            if self.usb_burn_cmd is None:
                self.widgets.get_object('usb_box').hide()
                if self.cd_burn_cmd is None:
                    self.widgets.get_object('nomediabutton').set_active(True)

            self.widgets.get_object('wizard').set_page_complete(page,True)

        elif page == self.widgets.get_object('conf_page') or \
                     widget == self.widgets.get_object('version'):

            if self.widgets.get_object('dvdbutton').get_active():
                type=self.widgets.get_object('dvdbutton').get_label()
            elif self.widgets.get_object('usbbutton').get_active():
                type=self.widgets.get_object('usbbutton').get_label()
            else:
                type=_("ISO Image")
            text = ''
            text+="<b>" + _("Media Type: ") + '</b>' + type + '\n'
            if self.rp:
                if not "/dev" in self.rp:
                    text+="<b>" + _("Recovery Partition: ") + '</b>' + self.rp + '\n'
                else:
                    text+="<b>" + _("Recovery Partition: ") + '</b>' + _("Included") + '\n'


            self.widgets.get_object('conf_text').set_markup(text)

            if page:
                self.widgets.get_object('wizard').set_page_title(page,_("Confirm Selections"))
                self.widgets.get_object('wizard').set_page_complete(page,True)
