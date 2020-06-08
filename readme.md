# For what is this program?
The purpose is to recover truncated  files in XFS system

This program maybe useful with deleted files only if they are very big, xfs_undelete doesn't recover it and you are lucky to find its inode numbers manually 
# What's the difference from xfs_undelete?
+ It supports only big files have stored in B+Tree extent list format (xfs_undelete supports only linear extent list inode format)
+ It's semi-automatic (xfs_undelete is an automatic tool)
+ It recovers only one file by given inode number, for deleted files you need to find it in logs or using modified by yourself xfs_undelete (remove metadata checks, use V3 format \03 in magic inode signature, look at ctime)
# How to use if you have been truncated big file in your XFS partition
## Unmount immediately and make backup
Umount mounted to your */mountpoint* drive:

    kill $(lsof | grep */mountpoint*)
    umount */mountpoint*
Get drive with free space greater that XFS volume size at */recoverypoint/* and make backup:

    dd if=/dev/<partition> of=/recoverypoint/dump.img bs=512 conv=noerror,sync status=progress
    
For _partition_ use device with direct access to XFS partition (eg /dev/mapper/<logical volume name> for LVM volume, /dev/sdc2 for volume 2 on disk with index c etc) 

## Prepare for investigation (debian-based)

    apt install -y xfsprogs

## Investigate XFS entries about truncate file  
Dump XFS log:

    xfs_logprint /recoverypoint/dump.img  > /recoverypoint/xfslog.log

Remount it to */newromountpoint* and find inode number of truncated file *./file/to/have/been/truncated*:

    mount -o loop,ro /recoverypoint/dump.img /newromountpoint
    cd /newromountpoint/file/to/have/been/
    ls -i ./truncated
    
Last command will display you inode number of truncated file, write it down. For example in this case inode number will be 142:

    root@kvm:/mnt/img# ls -i /newromountpoint/file/to/have/been/truncated    
    142 /newromountpoint/file/to/have/been/truncated

Let us find it in /recoverypoint/xfs.log file dumped before.  
Just open xfs.log file, scroll down to the end and use find function to find the last entry about your inode with using find in reverse direction. 
For finding use hex representation of inode number (example 142 - 0x8e)

For example the last record about your inode in xfs.log looks like this:

    Oper (1): tid: 78fb472e  len: 56  clientid: TRANS  flags: none
    INODE: #regs: 2   ino: 0x69  flags: 0x1   dsize: 0
            blkno: 96  len: 32  boff: 4608
    Oper (2): tid: 78fb472e  len: 176  clientid: TRANS  flags: none
    INODE CORE
    magic 0x494e mode 0100644 version 3 format 2
    nlink 1 uid 0 gid 0
    atime 0x5ed9354f mtime 0x5ed9354c ctime 0x5ed9354c
    size 0x0 nblocks 0x0 extsize 0x0 nextents 0x0
    naextents 0x0 forkoff 0 dmevmask 0x0 dmstate 0x0
    flags 0x0 gen 0xc0bca634
    flags2 0x0 cowextsize 0x0

Then you need to find last record about your inode with size field not equals to 0x0 (_size 0x0_).
The record should be doubled (first entry is inode value before truncate, last entry is inode value after truncate in same transaction operation record):

    Oper (28): tid: 1a370530  len: 56  clientid: TRANS  flags: none
    INODE: #regs: 2   ino: 0x69  flags: 0x1   dsize: 0
        blkno: 96  len: 32  boff: 4608
    Oper (29): tid: 1a370530  len: 176  clientid: TRANS  flags: none
    INODE CORE
    magic 0x494e mode 0100644 version 3 format 3
    nlink 1 uid 0 gid 0
    atime 0x5ed8de53 mtime 0x5ed89f6e ctime 0x5ed8a481
    size 0x10000000000 nblocks 0xf552b59 extsize 0x0 nextents 0x4d3d
    naextents 0x0 forkoff 0 dmevmask 0x0 dmstate 0x0
    flags 0x0 gen 0xc0bca634
    flags2 0x0 cowextsize 0x0

    INODE CORE
    magic 0x494e mode 0100644 version 3 format 3
    nlink 1 uid 0 gid 0
    atime 0x5ed8de53 mtime 0x5ed89f6e ctime 0x5ed8a481
    size 0x0 nblocks 0x6bfd383 extsize 0x0 nextents 0x1289
    naextents 0x0 forkoff 0 dmevmask 0x0 dmstate 0x0
    flags 0x0 gen 0xc0bca634
    flags2 0x0 cowextsize 0x0

**If you have format 2 in first (old, before truncate) record - stop reading this instruction, you need xfs_undelete or similar program.**

Format 2 or 3 in new records (after truncate) is normal situation, after marking most of inodes as free XFS converts inode format from B+Tree extents format to linear extents format.  
    
Pay attention - transaction IDs (tids) may differ because of XFS is splitting long transactions in linked transactions.
 
From first entry you need to **write down** old inode properties before truncating:
 
    size 0x10000000000 nblocks 0xf552b59 extsize 0x0 nextents 0x4d3d
 
From this record you will need:
* old_size = 0x10000000000
* old_nblocks = 0xf552b59
* old_nextents = 0x4d3d

# Try to recover truncated data
## Modify inode descriptor
**Warning! After described modifications filesystem will be in inconsistent state, so all operations must be done in copy of partition**

Recovering inode header will not unmark extents as non-free, and when you try to copy some data in usual way from file with recovered inode header you will get i/o error.
It was a cause for developing described program.

    xfs_db -x /recoverypoint/dump.img
    xfs_db> inode 105 <-- recorded inode number of truncated file to recover
    xfs_db> write core.nblocks 0xf552b59 <-- old_nblocks
    xfs_db> write core.size 0x10000000000 <-- old_size
    xfs_db> write core.nextents 0x4d3d <-- old_nextents
    xfs_db> write core.format 3 <-- because of truncated inodes are converted from V3 to V2
    xfs_db> q

## Download and set up xfs_untruncate
    apt install -y xfsprogs git python3     
    git clone https://github.com/<path_to_this_repo>
    cd ./<repo_name>
    chmod +x ./xfs_untruncate

## Use xfs_untruncate

    ./xfs_untruncate -i /recoverypoint/dump.img -n <inode_number_in_dec> -o /recoverypoint/untruncatedfile
    
If all set ok and file and B+Tree extent data were not rewrited, recoveried file is at /recoverypoint/untruncatedfile 

# License, future reading and credits

## License
xfs_undelete is free software, written and copyrighted by Andrey Ivanov <git@aivanoff.ru>. You may use, distribute and modify it under the terms of the attached LGPL license. See the file license.md for details.

## Future reading
* http://ftp.ntu.edu.tw/linux/utils/fs/xfs/docs/xfs_filesystem_structure.pdf - a good book about xfs sturcture
* https://github.com/ianka/xfs_undelete - undelete program for inodes with V2 format type (linear extents) and without holes
* https://xfs.wiki.kernel.org/ - sources, mans and more

## Credits
This program have been written thanks to my слабоумие и отвага