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
import vte

from Dell.recovery_backend import UnknownHandlerException, PermissionDeniedByPolicy, BackendCrashError, dbus_sync_call_signal_wrapper, Backend, DBUS_BUS_NAME

#Translation Support
domain='dell-recovery'
import gettext
from gettext import gettext as _
LOCALEDIR='/usr/share/locale'

#Glade directory
if os.path.isdir('gtk') and 'DEBUG' in os.environ:
    UIDIR= 'gtk'
else:
    UIDIR = '/usr/share/dell'


#Supported burners and their arguments
cd_burners = { 'brasero':['-i'],
               'nautilus-cd-burner':['--source-iso='] }
usb_burners = { 'usb-creator':['-n','--iso'],
                'usb-creator-gtk':['-n','--iso'],
                'usb-creator-kde':['-n','--iso'] }

git_trees = { 'ubuntu': 'http://humbolt.us.dell.com/pub/Applications/git-internal-projects/ubuntu-fid.git',
            }

class Frontend:
    def __init__(self,up,rp,version,media,target,overwrite,builder):

        #setup locales
        gettext.bindtextdomain(domain, LOCALEDIR)
        gettext.textdomain(domain)
        self.widgets = gtk.Builder()
        self.widgets.add_from_file(os.path.join(UIDIR,'recovery_media_creator.ui'))
        gtk.window_set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.png')

        self.widgets.set_translation_domain(domain)
        for widget in self.widgets.get_objects():
            if isinstance(widget, gtk.Label):
                widget.set_property('can-focus', False)
                widget.set_text(_(widget.get_text()))
            elif isinstance(widget, gtk.RadioButton):
                widget.set_label(_(widget.get_label()))
            elif isinstance(widget, gtk.Window):
                title = widget.get_title()
                if title:
                    widget.set_title(_(widget.get_title()))
        self.widgets.connect_signals(self)

        if builder:
            self.builder_init()

        self._dbus_iface = None

        self.timeout = 0

        self.check_burners()

        process=subprocess.Popen(['lsb_release','-r', '-s'], stdout=subprocess.PIPE)
        self.release=process.communicate()[0].strip('\n')
        process=subprocess.Popen(['lsb_release','-i', '-s'], stdout=subprocess.PIPE)
        self.distributor=process.communicate()[0].lower().strip('\n')

        #set any command line arguments
        self.up=up
        self.rp=rp
        self.widgets.get_object('version').set_text(version)
        self.media=media
        self.target=target
        self.overwrite=overwrite
        self.builder=builder

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

        #check any command line arguments
        if self.up is not None and not os.path.exists(self.up):
            header=_("Invalid utility partition") + _(" in command line arguments.  Falling back to DeviceKit or HAL based detection.")
            inst = None
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                parent=self.widgets.get_object('progress_dialog'))
            self.up=None
        if self.rp is not None and not os.path.exists(self.rp):
            header=_("Invalid recovery partition") + _(" in command line arguments.  Falling back to DeviceKit or HAL based detection.")
            inst = None
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                parent=self.widgets.get_object('progress_dialog'))
            self.rp=None
        if self.up is not None and self.rp is not None:
            return True

        try:
            #first try to use devkit-disks. if this fails, then we can fall back to hal
            dk_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', '/org/freedesktop/DeviceKit/Disks')
            dk = dbus.Interface(dk_obj, 'org.freedesktop.DeviceKit.Disks')
            devices = dk.EnumerateDevices()
            for device in devices:
                dev_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', device)
                dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

                label = dev.Get('org.freedesktop.DeviceKit.Disks.Device','id-label')
                fs = dev.Get('org.freedesktop.DeviceKit.Disks.Device','id-type')

                if not self.up and 'DellUtility' in label:
                    self.up=dev.Get('org.freedesktop.DeviceKit.Disks.Device','device-file')
                elif not self.rp and ('install' in label or 'OS' in label) and 'vfat' in fs:
                    self.rp=dev.Get('org.freedesktop.DeviceKit.Disks.Device','device-file')

                if self.up and self.rp:
                    return True

        except dbus.DBusException, e:
            print "Falling back to HAL"
            hal_obj = bus.get_object('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
            hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')
            devices = hal.FindDeviceByCapability('volume')

            for device in devices:
                dev_obj = bus.get_object('org.freedesktop.Hal', device)
                dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')

                label = dev.GetProperty('volume.label')
                fs = dev.GetProperty('volume.fstype')

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
        if os.path.exists(os.path.join(os.environ['HOME'], 'Downloads', self.iso)) and not self.overwrite:
            skip_creation=self.show_question(self.widgets.get_object('existing_dialog'))

        #GUI Elements
        self.widgets.get_object('wizard').hide()

        #Call our DBUS backend to build the ISO
        if not skip_creation:
            self.widgets.get_object('progress_dialog').connect('delete_event', self.ignore)
            self.widgets.get_object('action').set_text("Building Base image")
            #try to open the file as a user first so when it's overwritten, it
            #will be with the correct permissions
            try:
                file=open(os.path.join(os.environ['HOME'], 'Downloads', self.iso),'w')
                file.close()
            except IOError:
                #this might have been somwehere that the system doesn't want us
                #writing files as a user, oh well, we tried
                pass
            try:
                dbus_sync_call_signal_wrapper(self.backend(),
                    'create_' + self.distributor,
                    {'report_progress':self.update_progress_gui},
                    self.up,
                    self.rp,
                    self.widgets.get_object('version').get_text(),
                    os.path.join(os.environ['HOME'], 'Downloads',self.iso))
            except dbus.DBusException, e:
                if e._dbus_error_name == PermissionDeniedByPolicy._dbus_error_name:
                    header = _("Permission Denied")
                else:
                    header = str(e)
                self.show_alert(gtk.MESSAGE_ERROR, header,
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
                cmd=self.cd_burn_cmd + [os.path.join(os.environ['HOME'], 'Downloads', self.iso)]
            elif self.widgets.get_object('usbbutton').get_active():
                cmd=self.usb_burn_cmd + [os.path.join(os.environ['HOME'], 'Downloads', self.iso)]
            else:
                cmd=None
            if cmd:
                subprocess.call(cmd)

        header = _("Recovery Media Creation Process Complete")
        body = _("If you would like to archive another copy, the generated image has been stored under the filename:\n") + os.path.join(os.environ['HOME'], 'Downloads', self.iso)
        self.show_alert(gtk.MESSAGE_INFO, header, body,
            parent=self.widgets.get_object('progress_dialog'))

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
                        parent=self.widgets.get_object('progress_dialog'))
                    self.destroy(None)
                    sys.exit(1)
                else:
                    raise

        return self._dbus_iface

#### Builder specific ####
    def builder_init(self):
        """Inserts builder widgets into the Gtk.Assistant"""
        self.builder_widgets=gtk.Builder()
        self.builder_widgets.add_from_file(os.path.join(UIDIR,'builder.ui'))

        wizard = self.widgets.get_object('wizard')
        #wizard.resize(400,400)
        wizard.set_title(wizard.get_title() + _(" (BTO Image Builder Mode)"))

        self.widgets.get_object('start_page').set_text(_("This application will integrate a Dell \
OEM FID framework & FISH package set into a customized \
OS media image.  You will have the option to \
create an USB key or DVD image."))

        self.file_dialog = gtk.FileChooserDialog("Choose Base BTO Image",
                                           None,
                                           gtk.FILE_CHOOSER_ACTION_OPEN,
                                           (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                            gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        self.file_dialog.set_default_response(gtk.RESPONSE_OK)

        #Set up the VTE window for GIT stuff
        self.builder_widgets.get_object('builder_vte_window').set_transient_for(
            self.widgets.get_object('wizard'))
        self.vte = vte.Terminal()
        self.builder_widgets.get_object('builder_vte_vbox').add(self.vte)
        self.vte.show()
        self.vte.connect("child-exited", self.builder_fid_vte_handler)

        #insert builder pages
        wizard.insert_page(self.builder_widgets.get_object('fish_page'),1)
        wizard.insert_page(self.builder_widgets.get_object('fid_page'),1)
        wizard.insert_page(self.builder_widgets.get_object('base_page'),1)

        #improve the summary
        self.widgets.get_object('version_hbox').show()

        self.builder_widgets.connect_signals(self)

    def builder_file_dialog(self, filter_name, filter_types):
        """Browses all files under a particular filter
           filter_name is a str
           filter_types is a an array of str"""
        filter = gtk.FileFilter()
        filter.set_name(filter_name)
        for type in filter_types:
            filter.add_pattern(type)
        self.file_dialog.add_filter(filter)
        response = self.file_dialog.run()
        self.file_dialog.hide()
        self.file_dialog.remove_filter(filter)
        if response == gtk.RESPONSE_OK:
            return self.file_dialog.get_filename() 
        else:
            return None

    def builder_base_toggled(self,widget):
        """Called when the radio button for the Builder base image page is changed"""
        base_browse_button=self.builder_widgets.get_object('base_browse_button')
        base_page = self.builder_widgets.get_object('base_page')
        wizard = self.widgets.get_object('wizard')
        label = self.builder_widgets.get_object('base_image_details_label')

        label.set_markup("")
        base_browse_button.set_sensitive(True)
        wizard.set_page_complete(base_page,False)
        
        if self.builder_widgets.get_object('iso_image_radio').get_active():
            self.file_dialog.set_action(gtk.FILE_CHOOSER_ACTION_OPEN)
        elif self.builder_widgets.get_object('directory_radio').get_active():
            self.file_dialog.set_action(gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER)
        else:
            base_browse_button.set_sensitive(False)
            self.builder_base_file_chooser_picked()

    def builder_base_file_chooser_picked(self,widget=None):
        """Called when a file is selected on the base page"""
        
        base_page = self.builder_widgets.get_object('base_page')
        wizard = self.widgets.get_object('wizard')

        wizard.set_page_complete(base_page,False)

        if widget == self.builder_widgets.get_object('base_browse_button'):
            ret=self.builder_file_dialog("Base Images",["*.iso"])
            if ret is not None:
                (self.bto_base, distributor, release, output_text) = self.backend().query_iso_information(ret)
                self.builder_base_image=ret
                wizard.set_page_complete(base_page,True)
            else:
                self.bto_base=False
                output_text=""
                distributor=''
                release=''
        else:
            (self.bto_base, distributor, release, output_text) = self.backend().query_iso_information(self.rp)
            self.builder_base_image=self.rp
            wizard.set_page_complete(base_page,True)

        #If this is a BTO image, then allow using built in framework
        if not self.bto_base and self.builder_widgets.get_object('builtin_radio').get_active():
            self.builder_widgets.get_object('git_radio').set_active(True)
        self.builder_widgets.get_object('builtin_hbox').set_sensitive(self.bto_base)

        if distributor:
            self.distributor=distributor
        if release:
            self.release=release

        self.builder_widgets.get_object('base_image_details_label').set_markup(output_text)

    def builder_fid_toggled(self,widget):
        """Called when the radio button for the Builder FID overlay page is changed"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        fid_browse_button = self.builder_widgets.get_object('fid_browse_button')
        file_chooser = self.builder_widgets.get_object('fid_file_chooser')
        git_tree_hbox = self.builder_widgets.get_object('fid_git_tree_hbox')
        git_tag_hbox = self.builder_widgets.get_object('fid_git_tag_hbox')
        label = self.builder_widgets.get_object('fid_overlay_details_label')

        label.set_markup("")
        wizard.set_page_complete(fid_page,False)
        fid_browse_button.set_sensitive(False)
        git_tree_hbox.set_sensitive(False)
        git_tag_hbox.set_sensitive(False)

        if self.builder_widgets.get_object('builtin_radio').get_active():
            wizard.set_page_complete(fid_page,True)
            label.set_markup("<b>Builtin</b>: BTO Image")
            self.builder_fid_overlay=None
            
        elif self.builder_widgets.get_object('git_radio').get_active():
            git_tree_hbox.set_sensitive(True)

        elif self.builder_widgets.get_object('folder_radio').get_active():
            fid_browse_button.set_sensitive(True)
            self.file_dialog.set_action(gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER)
            
        elif self.builder_widgets.get_object('tgz_radio').get_active():
            fid_browse_button.set_sensitive(True)
            self.file_dialog.set_action(gtk.FILE_CHOOSER_ACTION_OPEN)

    def builder_fid_file_chooser_picked(self,widget):
        """Called when the button to choose a tgz or folder is clicked"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        label = self.builder_widgets.get_object('fid_overlay_details_label')

        if widget == self.builder_widgets.get_object('fid_browse_button'):
            ret=self.builder_file_dialog("FID Overlays",["*.tar.gz","*.tgz"])
            if ret is not None:
                wizard.set_page_complete(fid_page,True)
                output_text=_("<b>Local</b>: %s" % ret)
                self.builder_fid_overlay=ret
            else:
                output_text=''
                wizard.set_page_complete(fid_page,False)
            label.set_markup(output_text)

    def builder_fid_fetch_button_clicked(self,widget):
        """Called when the button to test a git tree is clicked"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        label=self.builder_widgets.get_object('fid_overlay_details_label')
        vte_close=self.builder_widgets.get_object('builder_vte_close')

        if not os.path.exists('/usr/bin/git'):
            output_text=_("<b>ERROR</b>: git is not installed")
            wizard.set_page_complete(fid_page,False)
        else:
            output_text=''
            vte_close.set_sensitive(False)
            if not os.path.exists(os.path.join(os.environ['HOME'],'.config','dell-recovery')):
                os.makedirs(os.path.join(os.environ['HOME'],'.config','dell-recovery'))
            if not os.path.exists(os.path.join(os.environ['HOME'],'.config','dell-recovery',self.distributor + '-fid')):
                command=["git", "clone", self.builder_widgets.get_object('git_url').get_text(),
                         os.path.join(os.environ["HOME"],'.config','dell-recovery',self.distributor + '-fid')]
                cwd=os.path.join(os.environ["HOME"],'.config','dell-recovery')
            else:
                command=["git", "fetch", "--verbose"]
                cwd=os.path.join(os.environ["HOME"],'.config','dell-recovery',self.distributor + '-fid')
            self.widgets.get_object('wizard').set_sensitive(False)
            self.builder_widgets.get_object('builder_vte_window').show()
            self.vte.fork_command(command=command[0],argv=command,directory=cwd)
        label.set_markup(output_text)

    def builder_fid_vte_handler(self,widget):
        """Handler for VTE dialog closing"""
        def fill_liststore_from_command(command, liststore_name):
            """Fills up the data in a liststore"""
            liststore=self.builder_widgets.get_object(liststore_name)
            liststore.clear()
            cwd=os.path.join(os.environ["HOME"],'.config','dell-recovery',self.distributor + '-fid')
            list_command=subprocess.Popen(args=command,cwd=cwd,stdout=subprocess.PIPE)
            output=list_command.communicate()[0].split('\n')
            for item in output:
                if item and not "HEAD" in item:
                    liststore.append([item])
            #Add this so that we can build w/o a tag
            liststore.append(['origin/master'])

        if widget == self.builder_widgets.get_object('builder_vte_close'):
            #reactivate GUI
            self.builder_widgets.get_object('builder_vte_window').hide()
            self.widgets.get_object('wizard').set_sensitive(True)
            self.builder_widgets.get_object('fid_git_tag_hbox').set_sensitive(True)

            #update the tag list in the GUI
            command=["git","tag","-l"]
            fill_liststore_from_command(command,'tag_liststore')

        else:
            self.builder_widgets.get_object('builder_vte_close').set_sensitive(True)

    def builder_fid_git_changed(self,widget):
        """If we have selected a tag"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')

        active_iter=self.builder_widgets.get_object('git_tags').get_active_iter()
        active_tag=''
        output_text=''
        if active_iter:
            active_tag=self.builder_widgets.get_object('tag_liststore').get_value(
                active_iter,0)

        if active_tag:
            cwd=os.path.join(os.environ["HOME"],'.config','dell-recovery',self.distributor + '-fid')
            #switch checkout branches
            command=["git","checkout",active_tag.strip()]
            subprocess.call(command,cwd=cwd)

            self.builder_fid_overlay=os.path.join(cwd,'framework')

            #if we don't have a tag set, set one
            if not self.widgets.get_object('version').get_text():
                tag=active_tag.strip().split('_')
                if len(tag) > 1:
                    self.widgets.get_object('version').set_text(tag[1])
                else:
                    self.widgets.get_object('version').set_text('X00')

            output_text = "<b>GIT Tree</b>, Version: %s" % active_tag
            wizard.set_page_complete(fid_page,True)
        else:
            wizard.set_page_complete(fid_page,False)
        self.builder_widgets.get_object('fid_overlay_details_label').set_markup(output_text)

    def builder_fish_action(self,widget):
        """Called when the add or remove buttons are pressed on the fish action page"""
        add_button = self.builder_widgets.get_object('fish_add')
        remove_button = self.builder_widgets.get_object('fish_remove')
        fish_treeview = self.builder_widgets.get_object('fish_treeview')
        model = fish_treeview.get_model()
        if widget == add_button:
            ret=self.builder_file_dialog(_("Dell FISH Packages"),["*.tar.gz","*.tgz"])
            if ret is not None:
                model.append([ret])
        elif widget == remove_button:
            item = fish_treeview.get_selection()
            model.remove(model.get_iter(item.get_selected_rows()[1][0]))

    def build_builder_page(self,widget,page):
        """Processes output that should be done on a builder page"""
        wizard = self.widgets.get_object('wizard')
        if page == self.builder_widgets.get_object('base_page'):
            if self.rp:
                self.builder_widgets.get_object('recovery_hbox').set_sensitive(True)
            wizard.set_page_title(page,_("Choose Base OS Image"))
            
        elif page == self.builder_widgets.get_object('fid_page'):
            wizard.set_page_title(page,_("Choose FID Overlay"))
            for operating_system in git_trees:
                if operating_system == self.distributor:
                    self.builder_widgets.get_object('git_url').set_text(git_trees[operating_system])
            self.builder_fid_toggled(None)

        elif page == self.builder_widgets.get_object('fish_page'):
            wizard.set_page_title(page,_("Choose FISH Packages"))
            self.file_dialog.set_action(gtk.FILE_CHOOSER_ACTION_OPEN)
            wizard.set_page_complete(page,True)
            
        elif page == self.widgets.get_object('conf_page') or \
             widget == self.widgets.get_object('version'):

            if page:
                wizard.set_page_title(page,_("Builder Summary"))
            output_text = "<b>Base Image Distributor</b>: " + self.distributor + '\n'
            output_text+= "<b>Base Image Release</b>: " + self.release + '\n'
            if self.bto_base:
                output_text+= "<b>BTO Base Image</b>: " + self.builder_base_image + '\n'
            else:
                output_text+= "<b>Base Image</b>: " + self.builder_base_image + '\n'
            if self.builder_fid_overlay:
                output_text+= "<b>FID Overlay</b>: " + self.builder_fid_overlay + '\n'

            model = self.builder_widgets.get_object('fish_liststore')
            iterator = model.get_iter_first()
            if iterator is not None:
                output_text += "<b>FISH Packages</b>:\n"
            while iterator is not None:
                output_text+= "\t" + model.get_value(iterator,0) + '\n'
                iterator = model.iter_next(iterator)

            output_text+= self.widgets.get_object('conf_text').get_label()

            self.widgets.get_object('conf_text').set_markup(output_text)

#### GUI Functions ###
# This application is functional via command line by using the above functions #

    def run(self):
        if self.check_preloaded_system() or self.builder:
            self.widgets.get_object('wizard').show()
        else:
            header=_("This tool requires that a Utility Partition and Linux Recovery partition are present to function.")
            inst = None
            self.show_alert(gtk.MESSAGE_ERROR, header, inst,
                    parent=self.widgets.get_object('progress_dialog'))
            return

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

    def check_close(self,widget):
        """Asks the user before closing the dialog"""
        response = self.widgets.get_object('close_dialog').run()
        if response == gtk.RESPONSE_YES:
            self.destroy()
        else:
            self.widgets.get_object('close_dialog').hide()

    def show_question(self,dialog):
        """Presents the user with a question"""
        response = dialog.run()
        dialog.hide()
        if response == gtk.RESPONSE_YES:
            return False
        return True

    def update_progress_gui(self,progress_text,progress):
        """Updates the progressbar to show what we are working on"""
        self.widgets.get_object('progress_dialog').show()
        self.widgets.get_object('progressbar').set_fraction(float(progress)/100)
        if progress_text != None:
            self.widgets.get_object('action').set_markup("<i>"+_(progress_text)+"</i>")
        while gtk.events_pending():
            gtk.main_iteration()
        return True

    def build_page(self,widget,page=None):
        """Prepares our GTK assistant"""

        if page == self.widgets.get_object('start_page'):
            self.widgets.get_object('wizard').set_page_title(page,_("Welcome"))

            self.widgets.get_object('wizard').set_page_complete(page,True)
        elif page == self.widgets.get_object('media_type_page'):
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
                self.widgets.get_object('dvdbutton').set_sensitive(False)
                self.widgets.get_object('usbbutton').set_active(True)
            if self.usb_burn_cmd is None:
                self.widgets.get_object('usbbutton').set_sensitive(False)
                if self.cd_burn_cmd is None:
                    self.widgets.get_object('nomediabutton').set_active(True)

            self.widgets.get_object('wizard').set_page_complete(page,True)

        elif page == self.widgets.get_object('conf_page') or \
                     widget == self.widgets.get_object('version'):

            #Fill in dynamic data
            if not self.widgets.get_object('version').get_text():
                self.widgets.get_object('version').set_text(self.backend().query_bto_version(self.rp))
            self.iso = self.distributor + '-' + self.release + '-dell_' + self.widgets.get_object('version').get_text() + ".iso"

            if self.widgets.get_object('dvdbutton').get_active():
                type=self.widgets.get_object('dvdbutton').get_label()
            elif self.widgets.get_object('usbbutton').get_active():
                type=self.widgets.get_object('usbbutton').get_label()
            else:
                type="<b>" + _("ISO Image") + '</b>'
            text = ''
            if self.up:
                text+="<b>" + _("Utility Partition: ") + '</b>' + self.up + '\n'
            if self.rp:
                text+="<b>" + _("Recovery Partition: ") + '</b>' + self.rp + '\n'
            text+="<b>" + _("Media Type: ") + '</b>' + type + '\n'
            text+="<b>" + _("File Name: ") + '</b>' + os.path.join(os.environ['HOME'], 'Downloads', self.iso) + '\n'

            self.widgets.get_object('conf_text').set_markup(text)

            if page:
                self.widgets.get_object('wizard').set_page_title(page,_("Confirm Selections"))
                self.widgets.get_object('wizard').set_page_complete(page,True)

        if self.builder:
            self.build_builder_page(widget,page)

    def ignore(*args):
        """Ignores a signal"""
        return True

    def destroy(self, widget=None, data=None):
        gtk.main_quit()
