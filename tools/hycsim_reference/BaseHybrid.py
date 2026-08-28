#
# Author: Carlos Escuin
# HyCSim
#
import math
from DisablingManager import *


class Block(object):
  """
  Baseline cache block class
  
  Parameters
  ----------
  address (int, hex): Physical address of the block
  blocksize (int): Size of the data of the block in bytes
  time (int): Last time the block has been used or touched in the LLC
  core_id (int): Id of the cpu the block belongs to
  dirty (bool): Whether the block is dirty or not
  tc_bit: Whether the block has completed a full trip between the private levels and the LLC
  """

  def __init__(self, address, blocksize, time, core_id, dirty=False, tc_bit=False):
    self.address = address
    self.blocksize = blocksize
    self.mru = time
    self.core_id = core_id
    self.dirty = dirty
    self.tcbit = tc_bit

  def setTcbit(self):
    self.tcbit = True


class Frame:
  """
  Cache frame class, representing the physical bitcells of the cache that hold a cache block

  Parameters
  ----------
  alive_bytes (int): Number of non-faulty bytes in the frame
  block (Block): Cache block held by the frame
  """

  def __init__(self, alive_bytes, block=None):
    self.alive_bytes = alive_bytes
    self.bytes_written = 0
    self.block = block
    self.valid = False
    self.insertions = 0
    if self.block != None:
      self.valid = True

  def invalidate(self):
    self.block = None
    self.valid = False

  def isValid(self):
    return self.valid

  def insertBlock(self, block, warmup):
    assert self.alive_bytes >= block.blocksize
    self.block = block
    self.valid = True
    if not warmup:
      self.bytes_written += block.blocksize
      self.insertions += 1


