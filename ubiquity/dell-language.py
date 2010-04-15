#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-language» - A Ubiquity plugin to force a title
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

NAME = 'dell-language'
AFTER = 'language'
WEIGHT = 12
OEM = False

from ubiquity.plugin import *

class PageGtk(PluginUI):
    '''This plugin is a hack really, but it should at least accomplish it's
       primary task, keeping the title sane'''
    def __init__(self, controller, *args, **kwargs):
        self.plugin_widgets = None
        #If we are in factory mode there will be nothing here and this page stays away
        with open('/proc/cmdline','r') as fd:
            if 'dell-recovery/recovery_type' not in fd.readline():
                return
        self.controller = controller
        import gtk
        self.plugin_widgets = gtk.Label()

    def plugin_get_current_page(self):
        self.plugin_widgets.get_parent_window().set_title('Dell Recovery')
        self.controller.go_forward()
        return self.plugin_widgets
