#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  2 11:31:23 2019

@author: ubuntu
"""

gpus = [0]
parallel = False
distribute = False                       
n_epochs = 10
imgs_per_core = 2               # 如果是gpu, 则core代表gpu，否则core代表cpu(等效于batch_size)
workers_per_core = 2
save_checkpoint_interval = 1     # 每多少个epoch保存一次epoch
work_dir = '/home/ubuntu/mytrain/retinanet_resnet50_voc/'
resume_from = None               # 恢复到前面指定的设备
load_from = None
load_device = 'cuda'             # 额外定义用于评估预测的设备: ['cpu', 'cuda']，可在cpu预测
lr = 0.001
img_size = (1333, 800)

lr_processor = dict(
        type='list',
        params = dict(
                step=[6, 8],       # 代表第2个(从1开始算)
                lr = [0.0005, 0.0001],
                warmup_type='linear',
                warmup_iters=500,
                warmup_ratio=1./3))

logger = dict(
                log_level='INFO',
                log_dir=work_dir,
                interval=1)

model = dict(
        type='one_stage_detector')
        
backbone=dict(
        type='resnet',
        params=dict(
                depth=50,
                pretrained= '/home/ubuntu/MyWeights/pytorch/resnet50-19c8e357.pth',
                out_indices=(0, 1, 2, 3),  # 代表第几组block输出
                strides=(1, 2, 2, 2)))

neck=dict(
        type='fpn',
        params=dict(
                in_channels=(256, 512, 1024, 2048),
                out_channels=256,
                use_levels=(1, 2, 3),  # 表示作用在哪几层，但mmdetection中FPN只使用了1,2,3层，0层丢弃
                num_outs=5,  # 额外输出一层
                extra_convs_on_inputs=True
                ))

head=dict(
        type='retinanet_head',
        params=dict(
                input_size=img_size,
                num_classes=21,
                in_channels=(256, 256, 256, 256, 256),
                base_scale=4,
                ratios = [1/2, 1, 2],
                anchor_strides=(8, 16, 32, 64, 128),
                target_means=(.0, .0, .0, .0),
                target_stds=(0.1, 0.1, 0.2, 0.2),
                alpha=0.25,
                gamma=2,
                ))

assigner = dict(
        type='max_iou_assigner',
        params=dict(
                pos_iou_thr=0.5,
                neg_iou_thr=0.4,
                min_pos_iou=0.))

sampler = dict(
        type='posudo_sampler',
        params=dict(
                ))

neg_pos_ratio = 3  
nms = dict(
        type='nms',
        pre_nms=4000,
        score_thr=0.02,
        max_per_img=200,
        params=dict(
                iou_thr=0.5)) # nms过滤iou阈值

transform = dict(
        img_params=dict(
                mean=[123.675, 116.28, 103.53],  # 采用pytorch的模型，且没有归一化
                std=[58.395, 57.12, 57.375],
                norm=False,     # 归一化img/255
                to_rgb=True,    # bgr to rgb
                to_tensor=True, # numpy to tensor 
                to_chw=True,    # hwc to chw
                flip_ratio=None,
                scale=img_size,  # 选择300的小尺寸
                size_divisor=32,
                keep_ratio=True),
        label_params=dict(
                to_tensor=True,
                to_onehot=None),
        bbox_params=dict(
                to_tensor=True
                ),
        aug_params=None)

transform_val = dict(
        img_params=dict(
                mean=[123.675, 116.28, 103.53],  # 采用pytorch的模型，且没有归一化
                std=[58.395, 57.12, 57.375],
                norm=False,
                to_rgb=True,    # bgr to rgb
                to_tensor=True, # numpy to tensor 
                to_chw=True,    # hwc to chw
                flip_ratio=None,
                scale=[1333, 800],  # 选择300的小尺寸
                size_divisor=32,
                keep_ratio=True),
        label_params=dict(
                to_tensor=True,
                to_onehot=None),
        bbox_params=dict(
                to_tensor=True
                ),
        aug_params=None)

data_root_path='/home/ubuntu/MyDatasets0/voc/VOCdevkit/'
trainset = dict(
        type='voc',
        repeat=0,
        params=dict(
                root_path=data_root_path, 
                ann_file=[data_root_path + 'VOC2007/ImageSets/Main/trainval.txt',
                          data_root_path + 'VOC2012/ImageSets/Main/trainval.txt'], #分为train.txt, val.txt, trainval.txt, test.txt
                img_prefix=[data_root_path + 'VOC2007/',
                          data_root_path + 'VOC2012/'],
                ))
valset = dict(
        type='voc',
        repeat=0,
        params=dict(
                root_path=data_root_path, 
                ann_file=[data_root_path + 'VOC2007/ImageSets/Main/test.txt'],
                img_prefix=[data_root_path + 'VOC2007/'],                         #注意只有2007版本有test.txt，到2012版取消了。
                ))

trainloader = dict(
        params=dict(
                shuffle=True,
                batch_size=imgs_per_core,
                num_workers=workers_per_core,
                pin_memory=False,   # 数据送入GPU进行加速(默认False)
                drop_last=False,
                collate_fn='dict_collate',    # 'default_collate','multi_collate', 'dict_collate'
                sampler=None))

valloader = dict(        
        params=dict(
                shuffle=False,
                batch_size=1,
                num_workers=workers_per_core,
                pin_memory=False,   # 数据送入GPU进行加速(默认False)
                drop_last=False,
                collate_fn='dict_collate',    # 'default_collate','multi_collate', 'dict_collate'
                sampler=None))

optimizer = dict(
        type='sgd',
        params=dict(
                lr=lr, 
                momentum=0.9, 
                weight_decay=5e-4))

