# -*- coding: utf-8 -*-
#
# «recovery_dvd» - Dell Recovery DVD Creator
#
# Copyright (C) 2008, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>
#
# Mythbuntu is free software; you can redistribute it and/or modify it under
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
import shutil
import tempfile
import atexit
import time

import pygtk
pygtk.require("2.0")

import gtk
import gtk.glade

#Borrowed from Canonical/Ubiquity
import gconftool

#Translation Support
from gettext import gettext as _

#Glade directory
GLADEDIR = '/usr/share/dell/glade'

#Place to do operations on
DRIVE='/dev/sda'
UTILITY_PARTITION='1'
RECOVERY_PARTITION='2'

#Resultant Image
ISO='ubuntu-dell-reinstall.iso'

class DVD():
    def __init__(self):
        self.glade = gtk.glade.XML(GLADEDIR + '/' + 'progress_dialogs.glade')
        for widget in self.glade.get_widget_prefix(""):
            setattr(self, widget.get_name(), widget)
            if isinstance(widget, gtk.Label):
                widget.set_property('can-focus', False)
        self.glade.signal_autoconnect(self)
        self.destination = os.getenv('HOME') + '/' + ISO

    def mount_drives(self):
        subprocess.call(['mount', DRIVE + RECOVERY_PARTITION , self._mntdir])
        atexit.register(self.unmount_drives)

    def unmount_drives(self):
        subprocess.call(['umount', DRIVE + RECOVERY_PARTITION])

    def build_up(self,gui=False):
        """Builds a Utility partition Image"""
        ##Create UP
        if gui is not False:
            self.update_gui(0.003,"Building Utility Partition")
        self._tmpdir=tempfile.mkdtemp()
        os.mkdir(self._tmpdir + '/up')
        #MBR
        subprocess.call(['dd','if=' + DRIVE,'bs=512','count=1','of=' + self._tmpdir + '/up/mbr.bin'])
        #UP Partition
        p1 = subprocess.Popen(['dd','if='+ DRIVE + UTILITY_PARTITION,'bs=1M'], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
        partition_file=open(self._tmpdir + '/up/' + 'upimg.bin', "w")
        partition_file.write(p2.communicate()[0])
        partition_file.close()
        self._mntdir=tempfile.mkdtemp()
        if gui is not False:
            self.update_gui(0.005,"Building Utility Partition")

        #Mount the RP & clean it up
        if gui is not False:
            self.update_gui(0.007,"Preparing Recovery Partition")
        self._mntdir=tempfile.mkdtemp()
        self.mount_drives()
        for file in os.listdir(self._mntdir):
            if ".exe" in file or "pagefile.sys" in file:
                os.remove(self._mntdir + '/' + file)
        if gui is not False:
            self.update_gui(0.009,"Building Recovery Partition")

    def build_iso(self,gui=False):
        """Builds an ISO image"""
        if gui is not False:
            self.update_gui(0.01,"Building ISO image")
        #Boot sector for ISO
        shutil.copy(self._mntdir + '/isolinux/isolinux.bin', self._tmpdir)

        #ISO Creation
        genisoargs=['genisoimage', '-o', self.destination,
            '-input-charset', 'utf-8',
            '-b', 'isolinux/isolinux.bin', '-c', 'isolinux/boot.catalog',
            '-no-emul-boot', '-boot-load-size', '4', '-boot-info-table',
            '-pad', '-r', '-J', '-joliet-long', '-N', '-hide-joliet-trans-tbl',
            '-cache-inodes', '-l',
            '-publisher', 'Dell Inc.',
            '-V', 'Dell Ubuntu Reinstallation Media',
            self._mntdir + '/', self._tmpdir + '/up/']
        p3 = subprocess.Popen(genisoargs,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        retval = p3.poll()
        while (retval is None):
            output = p3.stderr.readline()
            if ( output != "" ):
                progress = output.split()[0]
                if (progress[-1:] == '%'):
                    if gui is not False:
                        self.update_gui(float(progress[:-1])/100,"Building ISO Image")
                    else:
                        print progress[:-1] + " % Done"
        #umount drive
        self.unmount_drives()

    def burn_iso(self):
        """Calls an external CD burning application to burn this ISO"""
        subprocess.call(['nautilus-cd-burner', '--source-iso=' + self.destination])

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def run(self):
        self.progress_dialog.show()
        gtk.main()

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

    def disable_volume_manager(self):
        gvm_root = '/desktop/gnome/volume_manager'
        gvm_automount_drives = '%s/automount_drives' % gvm_root
        gvm_automount_media = '%s/automount_media' % gvm_root
        volumes_visible = '/apps/nautilus/desktop/volumes_visible'
        media_automount = '/apps/nautilus/preferences/media_automount'
        media_automount_open = '/apps/nautilus/preferences/media_automount_open'
        self.gconf_previous = {}
        for gconf_key in (gvm_automount_drives, gvm_automount_media,
                          volumes_visible,
                          media_automount, media_automount_open):
            self.gconf_previous[gconf_key] = gconftool.get(gconf_key)
            if self.gconf_previous[gconf_key] != 'false':
                gconftool.set(gconf_key, 'bool', 'false')

        atexit.register(self.enable_volume_manager)

    def enable_volume_manager(self):
        gvm_root = '/desktop/gnome/volume_manager'
        gvm_automount_drives = '%s/automount_drives' % gvm_root
        gvm_automount_media = '%s/automount_media' % gvm_root
        volumes_visible = '/apps/nautilus/desktop/volumes_visible'
        media_automount = '/apps/nautilus/preferences/media_automount'
        media_automount_open = '/apps/nautilus/preferences/media_automount_open'
        for gconf_key in (gvm_automount_drives, gvm_automount_media,
                          volumes_visible,
                          media_automount, media_automount_open):
            if self.gconf_previous[gconf_key] == '':
                gconftool.unset(gconf_key)
            elif self.gconf_previous[gconf_key] != 'false':
                gconftool.set(gconf_key, 'bool',
                              self.gconf_previous[gconf_key])

    def update_gui(self,progress,new_text=None):
        """Updates the GUI to show what we are working on"""
        self.progressbar.set_fraction(progress)
        if new_text != None:
            self.action.set_markup("<i>"+_(new_text)+"</i>")
        time.sleep(0.5)
        while gtk.events_pending():
            gtk.main_iteration()


    def create_dvd(self,widget):
        """Starts the DVD Creation Process"""
        success=True
        self.buttons.hide()
        self.progressbar.show()
        self.disable_volume_manager()
        self.action.set_text("Building DVD image")
        self.update_gui(0.0, "Preparing to build DVD")

        try:
            self.build_up(True)
        except:
            header = _("Could not build UP")
            body = _("Unable to build utility partition.")
            self.show_alert(gtk.MESSAGE_ERROR, header, body,
                parent=self.progress_dialog)
            success=False
        try:
            if success:
                self.build_iso(True)
        except:
            header = _("Could not build ISO")
            body = _("Unable to build ISO image.")
            self.show_alert(gtk.MESSAGE_ERROR, header, body,
                parent=self.progress_dialog)
            success=False

            #Cleanup if we need to
            if self._mounted:
                subprocess.call(['umount', DRIVE + RECOVERY_PARTITION])

        try:
            if success:
                self.burn_iso()
        except:
            header = _("Could not burn ISO")
            body = _("Unable to burn ISO image.")
            self.show_alert(gtk.MESSAGE_ERROR, header, body,
                parent=self.progress_dialog)
            success=False

        if success:
            header = _("Successfully Created DVD")
            body = _("If you would like to burn another copy,\
                      a copy is placed in your home directory.")
            self.show_alert(gtk.MESSAGE_INFO, header, body,
                parent=self.progress_dialog)
        self.enable_volume_manager()

    def destroy(self, widget, data=None):
        gtk.main_quit()
