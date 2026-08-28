#
# Author: Carlos Escuin
# HyCSim
# Adaptation of LHybrid [1]
# [1]: H. -Y. Cheng et al., "LAP: Loop-Block Aware Inclusion Properties for Energy-Efficient Asymmetric Last Level Caches," 2016 ACM/IEEE 43rd Annual International Symposium on Computer Architecture (ISCA), 2016, pp. 103-114, doi: 10.1109/ISCA.2016.19.
#
from BaseHybrid import *


class LHybridBlock(Block):
  """
  This class extends the fucntionality of the baseline Block class

  Loop-block (bool): A block is considered a loop-block if it has completed a full
  trip between the private levels and the LLC and it is clean
  """
  def __init__(self, address, blocksize, time, core_id, dirty=False, tcbit=False):
    super(LHybridBlock, self).__init__(address, blocksize, time, core_id, dirty, tcbit)
    self.loop_block = self.tcbit and not self.dirty

  def setTcbit(self):
    self.tcbit = True
    self.loop_block = self.tcbit and not self.dirty


class LHybrid(BaseHybrid):
  """
  LHybrid LLC model class
  Implementation of the hybrid LLC design proposal by Cheng et al. [1]
  """

  def allocate(self, cacheSet, block, warmup):
    #Look for an invalid frame from SRAM
    for j,f in enumerate(self.cacheFrames[cacheSet][self.nvm_ways:self.ways]):
      if not f.isValid():
        f.insertBlock(block, warmup) 
        return
    
    #If there is no invalid block in SRAM, one block in
    #SRAM would be migrated to STT-RAM or evicted from the LLC

    #If the incoming block is a loop-block, it will be the MRU
    #It should be placed in NVM
    if block.loop_block:
      self.allocateNVM(cacheSet, block, warmup)
    else:
      #Look for the MRU loop block
      candidates = []
      for j in range(self.nvm_ways,self.ways):
        f = self.cacheFrames[cacheSet][j]
        if f.block.loop_block:
          candidates.append(j)
      #If there are loop blocks in SRAM migrate the MRU block to NVM
      if candidates:
        migrate_way = candidates[0]
        migrate_mru = self.cacheFrames[cacheSet][migrate_way].block.mru
        for j in candidates:
          if self.cacheFrames[cacheSet][j].block.mru > migrate_mru:
            migrate_way = j
            migrate_mru = self.cacheFrames[cacheSet][migrate_way].block.mru
        self.allocateNVM(cacheSet, self.cacheFrames[cacheSet][migrate_way].block, warmup)
        self.cacheFrames[cacheSet][migrate_way].insertBlock(block, warmup) 
      #If there are not loop blocks in SRAM evict the LRU in SRAM
      else:
        for j in range(self.nvm_ways, self.ways):
          candidates.append(j)

        way = self.rpl_findVictim(cacheSet, candidates)
        self.cacheFrames[cacheSet][way].insertBlock(block, warmup) 

  def allocateNVM(self, cacheSet, block, warmup):
    #If there is an invalid entry in STT-RAM, the migrated block
    #would replace the invalid block.
    for j,f in enumerate(self.cacheFrames[cacheSet][:self.nvm_ways]):
      if not f.isValid() and self.disablingManager.fitInFrame(cacheSet, j, block.blocksize):
        f.insertBlock(block, warmup)
        return
    
    #An LRU loop-block would be evicted since non-loop-blocks cannot be in STT-RAM
    candidates = []
    way = -1
    for j,f in enumerate(self.cacheFrames[cacheSet][:self.nvm_ways]):
      if self.disablingManager.fitInFrame(cacheSet, j, block.blocksize):
        assert(f.block.loop_block)
        candidates.append(j)
    way = self.rpl_findVictim(cacheSet, candidates)

    if way != -1:
      self.cacheFrames[cacheSet][way].insertBlock(block, warmup)
    elif not warmup:
      self.bypass += 1

  def process_L2Rpl(self, warmup, address, time, blocksize, core_id, dirty):
    cacheSet = self.addressToCacheSet(address)
    way = self.findTagInSet(cacheSet, address)

    #If tcbit was set in L2, insert block with set tcbit
    tcbit = False                                
    if address in self.L2TcbitBlocks:                   
      tcbit = True                               
      del self.L2TcbitBlocks[address]                   

    block = LHybridBlock(address, self.blocksize, time, core_id, dirty, tcbit)

    # If the incoming block is dirty and it hits a block in STT-RAM, invalidate it
    if way > -1 and way < self.nvm_ways and block.dirty:
      if self.cacheFrames[cacheSet][way].block.tcbit:
        block.setTcbit()
      self.cacheFrames[cacheSet][way].invalidate()
      way = -1

    # If the block is present
    if way > -1:
      assert(not block.dirty)
      if self.cacheFrames[cacheSet][way].block.tcbit:
        block.setTcbit()
      self.rpl_touch(address, time)

    #Insert in SRAM
    else:
      self.allocate(cacheSet, block, warmup)

  def printCacheSetInfo(self, cacheSet):
    print('CacheSet ' + str(cacheSet) + '')
    for j,f in enumerate(self.cacheFrames[cacheSet]):
      string = 'Frame ' + str(j) + ' (' + str(f.alive_bytes) + 'B): Valid (' + str(f.isValid()) + ')'
      if f.isValid():
        string += ' A(' + str(hex(f.block.address)) + ') LB(' + str(f.block.loop_block) + ') R(' + str(f.block.tcbit) + ') D(' + str(f.block.dirty) + ') S(' + str(f.block.blocksize) + ')'
      print(string)


class CMPLHybrid(LHybrid):
  """
  Hybrid LLC implementing LHybrid and compression
  This class extends the LHybrid LLC model with compression
  """

  def process_L2Rpl(self, warmup, address, time, blocksize, core_id, dirty):
    cacheSet = self.addressToCacheSet(address)
    way = self.findTagInSet(cacheSet, address)

    #If tcbit was set in L2, insert block with set tcbit
    tcbit = False      
    if address in self.L2TcbitBlocks:
      tcbit = True                   
      del self.L2TcbitBlocks[address]

    block = LHybridBlock(address, blocksize, time, core_id, dirty, tcbit)

    # If the incoming block is dirty and it hits a block in STT-RAM, invalidate it
    if way > -1 and way < self.nvm_ways and block.dirty:
      if self.cacheFrames[cacheSet][way].block.tcbit:
        block.setTcbit()
      self.cacheFrames[cacheSet][way].invalidate()
      way = -1

    # If the block is present
    if way > -1:
      assert(not block.dirty)
      if self.cacheFrames[cacheSet][way].block.tcbit:
        block.setTcbit()
      self.rpl_touch(address, time)

    #Insert in SRAM
    else:
      self.allocate(cacheSet, block, warmup)

