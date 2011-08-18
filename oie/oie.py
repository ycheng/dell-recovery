#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «oie» - Display Green/Red screen on pass fail
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
################################################################################
import sys
import os
from gi.repository import Gtk, GdkPixbuf
import Dell.recovery_common as magic

if 'debug' in os.environ:
    OIE_DIRECTORY = '.'
else:
    OIE_DIRECTORY = '/usr/share/dell/oie'

class OIEGTK:
    def __init__(self, image):
        self.widgets = Gtk.Builder()
        self.widgets.add_from_file(os.path.join(OIE_DIRECTORY, 'oie.ui'))
        Gtk.Window.set_default_icon_from_file('/usr/share/pixmaps/dell-dvd.svg')
        self.widgets.connect_signals(self)
        if 'pass' in image:
            animation = GdkPixbuf.PixbufAnimation.new_from_file(os.path.join(OIE_DIRECTORY, 'pass.gif'))
        else:
            animation = GdkPixbuf.PixbufAnimation.new_from_file(os.path.join(OIE_DIRECTORY, 'fail.gif'))
        image = self.widgets.get_object('image')
        image.set_from_animation(animation)

    def run(self):
        """Runs the GTK application's main functions"""
        self.widgets.get_object('toplevel').show()
        Gtk.main()

    def destroy(self, widget=None, data=None):
        """Closes any open backend connections and stops GTK threads"""
        Gtk.main_quit()

type = 'pass'
if len(sys.argv) > 1:
    type = sys.argv[1]
oie = OIEGTK(type)
oie.run()

#Don't run OIE mode again
seed = '/cdrom/preseed/dell-recovery.seed'
if type == 'pass' and os.path.exists(seed):
    keys = magic.parse_seed(seed)
    keys['dell-recovery/oie_mode'] = 'false'
    magic.write_seed(seed, keys)
