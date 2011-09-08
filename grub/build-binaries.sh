#!/bin/sh -e

TARGET=/var/lib/dell-recovery
mkdir -p $TARGET

common_modules="loadenv part_gpt fat ntfs ext2 ntfscomp search linux boot \
                minicmd cat cpuid chain halt help ls reboot echo test     \
                configfile sleep keystatus normal true font"

#x86_64-efi, EFI target.  requires grub-efi-amd64-bin
if [ -d /usr/lib/grub/x86_64-efi ]; then
    echo "Building bootloader images for x86_64-efi"
    efi_modules="efi_uga efi_gop gfxterm part_gpt"
    grub-mkimage -c /usr/share/dell/grub/embedded.cfg \
                 --prefix=/factory                    \
                 -o $TARGET/grubx64.efi -O x86_64-efi \
                 $common_modules $efi_modules
fi

#i386-pc, legacy target.  reguires grub-pc-bin
if [ -d /usr/lib/grub/i386-pc ]; then
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
