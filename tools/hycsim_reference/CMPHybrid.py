#
# Author: Carlos Escuin
# HyCSim
#
from BaseHybrid import *

class CMPHybrid(BaseHybrid):
  """
  This class extends the functionality of the conventional hybrid LLC with compression
  Blocks are inserted assuming they have been compressed by a mechanism and taking into
  account the fault map information
  """

  def process_L2Rpl(self, warmup, address, time, blocksize, core_id, dirty):
    cacheSet = self.addressToCacheSet(address)
    way = self.findTagInSet(cacheSet, address)

    #If tcbit was set in L2, insert block with set tcbit
    tcbit = False                                
    if address in self.L2TcbitBlocks:                   
      tcbit = True                               
      del self.L2TcbitBlocks[address]                   

    block = Block(address, blocksize, time, core_id, dirty, tcbit)

    #Block is not present
    if way == -1:
      way = self.allocate(cacheSet, block, warmup)
    else:
      assert(not block.dirty)
      self.rpl_touch(address, time)

  def printCacheSetInfo(self, cacheSet):
    print('CacheSet ' + str(cacheSet) + '')                                                                         
    for j,f in enumerate(self.cacheFrames[cacheSet]):                                                               
      string = 'Frame ' + str(j) + ' (' + str(f.alive_bytes) + 'B): Valid (' + str(f.isValid()) + ')'               
      if f.isValid():                                                                                               
        string += ' A(' + str(hex(f.block.address)) + ') R(' + str(f.block.tcbit) + ') D(' + str(f.block.dirty) + ') S(' + str(f.block.blocksize) + ')'
      print(string)

