#!/bin/sh
# vim:tw=0:ts=8:sw=8:et:

ISO=$HOME/ubuntu-dell-reinstall.iso
tmpdir=$(mktemp -d /tmp/geniso-XXXXXX)
mntdir=$(mktemp -d /tmp/genisomnt-XXXXXX)
trap 'rm -rf $tmpdir; umount $mntdir/isolinux/isolinux.bin; umount $mntdir; rmdir $tmpdir $mntdir' QUIT EXIT TERM HUP

echo "Creating Reinstallation ISO image. Please wait, this takes a while..."

# if we want to disable the nautilus popup, we would have to figure out which of the following gconf keys to reset.
#  /desktop/gnome/volume_manager/automount_drives, /desktop/gnome/volume_manager/automount_media, /apps/nautilus/desktop/volumes_visible, /apps/nautilus/preferences/media_automount, /apps/nautilus/preferences/media_automount_open

mkdir $tmpdir/up
dd if=/dev/sda bs=512 count=1 of=$tmpdir/up/mbr.bin
dd if=/dev/sda1 bs=1M | gzip -c > $tmpdir/up/upimg.bin

mount /dev/sda2 $mntdir
rm -f $mntdir/pagefile.sys
rm -f $mntdir/*.exe

# isolinux.bin needs to be writeable
cp $mntdir/isolinux/isolinux.bin $tmpdir/
mount --bind $tmpdir/isolinux.bin  $mntdir/isolinux/isolinux.bin

genisoimage -o $ISO \
    -input-charset utf-8 \
    -b isolinux/isolinux.bin -c isolinux/boot.catalog \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -pad -r -J -joliet-long -N -hide-joliet-trans-tbl \
    -cache-inodes -l \
    -publisher "Dell Inc." \
    -V "Dell Ubuntu Reinstallation Media" \
    $mntdir $tmpdir/up/

echo
echo
echo "The reinstallation ISO has been created."
echo "It is located in your home directory as $(basename $ISO)"
echo "The full path to the ISO is: $ISO"
echo
echo "Press <ENTER> to close this window"
read pause
