#
# Author: Carlos Escuin
# HyCSim
#
from BaseHybrid import *
from LHybrid import *
from CMPHybrid import *
from optparse import OptionParser
import utils
import time


def load_trace(trace_name, vconf):
  print('Loading trace from mix ' + trace_name)
  return open(vconf['traces_dir'] + trace_name + ".txt", 'r').readlines()
    

def process_trace(hybrid_llc, trace, vconf):
  warmup = True
  if "Hits" in trace[0]:
    del trace[0]
  t0 = int(trace[0].split()[0])

  for line in trace:
    if "WARMUP COMPLETED" in line:
      warmup = False
      continue
    else:
      t = int(line.split()[0]) - t0
      core_id = int(line.split()[1])
      operation = line.split()[2]
      address = int(line.split()[3], 16)
      blocksize = vconf['blocksize']
      dirty = False
      tcbit = False
      if operation == "L2_Rpl":
        blocksize = int(line.split()[4])
        dirty = bool(int(line.split()[5]))
        tcbit = bool(int(line.split()[6]))

    if operation == "GetS":
      hybrid_llc.process_GetS(warmup, address, t, core_id)
    elif operation == "GetX":
      hybrid_llc.process_GetX(warmup, address, t, core_id)
    elif operation == "L2_Rpl":
      hybrid_llc.process_L2Rpl(warmup, address, t, blocksize, core_id, dirty)
    else:
      assert(False)


def dump_stats_trace(hybrid_llc, trace_name, vconf, fault_map):
  bytes_written = [[0 for j in range(vconf['ways'])] for i in range(vconf['sets'])]
  bytes_written_nvm = [[0 for j in range(vconf['ways'])] for i in range(vconf['sets'])]
  wb_avg = 0.0
  wb_avg_nvm = 0.0
  file_name = vconf['trace_out_dir'] + trace_name + '_' + vconf['suffix'] + '_' + fault_map + '.out'
  f = open(file_name , 'w')

  f.write('Hits: ' + str(hybrid_llc.hits) + "\n")
  f.write('Hits NVM: ' + str(hybrid_llc.hits_nvm) + "\n")
  f.write('Hits SRAM: ' + str(hybrid_llc.hits_sram) + "\n")
  f.write('Misses: ' + str(hybrid_llc.misses) + "\n")
  f.write('Bypass: ' + str(hybrid_llc.bypass) + "\n")

  for i in range(vconf['sets']):
    for j in range(vconf['ways']):
      f.write('Bytes_written.' + str(i) + '.' + str(j) + ': ' + str(hybrid_llc.cacheFrames[i][j].bytes_written) + '\n')

  f.close()


def main():
  parser = OptionParser()
  parser.add_option("-v", "--version", type='choice', choices=['LHybrid', 'BaseHybrid', 'CMPHybrid', 'CMPLHybrid'])
  parser.add_option("-t", "--trace", type='string')
  parser.add_option("-s", "--suffix", type='string', default='')
  parser.add_option("-f", "--fault-map", type='string')
  parser.add_option("--size", type='string', default='')
  parser.add_option("--sram-ways", type='int', default=4)
  parser.add_option("--nvm-ways", type='int', default=12) 

  (options,args) = parser.parse_args()

  start_time = time.time()

  vconf = utils.load_version_json(options.version, options.suffix, options.size, sram_ways=options.sram_ways, nvm_ways=options.nvm_ways)

  trace_name = options.trace
  trace = load_trace(trace_name, vconf)
  hybrid_llc = None
  if options.version == "LHybrid":
    hybrid_llc = LHybrid(vconf, options.fault_map)
  elif options.version == "CMPHybrid":
    hybrid_llc = CMPHybrid(vconf, options.fault_map)
  elif options.version == "CMPLHybrid":
    hybrid_llc = CMPLHybrid(vconf, options.fault_map)
  elif options.version == "BaseHybrid":
    hybrid_llc = BaseHybrid(vconf, options.fault_map)
  else:
    print("Need to specify a correct Hybrid LLC model")
    exit()
  process_trace(hybrid_llc, trace, vconf)
  dump_stats_trace(hybrid_llc, trace_name, vconf, options.fault_map)

  print('Hits: ' + str(hybrid_llc.hits))
  print('Misses: ' + str(hybrid_llc.misses))
  print('Bypass: ' + str(hybrid_llc.bypass))
  print('Execute trace time: ' + str(time.time()-start_time))
  print('')
  

if __name__ == '__main__':
  main()

