#!/bin/sh -e

#Dell factory GRUB2 binary builder
#Creates binaries for use within Dell factory process

#This script can be easily ran on a development system by modifying some
#environment variables for source and target directories.
#
# TARGET specifies where the binaries will end up
# PATCHES specifies where the patches
# GRUB_SRC specifies where to find a grub source tree containing a debian/
#          directory (including a collection of distro patches)

[ -n "$TARGET" ] || TARGET=/var/lib/dell-recovery
[ -n "$PATCHES" ] || PATCHES=/usr/share/dell/grub/patches
mkdir -p $TARGET

common_modules="loadenv part_gpt fat ntfs ext2 ntfscomp search linux boot \
                minicmd cat cpuid chain halt help ls reboot echo test     \
                configfile sleep keystatus normal true font"

#x86_64-efi, EFI target.  requires grub-efi-amd64-bin
if [ -d /usr/lib/grub/x86_64-efi ] &&
   [ ! -f $TARGET/grubx64.efi ]; then
    echo "Building bootloader images for x86_64-efi"
    efi_modules="efi_uga efi_gop gfxterm part_gpt"
    grub-mkimage -c /usr/share/dell/grub/embedded.cfg \
                 --prefix=/factory                    \
                 -o $TARGET/grubx64.efi -O x86_64-efi \
                 $common_modules $efi_modules
fi

#i386-pc, legacy target.  reguires grub-pc-bin
if [ -d /usr/lib/grub/i386-pc ] &&
   [ ! -f $TARGET/core.img ]; then
    echo "Building bootloader images for i386-pc"
    x86_modules="biosdisk part_msdos vga vga_text"
    #build core image
    grub-mkimage -c /usr/share/dell/grub/embedded.cfg \
                 --prefix=/factory                    \
                 -o $TARGET/core.img -O i386-pc       \
                 $common_modules $x86_modules
    #copy boot.img
    cat /usr/lib/grub/i386-pc/boot.img > $TARGET/boot.img
fi

#generate grub.cfg
OS=$(lsb_release -s -d)
sed "s,#OS#,$OS,; /^search/d" \
    /usr/share/dell/grub/recovery_partition.cfg \
    > $TARGET/grub.cfg

#blank grubenv so we can fail installs
grub-editenv $TARGET/grubenv unset recordfail

#grub-setup.exe
if [ -d /usr/lib/gcc/i586-mingw32msvc ] &&
   [ -d $PATCHES ] &&
   [ -x /usr/bin/quilt ] &&
   [ -x /usr/bin/autogen ] &&
   [ -x /usr/bin/autoreconf ] &&
   [ -x /usr/bin/libtoolize ] &&
   [ -x /usr/bin/bison ] &&
   [ -x /usr/bin/flex ] &&
   [ -x /usr/bin/dpkg-source ] &&
   [ ! -f $TARGET/grub-setup.exe ]; then
    echo "Building bootloader installer for mingw32"
    BUILD_DIR=$(mktemp -d)
    echo $BUILD_DIR
    cd $BUILD_DIR
    if [ -n "$GRUB_SRC" ]; then
        cp -R $GRUB_SRC .
    else
        apt-get source -qq grub2
    fi
    cd grub2*
    for item in $(ls $PATCHES); do
        echo $item >> debian/patches/series
        cp -f $PATCHES/$item debian/patches
    done
    QUILT_PATCHES=debian/patches quilt push -a -q
    ./autogen.sh >/dev/null 2>&1
    CC=i586-mingw32msvc-gcc ./configure --host=i586-mingw32msvc >/dev/null
    cd grub-core/gnulib && make > /dev/null && cd ../..
    make grub_script.tab.h grub_script.yy.h grub-setup.exe >/dev/null
    cp grub-setup.exe $TARGET
    rm -rf $BUILD_DIR
fi
