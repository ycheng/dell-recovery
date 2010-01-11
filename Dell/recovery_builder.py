#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «recovery_builder» - Dell Recovery DVD Creator
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
import sys
import gtk
import subprocess
from Dell.recovery_frontend import GTKFrontend
from Dell.recovery_common import *

try:
    from aptdaemon import client
    from aptdaemon.enums import *
    from aptdaemon.gtkwidgets import (AptErrorDialog,
                                      AptProgressDialog,
                                      AptMessageDialog)
except ImportError:
    pass

#Translation support
import gettext
from gettext import gettext as _

class GTKBuilderFrontend(GTKFrontend):

    def __init__(self,up,rp,version,media,target,overwrite,xrev,branch):
        """Inserts builder widgets into the Gtk.Assistant"""
        try:
            import vte
        except ImportError:
            header = _("python-vte is missing")
            body = _("Builder mode requires python-vte to function")
            self.show_alert(gtk.MESSAGE_ERROR, header, body,
                parent=None)
            sys.exit(1)

        #Run the normal init first
        GTKFrontend.__init__(self,up,rp,version,media,target,overwrite,xrev,branch)

        #Build our extra GUI in
        self.builder_widgets=gtk.Builder()
        self.builder_widgets.add_from_file(os.path.join(UIDIR,'builder.ui'))
        self.builder_widgets.connect_signals(self)

        wizard = self.widgets.get_object('wizard')
        #wizard.resize(400,400)
        wizard.set_title(wizard.get_title() + _(" (BTO Image Builder Mode)"))

        self.widgets.get_object('start_page').set_text(_("This application will integrate a Dell \
OEM FID framework & FISH package set into a customized \
OS media image.  You will have the option to \
create an USB key or DVD image."))

        self.file_dialog = gtk.FileChooserDialog("Choose Item",
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

        #builder variable defaults
        self.builder_fid_overlay=''
        self.builder_base_image=''
        self.bto_base=False

        self.builder_widgets.connect_signals(self)

    def build_page(self,widget,page=None):
        """Processes output that should be done on a builder page"""
        #Do the normal processing first
        GTKFrontend.build_page(self,widget,page)

        wizard = self.widgets.get_object('wizard')
        if page == self.builder_widgets.get_object('base_page'):
            if self.rp:
                self.builder_widgets.get_object('recovery_hbox').set_sensitive(True)
            filter = gtk.FileFilter()
            filter.add_pattern("*.iso")
            self.file_dialog.set_filter(filter)
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
            filter = gtk.FileFilter()
            filter.add_pattern("*.tgz")
            filter.add_pattern("*.tar.gz")
            filter.add_pattern("*.deb")
            filter.add_pattern("*.pdf")
            filter.add_pattern("*.py")
            filter.add_pattern("*.sh")

            self.file_dialog.set_filter(filter)
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

    def wizard_complete(self, widget):
        """Finished answering wizard questions, and can continue process"""
        #update gui
        self.widgets.get_object('action').set_text('Assembling Image Components')

        #build fish list
        fish_list=[]
        model = self.builder_widgets.get_object('fish_liststore')
        iterator = model.get_iter_first()
        while iterator is not None:
            fish_list.append(model.get_value(iterator,0))
            iterator = model.iter_next(iterator)
        function='assemble_image'
        args = (self.builder_base_image,
                self.builder_fid_overlay,
                fish_list,
                'create_' + self.distributor,
                self.up,
                self.widgets.get_object('version').get_text(),
                os.path.join(self.path,self.iso))

        GTKFrontend.wizard_complete(self,widget,function, args)

    def builder_file_dialog(self):
        """Browses all files under a particular filter"""
        response = self.file_dialog.run()
        self.file_dialog.hide()
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

        bto_version=''
        output_text=''
        distributor=''
        release=''
        if widget == self.builder_widgets.get_object('base_browse_button'):
            ret=self.builder_file_dialog()
            if ret is not None:
                (bto_version, distributor, release, output_text) = self.backend().query_iso_information(ret)
                self.bto_base=not not bto_version
                self.builder_base_image=ret
                wizard.set_page_complete(base_page,True)
        else:
            (bto_version, distributor, release, output_text) = self.backend().query_iso_information(self.rp)
            self.bto_base=not not bto_version
            self.builder_base_image=self.rp
            wizard.set_page_complete(base_page,True)

        if not bto_version:
            bto_version='X00'
        #set the version string that we fetched from the image
        self.widgets.get_object('version').set_text(bto_version)

        if distributor:
            self.distributor=distributor
        if release:
            self.release=release

        #If this is a BTO image, then allow using built in framework
        if output_text and \
           not self.bto_base and \
           self.builder_widgets.get_object('builtin_radio').get_active():
            self.builder_widgets.get_object('git_radio').set_active(True)
        self.builder_widgets.get_object('builtin_hbox').set_sensitive(self.bto_base)

        self.builder_widgets.get_object('base_image_details_label').set_markup(output_text)

    def builder_fid_toggled(self,widget):
        """Called when the radio button for the Builder FID overlay page is changed"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        git_tree_hbox = self.builder_widgets.get_object('fid_git_tree_hbox')
        label = self.builder_widgets.get_object('fid_overlay_details_label')

        label.set_markup("")
        wizard.set_page_complete(fid_page,False)
        git_tree_hbox.set_sensitive(False)

        if self.builder_widgets.get_object('builtin_radio').get_active():
            wizard.set_page_complete(fid_page,True)
            label.set_markup("<b>Builtin</b>: BTO Image")
            self.builder_fid_overlay=''
            
        elif self.builder_widgets.get_object('git_radio').get_active():
            git_tree_hbox.set_sensitive(True)
            cwd=os.path.join(os.environ["HOME"],'.config','dell-recovery',self.distributor + '-fid')
            if os.path.exists(cwd):
                self.builder_fid_vte_handler(self.builder_widgets.get_object('git_radio'))

    def builder_fid_fetch_button_clicked(self,widget):
        """Called when the button to test a git tree is clicked"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        label=self.builder_widgets.get_object('fid_overlay_details_label')
        vte_close=self.builder_widgets.get_object('builder_vte_close')

        if not os.path.exists('/usr/bin/git'):
            output_text=_("<b>ERROR</b>: git is not installed")
            if not self.ac:
                try:
                    self.ac = client.AptClient()
                    self.builder_widgets.get_object('install_git_button').show()
                except NameError:
                    pass
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
        def fill_liststore_from_command(command, filter, liststore_name):
            """Fills up the data in a liststore, only items matching filter"""
            liststore=self.builder_widgets.get_object(liststore_name)
            liststore.clear()
            cwd=os.path.join(os.environ["HOME"],'.config','dell-recovery',self.distributor + '-fid')
            if not os.path.exists(cwd):
                return
            list_command=subprocess.Popen(args=command,cwd=cwd,stdout=subprocess.PIPE)
            output=list_command.communicate()[0].split('\n')
            #go through the list once to see if we have A rev tags at all
            use_xrev=self.xrev
            if not use_xrev:
                use_xrev=True
                for item in output:
                    if filter + "_A" in item:
                        use_xrev=False
                        break
            for item in output:
                #Check that we have a valid item
                # AND
                #It doesn't contain HEAD
                # AND
                # [ We are in branch mode
                #   OR
                #   [ 
                #     It contains our filter
                #     We show X rev builds
                #     It contains an X rev tag
                #   ]
                # ]

                if item and \
                   not "HEAD" in item and \
                   (self.branch or \
                   (filter in item and \
                    (use_xrev or \
                     not filter + "_X" in item))):
                    liststore.append([item])

            #Add this so that we can build w/o a tag only if we are in tag mode w/ dev on
            if use_xrev and not self.branch:
                liststore.append(['origin/master'])

        #Git radio was toggled OR
        #Close was pressed on the GUI
        if widget == self.builder_widgets.get_object('git_radio') or \
           widget == self.builder_widgets.get_object('builder_vte_close'):
            #reactivate GUI
            self.builder_widgets.get_object('builder_vte_window').hide()
            self.widgets.get_object('wizard').set_sensitive(True)
            self.builder_widgets.get_object('fid_git_tag_hbox').set_sensitive(True)

            #update the tag list in the GUI
            if self.branch:
                command=["git", "branch", "-r"]
            else:
                command=["git","tag","-l"]
            fill_liststore_from_command(command,self.release,'tag_liststore')
        #the vte command exited
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
            ret=self.builder_file_dialog()
            if ret is not None:
                model.append([ret])
        elif widget == remove_button:
            row = fish_treeview.get_selection().get_selected_rows()[1]
            if len(row) > 0:
                model.remove(model.get_iter(row[0]))


    def install_git(self,widget):
        """Launch into an installer for git"""
        widget.hide()
        t = self.ac.install_packages(['git-core'],
                                    wait=False,
                                    reply_handler=None,
                                    error_handler=None)
        wizard=self.widgets.get_object('wizard')
        dialog = AptProgressDialog(t, parent=wizard)
        try:
            dialog.run()
            super(AptProgressDialog, dialog).run()
        except dbus.exceptions.DBusException, e:
            msg = str(e)
            error = gtk.MessageDialog(parent=wizard, type=gtk.MESSAGE_ERROR,
                            buttons=gtk.BUTTONS_CLOSE,
                            message_format=msg)
            error.run()
            error.hide()
