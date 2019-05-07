#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue May  7 21:43:39 2019

@author: suliang

本部分用于对多模型进行集成，增强结果的精度
参考：https://github.com/MLWave/Kaggle-Ensemble-Guide/blob/master/src/kaggle_vote.py
"""

from collections import defaultdict, Counter
from glob import glob
import sys
import re

glob_files = sys.argv[1]
loc_outfile = sys.argv[2]
weights_strategy = "uniform"
if len(sys.argv) == 4:
  weights_strategy = sys.argv[3]

def kaggle_bag(glob_files, loc_outfile, method="average", weights="uniform"):
  pattern = re.compile(r"(.)*_[w|W](\d*)_[.]*")
  if method == "average":
    scores = defaultdict(list)
  with open(loc_outfile,"w") as outfile:
    #weight_list may be usefull using a different method
    weight_list = [1]*len(glob(glob_files))
    for i, glob_file in enumerate( glob(glob_files) ):
      print("parsing: {}".format(glob_file))
      if weights == "weighted":
         weight = pattern.match(glob_file)
         if weight and weight.group(2):
            print("Using weight: {}".format(weight.group(2)))
            weight_list[i] = weight_list[i]*int(weight.group(2))
         else:
            print("Using weight: 1")
      # sort glob_file by first column, ignoring the first line
      lines = open(glob_file).readlines()
      lines = [lines[0]] + sorted(lines[1:])
      for e, line in enumerate( lines ):
        if i == 0 and e == 0:
          outfile.write(line)
        if e > 0:
          row = line.strip().split(",")
          for l in range(1,weight_list[i]+1):
            scores[(e,row[0])].append(row[1])
    for j,k in sorted(scores):
      outfile.write("%s,%s\n"%(k,Counter(scores[(j,k)]).most_common(1)[0][0]))
    print("wrote to {}".format(loc_outfile))

kaggle_bag(glob_files, loc_outfile, weights=weights_strategy)