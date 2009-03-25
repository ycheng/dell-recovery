#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «create_iso» - Dell Recovery DVD Command line Processing Script
#
# Copyright (C) 2009, Dell Inc.
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

import getopt
import atexit
import sys
import tempfile
import os
import subprocess
import shutil

def walk_cleanup(directory):
    for root,dirs,files in os.walk(directory, topdown=False):
        for name in files:
            os.remove(os.path.join(root,name))
        for name in dirs:
            os.rmdir(os.path.join(root,name))       

def unmount_drives(mntdir,tmpdir):
    #only unmount places if they actually still exist
    if os.path.exists(mntdir):
        subprocess.call(['umount', mntdir + '/.disk/casper-uuid-generic'])
        subprocess.call(['umount', mntdir + '/casper/initrd.gz'])
        ret=subprocess.call(['umount', mntdir])
        #only cleanup the mntdir if we could properly umount
        if ret is 0:
            walk_cleanup(mntdir)
            os.rmdir(mntdir)

    if os.path.exists(tmpdir):
        subprocess.call(['umount', tmpdir])
        walk_cleanup(tmpdir)
        os.rmdir(tmpdir)

def main(up, rp, iso):
    #create temporary workspace
    tmpdir=tempfile.mkdtemp()
    os.mkdir(tmpdir + '/up')
    mntdir=tempfile.mkdtemp()

    #cleanup any mounts on exit
    atexit.register(unmount_drives,mntdir,tmpdir)

    #mount the RP
    subprocess.call(['mount', rp , mntdir])

    #Cleanup the RP
    #FIXME, we should just ignore rather than delete these files
    for file in os.listdir(mntdir):
        if ".exe" in file or ".sys" in file:
            os.remove(mntdir + '/' + file)

    #If necessary, build the UP
    if not os.path.exists(mntdir + '/upimg.bin'):
        sys.stdout.write('Building UP\n')
        sys.stdout.flush()
        p1 = subprocess.Popen(['dd','if=' + up,'bs=1M'], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
        partition_file=open(tmpdir + '/up/' + 'upimg.bin', "w")
        partition_file.write(p2.communicate()[0])
        partition_file.close()

    #Renerate UUID
    sys.stdout.write('Generating UUID\n')
    sys.stdout.flush()
    uuid_args = ['/usr/share/dell/bin/create-new-uuid',
                          mntdir + '/casper/initrd.gz',
                          tmpdir + '/',
                          tmpdir + '/']
    uuid = subprocess.Popen(uuid_args)
    retval = uuid.poll()
    while (retval is None):
        retval = uuid.poll()
    if retval is not 0:
        print >> sys.stderr, \
            "create-new-uuid exited with a nonstandard return value."
        sys.exit(1)

    #if we have ran this from a USB key, we might have syslinux which will
    #break our build
    if os.path.exists(mntdir + '/syslinux'):
        if os.path.exists(mntdir + '/isolinux'):
            #this means we might have been alternating between
            #recovery media formats too much
            walk_cleanup(mntdir + '/isolinux')
            os.rmdir(mntdir + '/isolinux')
        shutil.move(mntdir + '/syslinux', mntdir + '/isolinux')
    if os.path.exists(mntdir + '/isolinux/syslinux.cfg'):
        shutil.move(mntdir + '/isolinux/syslinux.cfg', mntdir + '/isolinux/isolinux.cfg')
    #FIXME^^^, this needs to learn how to do it without writing to the RP so the RP can be read only
    # possible solution is commented below:
    #if os.path.exists(mntdir + '/syslinux') and not os.path.exists(mntdir + '/isolinux'):
    #    subprocess.call(['mount', '-o', 'ro' ,'--bind', mntdir + '/syslinux', mntdir + '/isolinux'])

    #Loop mount these UUIDs so that they are included on the disk
    subprocess.call(['mount', '-o', 'ro' ,'--bind', tmpdir + '/initrd.gz', mntdir + '/casper/initrd.gz'])
    subprocess.call(['mount', '-o', 'ro', '--bind', tmpdir + '/casper-uuid-generic', mntdir + '/.disk/casper-uuid-generic'])

    #Boot sector for ISO
    shutil.copy(mntdir + '/isolinux/isolinux.bin', tmpdir)

    #ISO Creation
    sys.stdout.write('Building ISO\n')
    sys.stdout.flush()
    genisoargs=['genisoimage', '-o', iso,
        '-input-charset', 'utf-8',
        '-b', 'isolinux/isolinux.bin', '-c', 'isolinux/boot.catalog',
        '-no-emul-boot', '-boot-load-size', '4', '-boot-info-table',
        '-pad', '-r', '-J', '-joliet-long', '-N', '-hide-joliet-trans-tbl',
        '-cache-inodes', '-l',
        '-publisher', 'Dell Inc.',
        '-V', 'Dell Ubuntu Reinstallation Media',
        mntdir + '/',
        tmpdir + '/up/']
    p3 = subprocess.Popen(genisoargs,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    retval = p3.poll()
    while (retval is None):
        output = p3.stderr.readline()
        if ( output != "" ):
            progress = output.split()[0]
            if (progress[-1:] == '%'):
                sys.stdout.write(progress[:-1] + " % Done\n")
                sys.stdout.flush()
        retval = p3.poll()
    if retval is not 0:
        print >> sys.stderr, \
            "genisoimage exited with a nonstandard return value."
        sys.exit(1)
    
if __name__ == '__main__':
    utility_part = ''
    recovery_part = ''
    iso_name = ''
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'u:r:i:')
    except getopt.GetoptError:
        sys.exit(1)
    for opt, arg in opts:
        if opt == '-u':
            utility_part = arg
        elif opt == '-r':
            recovery_part = arg
        elif opt == '-i':
            iso_name = arg
    
    if utility_part and recovery_part and iso_name:
        main(utility_part, recovery_part, iso_name)
        if 'SUDO_UID' in os.environ and 'SUDO_GID' in os.environ and os.path.exists(iso_name):
            os.chown(iso_name,int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']))
        sys.exit(0)
    else:
        print >> sys.stderr, \
            'ISO name, UP name, and RP name are required.  Cannot continue.\n'
        sys.exit(1)