class BaseHybrid(object):
  """
  Conventional non-inclusive hybrid LLC functionalities

  Parameters
  ----------
  vconf (dict): Dictionary defining several cache parameters.
                Look at config.json and utils.py files for more information.
  fault_map: Name of the fault map file
  """

  def __init__(self, vconf, fault_map):
    self.sram_ways = vconf['sram_ways']
    self.nvm_ways = vconf['nvm_ways']
    self.ways = self.sram_ways + self.nvm_ways
    self.sets = vconf['sets']
    self.blocksize = vconf['blocksize']
    self.misses = 0
    self.hits = 0
    self.hits_nvm = 0
    self.hits_sram = 0
    self.bypass = 0
    self.disablingManager = None
    if vconf['disabling'] == "FrameDisabling":
      self.disablingManager = FrameDisabling(vconf, fault_map)
    else:
      self.disablingManager = ByteDisabling(vconf, fault_map)
    self.cacheFrames = [[Frame(self.disablingManager.getAliveBytes(i,j))
                         for j in range(self.ways)] for i in range(self.sets)]
    self.L2TcbitBlocks = {}

  def addressToCacheSet(self, address):
    """
    Set indexing fucntion. Convert the address to its location in the cache.
    Returns the number of the set the address belongs to.
    It assumes 4 banks and a simple xor function
    """
    #Get bits for the bank
    blocksize_bits = int(math.log(self.blocksize, 2))
    start_bank_bit = blocksize_bits
    bank_bits = extractKBits(address, 2, start_bank_bit)

    #Get two parts to xor
    start_set_bit = start_bank_bit + 2
    set_bits = int(math.log(self.sets, 2)) - 2
    x1 = extractKBits(address, set_bits, start_set_bit)
    x2 = extractKBits(address, set_bits, start_set_bit + set_bits)
    
    res = (x1 ^ x2) + (bank_bits << set_bits)

    return res

  def findTagInSet(self, cacheSet, address):
    """
    Given a cache index returns the way of the tag in a set if the block is present in the set.
    Returns -1 otherwise.
    """

    loc = -1

    for i, f in enumerate(self.cacheFrames[cacheSet]):
      if f.isValid() and f.block.address == address:
        loc = i

    return loc
  
  def isTagPresent(self, address):
    """
    Returns True if, and only if, the address is present in the cache
    """
    cacheSet = self.addressToCacheSet(address)
    way = self.findTagInSet(cacheSet, address)

    if way == -1:
      return False
    else:
      return True

  def printCacheSetInfo(self, cacheSet):
    print('CacheSet ' + str(cacheSet) + '')
    for j,f in enumerate(self.cacheFrames[cacheSet]):
      string = 'Frame ' + str(j) + ' (' + str(f.alive_bytes) + 'B): Valid (' + str(f.isValid()) + ')'
      if f.isValid():
        string += ' A(' + str(hex(f.block.address)) + ') R(' + str(f.block.tcbit) + ') D(' + str(f.block.dirty) + ') S(' + str(f.block.blocksize) + ')'
      print(string)

  def rpl_touch(self, address, time):
    """
    Updates the replacement algorithm information of the block corresponding to address
    """
    cacheSet = self.addressToCacheSet(address)
    way = self.findTagInSet(cacheSet, address)
    assert(way >= 0)

    f = self.cacheFrames[cacheSet][way]
    f.block.mru = time
    f.block.setTcbit()

  def allocate(self, cacheSet, block, warmup):
    """
    Allocates a block in the corresponding set selecting a victim block to be evicted
    if necessary
    """
    for i,f in enumerate(self.cacheFrames[cacheSet]):
      if not f.isValid() and self.disablingManager.fitInFrame(cacheSet, i, block.blocksize):
        self.cacheFrames[cacheSet][i].insertBlock(block, warmup)
        return
    
    candidates = []
    for i,f in enumerate(self.cacheFrames[cacheSet]):
      if self.disablingManager.fitInFrame(cacheSet, i, block.blocksize):
        candidates.append(i)

    way = self.rpl_findVictim(cacheSet, candidates)
    if way != -1:
      self.cacheFrames[cacheSet][way].insertBlock(block, warmup)
    elif not warmup:
      self.bypass += 1

  def rpl_findVictim(self, cacheSet, candidates):
    """
    LRU replacement algorithm to find a victim block from a pool of candidates
    in a giving set  
    """
    victim = -1

    if len(candidates) > 0:
      victim = candidates[0]
      victim_mru = self.cacheFrames[cacheSet][candidates[0]].block.mru

      for i in candidates:
        if self.cacheFrames[cacheSet][i].block.mru < victim_mru:
          victim = i
          victim_mru = self.cacheFrames[cacheSet][i].block.mru

    return victim

  def process_GetS(self, warmup, address, time, core_id):
    """
    Process a read request in the hybrid LLC of a block that is going to be read (load)
    """
    cacheSet = self.addressToCacheSet(address)
    hit = self.isTagPresent(address)
    way = -1

    if hit:
      way = self.findTagInSet(cacheSet, address)

      self.rpl_touch(address, time)
      self.L2TcbitBlocks[address] = 1

      #Invalidate the block if its dirty, private levels can write on it.
      if self.cacheFrames[cacheSet][way].block.dirty:
        self.cacheFrames[cacheSet][way].invalidate()

    if not warmup:
      if hit:
        self.hits += 1
        if way < self.nvm_ways:
          self.hits_nvm += 1
        else:
          self.hits_sram += 1
      else:
        self.misses += 1

  def process_GetX(self, warmup, address, time, core_id):
    """
    Process a read request in the hybrid LLC of a block that is going to be written (store)
    """
    cacheSet = self.addressToCacheSet(address)
    hit = self.isTagPresent(address)
    way = -1

    if hit:
      way = self.findTagInSet(cacheSet, address)
      self.rpl_touch(address, time)

      #Update: hit only if the block is dirty 
      hit = self.cacheFrames[cacheSet][way].block.dirty
 
      self.L2TcbitBlocks[address] = 1
      self.cacheFrames[cacheSet][way].invalidate()

    if not warmup:
      if hit:
        self.hits += 1
        if way < self.nvm_ways:
          self.hits_nvm += 1
        else:
          self.hits_sram += 1
      else:
        self.misses += 1

  def process_L2Rpl(self, warmup, address, time, blocksize, core_id, dirty):
    """
    Process the evictions of the private levels
    """
    cacheSet = self.addressToCacheSet(address)
    way = self.findTagInSet(cacheSet, address)

    #If tcbit was set in L2, insert block with set tcbit
    tcbit = False
    if address in self.L2TcbitBlocks:
      tcbit = True
      del self.L2TcbitBlocks[address]

    block = Block(address, self.blocksize, time, core_id, dirty, tcbit)

    #Block is not present
    if way == -1:
      way = self.allocate(cacheSet, block, warmup)
    else:
      assert(not block.dirty)
      self.rpl_touch(address, time)


###### Static Functions #######
# Extract 'k' bits from pos 'p'
def extractKBits(num, k, p):
  a = 2 ** p - 1
  b = 2 ** (k + p) - 1

  res = (num & (b - a)) >> p

  return res

