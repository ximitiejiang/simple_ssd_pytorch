#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 25 10:12:55 2019

@author: ubuntu
"""
import torch
import numpy as np
# %%
def bbox2delta(prop, gt, means, stds):
    """把筛选出来的正样本anchor与实际gt的差异转换成偏差坐标dx,dy,dw,dh, 并做normalize
    基本逻辑：由于神经网络从anchor中筛选出来的proposal是固定尺寸固定位置的，即使通过盒海战术能找到iou比较接近的，
    但往往还是跟gt_bbox有一定的偏差，所以需要神经网络去学习到这种偏差，因此需要有这个delta(即偏差)转换过程。
    偏差的求法很自然，但为了让偏差有一个统一的范围，实际是计算该偏差相对proposal的变化，也就是dx/w, dy/h, log(dw/w),log(dh/h)
    
    基本逻辑：由前面的卷积网络可以得到预测xmin,ymin,xmax,ymax，并转化成px,py,pw,ph.
    此时存在一种变换dx,dy,dw,dh，可以让预测值变成gx',gy',gw',gh'且该值更接近gx,gy,gw,gh
    所以目标就变成找到dx,dy,dw,dh，寻找的方式就是dx=(gx-px)/pw, dy=(gy-py)/ph, dw=log(gw/pw), dh=log(gh/ph)
    因此卷积网络前向计算每次都得到xmin/ymin/xmax/ymax经过head转换成dx,dy,dw,dh，力图让loss最小使这个变换
    最后测试时head计算得到dx,dy,dw,dh，就可以通过delta2bbox()反过来得到xmin,ymin,xmax,ymax
    """
    # 把anchor的xmin,ymin,xmax,ymax转换成x_ctr,y_ctr,w,h
    px = (prop[...,0] + prop[...,2]) * 0.5
    py = (prop[...,1] + prop[...,3]) * 0.5
    pw = (prop[...,2] - prop[...,0]) + 1.0  
    ph = (prop[...,3] - prop[...,1]) + 1.0
    # 把gt的xmin,ymin,xmax,ymax转换成xctr,yctr,w,h
    gx = (gt[...,0] + gt[...,2]) * 0.5
    gy = (gt[...,1] + gt[...,3]) * 0.5
    gw = (gt[...,2] - gt[...,0]) + 1.0  
    gh = (gt[...,3] - gt[...,1]) + 1.0
    # 计算dx,dy,dw,dh
    dx = (gx - px) / pw
    dy = (gy - py) / ph
    dw = torch.log(gw / pw)
    dh = torch.log(gh / ph)
    deltas = torch.stack([dx, dy, dw, dh], dim=-1) # (n, 4)
    # 归一化
    means = deltas.new_tensor(means).reshape(1,-1)   # (1,4)
    stds = deltas.new_tensor(stds).reshape(1,-1)      # (1,4)
    deltas = (deltas - means) / stds    # (n,4)-(1,4) / (1,4) -> (n,4) / (1,4) -> (n,4) 
    
    return deltas



def delta2bbox(prop, deltas, means=[0, 0, 0, 0], stds=[1, 1, 1, 1],
               max_shape=None, wh_ratio_clip=16 / 1000):
    """把模型预测的差异dx,dy,dw,dh(真值与anchor的差异)转换成实际的真实预测xmin,ymin,xmax,ymax
    bbox2delta是已知prop, gt求解deltas = gt - prop
    delta2bbox则是已知prop,delta求解gt = prop + deltas
    """
    means = deltas.new_tensor(means).repeat(1, deltas.size(1) // 4)
    stds = deltas.new_tensor(stds).repeat(1, deltas.size(1) // 4)
    denorm_deltas = deltas * stds + means
    dx = denorm_deltas[:, 0::4]
    dy = denorm_deltas[:, 1::4]
    dw = denorm_deltas[:, 2::4]
    dh = denorm_deltas[:, 3::4]
    max_ratio = np.abs(np.log(wh_ratio_clip))
    dw = dw.clamp(min=-max_ratio, max=max_ratio)
    dh = dh.clamp(min=-max_ratio, max=max_ratio)
    
    px = ((prop[:, 0] + prop[:, 2]) * 0.5).unsqueeze(1).expand_as(dx)
    py = ((prop[:, 1] + prop[:, 3]) * 0.5).unsqueeze(1).expand_as(dy)
    pw = (prop[:, 2] - prop[:, 0] + 1.0).unsqueeze(1).expand_as(dw)
    ph = (prop[:, 3] - prop[:, 1] + 1.0).unsqueeze(1).expand_as(dh)
    
    gw = pw * dw.exp()
    gh = ph * dh.exp()
    gx = torch.addcmul(px, 1, pw, dx)  # gx = px + pw * dx
    gy = torch.addcmul(py, 1, ph, dy)  # gy = py + ph * dy
    
    x1 = gx - gw * 0.5 + 0.5
    y1 = gy - gh * 0.5 + 0.5
    x2 = gx + gw * 0.5 - 0.5
    y2 = gy + gh * 0.5 - 0.5
    if max_shape is not None:
        x1 = x1.clamp(min=0, max=max_shape[1] - 1)
        y1 = y1.clamp(min=0, max=max_shape[0] - 1)
        x2 = x2.clamp(min=0, max=max_shape[1] - 1)
        y2 = y2.clamp(min=0, max=max_shape[0] - 1)
    bboxes = torch.stack([x1, y1, x2, y2], dim=-1).view_as(deltas)
    return bboxes


# %%
def landmark2delta(prop, gt, means, stds):
    """把筛选出来的正样本anchor作为proposal, 其每个proposal对应gt landmark作为gt, 转换每个proposal的中心点到gt的距离作为
    args:
        prop: (k, 4)代表propsal的anchor(有的算法取的是所有anchor，有的算法取的是正样本的anchor, 这里取正样本的anchor), xmin,ymin,xmax,ymax
        gt: (k, 5, 2)代表每个proposal的anchor所对应的gt_landmark
    return:
        deltas(k, 10)
    """
    # 把anchor的xmin,ymin,xmax,ymax转换成x_ctr,y_ctr,w,h
    px = (prop[...,0] + prop[...,2]) * 0.5  
    py = (prop[...,1] + prop[...,3]) * 0.5
    pw = (prop[...,2] - prop[...,0]) + 1.0  
    ph = (prop[...,3] - prop[...,1]) + 1.0
    # 把proposal的坐标扩维度
    px = px.reshape(-1, 1).expand(gt.size(0), gt.size(1))  # (41,) -> (41, 5)
    py = py.reshape(-1, 1).expand(gt.size(0), gt.size(1))
    pw = pw.reshape(-1, 1).expand(gt.size(0), gt.size(1))
    ph = ph.reshape(-1, 1).expand(gt.size(0), gt.size(1))
    # 计算偏差
    dx = (gt[:, :, 0] - px) / pw   # 中心点到每个prop的x相对w的比例距离(41, 5)
    dy = (gt[:, :, 1] - py) / ph   # 中心点到每个prop的y相对h的比例距离(41, 5)
    deltas = torch.stack([dx, dy], dim=-1) # (41, 5, 2)
    # 归一化
    means = deltas.new_tensor(means[:2]).reshape(1,-1)   # (1,2)
    stds = deltas.new_tensor(stds[:2]).reshape(1,-1)      # (1,2)
    deltas = (deltas - means) / stds    # (n,5,2)-(1,2) / (1,2) -> (n,5,2) / (1,2) -> (n,5,2) 
    # 展平便于计算loss
    deltas = deltas.reshape(-1, 10)
    return deltas  # (41, 10)
    

def delta2landmark(prop, deltas, means, stds):
    """把预测的偏差deltas与prop组合计算得到gt=prop + deltas
    args:
        prop(m, 4): 即anchors
        deltas(m, 10): 即预测输出
    return:
        ldmks(m, 5, 2)
    """    
    stds = deltas.new_tensor(stds)[:2]
    means = deltas.new_tensor(means)[:2]
    # 计算anchor prop的中心点坐标和w, h
    px = (prop[...,0] + prop[...,2]) * 0.5   # (m,)
    py = (prop[...,1] + prop[...,3]) * 0.5
    pw = (prop[...,2] - prop[...,0]) + 1.0  
    ph = (prop[...,3] - prop[...,1]) + 1.0
    prop = torch.stack([px, py, pw, ph], dim=1)  # (m, 4)
    # 逆归一化，然后乘以w,h, 然后平移
    ldmks = torch.cat([prop[:, :2] + (deltas[:, :2] * stds + means) * prop[:, 2:],  # (k,2)
                       prop[:, :2] + (deltas[:, 2:4] * stds + means) * prop[:, 2:],
                       prop[:, :2] + (deltas[:, 4:6] * stds + means) * prop[:, 2:],
                       prop[:, :2] + (deltas[:, 6:8] * stds + means) * prop[:, 2:],
                       prop[:, :2] + (deltas[:, 8:10] * stds + means) * prop[:, 2:]], dim=1)
    ldmks = ldmks.reshape(ldmks.shape[0], -1, 2)
    return ldmks


# %%
def bbox2lrtb(points, bboxes):
    """用来把每个points位置坐标(x,y)转化成points距离所对应bbox四边的距离(left,right, top,bottom)
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



def lrtb2bbox(points, lrtb, max_shape=None):
    """用来把point的位置尺寸(left,right,top, bottom)转换成bbox坐标(xmin,ymin,xmax,ymax)
    Decode distance prediction to bounding box.

    Args:
        points (Tensor): Shape (n, 2), [x, y].
        lrtb (Tensor): Distance from the given point to 4
            boundaries (left, top, right, bottom).
        max_shape (tuple): Shape of the image.

    Returns:
        Tensor: Decoded bboxes.
    """
    x1 = points[:, 0] - lrtb[:, 0]
    y1 = points[:, 1] - lrtb[:, 1]
    x2 = points[:, 0] + lrtb[:, 2]
    y2 = points[:, 1] + lrtb[:, 3]
    if max_shape is not None:
        x1 = x1.clamp(min=0, max=max_shape[1] - 1)
        y1 = y1.clamp(min=0, max=max_shape[0] - 1)
        x2 = x2.clamp(min=0, max=max_shape[1] - 1)
        y2 = y2.clamp(min=0, max=max_shape[0] - 1)
    return torch.stack([x1, y1, x2, y2], -1)

    