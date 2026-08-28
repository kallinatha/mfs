#
# Author: Carlos Escuin
# HyCSim
#

import utils
from optparse import OptionParser

class DisablingManager(object):
  """
  Management of the faulty information of every cache frame
  """

  def __init__(self, vconf):
    self.faultMap = []
    self.nvm_ways = vconf['nvm_ways']
    self.ways = vconf['ways']
    self.sets = vconf['sets']

  def getAliveBytes(self, cacheSet, way):
    """
    Returns the number non faulty bytes the frame has
    """
    return self.faultMap[cacheSet][way]

  def fitInFrame(self, cacheSet, way, blocksize):
    """
    Returns true if, and only if, block fits in the frame
    That is, if the number non faulty bytes the frame has is greater or equal than the blocksize
    """
    return self.faultMap[cacheSet][way] >= blocksize

  def fitInNVMFrames(self, cacheSet, blocksize):
    """
    Returns true if, and only if, block fits in any of the frames of the set
    """
    for j in range(self.nvm_ways):
      if self.faultMap[cacheSet][j] >= blocksize:
        return True

    return False


class FrameDisabling(DisablingManager):
  """
  Extends Disabling Manager with Frame Disabling mechanism
  Disables the whole frame in one of its bytes is faulty
  """

  def __init__(self, vconf, fault_map):
    super(FrameDisabling, self).__init__(vconf)
    self.loadFaultMap(vconf, fault_map)

  def loadFaultMap(self, vconf, fault_map):
    self.faultMap = [[None for j in range(self.ways)] for i in range(self.sets)]
    fault_map_file = vconf['fault_map_dir'] + 'fault_map_' + fault_map + '.txt'
        
    lines = open(fault_map_file, 'r').readlines()

    for i in range(self.sets):
      for j in range(self.ways):
        alive_bytes = int(lines[i*self.ways + j].replace("\n", ""))
        if alive_bytes < vconf['blocksize']:
          alive_bytes = 0
        self.faultMap[i][j] = alive_bytes
        if j > self.nvm_ways:
          assert alive_bytes >= vconf['blocksize']


class ByteDisabling(DisablingManager):
  """
  Extends Disabling Manager with Byte Disabling mechanism
  Disables the byte if it is faulty
  Different cache frames may have different capacities
  """

  def __init__(self, vconf, fault_map):
    super(ByteDisabling, self).__init__(vconf)
    self.loadFaultMap(vconf, fault_map)

  def loadFaultMap(self, vconf, fault_map):
    self.faultMap = [[None for j in range(self.ways)] for i in range(self.sets)]
    fault_map_file = vconf['fault_map_dir'] + 'fault_map_' + fault_map + '.txt'
        
    lines = open(fault_map_file, 'r').readlines()

    for i in range(self.sets):
      for j in range(self.ways):
        alive_bytes = int(lines[i*self.ways + j].replace("\n", ""))
        self.faultMap[i][j] = alive_bytes
        if j > self.nvm_ways:
          assert alive_bytes >= vconf['blocksize']


def generate_NVMDeadLLC(vconf, size):
  fault_map_file = vconf['fault_map_dir'] + 'fault_map_NVMDead_' + size + '.txt'

  f = open(fault_map_file, 'w')
  for i in range(vconf['sets']):
    for j in range(vconf['ways']):
      if j < vconf['nvm_ways']:
        f.write('0\n')
      else:
        f.write(str(vconf['blocksize']) + '\n')
  f.close()


def generate_AliveLLC(vconf, size):
  fault_map_file = vconf['fault_map_dir'] + 'fault_map_ABA_' + size + '.txt'

  f = open(fault_map_file, 'w')
  for i in range(vconf['sets']):
    for j in range(vconf['ways']):
      f.write(str(vconf['blocksize']) + '\n')
  f.close()


def main():
  parser = OptionParser()
  parser.add_option("--alive", action="store_true", default=False)
  parser.add_option("--nvm-dead", action="store_true", default=False)
  parser.add_option("--size", type="string")

  (options, args) = parser.parse_args() 
  vconf = utils.load_version_json('BaseHybrid', '', options.size)

  if options.alive:
    generate_AliveLLC(vconf, options.size)
  elif options.nvm_dead:
    generate_NVMDeadLLC(vconf, options.size)


if __name__ == '__main__':
  main()
