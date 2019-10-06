#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 16 18:40:47 2019

@author: ubuntu
"""
import numpy as np
import cv2
from pycocotools.coco import COCO
import xml.etree.ElementTree as ET
from PIL import Image

from dataset.base_dataset import BasePytorchDataset

class COCODataset(BasePytorchDataset):
    """COCO数据集：用于物体分类和物体检测
    2007版+2012版数据总数16551(5011 + 11540), 可以用2007版做小数据集试验。
    主要涉及3个文件夹：
        - ImageSets:  txt主体索引文件
                * main: 所有图片名索引，以及每一类的图片名索引(可用来做其中某几类的训练)
                * Segmentation: 所有分割图片名索引
        - Annotations: xml标注文件(label,bbox)
        - JPEGImages: jpg图片文件(img)
        - labels: txt标签文件
        - SegmentationClass: png语义分割文件(mask)
        - SegmentationObject: png实例分割文件(seg)
    输入：
        root_path: 根目录
        ann_file: 标注文件xml目录
        subset_path: 子数据集目录，比如2007， 2012
        seg_prefix: 分割数据目录，从而识别是用语义分割数据还是用实例分割数据
        difficult: 困难bbox(voc中有部分bbox有标记为困难，比如比较密集的bbox，当前手段较难分类和回归)，一般忽略
    """
    
    CLASSES = ('person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
               'train', 'truck', 'boat', 'traffic_light', 'fire_hydrant',
               'stop_sign', 'parking_meter', 'bench', 'bird', 'cat', 'dog',
               'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe',
               'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
               'skis', 'snowboard', 'sports_ball', 'kite', 'baseball_bat',
               'baseball_glove', 'skateboard', 'surfboard', 'tennis_racket',
               'bottle', 'wine_glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
               'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot',
               'hot_dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
               'potted_plant', 'bed', 'dining_table', 'toilet', 'tv', 'laptop',
               'mouse', 'remote', 'keyboard', 'cell_phone', 'microwave',
               'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock',
               'vase', 'scissors', 'teddy_bear', 'hair_drier', 'toothbrush')
    
    def __init__(self,
                 root_path=None,
                 ann_file=None,
                 subset_path=None,
                 seg_prefix=None,
                 img_transform=None,
                 label_transform=None,
                 bbox_transform=None,
                 aug_transform=None,
                 seg_transform=None,
                 data_type=None,
                 with_difficult=False):
        
        self.with_difficult = with_difficult
        self.seg_prefix = seg_prefix
        self.data_type = data_type
        # 变换函数
        self.img_transform = img_transform
        self.label_transform = label_transform
        self.bbox_transform = bbox_transform
        self.aug_transform = aug_transform
        self.seg_transform = seg_transform
        
        self.ann_file = ann_file
        self.subset_path = subset_path
        # 注意：这里对标签值的定义跟常规分类不同，常规分类问题的数据集标签是从0开始，
        # 这里作为检测问题数据集标签是从1开始，目的是把0预留出来作为背景anchor的标签。
        self.class_label_dict = {cat: i + 1 for i, cat in enumerate(self.CLASSES)}  # 从1开始(1-20). 
        # 加载图片标注表(只是标注所需文件名，而不是具体标注值)，额外加了一个w,h用于后续参考
        self.img_anns = self.load_annotations(self.ann_file) 
        
    
    def load_annotations(self, ann_file):
        """从多个标注文件读取标注列表
        """
        self.coco = COCO(ann_file)
        self.cat_ids = self.coco.getCatIds()
        self.cat2label = {
            cat_id: i + 1
            for i, cat_id in enumerate(self.cat_ids)
        }
        # 获取图片名
        self.img_ids = self.coco.getImgIds()
        img_anns = []
        for i in self.img_ids:
            info = self.coco.loadImgs([i])[0]
            info['filename'] = info['file_name']
            img_anns.append(info)
        
        
        img_anns = []
        for i, af in enumerate(ann_file): # 分别读取多个子数据源
            with open(af) as f:
                img_ids = f.readlines()
            for j in range(len(img_ids)):
                img_ids[j] = img_ids[j][:-1]  # 去除最后的\n字符
            # 基于图片id打开annotation文件，获取img/xml文件名
            for img_id in img_ids:
                img_file = self.subset_path[i] + 'JPEGImages/{}.jpg'.format(img_id)
                xml_file = self.subset_path[i] + 'Annotations/{}.xml'.format(img_id)
                seg_file = self.subset_path[i] + self.seg_prefix + '{}.png'.format(img_id)
                # 解析xml文件
                # TODO: 这部分解析是否可去除，原本只是为了获得img的w,h,在img_transform里边已经搜集
                tree = ET.parse(xml_file)
                root = tree.getroot()
                size = root.find('size')
                width = int(size.find('width').text)
                height = int(size.find('height').text) 
                
                img_anns.append(dict(img_id=img_id, img_file=img_file, xml_file=xml_file, width=width, height=height))
                # 补充segment file路径信息
                segmented = int(root.find('segmented').text)
                if segmented:   # 只有在包含segmented这个flag时，才存入seg file的路径，否则存入None
                    img_anns[-1].update(seg_file=seg_file)
                else:
                    img_anns[-1].update(seg_file=None)
        return img_anns
    
    def parse_ann_info(self, idx):
        """解析一张图片所对应的xml文件，提取相关ann标注信息：bbox, label
        """
        gt_bboxes = []
        gt_labels = []
        gt_bboxes_ignore = []
        gt_masks_ann = []

        for i, ann in enumerate(ann_info):
            if ann.get('ignore', False):
                continue
            x1, y1, w, h = ann['bbox']
            if ann['area'] <= 0 or w < 1 or h < 1:
                continue
            bbox = [x1, y1, x1 + w - 1, y1 + h - 1]
            if ann.get('iscrowd', False):
                gt_bboxes_ignore.append(bbox)
            else:
                gt_bboxes.append(bbox)
                gt_labels.append(self.cat2label[ann['category_id']])
                gt_masks_ann.append(ann['segmentation'])

        if gt_bboxes:
            gt_bboxes = np.array(gt_bboxes, dtype=np.float32)
            gt_labels = np.array(gt_labels, dtype=np.int64)
        else:
            gt_bboxes = np.zeros((0, 4), dtype=np.float32)
            gt_labels = np.array([], dtype=np.int64)

        if gt_bboxes_ignore:
            gt_bboxes_ignore = np.array(gt_bboxes_ignore, dtype=np.float32)
        else:
            gt_bboxes_ignore = np.zeros((0, 4), dtype=np.float32)

        seg_map = img_info['filename'].replace('jpg', 'png')

        ann = dict(
            bboxes=gt_bboxes,
            labels=gt_labels,
            bboxes_ignore=gt_bboxes_ignore,
            masks=gt_masks_ann,
            seg_map=seg_map)
        
        # 原来voc的内容
        xml_file = self.img_anns[idx]['xml_file']
        tree = ET.parse(xml_file)
        root = tree.getroot()
        bboxes = []
        labels = []
        # 存放difficult=1的困难数据
        bboxes_difficult = []  
        labels_difficult = []
        for obj in root.findall('object'):
            name = obj.find('name').text
            label = self.class_label_dict[name]    # label range (1-20)            
            difficult = int(obj.find('difficult').text)
            bnd_box = obj.find('bndbox')
            bbox = [
                int(bnd_box.find('xmin').text),
                int(bnd_box.find('ymin').text),
                int(bnd_box.find('xmax').text),
                int(bnd_box.find('ymax').text)
            ]
            # 困难数据单独存放
            if difficult:  
                bboxes_difficult.append(bbox)
                labels_difficult.append(label)
            else:
                bboxes.append(bbox)
                labels.append(label)
        # 如果没有bbox数据：则放空
        if not bboxes:
            bboxes = np.zeros((0, 4))
            labels = np.zeros((0, ))
        else:
            bboxes = np.array(bboxes, ndmin=2) - 1
            labels = np.array(labels)
        # 如果没有困难数据：则放空
        if not bboxes_difficult:
            bboxes_difficult = np.zeros((0, 4))
            labels_difficult = np.zeros((0, ))
        else:
            bboxes_difficult = np.array(bboxes_difficult, ndmin=2) - 1
            labels_difficult = np.array(labels_difficult)
        # 组合数据包为dict
        ann = dict(
            bboxes=bboxes.astype(np.float32),
            labels=labels.astype(np.int64),
            bboxes_difficult=bboxes_difficult.astype(np.float32),
            labels_difficult=labels_difficult.astype(np.int64))
        return ann
        
    def __getitem__(self, idx):
        """虽然ann中有解析出difficult的情况，但这里简化统一没有处理difficult的情况，只对标准数据进行输出。
        """
        # 读取图片
        img_info = self.img_anns[idx]
        img_path = img_info['img_file']
        img = cv2.imread(img_path)
        
        # 读取bbox, label
        ann_dict = self.parse_ann_info(idx)
        gt_bboxes = ann_dict['bboxes']
        gt_labels = ann_dict['labels']

        # aug transform
        if self.aug_transform is not None:
            img, gt_bboxes, gt_labels = self.aug_transform(img, gt_bboxes, gt_labels)
        # basic transform
        if self.img_transform is not None:    
            # img transform
            img, ori_shape, scale_shape, pad_shape, scale_factor, flip = self.img_transform(img)
            # bbox transform: 传入的是scale_shape而不是ori_shape        
            gt_bboxes = self.bbox_transform(gt_bboxes, scale_shape, scale_factor, flip)
            # label transform
            gt_labels = self.label_transform(gt_labels)
        else:
            ori_shape = None
            scale_shape = None
            pad_shape = None
            scale_factor = None
            flip = None
        
        # 组合img_meta
        img_meta = dict(ori_shape = ori_shape,
                        scale_shape = scale_shape,
                        pad_shape = pad_shape,
                        scale_factor = scale_factor,
                        flip = flip)
        # 组合数据: 注意img_meta数据无法堆叠，尺寸不一的img也不能堆叠，所以需要在collate_fn中自定义处理方式
        data = dict(img = img,
                    img_meta = img_meta,
                    gt_bboxes = gt_bboxes,
                    gt_labels = gt_labels,
                    stack_list = ['img','gt_seg'])
        
        # TODO: 增加分割数据
        if self.seg_prefix is not None and img_info['seg_file'] is not None:
            seg_path = self.img_anns[idx]['seg_file']
            gt_seg = cv2.imread(seg_path)   # (h,w,3) or (h,w,3)
            gt_seg = self.seg_transform(gt_seg.squeeze(), scale_factor, flip)
            gt_seg = imrescale(gt_seg, seg_scale_factor, interpolation='nearest')
            gt_seg = gt_seg[None, ...]
            data.update(gt_seg = gt_seg)
        # 如果gt bbox数据缺失，则重新迭代随机获取一个idx的图片
        while True:
            if len(gt_bboxes) == 0:
                idx = np.random.choice(len(self.img_anns))
                self.__getitem__(idx)
            return data

    def __len__(self):
        return len(self.img_anns)


if __name__ == "__main__":
    pass 