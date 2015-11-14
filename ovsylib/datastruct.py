import struct,os
from ovsylib import datamover, compression
import math

intsize = 2
longsize = 4

def getString(binfile, length):
    ret = ""
    read = 0
    for i in range(length):
        char = struct.unpack("B", binfile.read(1))[0]
        if char != 0:
            ret = ret + chr(char)
        else:
            read = i + 1
            break
    if read < length:
        binfile.seek(length - read, 1)
    return ret

def outString(binfile, length, text):
    for i in range(length):
        if i < len(text):
            binfile.write(struct.pack("c", text[i].encode("ascii")))
        else:
            binfile.write(struct.pack("B", 0))


class header:

    def __init__(self):
        self.nfiles = 0
        self.dwpack = ""

    def loadMetaData(self, binfile):
        self.dwpack = getString(binfile, 8)
        #print(binfile.read(longsize * 3))
        data = struct.unpack("III", binfile.read(longsize * 3))
        self.nfiles = data[1]

    def writeMetaData(self, binfile, dry_run=False):
        if not dry_run:
            outString(binfile, 8, self.dwpack)
            binfile.write(struct.pack("I", 0))
            binfile.write(struct.pack("I", self.nfiles))
            binfile.write(struct.pack("I", 0))

    def printInfo(self):
        print("  header (%s) File count: %d" % (self.dwpack, self.nfiles))


class fileentry:

    def __init__(self):
        self.id = 0
        self.name = ""
        self.size = 0
        self.offset = 0
        self.compressed = False
        self.compr_object = None
        self.comp_size = 0
        self.import_from = ""
        self.origin_offset = None
        self.offset_writeback_location = 0

    def loadMetaData(self, binfile):
        data = struct.unpack("II", binfile.read(longsize * 2))
        self.id = data[1]
        self.name = getString(binfile, 260)
        data = struct.unpack("IIIII", binfile.read(longsize * 5))
        self.comp_size = data[1]
        self.size = data[2]
        if data[3] != 0:
            self.compressed = True
        self.offset = data[4]

    def createFromFile(self, name, filepath, compress=True):
        self.name = name
        self.size = os.path.getsize(filepath)
        self.import_from = filepath
        if compress > 0:
            self.compressed = True
            self.compr_object = compression.chuunicomp()
            self.compr_object.fromFutureImport(math.ceil(self.size / self.compr_object.chunksize))

    def _dump2file(self, fromfile, tofile):
        actual_len = self.size
        if self.compressed:
            actual_len = self.comp_size
        datamover.dd(fromfile, tofile, self.offset, actual_len)

    def dumpMyself(self, location, binfile):
        print(repr(location))
        with open(location, "wb") as savefile:
            self._dump2file(binfile, savefile)

    def extractMyself(self, location, binfile):
        print(repr(location))
        with open(location, "wb") as savefile:
            if self.compressed:
                binfile.seek(self.offset, 0)
                self.compr_object.decompress(binfile, savefile)
            else:
                self._dump2file(binfile, savefile)

    def writeMetadata(self, updated, dry_run=False):
        # remember to set the callback obejct
        if not dry_run:
            updated.write(struct.pack("I", 0))
            updated.write(struct.pack("I", self.id))
            outString(updated, 260, self.name)
            updated.write(struct.pack("I", 0))
        if self.compressed:
            self.compr_object.aftercompress_callback_obj = compression.after_comp_callback(updated.tell(), updated)
        if not dry_run:
            updated.write(struct.pack("I", self.comp_size))
            updated.write(struct.pack("I", self.size))
            compa = 0
            if self.compressed:
                compa = 1
            updated.write(struct.pack("I", compa))
            self.offset_writeback_location = updated.tell()
            updated.write(struct.pack("I", self.offset))

    def updateMyself(self, original, updated, metadata_offset, dry_run=False):
        if self.import_from != "":
            # if is a new file, compress it and write the result to the .pac
            # we didn't know the offset yet, so we must make a step back and fix this
            begin_data_pos = updated.tell()
            self.offset = begin_data_pos
            updated.seek(self.offset_writeback_location, 0)
            if not dry_run:
                updated.write(struct.pack("I", self.offset - metadata_offset))
            updated.seek(begin_data_pos, 0)
            with open(self.import_from, "rb") as importfile:
                if self.compressed:
                    self.compr_object.compress(importfile, updated, dry_run=dry_run)
                else:
                    if not dry_run:
                        datamover.dd(importfile, updated, 0, self.size)
        else:
            # write back the file
            # we do that again, since the size of some file inbetween may have changed
            begin_data_pos = updated.tell()
            old_offset = self.offset
            self.offset = begin_data_pos
            updated.seek(self.offset_writeback_location, 0)
            if not dry_run:
                updated.write(struct.pack("I", self.offset - metadata_offset))
            updated.seek(begin_data_pos, 0)
            if not dry_run:
                actual_len = self.size
                if self.compressed:
                    actual_len = self.comp_size
                datamover.dd(original, updated, self.origin_offset, actual_len)

    def loadCompressionInfo(self, binfile):
        binfile.seek(self.offset)
        self.compr_object = compression.chuunicomp()
        self.compr_object.fromBinfile(binfile)

    def adjustOffset(self, addendum):
        if self.origin_offset is None:
            self.origin_offset = self.offset + addendum
        self.offset = self.offset + addendum

    def printInfo(self):
        comprs = " no"
        if self.compressed:
            comprs = "yes"
        print("   file #%03d  %08x %10d  %s %10d  %s" %
              (self.id, self.offset, self.size, comprs, self.comp_size, self.name))

    def printDetailInfo(self):
        self.printInfo()
        if self.compr_object is not None:
            self.compr_object.printInfo()


