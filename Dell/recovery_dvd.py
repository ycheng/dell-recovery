# -*- coding: utf-8 -*-
#
# «recovery_dvd» - Dell Recovery DVD Creator
#
# This script:
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
import gconftool

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
        self.destination = os.getenv('HOME') + '/' + ISO
        self.progress_dialog.show()

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

        self.thunar_previous = self.thunar_set_volmanrc(
            {'AutomountDrives': 'FALSE', 'AutomountMedia': 'FALSE'})

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
        self.disable_volume_manager()
        self.action.set_text("")
        self.update_gui(0.0, "Preparing to build DVD")

        try:
            self.build_up(True)
        except:
            header = _("Could not build UP")
            body = _("Unable to build utility partition.")
            self.show_alert(gtk.MESSAGE_ERROR, header, body, msg,
                parent=self.progress_dialog)
            success=False
        try:
            if success:
                self.build_iso(True)
        except:
            header = _("Could not build ISO")
            body = _("Unable to build ISO image.")
            self.show_alert(gtk.MESSAGE_ERROR, header, body, msg,
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
            self.show_alert(gtk.MESSAGE_ERROR, header, body, msg,
                parent=self.progress_dialog)
            success=False

        self.enable_volume_manager()


    def build up(self,gui=False):
        """Builds a Utility partition Image"""

        ##Create UP
        if gui is not False:
            self.update_gui(0.01,"Building Utility Partition")
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
            self.update_gui(0.02,"Building Utility Partition")

        #Mount the RP & clean it up
        if gui is not False:
            self.update_gui(0.02,"Preparing Recovery Partition")
        self._mntdir=tempfile.mkdtemp()
        subprocess.call(['mount', DRIVE + RECOVERY_PARTITION , self._mntdir])
        for file in os.listdir(self._mntdir):
            if ".exe" in file or "pagefile.sys" in file:
                os.remove(self._mntdir + '/' + file)
        if gui is not False:
            self.update_gui(0.03,"Building Recovery Partition")

    def build_iso(self,gui=False):
        """Builds an ISO image"""
        if gui is not False:
            self.update_gui(0.04,"Building ISO image")
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
        self._mounted=True
        retval = p3.poll()
        while (retval is None):
            output = p3.stderr.readline()
            if ( output != "" ):
                progress = output.split()[0]
                if (progress[-1:] == '%'):
                    if gui is not False:
                        self.update_gui(float(progress[:-1])/10,"Building ISO Image")
                    else:
                        print progress[:-1] + " % Done"

        subprocess.call(['umount', DRIVE + RECOVERY_PARTITION])
        self._mounted=False

    def burn_iso(self):
        """Calls an external CD burning application to burn this ISO"""
        subprocess.call(['nautilus-cd-burner', '--source-iso=' + self.destination])

    def destroy(self, widget, data=None):
        #Cleanup if we need to
        if self._mounted:
            subprocess.call(['umount', DRIVE + RECOVERY_PARTITION])
        self.enable_volume_manager()
        gtk.main_quit()
