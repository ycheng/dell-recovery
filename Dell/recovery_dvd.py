# -*- coding: utf-8 -*-
#
# «recovery_dvd» - Dell Recovery DVD Creator
#
# Copyright (C) 2008, Dell Inc.
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
import shutil
import tempfile
import atexit
import time
import string
import stat

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
#GLADEDIR = '/home/test/dell-recovery/Dell'

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
        if 'SUDO_UID' in os.environ:
            self.uid = os.environ['SUDO_UID']
        if 'SUDO_GID' in os.environ:
            self.gid = os.environ['SUDO_GID']
        if self.uid is not None:
            file=open('/etc/passwd','r').readlines()
            for line in file:
                if self.uid in line:
                    self.destination = string.split(line,':')[5]
                    break
        else:
            self.destination = os.getenv('HOME')
        self.destination = self.destination + '/' + ISO

        #make temporary directories
        self._tmpdir=tempfile.mkdtemp()
        self._mntdir=tempfile.mkdtemp()
        os.mkdir(self._tmpdir + '/up')
        #make sure they are cleaned up no matter what happens
        atexit.register(self.unmount_drives)

    def mount_drives(self):
        subprocess.call(['mount', DRIVE + RECOVERY_PARTITION , self._mntdir])

    def unmount_drives(self):
        subprocess.call(['umount', self._mntdir + '/.disk/casper-uuid-generic'])
        subprocess.call(['umount', self._mntdir + '/casper/initrd.gz'])
        subprocess.call(['umount', self._mntdir])
        subprocess.call(['umount', self._tmpdir])

    def build_up(self,gui=False):
        """Builds a Utility partition Image"""
        ##Create UP
        if gui is not False:
            self.update_progress_gui(0.003,_("Building Utility Partition"))
        #MBR
        subprocess.call(['dd','if=' + DRIVE,'bs=512','count=1','of=' + self._tmpdir + '/up/mbr.bin'])
        #UP Partition
        p1 = subprocess.Popen(['dd','if='+ DRIVE + UTILITY_PARTITION,'bs=1M'], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
        partition_file=open(self._tmpdir + '/up/' + 'upimg.bin', "w")
        partition_file.write(p2.communicate()[0])
        partition_file.close()
        if gui is not False:
            self.update_progress_gui(0.005,_("Building Utility Partition"))

        #Mount the RP & clean it up
        # - Removes pagefile.sys which may have joined us during FI
        # - Removes mbr.bin/upimg.bin which may exist if creating recovery disks from recovery disks
        # - Removes all .exe files since we don't do $stuff on windows
        if gui is not False:
            self.update_progress_gui(0.007,_("Preparing Recovery Partition"))
        self.mount_drives()
        for file in os.listdir(self._mntdir):
            if ".exe" in file or ".bin" in file or "pagefile.sys" in file:
                os.remove(self._mntdir + '/' + file)
        if gui is not False:
            self.update_progress_gui(0.008,_("Building Recovery Partition"))

    def regenerate_uuid(self,gui=False):
        """Regenerates the UUID used on the casper image"""
        if gui is not False:
            self.update_progress_gui(0.009,_("Regenerating UUIDs"))
        uuid_args = ['/usr/share/dell/bin/create-new-uuid',
                          self._mntdir + '/casper/initrd.gz',
                          self._tmpdir + '/',
                          self._tmpdir + '/']
        uuid = subprocess.Popen(uuid_args)
        retval = uuid.poll()
        while (retval is None):
            retval = uuid.poll()
        if retval is not 0:
            raise RuntimeError, _("create-new-uuid exited with a nonstandard return value.")

        #Loop mount these UUIDs so that they are included on the disk
        subprocess.call(['mount', '-o', 'ro' ,'--bind', self._tmpdir + '/initrd.gz', self._mntdir + '/casper/initrd.gz'])
        subprocess.call(['mount', '-o', 'ro', '--bind', self._tmpdir + '/casper-uuid-generic', self._mntdir + '/.disk/casper-uuid-generic'])

    def build_iso(self,gui=False):
        """Builds an ISO image"""
        if gui is not False:
            self.update_progress_gui(0.01,_("Building ISO image"))
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
            self._mntdir + '/',
            self._tmpdir + '/up/']
        p3 = subprocess.Popen(genisoargs,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        retval = p3.poll()
        while (retval is None):
            output = p3.stderr.readline()
            if ( output != "" ):
                progress = output.split()[0]
                if (progress[-1:] == '%'):
                    if gui is not False:
                        self.update_progress_gui(float(progress[:-1])/100,_("Building ISO Image"))
                    else:
                        print progress[:-1] + " % Done"
            retval = p3.poll()
        if retval is not 0:
            raise RuntimeError, _("genisoimage exited with a nonstandard return value.")
        #umount drive
        self.unmount_drives()

    def fix_permissions(self):
        """Makes the ISO readable by a normal user"""
        self.update_progress_gui(1.00,_("Adjusting Permissions"))
        if self.uid is not None and self.gid is not None:
            os.chown(self.destination,int(self.uid),int(self.gid))
        else:
            raise RuntimeError, _("Error adjusting permissions.")


    def burn_iso(self,gui=False):
        """Calls an external CD burning application to burn this ISO"""
        self.update_progress_gui(1.00,_("Opening DVD Burner"))
        if gui is not False:
            self.progress_dialog.hide()
            while gtk.events_pending():
                gtk.main_iteration()

        ret=subprocess.call(['nautilus-cd-burner', '--source-iso=' + self.destination])
        if ret != 0:
            raise RuntimeError, _("Nautilus CD Burner") + _("returned a nonstandard return code.")

    def burn_usb(self,gui=False):
        """Writes an ISO image to a flash drive and makes it bootable"""
        self.update_progress_gui(1.00,_("Opening USB Burner"))
        
        if gui is not False:
            self.progress_dialog.hide()
            while gtk.events_pending():
                gtk.main_iteration()

        ret=subprocess.call(['usb-creator', '--iso=' + self.destination, '-n'])
        if ret != 0:
            raise RuntimeError, _("Canonical USB Creator") + _("returned a nonstandard return code.")

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def run(self):
        self.wizard.show()
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

    def check_close(self,widget):
        """Asks the user before closing the dialog"""
        response = self.close_dialog.run()
        if response == gtk.RESPONSE_YES:
            self.destroy()
        else:
            self.close_dialog.hide()

    def remove_existing(self):
        """Asks the user about removing an old ISO"""
        response = self.existing_dialog.run()
        self.existing_dialog.hide()
        if response == gtk.RESPONSE_YES:
            return False
        else:
            return True

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

    def update_progress_gui(self,progress,new_text=None):
        """Updates the progressbar to show what we are working on"""
        self.progressbar.set_fraction(progress)
        if new_text != None:
            self.action.set_markup("<i>"+_(new_text)+"</i>")
        time.sleep(0.5)
        while gtk.events_pending():
            gtk.main_iteration()

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

            media_header=_("Media Type: ")

            #Fill in dynamic data
            if self.dvdbutton.get_active():
                type=self.dvdbutton.get_label()
            else:
                type=self.usbbutton.get_label()

            self.conf_text.set_text(media_header + type + "\n")
            self.wizard.set_page_complete(page,True)


    def create_dvd(self,widget):
        """Starts the DVD Creation Process"""

        #GUI Elements
        self.wizard.hide()
        self.progress_dialog.show()
        self.progress_dialog.connect('delete_event', self.ignore)
        self.action.set_text("Building Base image")
        self.update_progress_gui(0.0, _("Preparing to build base image"))

        #Check for existing image
        skip_creation=False
        if os.path.exists(self.destination):
            skip_creation=self.remove_existing()

        success=True

        if not skip_creation:
            self.disable_volume_manager()
            try:
                self.build_up(True)
            except Exception, inst:
                header = _("Could not build UP")
                self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
                success=False

            try:
                self.regenerate_uuid(True)
            except Exception, inst:
                header = _("Could not regenerate UUID")
                self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
                success=False

            try:
                if success:
                    self.build_iso(True)
            except Exception, inst:
                header = _("Could not build image")
                self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
                success=False

            try:
                if success:
                    self.fix_permissions()
            except Exception, inst:
                header = _("Could not adjust permissions")
                self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
                success=False
            self.enable_volume_manager() 
        
        try:
            if success:
                if self.dvdbutton.get_active():
                    self.burn_iso(True)
                else:
                    self.burn_usb(True)
        except Exception, inst:
            header = _("Could not write image")
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                parent=self.progress_dialog)
            success=False

        if success:
            header = _("Media Creation Complete")
            body = _("If you would like to create another copy, the generated image has been stored in your home directory under the filename: ") + self.destination
            self.show_alert(gtk.MESSAGE_INFO, header, body,
                parent=self.progress_dialog)
        self.destroy(None)

    def ignore(*args):
        """Ignores a signal"""
        return True

    def destroy(self, widget=None, data=None):
        gtk.main_quit()
        self.unmount_drives()
        os.removedirs(self._mntdir)
        os.removedirs(self._tmpdir)