class pacfile:

    def __init__(self):
        self.files = []
        self.header = header()
        self.metadata_offset = 0

    def loadFromFile(self, binfile):
        self.header.loadMetaData(binfile)
        self.loadFiles(binfile)
        self.metadata_offset = binfile.tell()
        self.adjustMetaoffDisplace()
        self.loadCompressionInfo(binfile)

    def loadFiles(self, binfile):
        for i in range(self.header.nfiles):
            newfile = fileentry()
            newfile.loadMetaData(binfile)
            self.files.append(newfile)

    def theorizeMetadataOffset(self):
        return len(self.files) * ((7 * longsize) + 260) + (longsize * 3 + 8)

    def adjustMetaoffDisplace(self):
        for f in self.files:
            f.adjustOffset(self.metadata_offset)

    def rollbackMetaoffDisplace(self):
        for f in self.files:
            f.adjustOffset(-self.metadata_offset)

    def loadCompressionInfo(self, binfile):
        for f in self.files:
            f.loadCompressionInfo(binfile)

    def getFileById(self, fid):
        for f in self.files:
            if f.id == fid:
                return f
        return None

    def createDestination(self, fid, destination):
        file = self.getFileById(fid)
        fname = file.name.replace("\\","/")
        fullpath = os.path.join(destination, fname)
        os.makedirs(os.path.dirname(fullpath), exist_ok=True)
        return fullpath

    def dumpFileId(self, fid, destination, binfile):
        file = self.getFileById(fid)
        fullpath = self.createDestination(fid, destination)
        file.dumpMyself(fullpath, binfile)

    def extractFileId(self, fid, destination, binfile):
        file = self.getFileById(fid)
        fullpath = self.createDestination(fid, destination)
        file.extractMyself(fullpath, binfile)

    def appendFile(self, file):
        """ The file is appended on the bottom, so the other files' offsets have to be
         updated only for what concerns the growth of the metadata """
        self.rollbackMetaoffDisplace()
        file.id = len(self.files)
        self.files.append(file)
        self.header.nfiles += 1
        self.metadata_offset = self.theorizeMetadataOffset()
        self.adjustMetaoffDisplace()

    def createCopy(self, original, filename, dry_run=False):
        with open(filename, "wb") as updatedversion:
            self.header.writeMetaData(updatedversion, dry_run=dry_run)
            for file in self.files:
                file.writeMetadata(updatedversion, dry_run=dry_run)
            for file in self.files:
                file.updateMyself(original, updatedversion, self.metadata_offset, dry_run=dry_run)

    def printInfo(self):
        print("Metadata size: %06x" % (self.metadata_offset,))
        self.header.printInfo()
        print("          id    offset       size  compress  size  filename")
        for file in self.files:
            file.printInfo()

    def printDetailInfo(self):
        print("Metadata size: %06x" % (self.metadata_offset,))
        self.header.printInfo()
        print("          id    offset       size  compress  size  filename")
        for file in self.files:
            file.printDetailInfo()
