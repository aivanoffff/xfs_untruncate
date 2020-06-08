#!/usr/bin/python3

import argparse
import subprocess
import re


xfs_signature = b'XFSB'
xfs_db = 'xfs_db'
# TODO add block size detection
fs_blocksize = 4096

parser = argparse.ArgumentParser(prog="xfs_untruncate", usage='%(prog)s [options]', description='Reconstruct XFS file directly from XFS inode V3 two-level B-Tree metadata')
parser.add_argument('--image', '-i', type=str, help='filesystem image', required=True, action='store', dest='imageFile')
parser.add_argument('--inode', '-n', type=int, help='inode to recover', required=True, action='store', dest='targetInode')
parser.add_argument('--outFile', '-o', type=str, help='target file to write recovered data', required=False, action='store', dest='outFile')
parser.add_argument('--limit', '-l', type=str, help='maximum of first bytes to be recovered', required=False, action='store', dest='outLimit')

args = parser.parse_args()

print("Processing XFS filesystem image " + args.imageFile + " to recover inode " + str(args.targetInode))

fsraw = open(args.imageFile, 'rb')
fsraw.seek(0, 0)
if fsraw.read(4) == xfs_signature:
    print("XFS detected")
else:
    print("Unknown FS type")
    raise SyntaxError
fsraw.close();



result = subprocess.run([xfs_db, args.imageFile, '-c', 'inode {0}'.format(args.targetInode), '-c', 'p'], stdout=subprocess.PIPE)
resultstr = result.stdout.decode('utf-8')
if resultstr.find("Metadata corruption detected") != -1:
    print("Bad inode header - possibly incorrect number")
    raise SyntaxError
if resultstr.find("Metadata CRC error detected") != -1:
    print("Inode header corrupted")
    raise SyntaxError

inode_meta = dict(re.findall(r'(\S+)\s+=\s+(\S+)', resultstr))

if inode_meta['core.version'] != '3' or inode_meta['core.format'] != '3':
    print("Inode type unsupported - try using xfs_undelete")
    raise SyntaxError

# [startoff,startblock,blockcount,extentflag]
extentsMap = {}

def walkBTreeExtents(level, prevLevelCmd):
    if(level > 0):
        result = subprocess.run(prevLevelCmd + ['-c','p'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print("Error at level " + str(level) + " on cmd " + ' '.join(prevLevelCmd));
            return {}
        if result.stderr.decode('utf-8').find("Metadata CRC error detected") != -1:
            print("Error at level " + str(level) + " on cmd " + ' '.join(prevLevelCmd));
            return {}
        resultstr = result.stdout.decode('utf-8')
        curEntryMeta = dict(re.findall(r'(\S+)\s+=\s+(\S+)', resultstr))
        if curEntryMeta['magic'] != '0x424d4133':
            print("Incorrect magic at level " + level)
            return {}
        currentLevelextentsMap = {}
        for i in range(1, (int(curEntryMeta['numrecs']) + 1)):
            nextLevelCmd = prevLevelCmd + ['-c', 'addr ptrs[{0}]'.format(i)]
            currentLevelextentsMap.update(walkBTreeExtents(int(curEntryMeta['level']) - 1, nextLevelCmd))
        return currentLevelextentsMap
    if(level == 0):
        result = subprocess.run(prevLevelCmd + ['-c','p'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print("Error at level " + str(level) + " on cmd " + ' '.join(prevLevelCmd));
            return {}
        if result.stderr.decode('utf-8').find("Metadata CRC error detected") != -1:
            print("Error at level " + str(level) + " on cmd " + ' '.join(prevLevelCmd));
            return {}
        resultstr = result.stdout.decode('utf-8')
        return dict(re.findall(r'\d+:\[(\d+),(\d+,\d+,\d+)\]', resultstr))


for i in range(1,(int(inode_meta['u3.bmbt.numrecs']) + 1)):
    baseLevelCmd = [xfs_db, args.imageFile, '-c', 'inode {0}'.format(args.targetInode), '-c',
                    'addr u3.bmbt.ptrs[{0}]'.format(i)]
    extentsMap.update(walkBTreeExtents(int(inode_meta['u3.bmbt.level']) - 1, baseLevelCmd))

extentsMap = {int(k):v for k,v in extentsMap.items()}
totalRecoveredBytes = 0
lastWrittenBlock = 0
blockDivider = int(fs_blocksize / 512);
entryNumber = 0

def recoverData(ifFile, fileOffsetBlock, diskStartblock, blockcount):
    if args.outFile != None:
        result = subprocess.run(
            ['dd', 'if={0}'.format(ifFile), 'of={0}'.format(args.outFile), 'seek={0}'.format(fileOffsetBlock),
             'skip={0}'.format(diskStartblock), 'count={0}'.format(blockcount), 'bs={0}'.format(fs_blocksize),
             'conv=notrunc,sync'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print("Error while writting from block " + str(diskStartblock) + " to block " + str(fileOffsetBlock) + " count " + str(blockcount))
            print(result.stderr.decode('utf-8'))

extentsCheckRecover = []

for fileOffsetBlock in sorted(extentsMap.keys()):
    if args.outLimit:
        if fileOffsetBlock * fs_blocksize > int(args.outLimit):
            print("Breaking due to limit exceeded")
            break

    if fileOffsetBlock > lastWrittenBlock:
        print("\t" + str(entryNumber) + ": [" + str(lastWrittenBlock * blockDivider) + ".." + str(fileOffsetBlock * blockDivider - 1) + "]: hole")
        recoverData('/dev/zero', lastWrittenBlock, 0, fileOffsetBlock - lastWrittenBlock)
        extentsCheckRecover.append([lastWrittenBlock, fileOffsetBlock - lastWrittenBlock])
        totalRecoveredBytes = totalRecoveredBytes + (fileOffsetBlock - lastWrittenBlock) * fs_blocksize
        lastWrittenBlock = fileOffsetBlock
        entryNumber = entryNumber + 1

    [curStartblock, curBlockcount, curExtentflag] = extentsMap[fileOffsetBlock].split(',')
    print("\t" + str(entryNumber) + ": [" + str(int(lastWrittenBlock * blockDivider)) + ".." + str(int((lastWrittenBlock + int(curBlockcount)) * blockDivider - 1)) + "]: " + str(int(curStartblock) * blockDivider) + ".." + str((int(curStartblock) + int(curBlockcount)) * blockDivider - 1))
    recoverData(args.imageFile, fileOffsetBlock, int(curStartblock), int(curBlockcount))
    extentsCheckRecover.append([fileOffsetBlock, int(curBlockcount)])
    totalRecoveredBytes = totalRecoveredBytes + int(curBlockcount) * fs_blocksize
    lastWrittenBlock = lastWrittenBlock + int(curBlockcount)
    entryNumber = entryNumber + 1


lastRecord = [];
for recoveredRecord in extentsCheckRecover:
    if lastRecord:
        if lastRecord[0] + lastRecord[1] != recoveredRecord[0]:
            print("Missing extent record before " + str(recoveredRecord[0]))
    lastRecord = recoveredRecord;

print("Total recovered: " + str(totalRecoveredBytes))

if args.outFile == None:
    print("Warning! It have been dry run, to save data specify out file with -o option")




