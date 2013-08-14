#!/usr/bin/python
#
#       <01-jockey-drivers.py>
#
#       Installs additional drivers using Jockey
#        In order to be able to support updated drivers in the factory
#        it will build an apt pool of updated drivers and register
#        them with apt prior to the Jockey run.  This ensures
#        that each driver only gets installed once
#
#       Copyright 2009 Dell Inc.
#           Mario Limonciello <Mario_Limonciello@Dell.com>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.

import subprocess
import os
import signal

DONT_BUILD_DKMS_FILE = "/tmp/do_not_build_dkms_module"

class ProcessJockey():

    def kill_old(self):
        '''kill all old jockey processes'''
        p = subprocess.Popen(['ps', '-A'], stdout=subprocess.PIPE)
        out, err = p.communicate()
        for line in out.splitlines():
            if 'jockey-backend' in line:
                pid = int(line.split(None, 1)[0])
                os.kill(pid, signal.SIGKILL)

    def find_and_install_drivers(self):
        '''Uses jockey to detect and install necessary drivers'''

        #If we installed in a different language, this might actually not be working properly otherwise
        os.environ['LANG'] = 'C'
        os.environ['LANGUAGE'] = 'C'

        #spawn jockey backend inside the chroot (if we let service activation do it, it would be outside the chroot)
        backend = subprocess.Popen(["/usr/share/jockey/jockey-backend"])
        code = backend.poll()
        if (code and code != 0):
            print "Error starting jockey backend"
            exit(code)
      
        #call out jockey detection algorithms
        ret = subprocess.Popen(["jockey-text", "-l"],stdout=subprocess.PIPE)
        output = ret.communicate()[0]
        code = ret.wait()
        if (code != 0):
            print "jockey returned a non-zero return code"
        output = output.split('\n')

        #Build and array of things to install
        install = []
        biggest_nv = ''
        for line in output:
            if len(line) > 0:
                if 'nvidia' in line:
                    if not biggest_nv or (biggest_nv and line.split()[0] > biggest_nv.split()[0]):
                        biggest_nv = line
                elif 'Disabled' in line:
                    print "Marking for installation: %s" % line.split()[0]
                    install.append(line.split()[0])

        #Append the selected nvidia driver
        if len(biggest_nv) > 0 and 'Disabled' in biggest_nv:
            print "Marking for installation: %s" % biggest_nv.split()[0]
            install.append(biggest_nv.split()[0])

        #Install any detected drivers
        if len(install) > 0:
            #Disable modprobe during installation
            fake_binaries = ['/sbin/modprobe',
                             '/usr/sbin/update-initramfs',
                             '/sbin/modinfo']
            for binary in fake_binaries:
                os.rename(binary, '%s.REAL' % binary)
                with open(binary, 'w') as f:
                    print >>f, """\
#!/bin/sh
echo 1>&2
echo 'Warning: Fake %s called, doing nothing.' 1>&2
exit 0""" % binary
                os.chmod(binary, 0755)

            #Perform installation
            for item in install:
                print "Installing: %s" % item
                if item == "xorg:fglrx":
                    with open(DONT_BUILD_DKMS_FILE,'w'):
                        pass
                ret = subprocess.Popen(["jockey-text", "-e", item],stdout=subprocess.PIPE)
                output = ret.communicate()[0]
                code = ret.wait()
                if (code != 0):
                    print "Error installing: %s" % item
                if os.path.exists(DONT_BUILD_DKMS_FILE):
                    os.remove(DONT_BUILD_DKMS_FILE)

            #Re-enable fake binaries
            for binary in fake_binaries:
                os.rename('%s.REAL' % binary, binary)
            #Update initramfs now
            ret = subprocess.Popen(["/usr/sbin/update-initramfs", "-u"],stdout=subprocess.PIPE)
            output = ret.communicate()[0]
            code = ret.wait()
            if (code != 0):
                print "Error updating initramfs"

        else:
            print "No Jockey supported drivers necessary"

        backend.terminate()
        if code:
            exit(code)

if __name__ == "__main__":
    if os.path.exists('/usr/share/jockey/jockey-backend'):
        processor = ProcessJockey()
        processor.kill_old()
        processor.find_and_install_drivers()
    else:
        print "Jockey isn't installed on target.  Unable to detect drivers"
