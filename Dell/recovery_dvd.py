#!/usr/bin/python
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
import dbus

import pygtk
pygtk.require("2.0")

import gtk
import gtk.glade

#Borrowed from Canonical/Ubiquity
import gconftool

#Translation Support
domain='dell-recovery'
import gettext
from gettext import gettext as _
LOCALEDIR='/usr/share/locale'

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

        #these directories may get used during creation
        self._mntdir=None
        self._tmpdir=None

        #make sure they are cleaned up no matter what happens
        atexit.register(self.unmount_drives)

    def check_preloaded_system(self):
        """Checks that the system this tool is being run on contains a
           utility partition and recovery partition"""
        bus = dbus.SystemBus()
        hal_obj = bus.get_object('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
        hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')

        up=False
        rp=False

        udis = hal.FindDeviceByCapability('volume')
        for udi in udis:
            dev_obj = bus.get_object('org.freedesktop.Hal', udi)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')

            property = dev.GetProperty('volume.label')

            if 'DellUtility' in property:
                up=True
            elif 'install' in property or 'OS' in property:
                rp=True

            if up and rp:
                return True
        return False

    def mount_drives(self):
        #only mount place if they really exist
        if self._mntdir is not None:
            subprocess.call(['mount', DRIVE + RECOVERY_PARTITION , self._mntdir])

    def unmount_drives(self):
        #only unmount places if they actually still exist
        if self._mntdir is not None:
            subprocess.call(['umount', self._mntdir + '/.disk/casper-uuid-generic'])
            subprocess.call(['umount', self._mntdir + '/casper/initrd.gz'])
            subprocess.call(['umount', self._mntdir])
            self.walk_cleanup(self._mntdir)
            os.rmdir(self._mntdir)
            self._mntdir=None

        if self._tmpdir is not None:
            subprocess.call(['umount', self._tmpdir])
            self.walk_cleanup(self._tmpdir)
            os.rmdir(self._tmpdir)
            self._tmpdir=None

    def walk_cleanup(self,directory):
        for root,dirs,files in os.walk(directory, topdown=False):
            for name in files:
                os.remove(os.path.join(root,name))
            for name in dirs:
                os.rmdir(os.path.join(root,name))

    def create_tempdirs(self):
        """Creates temporary directories to be used while building ISO"""
        #Temporary directories that will be useful
        self._tmpdir=tempfile.mkdtemp()
        os.mkdir(self._tmpdir + '/up')
        self._mntdir=tempfile.mkdtemp()

    def build_up(self,gui=False):
        """Builds a Utility partition Image"""

        #Mount the RP & clean it up
        # - Removes pagefile.sys which may have joined us during FI
        # - Removes all .exe files since we don't do $stuff on windows
        self.mount_drives()
        if gui is not False:
            self.update_progress_gui(0.003,_("Preparing Recovery Partition"))
        for file in os.listdir(self._mntdir):
            if ".exe" in file or ".sys" in file:
                os.remove(self._mntdir + '/' + file)

        ##Create UP only if it isn't already made (it can be from multiple recoveries)
        if not os.path.exists(self._mntdir + '/upimg.bin'):
            if gui is not False:
                self.update_progress_gui(0.005,_("Building Utility Partition"))
            p1 = subprocess.Popen(['dd','if='+ DRIVE + UTILITY_PARTITION,'bs=1M'], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
            partition_file=open(self._tmpdir + '/up/' + 'upimg.bin', "w")
            partition_file.write(p2.communicate()[0])
            partition_file.close()
            if gui is not False:
                self.update_progress_gui(0.007,_("Building Utility Partition"))

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

        #if we have ran this from a USB key, we might have syslinux which will
        #break our build
        if os.path.exists(self._mntdir + '/syslinux'):
            if os.path.exists(self._mntdir + '/isolinux'):
                #this means we might have been alternating between
                #recovery media formats too much
                self.walk_cleanup(self._mntdir + '/isolinux')
                os.rmdir(self._mntdir + '/isolinux')
            shutil.move(self._mntdir + '/syslinux', self._mntdir + '/isolinux')
        if os.path.exists(self._mntdir + '/isolinux/syslinux.cfg'):
            shutil.move(self._mntdir + '/isolinux/syslinux.cfg', self._mntdir + '/isolinux/isolinux.cfg')

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

    def burn(self,type,gui=False):
        """Calls an external application for burning this ISO"""
        if gui is not False:
            self.update_progress_gui(1.00,_("Opening Burner"))
            self.hide_progress()
        if type=="iso":
            ret=subprocess.call(['brasero', '-i', self.destination])
            err_str=_("Brasero")
        elif type=="usb":
            ret=subprocess.call(['usb-creator', '--iso=' + self.destination, '-n'])
            err_str=_("Canonical USB Creator")
        else:
            raise RuntimeError, _("Unknown image burn type.")
            return False
        if ret != 0:
            if type=="iso":
                raise RuntimeError, err_str +" " + _("returned a nonstandard return code.")
            return False
        return True

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
        self.update_progress_gui(0.0, _("Preparing to build base image"))

        #Full process for creating an image
        success=True
        if not skip_creation:

            try:
                self.create_tempdirs()
            except Exception, inst:
                header = _("Couldn't create temp directories")
                self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
                success=False
            try:
                if success:
                    self.build_up(True)
            except Exception, inst:
                header = _("Could not build UP")
                self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.progress_dialog)
                success=False

            try:
                if success:
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

        #After ISO creation is done, we fork out to other more
        #intelligent applications for doing lowlevel writing etc
        if success:
            success=False
            while not success:
                try:
                    if self.dvdbutton.get_active():
                        success=self.burn("iso",True)
                    else:
                        success=self.burn("usb",True)
                except Exception, inst:
                    success=False
                if not success:
                    success=self.show_question(self.retry_dialog)

        if success:
            header = _("Recovery Media Creation Process Complete")
            body = _("If you would like to archive another copy, the generated image has been stored in your home directory under the filename:") + ' ' + self.destination
            self.show_alert(gtk.MESSAGE_INFO, header, body,
                parent=self.progress_dialog)
        self.destroy(None)

    def ignore(*args):
        """Ignores a signal"""
        return True

    def destroy(self, widget=None, data=None):
        gtk.main_quit()
        self.unmount_drives()
