#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  2 16:46:38 2019

@author: ubuntu
"""
import torch
import torch.nn as nn
import os
import time

from utils.prepare_training import get_config, get_logger, get_dataset, get_dataloader 
from utils.prepare_training import get_root_model, get_optimizer, get_lr_processor, get_loss_fn
from utils.visualization import vis_loss_acc
from utils.tools import accuracy
from utils.checkpoint import load_checkpoint, save_checkpoint
from utils.transform import to_device
    

class BatchProcessor(): 
    """batchProcessor独立出来，是为了让Runner更具有通用性：
    1. 只跟顶层的self.model做计算，不接触model下面的子模型：从而简化使用。
    """
    def __call__(self):
        raise NotImplementedError('BatchProcessor class is not callable.')


class BatchDetector(BatchProcessor):    
    def __call__(self, model, data, device, return_loss=True, **kwargs): # kwargs用来兼容分类时传入的loss_fn
        # TODO: 是否可以把to_device集成到transform中或者collate_fn中或者model_wrapper的scatter中？
        # 数据送入设备：注意数据格式问题改为在to_tensor中完成，数据设备问题改为在to_device完成
        imgs = to_device(data['img'], device)
        gt_bboxes = to_device(data['gt_bboxes'], device)
        gt_labels = to_device(data['gt_labels'], device)
        img_metas = data['img_meta']
        # 计算模型输出
        if not return_loss:
            bbox_list = model(imgs, img_metas, 
                              return_loss=False)
            outputs = dict(bbox_list=bbox_list)
            
        if return_loss:
            losses = model(imgs, img_metas, 
                           gt_bboxes=gt_bboxes, 
                           gt_labels=gt_labels, return_loss=True)  # 
            # 损失缩减：先分别对分类和回归损失进行求和，然后把分类和回归损失再求和。
            loss_sum = {}
            for name, value in zip(losses.keys(), losses.values()):
                loss_sum[name] = sum(data for data in value)
            loss = sum(data for data in loss_sum.values())
            outputs = dict(loss=loss)
            
        return outputs


class BatchClassifier(BatchProcessor):
    """主要完成分类器的前向计算，生成outputs"""
    def __call__(self, model, data, device, return_loss=True, loss_fn=None, **kwargs):
        # 数据送入设备
        img = to_device(data['img'], device)  # 由于model要送入device进行计算，且该计算只跟img相关，跟label无关，所以可以只送img到device
        label = to_device(data['gt_labels'], device)
        
        outputs = {}
        y_pred = model(img)
        # 这里需要先组合label：分类问题中的一个batch多张图多个label等效于检测中的一张图多个bbox对应多个label                          
        label = torch.cat(label, dim=0)               # (b,)
        acc1 = accuracy(y_pred, label, topk=1)
        outputs.update(acc1=acc1)
        
        if return_loss:
            # 计算损失(!!!注意，一定要得到标量loss)
            loss = loss_fn(y_pred, label)  # pytorch交叉熵包含了前端的softmax/one_hot以及后端的mean
            outputs.update(loss=loss)
        return outputs


def get_batch_processor(cfg):
    if cfg.task == 'classifier':
        return BatchClassifier()
    elif cfg.task == 'detector':
        return BatchDetector()
    else:
        raise ValueError('Wrong task input.')



class Runner():
    """创建一个runner类，用于服务pytorch中的模型训练和验证: 支持cpu/单gpu/多gpu并行训练/分布式训练
    Runner类用于操作一个主模型，该主模型可以是单个分类模型，也可以是一个集成检测模型。
    所操作的主模型需要继承自pytorch的nn.module，且包含forward函数进行前向计算。
    如果主模型是一个集成检测模型，则要求该主模型所包含的所有下级模型也要继承
    自nn.module且包含forward函数。
    """
    def __init__(self, cfg_path,):
        # 共享变量: 需要声明在resume/load之前，否则会把resume的东西覆盖
        self.c_epoch = 0
        self.c_iter = 0
        self.weight_ready = False
        self.buffer = {'loss': [],
                       'acc': [],
                       'lr':[]}
        # 获得配置
        self.cfg = get_config(cfg_path)
        # 检查文件夹和文件是否合法
        self.check_dir_file(self.cfg)
        #设置logger
        self.logger = get_logger(self.cfg.logger)
        self.logger.info('start logging info.')
        #设置设备
        if self.cfg.gpus > 0 and torch.cuda.is_available():
            self.device = torch.device("cuda")   # 设置设备GPU: "cuda"和"cuda:0"的区别？
            self.logger.info('Operation will start in GPU!')
        elif self.cfg.gpus == 0:
            self.device = torch.device("cpu")      # 设置设备CPU
            self.logger.info('Operation will start in CPU!')
        else:
            raise ValueError('can not define device on CPU or GPU!')
        #创建batch处理器
        self.batch_processor = get_batch_processor(self.cfg)
        #创建数据集
        self.trainset = get_dataset(self.cfg.trainset, self.cfg.transform)
        self.valset = get_dataset(self.cfg.valset, self.cfg.transform_val) # 做验证的变换只做基础变换，不做数据增强
        
        data = self.trainset[0]
        
        #创建数据加载器
        self.dataloader = get_dataloader(self.trainset, self.cfg.trainloader)
        self.valloader = get_dataloader(self.valset, self.cfg.valloader)
        # 创建模型并初始化
        self.model = get_root_model(self.cfg)
        
        # 创建损失函数
        self.loss_fn_clf = get_loss_fn(self.cfg.loss_clf)
        if self.cfg.get('loss_reg', None) is not None:
            self.loss_fn_reg = get_loss_fn(self.cfg.loss_reg)
        # 优化器：必须在model送入cuda之前创建
        self.optimizer = get_optimizer(self.cfg.optimizer, self.model)
        # 学习率调整器
        self.lr_processor = get_lr_processor(self, self.cfg.lr_processor)
        # 送入GPU
        # 包装并行模型是在optimizer提取参数之后，否则可能导致无法提取，因为并行模型在model之下加了一层module壳
        if self.cfg.parallel and torch.cuda.device_count() > 1:
            self.model = nn.DataParallel(self.model)
        self.model.to(self.device)
        # 注意：恢复或加载是直接加载到目标设备，所以必须在模型传入设备之后进行，确保设备匹配
        # 加载模型权重和训练参数，从之前断开的位置继续训练
        if self.cfg.resume_from:
            self.resume_training(checkpoint_path=self.cfg.resume_from, 
                                 map_location=self.device)  # 沿用设置的device
        # 加载模型权重，但没有训练参数，所以一般用来做预测
        elif self.cfg.load_from:
            load_device = torch.device(self.cfg.load_device)
            self._load_checkpoint(checkpoint_path=self.cfg.load_from, 
                                  map_location=load_device)
            self.weight_ready = True
      
    def check_dir_file(self, cfg):
        """检查目录和文件的合法性，防止运行中段报错导致训练无效"""
        # 检查文件合法性
        if cfg.get('resume_from', None) is not None:
            file = cfg.resume_from
            if not os.path.isfile(file):
                raise FileNotFoundError('resume_from file is not a file.')
        if cfg.get('load_from', None) is not None:
            file = cfg.load_from
            if not os.path.isfile(file):
                raise FileNotFoundError('load_from file is not a file.')
        # 检查路径合法性
        if cfg.get('work_dir', None) is not None:
            dir = cfg.work_dir
            if not os.path.isdir(dir):
                raise FileNotFoundError('work_dir is not a dir.')
        if cfg.get('data_root_path', None) is not None:
            dir = cfg.trainset.params.root_path
            if not os.path.isdir(dir):
                raise FileNotFoundError('trainset path is not a dir.')
    
    def current_lr(self):
        """获取当前学习率: 其中optimizer.param_groups有可能包含多个groups(但在我的应用中只有一个group)
        也就是为一个单元素list, 取出就是一个dict.
        即param_groups[0].keys()就包括'params', 'lr', 'momentum','dampening','weight_decay'
        返回：
            current_lr(list): [value0, value1,..], 本模型基本都是1个value，除非optimizer初始化传入多组lr
        """
        return [group['lr'] for group in self.optimizer.param_groups]  # 取出每个group的lr返回list，大多数情况下，只有一个group
    
    def train(self, vis=True):
        """用于模型在训练集上训练"""
        self.model.train() # module的通用方法，可自动把training标志位设置为True
        self.lr_processor.set_base_lr_group()  # 设置初始学习率(直接从optimizer读取到：所以save model时必须保存optimizer) 
        start = time.time()
        while self.c_epoch < self.cfg.n_epochs:
            self.lr_processor.set_regular_lr_group()  # 设置常规学习率(计算出来并填入optimizer)
            for self.c_iter, data_batch in enumerate(self.dataloader):
                self.lr_processor.set_warmup_lr_group() # 设置热身学习率(计算出来并填入optimizer)
                # 前向计算
                outputs = self.batch_processor(self.model, 
                                               data_batch, 
                                               self.device,
                                               return_loss=True,
                                               loss_fn=self.loss_fn_clf)
                # 反向传播: 注意随时检查梯度是否爆炸
                outputs['loss'].backward()  # 更新反向传播, 用数值loss进行backward()      
                self.optimizer.step()   
                self.optimizer.zero_grad()       # 每个batch的梯度清零
                # 存放结果
                self.buffer['loss'].append(outputs.get('loss', 0))
                self.buffer['acc'].append(outputs.get('acc1', 0))
                self.buffer['lr'].append(self.current_lr()[0])
                # 显示text
                if (self.c_iter+1)%self.cfg.logger.interval == 0:
                    lr_str = ','.join(['{:.4f}'.format(lr) for lr in self.current_lr()]) # 用逗号串联学习率得到一个字符串
                    log_str = 'Epoch [{}][{}/{}]\tloss: {:.4f}\tacc: {:.4f}\tlr: {}'.format(self.c_epoch+1, 
                            self.c_iter+1, len(self.dataloader), 
                            self.buffer['loss'][-1].item(), self.buffer['acc'][-1], lr_str)

                    self.logger.info(log_str)
            # 保存模型
            if self.c_epoch%self.cfg.save_checkpoint_interval == 0:
                self.save_training(self.cfg.work_dir)            
            self.c_epoch += 1
        times = time.time() - start
        self.logger.info('training finished with times(s): {}'.format(times))
        # 绘图
        if vis:
            vis_loss_acc(self.buffer, title='train')
        self.weight_ready= True
    
    def val(self, vis=True):
        """用于模型验证"""
        self.buffer = {'acc': []}  # 重新初始化buffer,否则acc会继续累加
        self.n_correct = 0    # 用于计算全局acc
        if self.weight_ready:
            self.model.eval()   # 关闭对batchnorm/dropout的影响，不再需要手动传入training标志
            for c_iter, data_batch in enumerate(self.valloader):
                with torch.no_grad():  # 停止反向传播，只进行前向计算
                    outputs = self.batch_processor(self.model, 
                                                   data_batch, 
                                                   self.device,
                                                   return_loss=False)
                    self.buffer['acc'].append(outputs['acc1'])
                # 计算总体精度
                self.n_correct += self.buffer['acc'][-1] * len(data_batch['gt_labels'])
            
            if vis:
                vis_loss_acc(self.buffer, title='val')
            self.logger.info('ACC on valset: %.3f', self.n_correct/len(self.valset))
        else:
            raise ValueError('no model weights loaded.')
    
    
    def save_training(self, out_dir):
        meta = dict(c_epoch = self.c_epoch,
                    c_iter = self.c_iter)
        filename = out_dir + 'epoch_{}.pth'.format(self.c_epoch + 1)
        optimizer = self.optimizer
        save_checkpoint(filename, self.model, optimizer, meta)
        
    
    def resume_training(self, checkpoint_path, map_location='default'):
        # 先加载checkpoint文件
        if map_location == 'default':
            device_id = torch.cuda.current_device()  # 获取当前设备
            checkpoint = load_checkpoint(self.model, 
                                         checkpoint_path, 
                                         map_location=lambda storage, loc: storage.cuda(device_id))
        else:
            checkpoint = load_checkpoint(self.model,
                                         checkpoint_path, 
                                         map_location=map_location)
        #再恢复训练数据
        self.c_epoch = checkpoint['meta']['c_epoch'] + 1  # 注意：保存的是上一次运行的epoch，所以恢复要从下一次开始
        self.c_iter = checkpoint['meta']['c_iter'] + 1
        self.optimizer.load_state_dict(checkpoint['optimizer'])
        self.logger.info('resumed epoch %d, iter %d', self.c_epoch, self.c_iter)
    
    
    def _load_checkpoint(self, checkpoint_path, map_location):
        self.logger.info('load checkpoint from %s'%checkpoint_path)
        return load_checkpoint(self.model, checkpoint_path, map_location)


class TFRunner(Runner):
    """用于支持tensorflow的模型训练"""
    pass
    

class MXRunner(Runner):
    """用于支持mxnet的模型训练"""
    pass
    
