#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 20 10:07:24 2019

@author: ubuntu
"""
import torch
from model.anchor_assigner_sampler_lib import MaxIouAssigner, PseudoSampler
from model.bbox_regression_lib import bbox2delta

def get_anchor_target(anchor_list, gt_bboxes_list, gt_labels_list,
                      img_metas_list, assigner_cfg, sampler_cfg, 
                      num_level_anchors, target_means, target_stds):
        """在ssd算法中计算一个batch的多张图片的anchor target，也就是对每个anchor定义他所属的label和坐标：
        其中如果是正样本则label跟对应bbox一致，坐标跟对应bbox也一致；如果是负样本则label
        Input:
            anchor_list: (b, )(s, 4)
            gt_bboxes_list: (b, )(k, 4)
            gt_labels_list: (b, )(k, )
            img_metas_list： (b, )(dict)
        """
        # 初始化
        all_labels = []
        all_label_weights = []
        all_bbox_targets = []
        all_bbox_weights = []
        
        all_pos_inds_list = []
        all_neg_inds_list = []
        # 循环对每张图分别计算anchor target
        for i in range(len(img_metas_list)):        
            bbox_targets, bbox_weights, labels, label_weights, pos_inds, neg_inds = \
                get_one_img_anchor_target(anchor_list[i],
                                          gt_bboxes_list[i],
                                          gt_labels_list[i],
                                          img_metas_list[i],
                                          assigner_cfg,
                                          sampler_cfg,
                                          target_means,
                                          target_stds)
            # batch图片targets汇总
            all_bbox_targets.append(bbox_targets)   # (b, ) (n_anchor, 4)
            all_bbox_weights.append(bbox_weights)   # (b, ) (n_anchor, 4)
            all_labels.append(labels)                # (b, ) (n_anchor, )
            all_label_weights.append(label_weights)  # (b, ) (n_anchor, )
            all_pos_inds_list.append(pos_inds)       # (b, ) (k, )
            all_neg_inds_list.append(neg_inds)       # (b, ) (j, )
            
        # 对targets数据进行变换，把多张图片的同尺寸特征图的数据放一起，统一做loss
        all_bbox_targets = torch.stack(all_bbox_targets, dim=0)   # (b, n_anchor, 4)
        all_bbox_weights = torch.stack(all_bbox_weights, dim=0)   # (b, n_anchor, 4)
        all_labels = torch.stack(all_labels, dim=0)               # (b, n_anchor)
        all_label_weights = torch.stack(all_label_weights, dim=0) # (b, n_anchor)
        
        num_batch_pos = sum([inds.numel() for inds in all_pos_inds_list])
        num_batch_neg = sum([inds.numel() for inds in all_neg_inds_list])

        return (all_bbox_targets, all_bbox_weights, all_labels, all_label_weights, num_batch_pos, num_batch_neg)


def get_one_img_anchor_target(anchors, gt_bboxes, gt_labels, img_metas, 
                              assigner_cfg, sampler_cfg, target_means, target_stds):
    """针对单张图的anchor target计算： 本质就是把正样本的坐标和标签作为target
    提供一种挑选target的思路：先iou筛选出正样本(>0)和负样本(=0)，去掉无关样本(-1)
    然后找到对应正样本anchor, 换算出对应bbox和对应label
    
    args:
        anchors (s, 4)
        gt_bboxes (k, 4)
        gt_labels (k, )
        img_metas (dict)
        assigner_cfg (dict)
        sampler_cfg (dict)
    """
    # 1.指定: 指定每个anchor是正样本还是负样本(通过让anchor跟gt bbox进行iou计算来评价，得到每个anchor的)
    bbox_assigner = MaxIouAssigner(**assigner_cfg.params)
    assign_result = bbox_assigner.assign(anchors, gt_bboxes, gt_labels)
    assigned_gt_inds, assigned_gt_labels, ious = assign_result  # (m,), (m,) 表示anchor的身份, anchor对应的标签，[0,1,0,..2], [0,15,...18]
    
    # 2. 采样： 采样一定数量的正负样本, 通常用于预防正负样本不平衡
    #　但在SSD中没有采样只是区分了一下正负样本，所以这里只是一个假采样。(正负样本不平衡是通过最后loss的OHEM完成)
    bbox_sampler = PseudoSampler(**sampler_cfg.params)
    sampling_result = bbox_sampler.sample(*assign_result, anchors, gt_bboxes)
    pos_inds, neg_inds = sampling_result  # (k,), (j,) 表示正/负样本的位置号，比如[7236, 7249, 8103], [0,1,2...8104..]
    
    # 3. 初始化target    
    bbox_targets = torch.zeros_like(anchors)
    bbox_weights = torch.zeros_like(anchors)
    labels = anchors.new_zeros(len(anchors), dtype=torch.long)       # 借用anchors的device
    label_weights = anchors.new_zeros(len(anchors), dtype=torch.long)# 借用anchors的device
    # 4. 把正样本 bbox坐标转换成delta坐标并填入
    pos_bboxes = anchors[pos_inds]                   # (k,4)获得正样本bbox
    
    pos_assigned_gt_inds = assigned_gt_inds[pos_inds] - 1 # 表示正样本对应的label也就是gt_bbox是第0个还是第1个(已经减1，就从1-n变成0-n-1)
    pos_gt_bboxes = gt_bboxes[pos_assigned_gt_inds]  # (k,4)获得正样本bbox对应的gt bbox坐标
    
    pos_bbox_targets = bbox2delta(pos_bboxes, pos_gt_bboxes, target_means, target_stds)  
    bbox_targets[pos_inds] = pos_bbox_targets
    bbox_weights[pos_inds] = 1
    # 5. 把正样本labels填入
    labels[pos_inds] = gt_labels[pos_assigned_gt_inds] # 获得正样本对应label
    label_weights[pos_inds] = 1  # 这里设置正负样本权重都为=1， 如果有需要可以提高正样本权重
    label_weights[neg_inds] = 1
    
    return bbox_targets, bbox_weights, labels, label_weights, pos_inds, neg_inds


# %%
    
def get_point_target(points, regress_ranges, gt_bboxes_list, gt_labels_list, num_level_points):
    """计算target
    args:
        points: (k,2)，为已合并的points，由于对每张图都一样，所以points不需要分batch
        regress_ranges: ((-1,64),(64,128),(..),(..),(..))，由于对每张图都一样，所以regress_rnage不需要分batch
        gt_bboxes_list: (b,)(m, 4)
        gt_labels_list: (b,)(m,)
        num_level_points: (5,) 表示每层特征图上的points个数
    return
        all_bbox_targets
        all_labels
    """
    # 堆叠regress_ranges：让每个point对应一个regress range
    repeat_regress_ranges = []
    for i, ranges in enumerate(regress_ranges):
        repeat_regress_ranges.append(points.new_tensor(ranges).repeat(num_level_points[i], 1))
    regress_ranges = torch.cat(repeat_regress_ranges, dim=0)  # 堆叠 (k, 2)
    # 计算每张图的target
    num_imgs = len(gt_bboxes_list)
    all_bbox_targets = []
    all_labels = []
    for i in range(num_imgs):
        bbox_targets, labels = get_one_img_point_target(
                points, regress_ranges, gt_bboxes_list[i], gt_labels_list[i])
        all_bbox_targets.append(bbox_targets)  # (b,)(k,4)
        all_labels.append(labels)             # (b,)(k,)
    # 分拆到每个level
    
    return all_bbox_targets, all_labels  # TODO: 是否要变换成(5,)(k,)  (5,)(k,4)
    
    
    
def get_one_img_point_target(points, regress_ranges, gt_bboxes, gt_labels):
    """单张图的target计算
    1. 如果point在某一gt bbox内，则该点为正样本，否则为负样本
    2. 如果point对应最大l/r/t/b大于回归值，则该点不适合在该特征图，取为负样本
    args:
        points: (k, 2)
        regress_ranges: (k, 2)
        gt_bboxes: (m, 4)
        gt_labels: (m, )
    """
    num_points = points.shape[0]
    num_gts = gt_labels.shape[0]
    # 计算每个gt bbox的面积: 用于后边取最小面积
    areas = (gt_bboxes[:, 2] - gt_bboxes[:, 0] + 1) * (gt_bboxes[:, 3] - gt_bboxes[:, 1] + 1) # (m,)

    # 计算每个point与每个gt bbox的对应样本的l,r,t,b
    left, right, top, bottom = calc_lrtb(points, gt_bboxes)
    # 得到每个point针对每个gt bbox的target，然后再筛选
    bbox_targets = torch.stack([left,right,top,bottom], dim=-1) # (k, m, 4)
    
    # 1. 判断point是否在gt bbox中
    inside_gt_bbox_mask = bbox_targets.min(dim=-1)[0] > 0  # 取l,r,t,b中最小值>0，则说明l,r,t,b4个值都大于0，必在bbox内
    
    # 2. 判断point的最大回归值是在哪个回归范围，从而判断该点应该在哪张特征图
    regress_ranges = regress_ranges[:, None, :].expand(num_points, num_gts, 2)  # 变换为(k,m,2)便于跟(k,m)做运算
    max_regress_distance = bbox_targets.max(dim=-1)[0]     #(k, m) 
    inside_regress_range_mask = (
            max_regress_distance >= regress_ranges[..., 0]) & (
                    max_regress_distance <= regress_ranges[..., 1])   # 取l,r,t,b中最大值检查是否在regress range内
    # 3. 剔除mask中为0的: 如果一个point对应了多个gt，取面积更小的gt bbox作为该point对应的bbox
    areas = areas.repeat(num_points, 1)    # (k, m)
    areas[inside_gt_bbox_mask == 0] = 1e+8
    areas[inside_regress_range_mask == 0] = 1e+8
    min_areas, min_area_inds = areas.min(dim=1)    # 为每个point在m个gt中找到面积最小对应的gt bbox
    
    # 为每一个point指定一个label
    labels = gt_labels[min_area_inds]   # 取最小面积对应的那个bbox的label(都是作为正样本取标签，1-20)
    labels[min_areas == 1e+8] = 0       # 但如果该最小面积不在gt bbox，也不在回归范围，则属于负样本取标签=0
    # 取最小面积ind对应的bbox坐标
    # 注意这里筛选bbox_target的用法很容易忽略出错，是对第0,第1维度分别用列表筛选，得到的是两个列表交汇的位置的坐标，所以降维了。
    bbox_targets = bbox_targets[range(num_points), min_area_inds]   # (k,m,4)-> (k,4)
    return bbox_targets, labels  # (k,4) , (k,)
     
    
    
    
def calc_lrtb(points, bboxes):
    """计算每个points与每个gtbbox的left,right,top,bottom
    points(k,2), bboxes(m,4)
    returns:
        l(k,m), r(k,m), t(k,m), b(k,m) 
    """
    num_bboxes = bboxes.shape[0]
    # 计算中心点
    x = points[:, 0]
    y = points[:, 1]
    # 中心点堆叠
    xx = x[:, None].repeat(1, num_bboxes)
    yy = y[:, None].repeat(1, num_bboxes)
    # 计算中心点相对bbox边缘的左右上下距离left,right,top,bottom
    l = xx - bboxes[:, 0]
    r = bboxes[:, 2] - xx
    t = yy - bboxes[:, 1]
    b = bboxes[:, 3] - yy
    return l, r, t, b
        