#
# Author: Carlos Escuin
# HyCSim
#
import json

CONFIG_FILE = 'config.json'

def load_version_json(version, suffix='', size='', sram_ways=0, nvm_ways=0):
  config = {}

  with open(CONFIG_FILE) as versions_json:
    versions_config = json.load(versions_json)

  #Import general config
  for key,value in versions_config['general'].items():
    config[key] = value

  if size !='':
    config['size'] = size
  config['size'] = convertToMemorySize(str(config['size']))

  if sram_ways != 0 or nvm_ways!= 0:
    config['sram_ways'] = sram_ways
    config['nvm_ways'] = nvm_ways

  config['ways'] = config['nvm_ways'] + config['sram_ways']
  config['sets'] = int(config['size'] / config['blocksize'] / config['ways'])

  #Import version config
  for key,value in versions_config[version].items():
    config[key] = value

  #Add suffix
  if suffix != '':
    config['suffix'] = config['suffix'] + suffix

  return config


def convertToMemorySize(size_str):
  if "MB" in size_str:
    return 2**20 * int(size_str.split("MB")[0])
  elif "kB" in size_str:
    return 2**10 * int(size_str.split("kB")[0])

